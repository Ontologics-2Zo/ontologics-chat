"""Versioned, strict contracts for the generic evidence-document vertical slice.

These contracts do not authorize BOM registration or purchase/inventory changes.
Caller identity is supplied by the server and is never part of an input model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CANONICAL_VERSION = "ontologics-json-v1"
MAX_PAYLOAD_BYTES = 64 * 1024
RESERVED_AUTH_FIELDS = frozenset({"actor_id", "approved_by", "reviewed_by", "role", "approved", "permissions"})


def canonical_bytes(value: Any) -> bytes:
    """Encode approved content once; the resulting bytes are persisted verbatim."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _check_payload(value: Any) -> None:
    if isinstance(value, dict):
        if any(key in RESERVED_AUTH_FIELDS for key in value):
            raise ValueError("IDENTITY_FIELDS_FORBIDDEN")
        for item in value.values():
            _check_payload(item)
    elif isinstance(value, list):
        for item in value:
            _check_payload(item)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class EvidenceDocument(Contract):
    kind: Literal["evidence_note"] = "evidence_note"
    payload: dict[str, Any]
    evidence_refs: list[str] = Field(min_length=1, max_length=100)
    source_version: str = Field(min_length=1, max_length=200)
    ontology_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)

    @field_validator("payload")
    @classmethod
    def valid_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_payload(value)
        if len(canonical_bytes(value)) > MAX_PAYLOAD_BYTES:
            raise ValueError("PAYLOAD_TOO_LARGE")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def valid_references(cls, value: list[str]) -> list[str]:
        if any(not ref or len(ref) > 500 or any(ord(char) < 32 for char in ref) for ref in value):
            raise ValueError("INVALID_EVIDENCE_REFERENCE")
        if len(set(value)) != len(value):
            raise ValueError("DUPLICATE_EVIDENCE_REFERENCE")
        return value


class ExactVersion(Contract):
    expected_version: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Revision(ExactVersion):
    content: EvidenceDocument


class CreateDocument(Contract):
    content: EvidenceDocument
    idempotency_key: str = Field(min_length=1, max_length=200)


class DocumentAction(ExactVersion):
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReviseDocument(DocumentAction):
    content: EvidenceDocument
