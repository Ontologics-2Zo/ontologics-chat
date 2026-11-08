"""Common typed tool boundary for UI and Agent consumers.

The host constructs Principal, context and shared budget. Domain handlers still
recheck permission and versions in their transaction. This module does not call
models, expose database handles, or provide persistent workflow/budget recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from threading import Event, Lock
from time import monotonic
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from service.contracts import Contract
from service.errors import ServiceError
from service.identity import Principal


class ToolContext(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    execution_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    context_revision: str = Field(min_length=1, max_length=200)
    company_id: str = Field(min_length=1, max_length=200)
    read_snapshot_id: str = Field(min_length=1, max_length=200)
    ledger_version: int = Field(ge=0)
    projection_version: int | None = Field(default=None, ge=0)
    ontology_version: str | None = None
    rule_version: str | None = None
    as_of: datetime | None = None
    known_as_of: datetime | None = None

    @field_validator("as_of", "known_as_of")
    @classmethod
    def aware_time(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("TIMEZONE_REQUIRED")
        return value


class EvidenceRef(Contract):
    evidence_id: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=200)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ToolOutput(Contract):
    status: Literal["completed", "needs_review"] = "completed"
    data: dict[str, Any]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    snapshot: ToolContext


class ToolResult(ToolOutput):
    contract_version: Literal["tool-v1"] = "tool-v1"
    tool_name: str
    tool_version: str
    mutating: bool


@dataclass
class ExecutionBudget:
    """One host-owned call allowance shared by all tools in an execution.

    This is a local dispatch limit, not monetary accounting or a durable budget.
    Cancellation stops future dispatch; a committed mutation is never relabeled
    cancelled merely because cancellation arrived while its handler was running.
    """

    execution_id: str
    max_tool_calls: int = 20
    deadline: float | None = None  # monotonic clock, set by the host
    _calls: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _cancelled: Event = field(default_factory=Event, init=False, repr=False)

    def __post_init__(self):
        if not self.execution_id or type(self.max_tool_calls) is not int or self.max_tool_calls < 1:
            raise ValueError("INVALID_EXECUTION_BUDGET")

    @property
    def tool_calls(self):
        with self._lock:
            return self._calls

    def cancel(self):
        self._cancelled.set()

    def check(self):
        if self._cancelled.is_set():
            raise ServiceError("TOOL_CANCELLED", 409)
        if self.deadline is not None and monotonic() >= self.deadline:
            raise ServiceError("TOOL_DEADLINE_EXCEEDED", 408)

    def reserve(self, execution_id):
        with self._lock:
            self.check()
            if execution_id != self.execution_id:
                raise ServiceError("EXECUTION_BUDGET_MISMATCH", 409)
            if self._calls >= self.max_tool_calls:
                raise ServiceError("TOOL_BUDGET_EXHAUSTED", 429)
            self._calls += 1


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: str
    mutating: bool
    input_model: type[BaseModel]
    handler: Callable[[Principal, BaseModel, ToolContext], ToolOutput]
    version: str = "1"


class ToolPort(Protocol):
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


class ModelPort(Protocol):
    """Text generation alone grants no tool, approval, or domain authority."""

    def complete(self, prompt: str) -> str: ...


class _BoundToolPort:
    __slots__ = ("__invoke",)

    def __init__(self, invoke):
        self.__invoke = invoke

    def call(self, name, arguments):
        return self.__invoke(name, arguments)


def _safe_error(error):
    if isinstance(error, ServiceError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code):
        return ServiceError(error.code, error.status_code)
    return ServiceError("TOOL_FAILED", 503)


class ToolGateway:
    def __init__(self, authorize: Callable[[Principal, str, str], None]):
        # authorize(principal, company_id, permission) must check current server identity.
        self._authorize = authorize
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec):
        if not re.fullmatch(r"[a-z][a-z0-9_.]{0,99}", spec.name) or spec.name in self._tools:
            raise ValueError("INVALID_OR_DUPLICATE_TOOL")
        if (not issubclass(spec.input_model, BaseModel)
                or spec.input_model.model_config.get("extra") != "forbid"
                or not spec.input_model.model_config.get("strict")
                or not callable(spec.handler) or not spec.permission or not spec.version):
            raise ValueError("INVALID_TOOL_CONTRACT")
        forbidden = {"actor_id", "role", "permissions", "approved_by", "principal", "company_id", "tenant_id"}
        if forbidden.intersection(spec.input_model.model_fields):
            raise ValueError("IDENTITY_INPUT_FORBIDDEN")
        if spec.mutating and "idempotency_key" not in spec.input_model.model_fields:
            raise ValueError("MUTATION_IDEMPOTENCY_REQUIRED")
        self._tools[spec.name] = spec

    def bind(self, *, principal: Principal, context: ToolContext, budget: ExecutionBudget) -> ToolPort:
        # Only the restricted callable is given to a consumer; no handler registry,
        # provider credential or service/database object is part of the port API.
        return _BoundToolPort(lambda name, args: self.execute(
            name, args, principal=principal, context=context, budget=budget))

    def execute(self, name: str, arguments: dict[str, Any], *, principal: Principal,
                context: ToolContext, budget: ExecutionBudget) -> ToolResult:
        if not isinstance(name, str) or name not in self._tools:
            raise ServiceError("TOOL_NOT_ALLOWED", 403)
        spec = self._tools[name]
        if not isinstance(principal, Principal) or not isinstance(context, ToolContext):
            raise ServiceError("SERVER_CONTEXT_REQUIRED", 403)
        if principal.company_id != context.company_id or spec.permission not in principal.permissions:
            raise ServiceError("PERMISSION_DENIED", 403)
        if not isinstance(arguments, dict):
            raise ServiceError("INVALID_TOOL_ARGUMENTS", 422)
        try:
            validated = spec.input_model.model_validate(arguments)
        except (ValidationError, ValueError, TypeError):
            raise ServiceError("INVALID_TOOL_ARGUMENTS", 422) from None
        if spec.mutating:
            key = getattr(validated, "idempotency_key", None)
            if not isinstance(key, str) or not key or len(key) > 200:
                raise ServiceError("INVALID_IDEMPOTENCY_KEY", 422)
        budget.check()
        try:
            self._authorize(principal, context.company_id, spec.permission)
        except Exception as error:
            raise _safe_error(error) from None
        budget.reserve(context.execution_id)
        try:
            output = spec.handler(principal, validated, context)
            if not isinstance(output, ToolOutput) or output.snapshot != context:
                code = "TOOL_OUTCOME_UNKNOWN" if spec.mutating else "TOOL_RESULT_CONTEXT_MISMATCH"
                raise ServiceError(code, 409)
            result = ToolResult(**output.model_dump(), tool_name=spec.name,
                                tool_version=spec.version, mutating=spec.mutating)
        except ServiceError as error:
            raise _safe_error(error) from None
        except Exception:
            # Once dispatched, an unknown write outcome requires operation
            # reconciliation. Blind retries with a fresh key are not safe.
            code = "TOOL_OUTCOME_UNKNOWN" if spec.mutating else "TOOL_FAILED"
            raise ServiceError(code, 503) from None
        if not spec.mutating:
            budget.check()
        return result
