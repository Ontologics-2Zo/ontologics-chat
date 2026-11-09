"""Typed internal-development purchasing commands; no external transmission."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from service.contracts import Contract, DocumentAction


def money(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("INVALID_PROCUREMENT_MONEY") from None
    if not number.is_finite() or number < 0 or number > 10**12 or number.as_tuple().exponent < -6:
        raise ValueError("INVALID_PROCUREMENT_MONEY")
    return format(number, "f")


class PurchaseContent(Contract):
    note: str = Field(min_length=8, max_length=2000)


class QuoteContent(PurchaseContent):
    kind: Literal["QUOTE"] = "QUOTE"
    quote_ref: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=24)
    unit_price: str = Field(max_length=40)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    moq: int = Field(ge=1, le=10**9)
    order_multiple: int = Field(ge=1, le=10**9)
    freight: str | None = Field(default=None, max_length=40)
    valid_until: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    lead_business_days: int | None = Field(default=None, ge=0, le=3650)
    provenance: Literal["DEMO_ASSUMPTION"] = "DEMO_ASSUMPTION"

    @field_validator("unit_price", "freight")
    @classmethod
    def amounts(cls, value):
        return money(value) if value is not None else None

    @field_validator("valid_until")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


class RequestContent(PurchaseContent):
    kind: Literal["PR"] = "PR"
    quote_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=10**9)
    material_basis: Literal["base", "planned"]
    acknowledge_partial_bom: bool = False


class OrderContent(PurchaseContent):
    kind: Literal["PO"] = "PO"
    pr_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=10**9)
    acknowledge_unknown_freight: bool = False


class ReceiptContent(PurchaseContent):
    kind: Literal["RECEIPT"] = "RECEIPT"
    po_id: str = Field(min_length=1, max_length=100)
    receipt_ref: str = Field(min_length=1, max_length=100)
    lot_code: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=10**9)


class SupplyConfirmationContent(PurchaseContent):
    kind: Literal["SUPPLY_CONFIRMATION"] = "SUPPLY_CONFIRMATION"
    po_id: str = Field(min_length=1, max_length=100)
    confirmation_ref: str = Field(min_length=1, max_length=100)
    confirmed_qty: int = Field(ge=1, le=10**9)


class InspectionContent(PurchaseContent):
    kind: Literal["IQC"] = "IQC"
    receipt_id: str = Field(min_length=1, max_length=100)
    inspection_ref: str = Field(min_length=1, max_length=100)
    passed_qty: int = Field(ge=0, le=10**9)
    failed_qty: int = Field(ge=0, le=10**9)

    @model_validator(mode="after")
    def nonempty(self):
        if not 0 < self.passed_qty + self.failed_qty <= 10**9:
            raise ValueError("EMPTY_OR_EXCESSIVE_INSPECTION")
        return self


class CancelContent(PurchaseContent):
    kind: Literal["CANCEL"] = "CANCEL"
    target_id: str = Field(min_length=1, max_length=100)


ProcurementContent = Annotated[Union[QuoteContent, RequestContent, OrderContent, ReceiptContent,
                                    SupplyConfirmationContent, InspectionContent, CancelContent], Field(discriminator="kind")]
CONTENT_TYPES = {cls.model_fields["kind"].default: cls for cls in
                 (QuoteContent, RequestContent, OrderContent, ReceiptContent, SupplyConfirmationContent, InspectionContent, CancelContent)}


class ProcurementCreate(Contract):
    expected_ledger_version: int = Field(ge=1)
    content: ProcurementContent
    idempotency_key: str = Field(min_length=1, max_length=200)


class ProcurementRevise(DocumentAction):
    expected_ledger_version: int = Field(ge=1)
    content: ProcurementContent


class ProcurementReject(DocumentAction):
    reason: str = Field(min_length=8, max_length=2000)
