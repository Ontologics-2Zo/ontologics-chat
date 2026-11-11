"""Bounded read workflow inputs; server identity and checkpoint state are not inputs."""

from typing import Literal

from pydantic import Field, model_validator

from service.contracts import Contract
from service.product_contracts import ProductReviewInput


Capability = Literal["fulfillment_read", "material_analysis", "product_review", "procurement_review"]
WORKFLOW_VERSION = "readonly-agent-v4"
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

    @model_validator(mode="after")
    def product_scope(self):
        if (self.capability == "product_review") != (self.product is not None):
            raise ValueError("PRODUCT_REVIEW_INPUT_REQUIRED")
        return self


class AgentCancel(Contract):
    expected_revision: int = Field(ge=1)


class SpecialistInput(Contract):
    question: str = Field(min_length=1, max_length=2000)
    ledger_version: int = Field(ge=1)
    context_entity_id: str | None = None
