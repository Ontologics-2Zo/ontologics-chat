"""Durable raw BOM registration with exact-version, independent review.

Registration certifies preserved source evidence and explicit conditional
application decisions. It never certifies manufacturing equivalence, creates
stock, executes expected scenario events, or rewrites an opening balance.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from service.bom_contracts import BomReviewerOutcome, BomCompare, BomCreate, BomDecision, BomReview, BomReviewFocus, BomRevise
from service.bom_compare import compare_bom_documents
from service.bom_focus import mapped_row_ids, review_focus
from service.contracts import CANONICAL_VERSION, DocumentAction, canonical_bytes, content_hash
from service.domain import DomainService, _public
from service.errors import ServiceError

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000


def _bounded(path: Path, maximum: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(maximum + 1)
        if not data or len(data) > maximum:
            raise ServiceError("BOM_SOURCE_SIZE_REJECTED", 413)
        return data
    except ServiceError:
        raise
    except OSError:
        raise ServiceError("BOM_SOURCE_UNAVAILABLE", 503) from None


def _source_path(root: Path, relative: str) -> Path:
    try:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError
        candidate = root.joinpath(rel)
        if any(root.joinpath(*rel.parts[:i]).is_symlink() for i in range(1, len(rel.parts) + 1)):
            raise ValueError
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError
        return resolved
    except (OSError, ValueError):
        raise ServiceError("BOM_SOURCE_LOCATION_REJECTED", 422) from None


def source_catalog(private_root: Path | str) -> list[dict]:
    """Return only manifest-listed raw BOM descriptors, never a client path."""
    try:
        root = Path(private_root).resolve(strict=True)
        if root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError
        manifest = json.loads(_bounded(_source_path(root, "SOURCE_MANIFEST.json"), 4 * 1024 * 1024))
        result = []
        for row in manifest["files"]:
            relative = row["path"]
            rel = Path(relative)
            if not relative.startswith("v1.7-data/data/raw/") or rel.suffix.lower() not in {".ods", ".csv", ".pdf"}:
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or not 0 < row["bytes"] <= MAX_SOURCE_BYTES:
                raise ValueError
            result.append({"source_id": sha256(relative.encode("utf-8")).hexdigest(), "filename": rel.name,
                           "sha256": row["sha256"], "bytes": row["bytes"], "format": rel.suffix.lower()[1:],
                           "relative_path": relative})
        if len({row["source_id"] for row in result}) != len(result):
            raise ValueError
        return sorted(result, key=lambda row: row["filename"])
    except ServiceError:
        raise
    except (ValueError, TypeError, KeyError, OSError):
        raise ServiceError("BOM_SOURCE_CATALOG_INVALID", 503) from None


def _number(value):
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite() or number <= 0 or number > 1000000 or number.as_tuple().exponent < -6:
        return None
    return format(number.normalize(), "f")


def _extract(parsed, source_hash, filename):
    """Map already sandbox-parsed cells into lossless, unreviewed source rows."""
    try:
        if not isinstance(parsed, dict) or not isinstance(parsed["sheets"], list) or len(parsed["sheets"]) > 32:
            raise ValueError
        rows, conflicts, seen = [], [], set()
        for sheet in parsed["sheets"]:
            name, data = sheet["name"], sheet["rows"]
            if not isinstance(name, str) or not name or len(name) > 300 or not isinstance(data, list):
                raise ValueError
            if "WROOM" in filename.upper() and "WROVER" in name.upper() or "WROVER" in filename.upper() and "WROOM" in name.upper():
                conflicts.append("SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT")
            columns = None
            sheet_has_content = False
            for raw in data:
                physical, cells = raw["row_number"], raw["cells"]
                if not isinstance(physical, int) or isinstance(physical, bool) or physical < 1 or not isinstance(cells, list) or len(cells) > 1024 or any(not isinstance(c, str) or len(c) > 20000 for c in cells):
                    raise ValueError
                normalized = [c.strip().casefold() for c in cells]
                sheet_has_content = sheet_has_content or any(normalized)
                if all(header in normalized for header in ("quantity", "designation", "designator")):
                    if any(normalized.count(header) != 1 for header in ("quantity", "designation", "designator")):
                        raise ServiceError("BOM_AMBIGUOUS_HEADER_REQUIRES_REVIEW", 422)
                    if columns is not None:
                        raise ServiceError("BOM_MULTIPLE_HEADERS_REQUIRES_REVIEW", 422)
                    columns = {key: normalized.index(key) for key in ("quantity", "designation", "designator")}
                    continue
                if columns is None or not any(c.strip() for c in cells):
                    continue
                def cell(key):
                    position = columns[key]
                    return cells[position] if position < len(cells) else ""
                locator = f"{name}!row={physical}"
                row_id = content_hash([source_hash, name, physical])
                if row_id in seen:
                    raise ServiceError("BOM_DUPLICATE_SOURCE_ROW", 422)
                seen.add(row_id)
                designators = [part.strip() for part in re.split(r"[,;\s]+", cell("designator").strip()) if part.strip()]
                rows.append({"row_id": row_id, "ordinal": len(rows) + 1, "source_row": physical, "sheet": name,
                             "locator": locator, "cells": cells, "designators": designators,
                             "source_quantity": _number(cell("quantity")), "source_quantity_text": cell("quantity"),
                             "source_description": cell("designation"),
                             "decision": BomDecision(row_id=row_id).model_dump(exclude={"row_id"})})
                if len(rows) > MAX_ROWS:
                    raise ServiceError("BOM_ROW_LIMIT", 413)
            if sheet_has_content and columns is None:
                # A second populated sheet can be another BOM table. Retaining
                # its bytes is not enough to claim complete extracted coverage.
                raise ServiceError("BOM_UNRECOGNIZED_SHEET_REQUIRES_REVIEW", 422)
        if not rows:
            raise ServiceError("BOM_TABLE_NOT_FOUND", 422)
        if any(row["source_quantity"] is None or not row["designators"] for row in rows):
            conflicts.append("SOURCE_ROW_REQUIRES_REVIEW")
        designators = [(row["sheet"], designator) for row in rows for designator in row["designators"]]
        if len(set(designators)) != len(designators):
            conflicts.append("DUPLICATE_SOURCE_DESIGNATOR")
        return rows, sorted(set(conflicts))
    except ServiceError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ServiceError("BOM_PARSED_CONTRACT_INVALID", 422) from None


def _summary(content):
    rows = content["rows"]
    counts = {state: sum(row["decision"]["application_status"] == state for row in rows)
              for state in ("UNRESOLVED", "APPLICABLE", "NOT_MOUNTED", "SETTING")}
    return {"source_row_count": len(rows), "application_counts": counts,
            "mapped_applicable_rows": sum(row["decision"]["application_status"] == "APPLICABLE" and row["decision"]["item_id"] is not None for row in rows),
            "technical_review": "UNREVIEWED", "actual_manufacturing_feasibility": "UNKNOWN",
            "application_state": "BLOCKED" if counts["UNRESOLVED"] == len(rows) else "CONDITIONAL_DEMO"}


class BomService(DomainService):
    def __init__(self, db, identity, private_root, runner):
        super().__init__(db, identity)
        self.private_root = Path(private_root)
        self.runner = runner

    def sources(self, principal):
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, principal.company_id)
            if not conn.execute("SELECT 1 FROM opening_imports WHERE company_id=%s AND run_id='INITIAL'", (principal.company_id,)).fetchone():
                raise ServiceError("LEDGER_NOT_INITIALIZED", 404)
        return {"sources": [{key: value for key, value in row.items() if key != "relative_path"}
                            for row in source_catalog(self.private_root)], "runner": self.runner.status()}

    @staticmethod
    def _snapshot(connection, company_id, expected_version, configuration_id):
        state = connection.execute("SELECT version FROM ledger_state WHERE company_id=%s FOR UPDATE", (company_id,)).fetchone()
        if not state:
            raise ServiceError("LEDGER_NOT_INITIALIZED", 409)
        if state["version"] != expected_version:
            raise ServiceError("BOM_LEDGER_VERSION_CHANGED", 409)
        row = connection.execute("SELECT payload FROM service_snapshots WHERE company_id=%s AND run_id='INITIAL' AND ledger_version=%s", (company_id, expected_version)).fetchone()
        if not row:
            raise ServiceError("BOM_LEDGER_NOT_READY", 409)
        payload = row["payload"]
        if payload["opening_balance"]["configuration"]["configuration_id"] != configuration_id:
            raise ServiceError("BOM_CONFIGURATION_MISMATCH", 422)
        return payload

    def _document(self, connection, company_id, document_id, *, lock=False):
        try:
            identifier = UUID(str(document_id))
        except (ValueError, TypeError, AttributeError):
            raise ServiceError("BOM_NOT_FOUND", 404) from None
        row = connection.execute("SELECT * FROM bom_documents WHERE company_id=%s AND id=%s" + (" FOR UPDATE" if lock else ""), (company_id, identifier)).fetchone()
        if not row:
            raise ServiceError("BOM_NOT_FOUND", 404)
        return row

    @staticmethod
    def _audit(connection, principal, document, action, extra=None):
        connection.execute("INSERT INTO bom_audit(audit_id,company_id,actor_id,action,document_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
                           (uuid4(), document["company_id"], principal.actor_id, action, document["id"], Jsonb({
                               "document_version": document["version"], "payload_hash": document["payload_hash"],
                               "status": document["status"], "identity_revision": principal.identity_revision, **(extra or {})})))

    @staticmethod
    def _revision(connection, principal, document):
        connection.execute("INSERT INTO bom_revisions(document_id,version,content,canonical_bytes,payload_hash,actor_id) VALUES (%s,%s,%s,%s,%s,%s)",
                           (document["id"], document["version"], Jsonb(document["content"]), document["canonical_bytes"], document["payload_hash"], principal.actor_id))

    @staticmethod
    def _view(document):
        return {**document, "summary": _summary(document["content"])}

    def create(self, principal, company_id, body: BomCreate):
        body = BomCreate.model_validate(body)
        def work(conn):
            self._snapshot(conn, company_id, body.expected_ledger_version, body.configuration_id)
            candidate = next((row for row in source_catalog(self.private_root) if row["source_id"] == body.source_id), None)
            if candidate is None:
                raise ServiceError("BOM_SOURCE_NOT_FOUND", 404)
            root = self.private_root.resolve(strict=True)
            data = _bounded(_source_path(root, candidate["relative_path"]), MAX_SOURCE_BYTES)
            if len(data) != candidate["bytes"] or sha256(data).hexdigest() != candidate["sha256"]:
                raise ServiceError("BOM_SOURCE_INTEGRITY_MISMATCH", 409)
            parsed = self.runner.parse(data, candidate["filename"])
            if len(canonical_bytes(parsed)) > 8 * 1024 * 1024:
                raise ServiceError("BOM_PARSED_SIZE_REJECTED", 413)
            rows, conflicts = _extract(parsed, candidate["sha256"], candidate["filename"])
            if not isinstance(parsed.get("parser_version"), str) or not parsed["parser_version"] or len(parsed["parser_version"]) > 200:
                raise ServiceError("BOM_PARSER_VERSION_REQUIRED", 422)
            source = {key: candidate[key] for key in ("source_id", "filename", "sha256", "format", "bytes")}
            source.update(parser_version=parsed["parser_version"], source_kind="actual_bom", parsed_sha256=content_hash(parsed))
            existing = conn.execute("SELECT * FROM bom_sources WHERE company_id=%s AND source_hash=%s", (company_id, source["sha256"])).fetchone()
            if existing:
                if bytes(existing["raw_bytes"]) != data or content_hash(existing["parsed"]) != source["parsed_sha256"]:
                    raise ServiceError("BOM_EXISTING_SOURCE_CONFLICT", 409)
            else:
                conn.execute("INSERT INTO bom_sources(company_id,source_hash,source_id,filename,format,raw_bytes,parsed,parser_version,byte_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (company_id, source["sha256"], source["source_id"], source["filename"], source["format"], data, Jsonb(parsed), source["parser_version"], len(data)))
            content = {"kind": "bom_registration", "configuration_id": body.configuration_id,
                       "expected_ledger_version": body.expected_ledger_version, "source": source, "source_conflicts": conflicts,
                       "rows": rows, "review_note": "", "technical_review": "UNREVIEWED",
                       "configuration_binding": "DEMO_CONTEXT_ONLY", "registration_scope": "SOURCE_WITH_APPLICATION_REVIEW"}
            raw = canonical_bytes(content)
            document = conn.execute("INSERT INTO bom_documents(id,company_id,source_hash,configuration_id,content,canonical_bytes,payload_hash,canonical_version,version,status,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,'DRAFT',%s) RETURNING *",
                                    (uuid4(), company_id, source["sha256"], body.configuration_id, Jsonb(content), raw, sha256(raw).hexdigest(), CANONICAL_VERSION, principal.actor_id)).fetchone()
            self._revision(conn, principal, document)
            self._audit(conn, principal, document, "bom.create")
            return self._view(document)
        return self._run(principal, company_id, "document.create", body.idempotency_key,
                         {"bom_action": "create", **body.model_dump()}, work)

    def revise(self, principal, company_id, document_id, body: BomRevise):
        body = BomRevise.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["status"] == "POSTED":
                raise ServiceError("POSTED_DOCUMENT_IMMUTABLE", 409)
            if document["status"] == "REJECTED":
                raise ServiceError("BOM_DOCUMENT_REJECTED", 409)
            if document["created_by"] != principal.actor_id:
                raise ServiceError("DOCUMENT_AUTHOR_REQUIRED", 403)
            payload = self._snapshot(conn, company_id, body.expected_ledger_version, document["configuration_id"])
            decisions = {row.row_id: row for row in body.rows}
            original_ids = {row["row_id"] for row in document["content"]["rows"]}
            if len(decisions) != len(body.rows) or set(decisions) != original_ids:
                raise ServiceError("BOM_REVIEW_COVERAGE_MISMATCH", 422)
            item_ids = {row["display_id"] for row in payload["entities"] if row["type"] == "InternalItem"}
            if any(row.item_id is not None and row.item_id not in item_ids for row in body.rows):
                raise ServiceError("BOM_ITEM_NOT_IN_LEDGER", 422)
            content = deepcopy(document["content"])
            content["expected_ledger_version"], content["review_note"] = body.expected_ledger_version, body.review_note
            for row in content["rows"]:
                row["decision"] = decisions[row["row_id"]].model_dump(exclude={"row_id"})
            raw = canonical_bytes(content)
            document = conn.execute("UPDATE bom_documents SET content=%s,canonical_bytes=%s,payload_hash=%s,version=version+1,status='DRAFT',reviewed_by=NULL,approved_by=NULL,approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,review_record=NULL,change_request=NULL,updated_at=now() WHERE id=%s RETURNING *",
                                    (Jsonb(content), raw, sha256(raw).hexdigest(), document["id"])).fetchone()
            self._revision(conn, principal, document)
            self._audit(conn, principal, document, "bom.revise")
            return self._view(document)
        return self._run(principal, company_id, "document.update", body.idempotency_key,
                         {"bom_action": "revise", "bom_id": document_id, **body.model_dump()}, work)

    def review(self, principal, company_id, document_id, body: BomReview):
        body = BomReview.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_REVIEW_FORBIDDEN", 403)
            if document["status"] != "DRAFT":
                raise ServiceError("DOCUMENT_STATE_CHANGED", 409)
            # Physical technical approval is unavailable in this source-registration
            # slice. Every reviewer must acknowledge that persistent limitation.
            if not body.acknowledge_unresolved:
                raise ServiceError("BOM_UNRESOLVED_ACKNOWLEDGEMENT_REQUIRED", 422)
            source = document["content"]["source"]
            evidence = conn.execute("SELECT raw_bytes,parsed FROM bom_sources WHERE company_id=%s AND source_hash=%s", (company_id, source["sha256"])).fetchone()
            if not evidence or sha256(bytes(evidence["raw_bytes"])).hexdigest() != source["sha256"] or content_hash(evidence["parsed"]) != source["parsed_sha256"]:
                raise ServiceError("BOM_SOURCE_INTEGRITY_MISMATCH", 409)
            record = {"scope": "SOURCE_REGISTRATION_AND_CONDITIONAL_APPLICATION_ONLY", "note": body.review_note,
                      "acknowledge_unresolved": True, "source_row_count": len(document["content"]["rows"]),
                      "technical_review": "UNREVIEWED", "actual_manufacturing_feasibility": "UNKNOWN"}
            document = conn.execute("UPDATE bom_documents SET status='REVIEWED',reviewed_by=%s,review_record=%s,updated_at=now() WHERE id=%s RETURNING *", (principal.actor_id, Jsonb(record), document["id"])).fetchone()
            self._audit(conn, principal, document, "bom.review", record)
            return self._view(document)
        return self._run(principal, company_id, "document.review", body.idempotency_key,
                         {"bom_action": "review", "bom_id": document_id, **body.model_dump()}, work)

    def approve(self, principal, company_id, document_id, body: DocumentAction):
        body = DocumentAction.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_APPROVAL_FORBIDDEN", 403)
            if document["status"] != "REVIEWED" or not document["review_record"]:
                raise ServiceError("REVIEW_REQUIRED", 409)
            self.identity.require_actor(document["reviewed_by"], company_id, "document.review", conn)
            document = conn.execute("UPDATE bom_documents SET status='APPROVED',approved_by=%s,approved_version=version,approved_hash=payload_hash,approval_policy_version=%s,updated_at=now() WHERE id=%s RETURNING *", (principal.actor_id, principal.permission_profile_version, document["id"])).fetchone()
            self._audit(conn, principal, document, "bom.approve")
            return self._view(document)
        return self._run(principal, company_id, "document.approve", body.idempotency_key,
                         {"bom_action": "approve", "bom_id": document_id, **body.model_dump()}, work)

    def _reviewer_outcome(self, principal, company_id, document_id, body, outcome):
        """A reviewer other than the author returns or rejects one exact version."""
        body = BomReviewerOutcome.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["created_by"] == principal.actor_id:
                raise ServiceError("SELF_REVIEW_FORBIDDEN", 403)
            if document["status"] not in {"DRAFT", "REVIEWED", "APPROVED"}:
                raise ServiceError("DOCUMENT_STATE_CHANGED", 409)
            record = {"reason": body.reason, "by": principal.actor_id, "version": document["version"],
                      "payload_hash": document["payload_hash"], "previous_status": document["status"]}
            if outcome == "request_changes":
                document = conn.execute("UPDATE bom_documents SET status='DRAFT',reviewed_by=NULL,approved_by=NULL,approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,review_record=NULL,change_request=%s,updated_at=now() WHERE id=%s RETURNING *",
                                        (Jsonb(record), document["id"])).fetchone()
            else:
                document = conn.execute("UPDATE bom_documents SET status='REJECTED',approved_by=NULL,approved_version=NULL,approved_hash=NULL,approval_policy_version=NULL,rejection=%s,updated_at=now() WHERE id=%s RETURNING *",
                                        (Jsonb(record), document["id"])).fetchone()
            self._audit(conn, principal, document, "bom." + outcome, record)
            return self._view(document)
        return self._run(principal, company_id, "document.review", body.idempotency_key,
                         {"bom_action": outcome, "bom_id": document_id, **body.model_dump()}, work)

    def request_changes(self, principal, company_id, document_id, body):
        return self._reviewer_outcome(principal, company_id, document_id, body, "request_changes")

    def reject(self, principal, company_id, document_id, body):
        return self._reviewer_outcome(principal, company_id, document_id, body, "reject")

    def post(self, principal, company_id, document_id, body: DocumentAction):
        body = DocumentAction.model_validate(body)
        def work(conn):
            document = self._document(conn, company_id, document_id, lock=True)
            self._exact(document, body.expected_version, body.payload_hash)
            if document["status"] != "APPROVED" or document["approved_version"] != body.expected_version or document["approved_hash"] != body.payload_hash or document["approved_by"] == document["created_by"]:
                raise ServiceError("EXACT_APPROVAL_REQUIRED", 409)
            approver = self.identity.require_actor(document["approved_by"], company_id, "document.approve", conn)
            if approver["permission_profile_version"] != document["approval_policy_version"]:
                raise ServiceError("APPROVAL_PERMISSION_CHANGED", 409)
            self.identity.require_actor(document["reviewed_by"], company_id, "document.review", conn)
            content = document["content"]
            self._snapshot(conn, company_id, content["expected_ledger_version"], document["configuration_id"])
            source = content["source"]
            evidence = conn.execute("SELECT raw_bytes,parsed FROM bom_sources WHERE company_id=%s AND source_hash=%s", (company_id, source["sha256"])).fetchone()
            if not evidence or sha256(bytes(evidence["raw_bytes"])).hexdigest() != source["sha256"] or content_hash(evidence["parsed"]) != source["parsed_sha256"]:
                raise ServiceError("BOM_SOURCE_INTEGRITY_MISMATCH", 409)
            version = conn.execute("UPDATE ledger_state SET version=version+1 WHERE company_id=%s RETURNING version", (company_id,)).fetchone()["version"]
            document = conn.execute("UPDATE bom_documents SET status='POSTED',ledger_version=%s,updated_at=now() WHERE id=%s RETURNING *", (version, document["id"])).fetchone()
            event_id = uuid4()
            approval = {key: document[key] for key in ("created_by", "reviewed_by", "approved_by", "approved_version", "approved_hash", "approval_policy_version")}
            event = {"event_type": "BomRegistered", "company_id": company_id, "ledger_version": version,
                     "bom_id": str(document["id"]), "bom_version": document["version"], "payload_hash": document["payload_hash"],
                     "configuration_id": document["configuration_id"], "source": content["source"], "rows": content["rows"],
                     "content": content, "approval": approval}
            conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,bom_document_id,event_type,payload,actor_id) VALUES (%s,%s,%s,NULL,%s,'BomRegistered',%s,%s)",
                         (event_id, company_id, version, document["id"], Jsonb(event), principal.actor_id))
            conn.execute("INSERT INTO outbox(event_id,company_id,ledger_version,payload) VALUES (%s,%s,%s,%s)", (event_id, company_id, version, Jsonb(event)))
            self._audit(conn, principal, document, "bom.post", {"ledger_version": version, "event_id": str(event_id)})
            return {**self._view(document), "event_id": str(event_id), "projection_status": "PENDING"}
        return self._run(principal, company_id, "document.post", body.idempotency_key,
                         {"bom_action": "post", "bom_id": document_id, **body.model_dump()}, work)

    def get(self, principal, company_id, document_id):
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id)
            return _public(self._view(self._document(conn, company_id, document_id)))

    def compare(self, principal, company_id, body: BomCompare):
        body = BomCompare.model_validate(body)
        refs = [ref for ref in (body.left, body.right) if ref is not None]
        try:
            identifiers = [UUID(ref.document_id) for ref in refs]
        except ValueError:
            raise ServiceError("BOM_NOT_FOUND", 404) from None
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id)
            # One SELECT observes both versions together. No source parsing,
            # row lock, operation, audit event or ledger write is performed.
            documents = conn.execute("SELECT * FROM bom_documents WHERE company_id=%s AND id=ANY(%s)",
                                     (company_id, identifiers)).fetchall()
            by_id = {str(document["id"]): document for document in documents}
            selected = []
            for ref, identifier in zip(refs, identifiers):
                document = by_id.get(str(identifier))
                if document is None:
                    raise ServiceError("BOM_NOT_FOUND", 404)
                self._exact(document, ref.expected_version, ref.payload_hash)
                selected.append(document)
            result = compare_bom_documents(selected[0] if body.left else None, selected[-1])
            result["meta"] = {"company_id": company_id, "identity_revision": principal.identity_revision}
            return result

    def review_focus(self, principal, company_id, body: BomReviewFocus):
        body = BomReviewFocus.model_validate(body)
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id)
            document = self._document(conn, company_id, body.document_id)
            self._exact(document, body.expected_version, body.payload_hash)
            reference = None
            if not mapped_row_ids(document):
                reference = conn.execute("""SELECT * FROM bom_documents
                    WHERE company_id=%s AND source_hash=%s AND configuration_id=%s AND status='POSTED'
                    AND EXISTS (SELECT 1 FROM jsonb_array_elements(content->'rows') AS source_row
                        WHERE source_row->'decision'->>'application_status'='APPLICABLE'
                          AND coalesce(source_row->'decision'->>'item_id','') <> '')
                    ORDER BY ledger_version DESC, updated_at DESC, id DESC LIMIT 1""",
                    (company_id, document["source_hash"], document["configuration_id"])).fetchone()
                if reference is not None:
                    self._exact(reference, reference["version"], reference["payload_hash"])
            result = review_focus(document, reference)
            # A concurrent edit must not leave a display hint attached to an
            # obsolete target. POSTED references are immutable source records.
            current = self._document(conn, company_id, body.document_id)
            self._exact(current, body.expected_version, body.payload_hash)
            result["meta"] = {"company_id": company_id, "identity_revision": principal.identity_revision}
            return result

    def list(self, principal, company_id, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ServiceError("INVALID_PAGE_LIMIT", 422)
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id)
            rows = conn.execute("SELECT * FROM bom_documents WHERE company_id=%s ORDER BY created_at DESC,id LIMIT %s", (company_id, limit)).fetchall()
            return _public([self._view(row) for row in rows])

    def source_bytes(self, principal, company_id, document_id):
        with self.db.connect() as conn:
            self.identity.require(principal, "document.read", conn, company_id)
            document = self._document(conn, company_id, document_id)
            row = conn.execute("SELECT filename,raw_bytes,source_hash FROM bom_sources WHERE company_id=%s AND source_hash=%s", (company_id, document["source_hash"])).fetchone()
            data = bytes(row["raw_bytes"])
            if sha256(data).hexdigest() != row["source_hash"]:
                raise ServiceError("BOM_SOURCE_INTEGRITY_MISMATCH", 409)
            return row["filename"], data
