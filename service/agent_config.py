"""Explicit, non-interpolating Agent settings; ledger runtime stays authoritative."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values

from service.errors import ServiceError
from service.langsmith_exporter import LangSmithConfig, approved_endpoint


@dataclass(frozen=True)
class AgentSettings:
    mode: str = "fake"
    model: str = "openai/gpt-6.1-sol"
    openrouter_api_key: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    langsmith_api_key: str = field(default="", repr=False)
    langsmith_enabled: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project_id: str = ""
    langsmith_workspace_id: str | None = None
    langfuse_public_key: str = field(default="", repr=False)
    langfuse_secret_key: str = field(default="", repr=False)
    langfuse_base_url: str = "http://127.0.0.1:53000"
    langfuse_enabled: bool = False
    langfuse_content_enabled: bool = False
    observability_purpose: str = "interactive"
    prior_spend_usd: Decimal | None = None
    budget_id: str = "dec-h18-original"
    budget_authorization_ref: str | None = None
    compose_enabled: bool = False

    def public(self):
        # Keys and host-specific paths must never enter health or trace payloads.
        return {"mode": self.mode, "model": self.model,
                "provider_key_configured": bool(self.openrouter_api_key),
                "prior_spend_known": self.prior_spend_usd is not None,
                "budget_id": self.budget_id,
                "local_observability_enabled": self.langfuse_enabled,
                "local_observability_content_enabled": self.langfuse_enabled and self.langfuse_content_enabled and self.observability_purpose == "interactive",
                "remote_observability_enabled": self.langsmith_enabled,
                "remote_observability_content_policy": "metadata_only",
                "observability_purpose": self.observability_purpose,
                "compose_enabled": self.compose_enabled}


def load_agent_settings(path: str | Path | None = None, *, environ: Mapping[str, str] | None = None):
    """Allowlisted settings only. Never source shell code or modify os.environ.

    Explicit process settings win over the optional dotenv file. DATABASE_URL,
    NEO4J_*, PRIVATE_* and arbitrary provider URLs are deliberately not loaded.
    """
    values = {}
    if path is not None:
        try:
            file = Path(path)
            if not file.is_file() or file.stat().st_size > 64 * 1024:
                raise ValueError()
            values.update(dotenv_values(file, interpolate=False))
        except (OSError, ValueError):
            raise ServiceError("AGENT_ENV_UNAVAILABLE", 503) from None
    if environ is not None:
        values.update(environ)

    def value(key, default=""):
        return (values.get(key) or default).strip()

    mode = value("ONTO_MODEL_MODE", "fake")
    if mode not in {"disabled", "fake", "live"}:
        raise ServiceError("INVALID_MODEL_MODE", 503)
    model = value("ONTO_MODEL", "openai/gpt-6.1-sol")
    if model != "openai/gpt-6.1-sol":
        raise ServiceError("MODEL_NOT_APPROVED", 503)
    prior = value("ONTO_MODEL_PRIOR_SPEND_USD")
    try:
        spend = Decimal(prior) if prior else None
        if spend is not None and (not spend.is_finite() or spend < 0):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ServiceError("INVALID_PRIOR_MODEL_SPEND", 503) from None
    enabled = value("ONTO_LANGFUSE_ENABLED", "false")
    if enabled not in {"true", "false"}:
        raise ServiceError("INVALID_OBSERVABILITY_SETTING", 503)
    content_enabled = value("ONTO_LANGFUSE_CONTENT_ENABLED", "false")
    purpose = value("ONTO_OBSERVABILITY_PURPOSE", "interactive")
    if content_enabled not in {"true", "false"} or purpose not in {"interactive", "evaluation"}:
        raise ServiceError("INVALID_OBSERVABILITY_SETTING", 503)
    url = value("LANGFUSE_BASE_URL", value("LANGFUSE_HOST", "http://127.0.0.1:53000"))
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or not parsed.port
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError()
    except ValueError:
        raise ServiceError("LOCAL_LANGFUSE_REQUIRED", 503) from None
    budget_id = value("ONTO_MODEL_BUDGET_ID", "dec-h18-original")
    authorization = value("ONTO_MODEL_BUDGET_AUTHORIZATION") or None
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", budget_id):
        raise ServiceError("INVALID_MODEL_BUDGET_ID", 503)
    if authorization and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", authorization):
        raise ServiceError("INVALID_MODEL_BUDGET_AUTHORIZATION", 503)
    compose = value("ONTO_MODEL_COMPOSE", "false")
    if compose not in {"true", "false"}:
        raise ServiceError("INVALID_MODEL_COMPOSE_SETTING", 503)
    smith_enabled = value("ONTO_LANGSMITH_ENABLED", "false")
    try:
        if smith_enabled not in {"true", "false"}:
            raise ValueError()
        smith_endpoint = approved_endpoint(value("ONTO_LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
        if smith_enabled == "true":
            LangSmithConfig(api_key=value("ONTO_LANGSMITH_API_KEY"),
                project_id=value("ONTO_LANGSMITH_PROJECT_ID"), endpoint=smith_endpoint,
                workspace_id=value("ONTO_LANGSMITH_WORKSPACE_ID") or None)
    except ValueError:
        raise ServiceError("INVALID_LANGSMITH_CONFIGURATION", 503) from None
    result = AgentSettings(mode=mode, model=model,
        openrouter_api_key=value("ONTO_OPENROUTER_API_KEY"),
        openai_api_key=value("ONTO_OPENAI_API_KEY"),
        langsmith_api_key=value("ONTO_LANGSMITH_API_KEY"),
        langsmith_enabled=smith_enabled == "true", langsmith_endpoint=smith_endpoint,
        langsmith_project_id=value("ONTO_LANGSMITH_PROJECT_ID"),
        langsmith_workspace_id=value("ONTO_LANGSMITH_WORKSPACE_ID") or None,
        langfuse_public_key=value("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=value("LANGFUSE_SECRET_KEY"),
        langfuse_base_url=url.rstrip("/"), langfuse_enabled=enabled == "true",
        langfuse_content_enabled=content_enabled == "true", observability_purpose=purpose,
        prior_spend_usd=spend, budget_id=budget_id, budget_authorization_ref=authorization,
        compose_enabled=compose == "true")
    if result.langfuse_enabled and not (result.langfuse_public_key and result.langfuse_secret_key):
        raise ServiceError("LANGFUSE_KEYS_REQUIRED", 503)
    return result
