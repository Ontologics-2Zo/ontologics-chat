"""PostgreSQL-backed exact-version review/approval/post foundation.

The only supported document kind is an evidence note. Posting records that note;
it deliberately cannot register a BOM, move stock, or dispatch a purchase order.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Callable
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from service.contracts import CANONICAL_VERSION, EvidenceDocument, canonical_bytes, content_hash
from service.errors import ServiceError


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key != "canonical_bytes"}
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class DomainService:
    def __init__(self, db, identity):
        self.db = db
        self.identity = identity

    def _run(self, principal, company_id: str, action: str, key: str, request: dict, work: Callable) -> dict:
        if not isinstance(key, str) or not key or len(key) > 200:
            raise ServiceError("INVALID_IDEMPOTENCY_KEY", 422)
        request_hash = content_hash({"canonical_version": CANONICAL_VERSION, "action": action, "input": request})
        with self.db.connect() as connection:
            self.identity.require(principal, action, connection, company_id=company_id)
            # Stable transaction lock resolves concurrent retries before either writes.
            lock_key = int.from_bytes(hashlib.sha256(canonical_bytes([company_id, principal.actor_id, key])).digest()[:8], "big", signed=True)
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            previous = connection.execute(
                "SELECT request_hash, result FROM operations WHERE company_id=%s AND actor_id=%s AND idempotency_key=%s",
                (company_id, principal.actor_id, key),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
                return previous["result"]
            result = _public(work(connection))
            result["operation_id"] = str(uuid4())
            connection.execute(
                "INSERT INTO operations(company_id,actor_id,idempotency_key,request_hash,operation_id,result) VALUES (%s,%s,%s,%s,%s,%s)",
                (company_id, principal.actor_id, key, request_hash, result["operation_id"], Jsonb(result)),
            )
            return result

    def _document(self, connection, company_id: str, document_id: str, *, lock: bool = False) -> dict:
        try:
            identifier = UUID(str(document_id))
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("DOCUMENT_NOT_FOUND", 404) from None
        document = connection.execute(
            "SELECT * FROM documents WHERE company_id=%s AND id=%s" + (" FOR UPDATE" if lock else ""),
            (company_id, identifier),
        ).fetchone()
        if not document:
            raise ServiceError("DOCUMENT_NOT_FOUND", 404)
        return document

    @staticmethod
    def _exact(document: dict, expected_version: int, payload_hash: str) -> None:
        if document["version"] != expected_version or document["payload_hash"] != payload_hash:
            raise ServiceError("DOCUMENT_VERSION_CHANGED", 409)
        if hashlib.sha256(bytes(document["canonical_bytes"])).hexdigest() != document["payload_hash"]:
            raise ServiceError("DOCUMENT_INTEGRITY_FAILURE", 409)
        if canonical_bytes(document["content"]) != bytes(document["canonical_bytes"]):
            raise ServiceError("DOCUMENT_INTEGRITY_FAILURE", 409)

    @staticmethod
    def _audit(connection, principal, document: dict, action: str) -> None:
        connection.execute(
            "INSERT INTO audit_events(audit_id,company_id,actor_id,action,document_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
            (uuid4(), document["company_id"], principal.actor_id, action, document["id"], Jsonb({
                "document_version": document["version"], "payload_hash": document["payload_hash"],
                "status": document["status"], "identity_revision": principal.identity_revision,
            })),
        )

    @staticmethod
    def _revision(connection, principal, document: dict) -> None:
        connection.execute(
            "INSERT INTO document_revisions(document_id,version,content,canonical_bytes,payload_hash,canonical_version,actor_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (document["id"], document["version"], Jsonb(document["content"]), document["canonical_bytes"], document["payload_hash"], CANONICAL_VERSION, principal.actor_id),
        )

    def create(self, principal, company_id: str, content: EvidenceDocument, idempotency_key: str) -> dict:
        content = EvidenceDocument.model_validate(content)
        value = content.model_dump(mode="json")
        raw = canonical_bytes(value)

        def work(connection):
            document = connection.execute(
                "INSERT INTO documents(id,company_id,kind,content,canonical_bytes,payload_hash,canonical_version,version,status,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1,'DRAFT',%s) RETURNING *",
                (uuid4(), company_id, content.kind, Jsonb(value), raw, hashlib.sha256(raw).hexdigest(), CANONICAL_VERSION, principal.actor_id),
            ).fetchone()
            self._revision(connection, principal, document)
            self._audit(connection, principal, document, "document.create")
            return document

        return self._run(principal, company_id, "document.create", idempotency_key, value, work)

    def revise(self, principal, company_id: str, document_id: str, expected_version: int, payload_hash: str,
               content: EvidenceDocument, idempotency_key: str) -> dict:
        content = EvidenceDocument.model_validate(content)
        value = content.model_dump(mode="json")
        raw = canonical_bytes(value)

        def work(connection):
            document = self._document(connection, company_id, document_id, lock=True)
            self._exact(document, expected_version, payload_hash)
            if document["status"] == "POSTED":
                raise ServiceError("POSTED_DOCUMENT_IMMUTABLE", 409)
            if document["created_by"] != principal.actor_id:
                raise ServiceError("DOCUMENT_AUTHOR_REQUIRED", 403)
            document = connection.execute(
                "UPDATE documents SET content=%s,canonical_bytes=%s,payload_hash=%s,version=version+1,status='DRAFT',reviewed_by=NULL,approved_by=NULL,"
                "approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,updated_at=now() WHERE id=%s RETURNING *",
                (Jsonb(value), raw, hashlib.sha256(raw).hexdigest(), document["id"]),
            ).fetchone()
            self._revision(connection, principal, document)
            self._audit(connection, principal, document, "document.update")
            return document

        request = {"document_id": document_id, "expected_version": expected_version, "payload_hash": payload_hash, "content": value}
        return self._run(principal, company_id, "document.update", idempotency_key, request, work)

    def review(self, principal, company_id: str, document_id: str, expected_version: int, payload_hash: str,
               idempotency_key: str) -> dict:
        def work(connection):
            document = self._document(connection, company_id, document_id, lock=True)
            self._exact(document, expected_version, payload_hash)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_REVIEW_FORBIDDEN", 403)
            if document["status"] != "DRAFT":
                raise ServiceError("DOCUMENT_STATE_CHANGED", 409)
            document = connection.execute(
                "UPDATE documents SET status='REVIEWED',reviewed_by=%s,updated_at=now() WHERE id=%s RETURNING *",
                (principal.actor_id, document["id"]),
            ).fetchone()
            self._audit(connection, principal, document, "document.review")
            return document

        return self._run(principal, company_id, "document.review", idempotency_key,
                         {"document_id": document_id, "expected_version": expected_version, "payload_hash": payload_hash}, work)

    def approve(self, principal, company_id: str, document_id: str, expected_version: int, payload_hash: str,
                idempotency_key: str) -> dict:
        def work(connection):
            document = self._document(connection, company_id, document_id, lock=True)
            self._exact(document, expected_version, payload_hash)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_APPROVAL_FORBIDDEN", 403)
            if document["status"] != "REVIEWED":
                raise ServiceError("REVIEW_REQUIRED", 409)
            document = connection.execute(
                "UPDATE documents SET status='APPROVED',approved_by=%s,approved_version=version,approved_hash=payload_hash,"
                "approval_policy_version=%s,updated_at=now() WHERE id=%s RETURNING *",
                (principal.actor_id, principal.permission_profile_version, document["id"]),
            ).fetchone()
            self._audit(connection, principal, document, "document.approve")
            return document

        return self._run(principal, company_id, "document.approve", idempotency_key,
                         {"document_id": document_id, "expected_version": expected_version, "payload_hash": payload_hash}, work)

    def post(self, principal, company_id: str, document_id: str, expected_version: int, payload_hash: str,
             idempotency_key: str) -> dict:
        def work(connection):
            document = self._document(connection, company_id, document_id, lock=True)
            self._exact(document, expected_version, payload_hash)
            if (document["status"] != "APPROVED" or document["approved_version"] != expected_version
                    or document["approved_hash"] != payload_hash or document["approved_by"] == document["created_by"]):
                raise ServiceError("EXACT_APPROVAL_REQUIRED", 409)
            approver = self.identity.require_actor(document["approved_by"], company_id, "document.approve", connection)
            policy_version = approver["permission_profile_version"] if isinstance(approver, dict) else approver.permission_profile_version
            if policy_version != document["approval_policy_version"]:
                raise ServiceError("APPROVAL_PERMISSION_CHANGED", 409)
            connection.execute("INSERT INTO ledger_state(company_id,version) VALUES (%s,0) ON CONFLICT DO NOTHING", (company_id,))
            ledger_version = connection.execute(
                "UPDATE ledger_state SET version=version+1 WHERE company_id=%s RETURNING version", (company_id,),
            ).fetchone()["version"]
            document = connection.execute(
                "UPDATE documents SET status='POSTED',ledger_version=%s,updated_at=now() WHERE id=%s RETURNING *",
                (ledger_version, document["id"]),
            ).fetchone()
            event_id = uuid4()
            event = {"event_type": "EvidenceDocumentPosted", "document_id": str(document["id"]),
                     "document_version": document["version"], "payload_hash": document["payload_hash"],
                     "canonical_version": CANONICAL_VERSION, "content": document["content"],
                     "company_id": company_id, "ledger_version": ledger_version}
            connection.execute(
                "INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,event_type,payload,actor_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (event_id, company_id, ledger_version, document["id"], event["event_type"], Jsonb(event), principal.actor_id),
            )
            self._audit(connection, principal, document, "document.post")
            connection.execute(
                "INSERT INTO outbox(event_id,company_id,ledger_version,payload) VALUES (%s,%s,%s,%s)",
                (event_id, company_id, ledger_version, Jsonb(event)),
            )
            return {**document, "event_id": str(event_id), "projection_status": "PENDING"}

        return self._run(principal, company_id, "document.post", idempotency_key,
                         {"document_id": document_id, "expected_version": expected_version, "payload_hash": payload_hash}, work)

    def get(self, principal, company_id: str, document_id: str) -> dict:
        with self.db.connect() as connection:
            self.identity.require(principal, "document.read", connection, company_id=company_id)
            return _public(self._document(connection, company_id, document_id))

    def list(self, principal, company_id: str, limit: int = 100) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ServiceError("INVALID_PAGE_LIMIT", 422)
        with self.db.connect() as connection:
            self.identity.require(principal, "document.read", connection, company_id=company_id)
            return _public(connection.execute(
                "SELECT * FROM documents WHERE company_id=%s ORDER BY created_at,id LIMIT %s", (company_id, limit),
            ).fetchall())

    def operation(self, principal, company_id: str, operation_id: str) -> dict:
        try:
            identifier = UUID(str(operation_id))
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("OPERATION_NOT_FOUND", 404) from None
        with self.db.connect() as connection:
            self.identity.require(principal, "document.read", connection, company_id=company_id)
            row = connection.execute(
                "SELECT result FROM operations WHERE company_id=%s AND actor_id=%s AND operation_id=%s",
                (company_id, principal.actor_id, identifier),
            ).fetchone()
            if not row:
                raise ServiceError("OPERATION_NOT_FOUND", 404)
            return row["result"]
