"""Typed internal-development purchasing commands; no external transmission."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
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
    # Maximum quantity this offer's evidence says can be supplied. None means
    # unknown, never unlimited and never zero (DEC-H47).
    supply_limit_qty: int | None = Field(default=None, ge=0, le=10**9)
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


class BoardsContent(PurchaseContent):
    """Fulfillment records name individual board serials (DEC-H49)."""
    board_serials: list[str] = Field(min_length=1, max_length=100)

    @field_validator("board_serials")
    @classmethod
    def serials(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", item) for item in value):
            raise ValueError("BOARD_SERIALS_INVALID")
        return value

    @property
    def quantity(self):
        return len(self.board_serials)


class ProductionContent(BoardsContent):
    """Boards built from a LOT: a newly inspected one (iqc_id) or an existing
    opening-balance LOT (lot_code). Exactly one is named."""
    kind: Literal["PRODUCTION"] = "PRODUCTION"
    iqc_id: str | None = Field(default=None, min_length=1, max_length=100)
    lot_code: str | None = Field(default=None, min_length=1, max_length=100)
    production_ref: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_source(self):
        if (self.iqc_id is None) == (self.lot_code is None):
            raise ValueError("PRODUCTION_SOURCE_REQUIRED")
        return self


class FinishedGoodsContent(BoardsContent):
    kind: Literal["FG_RECEIPT"] = "FG_RECEIPT"
    production_id: str = Field(min_length=1, max_length=100)
    receipt_ref: str = Field(min_length=1, max_length=100)


class ShipmentContent(BoardsContent):
    kind: Literal["SHIPMENT"] = "SHIPMENT"
    fg_receipt_id: str = Field(min_length=1, max_length=100)
    shipment_ref: str = Field(min_length=1, max_length=100)


class ArrivalContent(BoardsContent):
    kind: Literal["ARRIVAL"] = "ARRIVAL"
    shipment_id: str = Field(min_length=1, max_length=100)
    arrival_ref: str = Field(min_length=1, max_length=100)


class CohortContent(PurchaseContent):
    """Opening-balance boards without serials (LegacyCohort), moved by quantity (T10)."""
    kind: Literal["COHORT"] = "COHORT"
    cohort_ref: str = Field(min_length=1, max_length=100)
    round_id: str = Field(min_length=1, max_length=100)
    stage: Literal["FG_RECEIPT", "SHIPMENT", "ARRIVAL"]
    quantity: int = Field(ge=1, le=10000)


class ScenarioContent(PurchaseContent):
    """What-if schedule assumptions for one delivery round (T12). Not facts:
    the values are entered and approved as assumptions and labelled so."""
    kind: Literal["SCENARIO"] = "SCENARIO"
    scenario_ref: str = Field(min_length=1, max_length=100)
    round_id: str = Field(min_length=1, max_length=100)
    inspection_business_days: int = Field(ge=0, le=30)
    daily_capacity: int = Field(ge=1, le=10000)
    ship_prep_business_days: int = Field(ge=0, le=30)
    transit_days: int = Field(ge=0, le=60)
    provenance: Literal["DEMO_ASSUMPTION"] = "DEMO_ASSUMPTION"


ProcurementContent = Annotated[Union[QuoteContent, RequestContent, OrderContent, ReceiptContent,
                                    SupplyConfirmationContent, InspectionContent, CancelContent,
                                    ProductionContent, FinishedGoodsContent, ShipmentContent, ArrivalContent, ScenarioContent, CohortContent], Field(discriminator="kind")]
CONTENT_TYPES = {cls.model_fields["kind"].default: cls for cls in
                 (QuoteContent, RequestContent, OrderContent, ReceiptContent, SupplyConfirmationContent, InspectionContent, CancelContent,
                  ProductionContent, FinishedGoodsContent, ShipmentContent, ArrivalContent, ScenarioContent, CohortContent)}
FULFILLMENT_KINDS = {"PRODUCTION", "FG_RECEIPT", "SHIPMENT", "ARRIVAL"}


class ProcurementCreate(Contract):
    expected_ledger_version: int = Field(ge=1)
    content: ProcurementContent
    idempotency_key: str = Field(min_length=1, max_length=200)


class ProcurementRevise(DocumentAction):
    expected_ledger_version: int = Field(ge=1)
    content: ProcurementContent


class ProcurementReject(DocumentAction):
    reason: str = Field(min_length=8, max_length=2000)
