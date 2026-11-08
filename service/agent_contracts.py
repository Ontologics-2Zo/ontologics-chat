"""Bounded read workflow inputs; server identity and checkpoint state are not inputs."""

from typing import Literal

from pydantic import Field

from service.contracts import Contract


Capability = Literal["fulfillment_read", "material_analysis"]
WORKFLOW_VERSION = "readonly-agent-v1"
CHECKPOINT_VERSION = "agent-state-v1"


class AgentRequest(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    capability: Capability | None = None
    context_entity_id: str | None = Field(default=None, min_length=1, max_length=250)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AgentResume(Contract):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    capability: Capability
    question: str = Field(min_length=1, max_length=2000)


class AgentCancel(Contract):
    expected_revision: int = Field(ge=1)


class SpecialistInput(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    context_entity_id: str | None = None
