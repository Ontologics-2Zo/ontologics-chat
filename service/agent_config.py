"""Explicit, non-interpolating Agent settings; ledger runtime stays authoritative."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values

from service.errors import ServiceError


@dataclass(frozen=True)
class AgentSettings:
    mode: str = "fake"
    model: str = "openai/gpt-6.1-sol"
    openrouter_api_key: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    langsmith_api_key: str = field(default="", repr=False)
    langfuse_public_key: str = field(default="", repr=False)
    langfuse_secret_key: str = field(default="", repr=False)
    langfuse_base_url: str = "http://127.0.0.1:53000"
    langfuse_enabled: bool = False
    prior_spend_usd: Decimal | None = None
    budget_id: str = "dec-h18-original"
    budget_authorization_ref: str | None = None

    def public(self):
        # Keys and host-specific paths must never enter health or trace payloads.
        return {"mode": self.mode, "model": self.model,
                "provider_key_configured": bool(self.openrouter_api_key),
                "prior_spend_known": self.prior_spend_usd is not None,
                "budget_id": self.budget_id,
                "local_observability_enabled": self.langfuse_enabled}


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
    result = AgentSettings(mode=mode, model=model,
        openrouter_api_key=value("ONTO_OPENROUTER_API_KEY"),
        openai_api_key=value("ONTO_OPENAI_API_KEY"),
        langsmith_api_key=value("ONTO_LANGSMITH_API_KEY"),
        langfuse_public_key=value("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=value("LANGFUSE_SECRET_KEY"),
        langfuse_base_url=url.rstrip("/"), langfuse_enabled=enabled == "true",
        prior_spend_usd=spend, budget_id=budget_id, budget_authorization_ref=authorization)
    if result.langfuse_enabled and not (result.langfuse_public_key and result.langfuse_secret_key):
        raise ServiceError("LANGFUSE_KEYS_REQUIRED", 503)
    return result
