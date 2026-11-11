"""Bounded read workflow inputs; server identity and checkpoint state are not inputs."""

from typing import Literal

from pydantic import Field, model_validator

from service.contracts import Contract
from service.product_contracts import ProductReviewInput


Capability = Literal["fulfillment_read", "material_analysis", "product_review", "procurement_review", "procurement_draft"]
# v5: a purchase-request draft write behind a human approval interrupt (T07/T14).
WORKFLOW_VERSION = "agent-v5"
CHECKPOINT_VERSION = "agent-state-v1"


class AgentRequest(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    capability: Capability | None = None
    context_entity_id: str | None = Field(default=None, min_length=1, max_length=250)
    idempotency_key: str = Field(min_length=1, max_length=200)
    product: ProductReviewInput | None = None

    @model_validator(mode="after")
    def product_scope(self):
        if self.capability == "product_review":
            if self.product is None or self.product.ledger_version != self.ledger_version:
                raise ValueError("PRODUCT_REVIEW_INPUT_REQUIRED")
        elif self.product is not None:
            raise ValueError("PRODUCT_REVIEW_CAPABILITY_REQUIRED")
        return self


class AgentResume(Contract):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    capability: Capability
    question: str = Field(min_length=1, max_length=2000)
    product: ProductReviewInput | None = None
    # A write approval names the exact proposal it approves or declines.
    decision: Literal["approve", "decline"] | None = None
    proposal_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def product_scope(self):
        if (self.capability == "product_review") != (self.product is not None):
            raise ValueError("PRODUCT_REVIEW_INPUT_REQUIRED")
        if (self.decision is None) != (self.proposal_hash is None):
            raise ValueError("WRITE_DECISION_REQUIRES_PROPOSAL")
        if self.decision is not None and self.capability != "procurement_draft":
            raise ValueError("WRITE_DECISION_CAPABILITY_REQUIRED")
        return self


class AgentCancel(Contract):
    expected_revision: int = Field(ge=1)


class SpecialistInput(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    context_entity_id: str | None = None
