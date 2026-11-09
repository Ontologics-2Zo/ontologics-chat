"""Synthetic event summaries and real disposable-PostgreSQL read boundaries."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import unittest
from uuid import UUID

from service.contracts import content_hash
from service.errors import ServiceError
from service.ledger import ledger_event_view
from tests import test_ledger as fixtures


SECRET_TEXT = "synthetic-private-note-not-for-event-summary"
DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"
EVENT_ID = "22222222-2222-4222-8222-222222222222"
PUBLIC_KEYS = {"event_id", "ledger_version", "event_type", "recorded_at", "summary",
               "document_kind", "document_ref", "origin"}


def event(kind="bom_registration"):
    category = "BomRegistered" if kind == "bom_registration" else "EvidenceDocumentPosted" if kind == "evidence_note" else "ProcurementPosted"
    content = {"kind": kind, "note": SECRET_TEXT,
               "source": {"filename": SECRET_TEXT, "source_kind": "actual_bom"}, "rows": [SECRET_TEXT]}
    payload = {"event_type": category, "company_id": "DEMO-A", "ledger_version": 2,
               "content": content, "payload_hash": content_hash(content), "actor_id": SECRET_TEXT,
               "summary": SECRET_TEXT, "approval": {"approved_by": SECRET_TEXT}}
    payload.update({"bom_id": DOCUMENT_ID, "bom_version": 3} if category == "BomRegistered"
                   else {"document_id": DOCUMENT_ID, "document_version": 3})
    fk = {"BomRegistered": "bom_document_id", "EvidenceDocumentPosted": "document_id",
          "ProcurementPosted": "procurement_document_id"}[category]
    return {"event_id": UUID(EVENT_ID), "company_id": "DEMO-A", "ledger_version": 2,
            "event_type": category, "recorded_at": datetime(2025, 2, 2, 12, tzinfo=timezone.utc),
            "payload": payload, "actor_id": SECRET_TEXT, "document_id": None,
            "bom_document_id": None, "procurement_document_id": None, fk: UUID(DOCUMENT_ID)}


class LedgerEventViewTests(unittest.TestCase):
    def assert_public(self, row):
        before = deepcopy(row)
        result = ledger_event_view(row, "DEMO-A")
        self.assertEqual(row, before)
        self.assertEqual(set(result), PUBLIC_KEYS)
        self.assertNotIn(SECRET_TEXT, json.dumps(result))
        if result["document_ref"] is not None:
            self.assertEqual(set(result["document_ref"]), {"id", "type", "version", "payload_hash"})
        return result

    def test_all_supported_document_kinds_use_fixed_labels_and_exact_references(self):
        labels = {"bom_registration": "BOM 원본 등록", "evidence_note": "근거 문서", "QUOTE": "견적 기록",
                  "PR": "구매 요청", "PO": "발주 기록", "SUPPLY_CONFIRMATION": "공급 확인 기록",
                  "RECEIPT": "입하 기록", "IQC": "수입검사 기록", "CANCEL": "취소 기록"}
        for kind, label in labels.items():
            with self.subTest(kind=kind):
                row = event(kind); result = self.assert_public(row)
                self.assertEqual(result["summary"], label + " 반영 · 문서 11111111 / v3")
                self.assertEqual(result["document_ref"]["payload_hash"], row["payload"]["payload_hash"])
                self.assertEqual(result["document_ref"]["id"], DOCUMENT_ID)
                self.assertEqual(result["origin"], "actual_source_registration" if kind == "bom_registration" else "internal_development")

    def test_initial_import_is_distinct_and_has_no_invented_document(self):
        row = event(); row.update(event_type="OPENING_BALANCE_IMPORTED", ledger_version=1, bom_document_id=None,
            payload={"record_type": "opening_balance", "opening_balance": True, "run_id": "INITIAL", "note": SECRET_TEXT})
        result = self.assert_public(row)
        self.assertEqual(result["origin"], "synthetic_initial")
        self.assertIsNone(result["document_ref"])
        self.assertIn("합성", result["summary"])
        row["payload"]["run_id"] = "OTHER"
        self.assertEqual(self.assert_public(row)["origin"], "unknown")

    def test_malformed_or_mismatched_metadata_cannot_assert_a_document_reference(self):
        changes = [lambda r: r.update(company_id="DEMO-B"),
                   lambda r: r["payload"].update(company_id="DEMO-B"),
                   lambda r: r["payload"].update(ledger_version=3),
                   lambda r: r["payload"].update(ledger_version=True),
                   lambda r: r["payload"].update(event_type="ProcurementPosted"),
                   lambda r: r["payload"].update(bom_version=True),
                   lambda r: r["payload"].update(bom_version="3"),
                   lambda r: r["payload"].update(bom_id=SECRET_TEXT),
                   lambda r: r["payload"].update(payload_hash="f" * 64),
                   lambda r: r["payload"]["content"].update(kind="unrecognized"),
                   lambda r: r.update(bom_document_id=UUID(EVENT_ID)),
                   lambda r: r.update(document_id=UUID(DOCUMENT_ID)),
                   lambda r: r.update(payload=None)]
        for change in changes:
            row = event(); change(row)
            result = self.assert_public(row)
            self.assertIsNone(result["document_ref"])
            self.assertIsNone(result["document_kind"])
            self.assertEqual(result["origin"], "unknown")
            self.assertEqual(result["summary"], "원장 사건 · 근거 확인 필요")

    def test_unknown_future_type_or_purchase_kind_is_not_promoted(self):
        for row in (event("UNKNOWN_KIND"), {**event(), "event_type": "UnknownEvent"}):
            self.assertEqual(self.assert_public(row)["origin"], "unknown")
        row = event()
        row["payload"]["content"]["source"]["source_kind"] = "unknown_source"
        row["payload"]["payload_hash"] = content_hash(row["payload"]["content"])
        self.assertEqual(self.assert_public(row)["origin"], "unknown")


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Disposable TEST_DATABASE_URL is required")
class LedgerEventReadTests(unittest.TestCase):
    # Reuse the independent synthetic initial importer and schema lifecycle;
    # do not inherit or rerun the older LedgerTests' test cases.
    setUp = fixtures.LedgerTests.setUp
    remove_schema = fixtures.LedgerTests.remove_schema
    write_source = fixtures.LedgerTests.write_source
    payload = fixtures.LedgerTests.payload
    install = fixtures.LedgerTests.install
    post_note = fixtures.LedgerTests.post_note

    def business_state(self):
        tables = ("ledger_state", "ledger_events", "service_snapshots", "documents", "document_revisions",
                  "audit_events", "outbox", "operations")
        with self.db.connect() as conn:
            return {name: conn.execute("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) AS state FROM " + name + " t").fetchone()["state"] for name in tables}

    def test_snapshot_and_incremental_feed_share_metadata_without_mixing_versions_or_writes(self):
        self.install(); posted = self.post_note()
        before = self.business_state()
        snapshot = self.ledger.read(self.actor, version=1)
        feed = self.ledger.events(self.actor)
        self.assertEqual([row["ledger_version"] for row in snapshot["events"]], [1])
        self.assertEqual([row["ledger_version"] for row in feed], [1, 2])
        self.assertEqual(feed[0], snapshot["events"][0])
        self.assertEqual(feed[0]["origin"], "synthetic_initial")
        self.assertEqual(feed[1]["document_ref"], {"id": posted["id"], "type": "evidence",
            "version": posted["version"], "payload_hash": posted["payload_hash"]})
        self.assertEqual(self.ledger.events(self.actor, after=1), [feed[1]])
        self.assertEqual(self.ledger.events(self.actor, after=2), [])
        self.assertEqual(self.business_state(), before)
        self.assertEqual(self.ledger.advance_documents(), 1)
        before = self.business_state()
        self.assertEqual(self.ledger.read(self.actor)["events"], list(reversed(feed)))
        self.assertEqual(self.business_state(), before)

    def test_company_and_fresh_read_authorization_remain_enforced(self):
        self.install(); self.install(company="DEMO-B")
        mine = self.ledger.events(self.actor); theirs = self.ledger.events(self.other)
        self.assertNotEqual(mine[0]["event_id"], theirs[0]["event_id"])
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active=false WHERE actor_id=%s", (self.actor.actor_id,))
        before = self.business_state()
        for read in (lambda: self.ledger.read(self.actor), lambda: self.ledger.events(self.actor)):
            with self.assertRaises(ServiceError) as caught:
                read()
            self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.business_state(), before)


if __name__ == "__main__":
    unittest.main()
