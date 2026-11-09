"""Exact-version inputs for a read-only Product review, without authority fields."""

from pydantic import Field

from service.contracts import Contract


class ProductReviewInput(Contract):
    ledger_version: int = Field(ge=1)
    bom_id: str = Field(min_length=1, max_length=100)
    bom_version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_limit: int = Field(default=3, ge=1, le=5)
