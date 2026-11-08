"""Strict source registration contracts, separate from manufacturing approval."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, field_validator, model_validator

from service.contracts import Contract, DocumentAction


class BomCreate(Contract):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_id: str = Field(min_length=1, max_length=200)
    expected_ledger_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class BomDecision(Contract):
    row_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_status: Literal["UNRESOLVED", "APPLICABLE", "NOT_MOUNTED", "SETTING"] = "UNRESOLVED"
    quantity_per_unit: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, min_length=1, max_length=24)
    item_id: str | None = Field(default=None, min_length=1, max_length=200)
    review_basis: Literal["UNREVIEWED", "SOURCE_VERIFIED", "DEMO_ASSUMPTION"] = "UNREVIEWED"
    evidence_note: str = Field(default="", max_length=2000)

    @field_validator("quantity_per_unit")
    @classmethod
    def quantity(cls, value):
        if value is None:
            return value
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise ValueError("BOM_QUANTITY_INVALID") from None
        if not parsed.is_finite() or parsed <= 0 or parsed > 1000000 or parsed.as_tuple().exponent < -6:
            raise ValueError("BOM_QUANTITY_INVALID")
        return format(parsed.normalize(), "f")

    @field_validator("unit", "item_id", "evidence_note")
    @classmethod
    def text(cls, value):
        if value is not None and (value != value.strip() or any(ord(c) < 32 and c not in "\n\t" for c in value)):
            raise ValueError("BOM_TEXT_INVALID")
        return value

    @model_validator(mode="after")
    def application(self):
        if self.application_status == "UNRESOLVED":
            if self.item_id is not None or self.quantity_per_unit is not None or self.unit is not None:
                raise ValueError("BOM_UNRESOLVED_MUST_NOT_APPLY")
        else:
            # This vertical slice accepts explicit conditional demo decisions;
            # source structure alone cannot establish physical part equivalence.
            if self.review_basis != "DEMO_ASSUMPTION" or len(self.evidence_note.strip()) < 8:
                raise ValueError("BOM_APPLICATION_EVIDENCE_REQUIRED")
            if self.application_status == "APPLICABLE":
                if self.quantity_per_unit is None or self.unit is None:
                    raise ValueError("BOM_APPLICATION_QUANTITY_REQUIRED")
            elif any(v is not None for v in (self.quantity_per_unit, self.unit, self.item_id)):
                raise ValueError("BOM_EXCLUDED_MUST_NOT_APPLY")
        return self


class BomRevise(DocumentAction):
    expected_ledger_version: int = Field(ge=1)
    review_note: str = Field(min_length=8, max_length=3000)
    rows: list[BomDecision] = Field(min_length=1, max_length=2000)


class BomReview(DocumentAction):
    review_note: str = Field(min_length=8, max_length=3000)
    acknowledge_unresolved: bool = False
