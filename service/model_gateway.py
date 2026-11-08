"""Host-owned, bounded model routing with durable accounting and no write tools.

Only a fixed set of synthetic routing features can cross this boundary. Raw
questions, ledger/BOM contents, arbitrary prompts, endpoints and credentials are
not request fields. Live execution requires explicit Host policy and a separately
initialized budget scope. This is not a general natural-language Agent.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
import math
import re
from time import monotonic
from typing import Literal, Protocol

import httpx
from pydantic import ConfigDict, Field, ValidationError, field_validator

from service.contracts import Contract, canonical_bytes, content_hash
from service.errors import ServiceError
from service.gateway import ToolContext
from service.identity import Principal


RouteFeature = Literal["fulfillment", "production", "materials", "inventory", "delivery", "ontology", "unknown"]
Capability = Literal["fulfillment_read", "material_analysis", "needs_clarification"]
FEATURES = ("fulfillment", "production", "materials", "inventory", "delivery", "ontology", "unknown")
CAPABILITIES = ("fulfillment_read", "material_analysis", "needs_clarification")


class ModelRequest(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
    call_id: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,200}$")
    features: tuple[RouteFeature, ...] = Field(min_length=1, max_length=7)
    profile_id: Literal["route-v1"] = "route-v1"
    retry_of: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    max_output_tokens: int = Field(default=128, ge=16, le=256)

    @field_validator("features")
    @classmethod
    def unique_features(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("DUPLICATE_ROUTE_FEATURE")
        return tuple(sorted(value))


class ModelContext(ToolContext):
    segment_id: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,200}$")
    budget_id: str = Field(default="DEC-H18", pattern=r"^[A-Za-z0-9_.:/-]{1,200}$")
    parent_span_id: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,200}$")
    workflow_version: str = Field(default="agent-workflow/1", max_length=100)
    run_id: str | None = Field(default=None, max_length=200)
    agent_version: str = Field(default="supervisor/1", max_length=100)
    prompt_version: Literal["route-synthetic/1"] = "route-synthetic/1"
    schema_version: Literal["route-output/1"] = "route-output/1"
    policy_version: str = Field(default="model-policy/1", max_length=100)
    deadline_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value):
        return ToolContext.aware_time(value)


class ModelUsage(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    source: Literal["fake", "provider", "unavailable"] = "unavailable"
    unknown_reason: str | None = Field(default="USAGE_NOT_RETURNED", pattern=r"^[A-Z][A-Z0-9_]{0,79}$")


class ModelResult(Contract):
    contract_version: Literal["model-v1"] = "model-v1"
    status: Literal["completed", "needs_review"]
    capability: Capability
    attempt_id: str
    execution_mode: Literal["fake", "live"]
    actual_provider: str | None
    actual_model: str | None
    usage: ModelUsage
    context: ModelContext
    replayed: bool = False


@dataclass(frozen=True)
class GatewayPolicy:
    budget_id: str = "DEC-H18"
    live_enabled: bool = False
    transmission_approved: bool = False
    input_usd_per_token: Decimal | None = None
    output_usd_per_token: Decimal | None = None
    pricing_version: str | None = None
    timeout_seconds: float = 20.0

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", self.budget_id):
            raise ValueError("INVALID_MODEL_BUDGET_ID")
        if type(self.live_enabled) is not bool or type(self.transmission_approved) is not bool:
            raise ValueError("INVALID_MODEL_POLICY")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("INVALID_MODEL_TIMEOUT")
        for price in (self.input_usd_per_token, self.output_usd_per_token):
            if price is not None and (not isinstance(price, Decimal) or not price.is_finite() or price < 0):
                raise ValueError("INVALID_MODEL_PRICING")


@dataclass(frozen=True)
class ProviderReply:
    capability: str | None
    usage: ModelUsage
    actual_provider: str | None
    actual_model: str | None
    provider_request_id: str | None = None
    error_code: str | None = None


class ProviderFailure(Exception):
    """Adapter errors carry safe codes only, never HTTP bodies or credentials."""

    def __init__(self, code="MODEL_PROVIDER_FAILED", *, usage=None, outcome_unknown=True):
        self.code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "MODEL_PROVIDER_FAILED"
        self.usage = usage or ModelUsage()
        self.outcome_unknown = outcome_unknown
        super().__init__(self.code)


class RouteProvider(Protocol):
    execution_mode: str
    provider_name: str
    model_id: str

    def input_token_bound(self, request: ModelRequest) -> int: ...
    def complete(self, request: ModelRequest, *, timeout_seconds: float) -> ProviderReply: ...


class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResult: ...


class FakeRouteProvider:
    execution_mode = "fake"
    provider_name = "fake"
    model_id = "synthetic-route/1"

    def __init__(self):
        self.calls = 0

    def input_token_bound(self, request):
        return 0

    def complete(self, request, *, timeout_seconds):
        self.calls += 1
        features = set(request.features)
        if features & {"materials", "inventory"}:
            capability = "material_analysis"
        elif features & {"fulfillment", "production", "delivery"}:
            capability = "fulfillment_read"
        else:
            capability = "needs_clarification"
        return ProviderReply(capability, ModelUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"), "fake", self.model_id)


class OpenRouterRouteProvider:
    """Explicit, non-streaming OpenRouter adapter; redirects/fallbacks are off.

    API contract: https://openrouter.ai/docs/api_reference/overview and
    https://openrouter.ai/docs/guides/routing/provider-selection . Availability,
    pricing and transmission authorization are checked separately by the Host.
    """

    execution_mode = "live"
    provider_name = "openrouter"
    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
    GENERATION_ENDPOINT = "https://openrouter.ai/api/v1/generation"
    MAX_RESPONSE_BYTES = 65536

    def __init__(self, api_key, *, model_id="openai/gpt-6.1-sol", provider_slug="openai", client=None,
                 input_usd_per_token=None, output_usd_per_token=None):
        if not isinstance(api_key, str) or not api_key or any(ord(c) < 32 for c in api_key):
            raise ServiceError("MODEL_CREDENTIAL_MISSING", 503)
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", model_id) or not re.fullmatch(r"[a-z0-9_.-]+", provider_slug):
            raise ServiceError("MODEL_PROFILE_INVALID", 503)
        self._api_key = api_key
        self.model_id = model_id
        self.provider_slug = provider_slug
        self._client = client
        self._price_caps = None
        if input_usd_per_token is not None and output_usd_per_token is not None:
            self.configure_price_caps(input_usd_per_token, output_usd_per_token)

    def configure_price_caps(self, input_price, output_price):
        if any(not isinstance(price, Decimal) or not price.is_finite() or price < 0 for price in (input_price, output_price)):
            raise ServiceError("MODEL_PRICING_UNVERIFIED", 409)
        caps = (input_price, output_price)
        if self._price_caps is not None and self._price_caps != caps:
            raise ServiceError("MODEL_PRICE_CAP_MISMATCH", 409)
        self._price_caps = caps

    @staticmethod
    def _per_million(price):
        sign, digits, exponent = price.as_tuple()
        exact = Decimal((sign, digits, exponent + 6))
        value = float(exact)
        if not math.isfinite(value):
            raise ServiceError("MODEL_PRICING_UNVERIFIED", 409)
        # JSON numeric serialization must never raise the approved ceiling.
        return math.nextafter(value, -math.inf) if Decimal(str(value)) > exact else value

    def _body(self, request):
        body = {
            "model": self.model_id, "stream": False, "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": "Choose one read-only capability from synthetic boolean routing features. Use material_analysis for materials or inventory; fulfillment_read for fulfillment, production or delivery; otherwise needs_clarification. Return only the requested JSON object. Never output facts, tools, actions or explanations."},
                {"role": "user", "content": json.dumps({feature: feature in request.features for feature in FEATURES}, sort_keys=True)},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "readonly_route", "strict": True,
                "schema": {"type": "object", "properties": {"capability": {"type": "string", "enum": list(CAPABILITIES)}},
                           "required": ["capability"], "additionalProperties": False}}},
            "provider": {"only": [self.provider_slug], "allow_fallbacks": False,
                         "require_parameters": True, "data_collection": "deny", "zdr": True},
            "plugins": [],
        }
        if self._price_caps is not None:
            body["provider"]["max_price"] = {"prompt": self._per_million(self._price_caps[0]),
                "completion": self._per_million(self._price_caps[1]), "request": 0}
        return body

    def input_token_bound(self, request):
        # Fixed ASCII prompts + seven booleans only. UTF-8 bytes with a generous
        # special/schema overhead is a conservative reservation, not actual usage.
        return len(canonical_bytes(self._body(request))) + 1024

    @staticmethod
    def _reject_constant(value):
        raise ValueError("INVALID_JSON_NUMBER")

    def _read(self, client, method, url, *, deadline, **kwargs):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ProviderFailure("MODEL_PROVIDER_TIMEOUT")
        with client.stream(method, url, headers={"Authorization": "Bearer " + self._api_key},
                           timeout=remaining, follow_redirects=False, **kwargs) as response:
            if response.status_code != 200:
                # Status alone is public-safe; provider error bodies may echo
                # credentials, inputs or internal upstream details.
                raise ProviderFailure(f"MODEL_PROVIDER_HTTP_{int(response.status_code)}")
            body = bytearray()
            for chunk in response.iter_bytes():
                if monotonic() >= deadline:
                    raise ProviderFailure("MODEL_PROVIDER_TIMEOUT")
                if len(body) + len(chunk) > self.MAX_RESPONSE_BYTES:
                    raise ProviderFailure("MODEL_PROVIDER_RESPONSE_TOO_LARGE")
                body.extend(chunk)
        try:
            result = json.loads(body, parse_float=Decimal, parse_constant=self._reject_constant)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError):
            raise ProviderFailure("MODEL_PROVIDER_RESPONSE_INVALID") from None

    @staticmethod
    def _usage(payload):
        raw = payload.get("usage")
        if not isinstance(raw, dict):
            return ModelUsage()
        def tokens(name):
            value = raw.get(name)
            return value if type(value) is int and value >= 0 else None
        cost = raw.get("cost")
        if type(cost) is int:
            cost = Decimal(cost)
        if not isinstance(cost, Decimal) or not cost.is_finite() or cost < 0:
            cost = None
        return ModelUsage(input_tokens=tokens("prompt_tokens"), output_tokens=tokens("completion_tokens"),
                          total_tokens=tokens("total_tokens"), cost_usd=cost, source="provider",
                          unknown_reason="COST_NOT_RETURNED" if cost is None else None)

    def complete(self, request, *, timeout_seconds):
        if self._price_caps is None:
            raise ProviderFailure("MODEL_PRICING_UNVERIFIED", outcome_unknown=False)
        deadline = monotonic() + timeout_seconds
        manager = nullcontext(self._client) if self._client is not None else httpx.Client(trust_env=False)
        try:
            with manager as client:
                payload = self._read(client, "POST", self.ENDPOINT, deadline=deadline, json=self._body(request))
                usage = self._usage(payload)
                model = payload.get("model")
                model = model if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", model) else None
                generation_id = payload.get("id")
                generation_id = generation_id if isinstance(generation_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", generation_id) else None
                actual_provider = payload.get("provider")
                if not isinstance(actual_provider, str) and generation_id:
                    try:
                        stats = self._read(client, "GET", self.GENERATION_ENDPOINT, deadline=deadline, params={"id": generation_id})
                        data = stats.get("data", {})
                        actual_provider = data.get("provider_name") if isinstance(data, dict) else None
                    except (ProviderFailure, httpx.HTTPError):
                        actual_provider = None
                actual_provider = actual_provider if isinstance(actual_provider, str) and re.fullmatch(r"[A-Za-z0-9_./ -]{1,100}", actual_provider) else None
                capability, error = None, None
                try:
                    choices = payload["choices"]
                    if len(choices) != 1 or choices[0].get("finish_reason") != "stop" or choices[0].get("error"):
                        raise ValueError
                    message = choices[0]["message"]
                    if message.get("tool_calls") or message.get("refusal"):
                        raise ValueError
                    decoded = json.loads(message["content"])
                    if not isinstance(decoded, dict) or set(decoded) != {"capability"} or decoded["capability"] not in CAPABILITIES:
                        raise ValueError
                    capability = decoded["capability"]
                except (KeyError, TypeError, ValueError, IndexError):
                    error = "MODEL_OUTPUT_INVALID"
                if model != self.model_id:
                    error = "MODEL_RETURNED_MODEL_MISMATCH"
                if actual_provider is None:
                    error = "MODEL_PROVIDER_UNCONFIRMED"
                elif actual_provider.casefold() != self.provider_slug.casefold():
                    error = "MODEL_RETURNED_PROVIDER_MISMATCH"
                return ProviderReply(capability, usage, actual_provider, model, generation_id, error)
        except ProviderFailure:
            raise
        except httpx.TimeoutException:
            raise ProviderFailure("MODEL_PROVIDER_TIMEOUT") from None
        except httpx.HTTPError:
            raise ProviderFailure("MODEL_PROVIDER_TRANSPORT_ERROR") from None
        except Exception:
            raise ProviderFailure("MODEL_PROVIDER_FAILED") from None


class _BoundModelPort:
    __slots__ = ("__invoke",)

    def __init__(self, invoke):
        self.__invoke = invoke

    def complete(self, request):
        return self.__invoke(request)


class ModelGateway:
    def __init__(self, store, provider: RouteProvider, authorize, *, observer=None, policy=None):
        self._store, self._provider, self._authorize = store, provider, authorize
        self._observer = observer
        self.policy = policy or GatewayPolicy()
        self.observation_failures = 0
        if isinstance(provider, OpenRouterRouteProvider) and self.policy.input_usd_per_token is not None and self.policy.output_usd_per_token is not None:
            provider.configure_price_caps(self.policy.input_usd_per_token, self.policy.output_usd_per_token)
        self._account("ensure_unknown_budget", self.policy.budget_id)

    def _account(self, method, *args, **kwargs):
        try:
            return getattr(self._store, method)(*args, **kwargs)
        except ServiceError as error:
            code = error.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code) else "MODEL_ACCOUNTING_UNAVAILABLE"
            raise ServiceError(code, error.status_code) from None
        except Exception:
            raise ServiceError("MODEL_ACCOUNTING_UNAVAILABLE", 503) from None

    def bind(self, *, principal, context, budget=None) -> ModelPort:
        return _BoundModelPort(lambda request: self.complete(request, context, principal=principal, budget=budget))

    @staticmethod
    def _check(context, budget):
        if context.deadline_at is not None and datetime.now(timezone.utc) >= context.deadline_at:
            raise ServiceError("MODEL_DEADLINE_EXCEEDED", 408)
        if budget is not None:
            budget.check()

    def _observe(self, context, attempt, usage, *, status, error_code, started_at):
        if self._observer is None:
            return
        metadata = {"attempt_id": attempt["attempt_id"], "execution_mode": attempt["execution_mode"],
                    "provider": attempt.get("actual_provider"), "model": attempt.get("actual_model"),
                    "profile_id": "route-v1", "segment_id": context.segment_id,
                    "workflow_version": context.workflow_version, "agent_version": context.agent_version,
                    "prompt_version": context.prompt_version, "schema_version": context.schema_version,
                    "policy_version": context.policy_version, "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens,
                    "cost_usd": str(usage.cost_usd) if usage.cost_usd is not None else None,
                    "usage_source": usage.source, "usage_unknown_reason": usage.unknown_reason, "error_code": error_code}
        try:
            self._observer.emit_span(execution_id=context.execution_id, span_id=attempt["attempt_id"],
                parent_span_id=context.parent_span_id, name="model.route", kind="generation",
                status="unknown" if status == "outcome_unknown" else status,
                metadata=metadata, started_at=started_at, ended_at=datetime.now(timezone.utc))
        except Exception:
            self.observation_failures += 1

    def complete(self, request, context, *, principal, budget=None) -> ModelResult:
        if not isinstance(principal, Principal):
            raise ServiceError("SERVER_CONTEXT_REQUIRED", 403)
        try:
            request = ModelRequest.model_validate(request)
            context = ModelContext.model_validate(context)
        except (ValidationError, TypeError, ValueError):
            raise ServiceError("INVALID_MODEL_REQUEST", 422) from None
        if principal.company_id != context.company_id or "query.read" not in principal.permissions:
            raise ServiceError("PERMISSION_DENIED", 403)
        if context.budget_id != self.policy.budget_id:
            raise ServiceError("MODEL_BUDGET_SCOPE_MISMATCH", 403)
        self._check(context, budget)
        try:
            self._authorize(principal, context.company_id, "query.read")
        except ServiceError as error:
            code = error.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code) else "MODEL_AUTHORIZATION_FAILED"
            raise ServiceError(code, error.status_code) from None
        except Exception:
            raise ServiceError("MODEL_AUTHORIZATION_FAILED", 403) from None
        mode = self._provider.execution_mode
        if mode not in ("fake", "live"):
            raise ServiceError("MODEL_MODE_INVALID", 503)
        reservation = Decimal(0)
        if mode == "live":
            if not self.policy.live_enabled or not self.policy.transmission_approved:
                raise ServiceError("MODEL_LIVE_NOT_AUTHORIZED", 403)
            if self.policy.input_usd_per_token is None or self.policy.output_usd_per_token is None or not self.policy.pricing_version:
                raise ServiceError("MODEL_PRICING_UNVERIFIED", 409)
            with localcontext() as decimal_context:
                decimal_context.prec = 80
                reservation = (self._provider.input_token_bound(request) * self.policy.input_usd_per_token
                               + request.max_output_tokens * self.policy.output_usd_per_token)
        scope = {key: getattr(context, key) for key in ("company_id", "read_snapshot_id", "ledger_version", "projection_version", "ontology_version", "rule_version", "workflow_version", "policy_version")}
        request_material = {"request": request.model_dump(mode="json", exclude={"retry_of"}),
                            "provider": self._provider.provider_name, "model": self._provider.model_id,
                            "mode": mode, "prompt_version": context.prompt_version, "schema_version": context.schema_version}
        started_at = datetime.now(timezone.utc)
        attempt = self._account("reserve", budget_id=context.budget_id, execution_id=context.execution_id,
            company_id=context.company_id, actor_id=principal.actor_id, scope_hash=content_hash(scope),
            call_id=request.call_id, retry_of=request.retry_of, request_hash=content_hash(request_material),
            mode=mode, provider=self._provider.provider_name, model=self._provider.model_id,
            reserved_usd=reservation, metadata={"segment_id": context.segment_id, "parent_span_id": context.parent_span_id,
                                              "pricing_version": self.policy.pricing_version})
        if not attempt["new"]:
            if attempt["status"] == "completed" and attempt["result"] is not None:
                result = ModelResult.model_validate_json(json.dumps(attempt["result"]))
                return result.model_copy(update={"replayed": True, "context": context})
            code = attempt.get("error_code") or "MODEL_ATTEMPT_OUTCOME_UNKNOWN"
            raise ServiceError(code, 409)
        # A failure before dispatch can safely release its reservation. After
        # dispatch, exceptions never imply that the provider charged nothing.
        try:
            self._check(context, budget)
            if budget is not None:
                budget.reserve(context.execution_id)
        except ServiceError as error:
            self._account("finish", attempt["attempt_id"], status="failed", usage=ModelUsage().model_dump(),
                               error_code=error.code, not_dispatched=True)
            raise ServiceError(error.code, error.status_code) from None
        self._account("mark_dispatched", attempt["attempt_id"])
        reply = None
        try:
            timeout = self.policy.timeout_seconds
            if context.deadline_at is not None:
                timeout = min(timeout, max(0.001, (context.deadline_at - datetime.now(timezone.utc)).total_seconds()))
            reply = self._provider.complete(request, timeout_seconds=timeout)
            if not isinstance(reply, ProviderReply):
                raise ProviderFailure("MODEL_PROVIDER_CONTRACT_INVALID")
            ModelUsage.model_validate(reply.usage)
            if mode == "fake" and any(value is not None for value in (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.total_tokens, reply.usage.cost_usd)):
                raise ProviderFailure("MODEL_FAKE_USAGE_FORBIDDEN", usage=ModelUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"))
            if reply.error_code:
                raise ProviderFailure(reply.error_code, usage=reply.usage, outcome_unknown=False)
            if reply.capability not in CAPABILITIES:
                raise ProviderFailure("MODEL_OUTPUT_INVALID", usage=reply.usage, outcome_unknown=False)
            self._check(context, budget)
            result = ModelResult(status="needs_review" if reply.capability == "needs_clarification" else "completed",
                capability=reply.capability, attempt_id=attempt["attempt_id"], execution_mode=mode,
                actual_provider=reply.actual_provider, actual_model=reply.actual_model, usage=reply.usage, context=context)
            finished = self._account("finish", attempt["attempt_id"], status="completed", usage=reply.usage.model_dump(),
                result=result.model_dump(mode="json"), actual_provider=reply.actual_provider,
                actual_model=reply.actual_model, provider_request_id=reply.provider_request_id)
        except Exception as error:
            if isinstance(error, ProviderFailure):
                usage, code = error.usage, error.code
                status = "outcome_unknown" if error.outcome_unknown else "failed"
            elif isinstance(error, ServiceError):
                usage, code, status = reply.usage if reply else ModelUsage(), error.code, "failed"
            else:
                usage, code, status = reply.usage if isinstance(reply, ProviderReply) else ModelUsage(), "MODEL_PROVIDER_FAILED", "outcome_unknown"
            finished = self._account("finish", attempt["attempt_id"], status=status, usage=usage.model_dump(), error_code=code,
                actual_provider=reply.actual_provider if isinstance(reply, ProviderReply) else None,
                actual_model=reply.actual_model if isinstance(reply, ProviderReply) else None,
                provider_request_id=reply.provider_request_id if isinstance(reply, ProviderReply) else None)
            self._observe(context, finished, usage, status=finished["status"], error_code=finished["error_code"], started_at=started_at)
            raise ServiceError(finished["error_code"] or code, 503) from None
        self._observe(context, finished, reply.usage, status=finished["status"], error_code=finished["error_code"], started_at=started_at)
        if finished["status"] != "completed":
            raise ServiceError(finished["error_code"] or "MODEL_ATTEMPT_FAILED", 409)
        return result
