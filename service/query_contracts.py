"""Read-only CQ guidance; an action opens evidence, never approves or posts it."""
from typing import Literal

from pydantic import Field

from service.contracts import Contract


class QueryBomReference(Contract):
    document_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrerequisiteCounts(Contract):
    source_rows: int = Field(ge=0)
    applicable_rows: int = Field(ge=0)
    unresolved_rows: int = Field(ge=0)
    calculation_items: int = Field(ge=0)


class QueryNextAction(Contract):
    type: Literal["REGISTER_BOM", "REVIEW_BOM_APPLICATION"]
    label: str = Field(min_length=1, max_length=100)
    read_only: Literal[True] = True
    requires_explicit_submission: Literal[True] = True


class QueryPrerequisite(Contract):
    status: Literal["BLOCKED", "PARTIAL", "UNKNOWN"]
    stage: Literal["APPLICABLE_BOM"] = "APPLICABLE_BOM"
    reason: Literal["REGISTERED_BOM_MISSING", "NO_REVIEWED_APPLICABLE_ROWS",
                    "APPLICATION_REVIEW_INCOMPLETE", "NO_APPLICABLE_COMPONENTS",
                    "TECHNICAL_REVIEW_UNCONFIRMED"]
    configuration_id: str = Field(min_length=1, max_length=200)
    bom: QueryBomReference | None
    counts: PrerequisiteCounts
    selected_item_ids: list[str]
    selected_calculation_items: int | None = Field(default=None, ge=0)
    next_action: QueryNextAction


class QueryAnswerBlock(Contract):
    """Sections of one completed query, not separate agent/tool executions."""
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    title: str = Field(min_length=1, max_length=160)
    tool: Literal["원장 조회"] = "원장 조회"
    paragraphs: list[str] = Field(min_length=1)
    kind: Literal["normal", "attention"]
