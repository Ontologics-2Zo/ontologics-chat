"""Actual PostgreSQL registration contracts with independent synthetic source cells.

The parser double is explicit: sandbox isolation is verified by test_bom_runner,
not these domain tests. Every DB case owns a disposable random schema.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from pydantic import ValidationError

from service.bom import BomService, _extract, source_catalog
from service.bom_contracts import BomCreate, BomDecision, BomReview, BomRevise
from service.contracts import DocumentAction
from service.db import Database
from service.errors import ServiceError
from service.identity import IdentityService
from service.imports import BASELINE_PATH, load_initial
from service.ledger import LedgerReader
from tests.test_imports import synthetic_baseline


class ParserDouble:
    def __init__(self):
        self.calls = 0
        self.result = {"parser_version": "explicit-test-double/1", "format": "csv", "sheets": [{"name": "Parts", "rows": [
            {"row_number": 1, "cells": ["Example independent BOM"]},
            {"row_number": 2, "cells": ["Quantity", "Designation", "Package", "Designator"]},
            {"row_number": 3, "cells": ["2", "Example resistor", "small", "R1 R2"]},
            {"row_number": 5, "cells": ["1", "Example option", "switch", "J1"]},
            {"row_number": 8, "cells": ["3", "Example unassigned", "small", "C1,C2,C3"]},
        ]}]}

    def parse(self, data, filename):
        self.calls += 1
        return deepcopy(self.result)

    def status(self):
        return {"ready": True, "code": "EXPLICIT_TEST_DOUBLE"}


class BomContractTests(unittest.TestCase):
    def test_quantity_and_application_contract_does_not_accept_fake_authority(self):
        base = dict(row_id="a" * 64, application_status="APPLICABLE", quantity_per_unit="2", unit="piece",
                    item_id=None, review_basis="DEMO_ASSUMPTION", evidence_note="Independent conditional example")
        self.assertIsNone(BomDecision(**base).item_id)
        for change in ({"quantity_per_unit": "NaN"}, {"quantity_per_unit": "Infinity"}, {"quantity_per_unit": "-1"},
                       {"quantity_per_unit": "1e-12"}, {"review_basis": "SOURCE_VERIFIED"}, {"approved_by": "forged"},
                       {"application_status": "UNRESOLVED"}, {"application_status": "NOT_MOUNTED"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                BomDecision(**{**base, **change})

    def test_source_extraction_preserves_original_cells_row_numbers_and_unknowns(self):
        parser = ParserDouble()
        rows, conflicts = _extract(parser.result, "b" * 64, "source.csv")
        self.assertEqual([row["source_row"] for row in rows], [3, 5, 8])
        self.assertEqual([row["ordinal"] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0]["source_quantity"], "2")
        self.assertEqual(rows[0]["designators"], ["R1", "R2"])
        self.assertEqual(rows[0]["decision"]["application_status"], "UNRESOLVED")
        self.assertIsNone(rows[0]["decision"]["quantity_per_unit"])
        self.assertFalse(conflicts)
        parser.result["sheets"][0]["rows"][2]["cells"][0] = "NA"
        rows, conflicts = _extract(parser.result, "b" * 64, "source.csv")
        self.assertIsNone(rows[0]["source_quantity"])
        self.assertIn("SOURCE_ROW_REQUIRES_REVIEW", conflicts)

    def test_duplicate_required_columns_never_choose_an_arbitrary_quantity(self):
        for duplicate in ("Quantity", "Designation", "Designator"):
            parsed = ParserDouble().result
            parsed["sheets"][0]["rows"][1]["cells"].append(duplicate)
            parsed["sheets"][0]["rows"][2]["cells"].append("99")
            with self.subTest(duplicate=duplicate), self.assertRaises(ServiceError) as caught:
                _extract(parsed, "b" * 64, "source.csv")
            self.assertEqual(caught.exception.code, "BOM_AMBIGUOUS_HEADER_REQUIRES_REVIEW")

    def test_populated_unrecognized_sheet_is_not_silently_omitted(self):
        parsed = ParserDouble().result
        parsed["sheets"].append({"name": "Other parts", "rows": [
            {"row_number": 1, "cells": ["Qty", "Part", "Ref"]},
            {"row_number": 2, "cells": ["9", "Another component", "U1"]},
        ]})
        with self.assertRaises(ServiceError) as caught:
            _extract(parsed, "b" * 64, "source.ods")
        self.assertEqual(caught.exception.code, "BOM_UNRECOGNIZED_SHEET_REQUIRES_REVIEW")

    def test_empty_additional_sheet_does_not_block_valid_source(self):
        parsed = ParserDouble().result
        parsed["sheets"].append({"name": "Empty", "rows": [
            {"row_number": 1, "cells": []}, {"row_number": 2, "cells": ["", " "]},
        ]})
        rows, conflicts = _extract(parsed, "b" * 64, "source.ods")
        self.assertEqual(len(rows), 3)
        self.assertFalse(conflicts)

    def test_configuration_filename_sheet_conflict_is_not_silently_resolved(self):
        parsed = ParserDouble().result
        parsed["sheets"][0]["name"] = "Example-WROVER"
        rows, conflicts = _extract(parsed, "b" * 64, "Example-WROOM.csv")
        self.assertEqual(len(rows), 3)
        self.assertIn("SOURCE_FILENAME_SHEET_CONFIGURATION_CONFLICT", conflicts)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is required for actual PostgreSQL tests")
class BomDomainTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["TEST_DATABASE_URL"]
        self.schema = "bom_test_" + uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.remove_schema)
        self.db = Database(self.dsn, schema=self.schema)
        self.db.migrate()
        self.identity = IdentityService(self.db, enabled=True, app_env="test")
        self.identity.provision_dev_accounts()
        self.author = self.identity.login("dev-operator-a").principal
        self.reviewer = self.identity.login("dev-reviewer-b").principal
        self.viewer = self.identity.login("dev-viewer-f").principal
        self.other = self.identity.login("dev-other-g").principal
        self.company = self.author.company_id
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        raw = json.dumps(synthetic_baseline()).encode()
        source = self.root / BASELINE_PATH
        source.parent.mkdir(parents=True)
        source.write_bytes(raw)
        self.relative = "v1.7-data/data/raw/independent.csv"
        self.raw_path = self.root / self.relative
        self.raw_path.parent.mkdir(parents=True)
        self.raw = b"independent test source, not private BOM\n"
        self.raw_path.write_bytes(self.raw)
        entries = [{"path": BASELINE_PATH, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)},
                   {"path": self.relative, "sha256": sha256(self.raw).hexdigest(), "bytes": len(self.raw)}]
        (self.root / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": entries}))
        self.ledger = LedgerReader(self.db, self.identity)
        initial = load_initial(self.root, company_id=self.company, run_id="INITIAL", profile_id="test-profile", imported_at="2025-02-02T12:00:00+00:00")
        self.ledger.install_initial(initial, local_development=True)
        self.initial = self.ledger.read(self.author)
        self.runner = ParserDouble()
        self.bom = BomService(self.db, self.identity, self.root, self.runner)
        self.source_id = sha256(self.relative.encode()).hexdigest()

    def remove_schema(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def assert_error(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def create(self, key="intake", actor=None):
        return self.bom.create(actor or self.author, self.company, BomCreate(source_id=self.source_id, configuration_id="config-a", expected_ledger_version=1, idempotency_key=key))

    def action(self, action, actor, document, key, **extra):
        body = dict(expected_version=document["version"], payload_hash=document["payload_hash"], idempotency_key=key, **extra)
        cls = BomReview if action == "review" else DocumentAction
        return getattr(self.bom, action)(actor, self.company, document["id"], cls(**body))

    def revised(self, document=None):
        document = document or self.create()
        rows = [dict(row_id=row["row_id"], **row["decision"]) for row in document["content"]["rows"]]
        rows[0].update(application_status="APPLICABLE", quantity_per_unit="2", unit="piece", item_id="item-a", review_basis="DEMO_ASSUMPTION", evidence_note="Explicit independent test mapping assumption")
        rows[1].update(application_status="NOT_MOUNTED", review_basis="DEMO_ASSUMPTION", evidence_note="Explicit independent test nonmount assumption")
        rows[2].update(application_status="APPLICABLE", quantity_per_unit="3", unit="piece", review_basis="DEMO_ASSUMPTION", evidence_note="Unknown item and inventory remain unknown")
        body = BomRevise(expected_version=document["version"], payload_hash=document["payload_hash"], idempotency_key="revise", expected_ledger_version=1, review_note="Independent row-level conditional review", rows=rows)
        return self.bom.revise(self.author, self.company, document["id"], body)

    def approved(self, document=None):
        document = document or self.revised()
        document = self.action("review", self.reviewer, document, "review", review_note="Source rows checked; no physical technical approval", acknowledge_unresolved=True)
        return self.action("approve", self.reviewer, document, "approve")

    def test_durable_all_rows_review_approval_event_and_no_inventory_mutation(self):
        drafted = self.create()
        self.assertEqual(drafted["summary"]["application_counts"]["UNRESOLVED"], 3)
        self.assertEqual(self.bom.source_bytes(self.author, self.company, drafted["id"])[1], self.raw)
        document = self.revised(drafted)
        approved = self.approved(document)
        self.assertEqual(approved["content"]["technical_review"], "UNREVIEWED")
        self.assertEqual(approved["summary"]["mapped_applicable_rows"], 1)
        posted = self.action("post", self.author, approved, "post")
        self.assertEqual(posted["ledger_version"], 2)
        self.assertEqual(posted["status"], "POSTED")
        self.assertEqual(self.action("post", self.author, approved, "post"), posted)
        resumed = BomService(self.db, self.identity, self.root, self.runner)
        self.assertEqual(resumed.get(self.author, self.company, posted["id"])["content"], posted["content"])
        with self.db.connect() as conn:
            event = conn.execute("SELECT * FROM ledger_events WHERE event_type='BomRegistered'").fetchone()
            self.assertIsNone(event["document_id"])
            self.assertEqual(str(event["bom_document_id"]), posted["id"])
            self.assertEqual(event["payload"]["approval"]["approved_by"], self.reviewer.actor_id)
            self.assertEqual(len(event["payload"]["rows"]), 3)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM bom_audit").fetchone()["n"], 5)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"], 2)
            original = conn.execute("SELECT payload FROM service_snapshots WHERE ledger_version=1").fetchone()["payload"]
        self.assertEqual(original["opening_balance"], self.initial["opening_balance"])

    def test_self_review_unapproved_post_and_viewer_write_rejected(self):
        document = self.create()
        self.assert_error("SELF_REVIEW_FORBIDDEN", lambda: self.action("review", self.author, document, "self-review", review_note="Invalid self review should fail", acknowledge_unresolved=True))
        self.assert_error("PERMISSION_DENIED", lambda: self.create("viewer", self.viewer))
        self.assert_error("EXACT_APPROVAL_REQUIRED", lambda: self.action("post", self.author, document, "early-post"))
        self.assert_error("LEDGER_NOT_INITIALIZED", lambda: self.bom.sources(self.other))
        self.assert_error("BOM_NOT_FOUND", lambda: self.bom.get(self.other, self.other.company_id, document["id"]))
        self.assert_error("PERMISSION_DENIED", lambda: self.bom.get(self.other, self.company, document["id"]))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT version FROM ledger_state").fetchone()["version"], 1)

    def test_source_integrity_and_replay_identity_are_enforced(self):
        original = self.create()
        self.assertEqual(self.create(), original)
        self.assertEqual(self.runner.calls, 1)
        self.raw_path.write_bytes(b"modified")
        self.assert_error("BOM_SOURCE_INTEGRITY_MISMATCH", lambda: self.create("modified"))
        self.assertEqual(self.bom.source_bytes(self.author, self.company, original["id"])[1], self.raw)

    def test_missing_acknowledgement_and_stale_approval_are_rejected(self):
        document = self.create()
        self.assert_error("BOM_UNRESOLVED_ACKNOWLEDGEMENT_REQUIRED", lambda: self.action("review", self.reviewer, document, "no-ack", review_note="No acknowledgement of technical unknowns"))
        revised = self.revised(document)
        self.assert_error("DOCUMENT_VERSION_CHANGED", lambda: self.action("review", self.reviewer, document, "old", review_note="Checking obsolete source proposal", acknowledge_unresolved=True))
        self.assertEqual(revised["version"], 2)

    def test_exact_all_row_coverage_and_known_item_boundary(self):
        document = self.create()
        base = dict(expected_version=document["version"], payload_hash=document["payload_hash"], expected_ledger_version=1, review_note="Independent complete source review")
        rows = [dict(row_id=row["row_id"], **row["decision"]) for row in document["content"]["rows"]]
        for changed in (rows[:-1], [rows[0], rows[0], rows[2]]):
            body = BomRevise(**base, idempotency_key=uuid4().hex, rows=changed)
            self.assert_error("BOM_REVIEW_COVERAGE_MISMATCH", lambda: self.bom.revise(self.author, self.company, document["id"], body))
        rows[0].update(application_status="APPLICABLE", quantity_per_unit="2", unit="piece", item_id="invented-item", review_basis="DEMO_ASSUMPTION", evidence_note="Attempted invented item mapping")
        body = BomRevise(**base, idempotency_key="invented", rows=rows)
        self.assert_error("BOM_ITEM_NOT_IN_LEDGER", lambda: self.bom.revise(self.author, self.company, document["id"], body))

    def test_ledger_advance_blocks_stale_bom_activation(self):
        approved = self.approved()
        with self.db.connect() as conn:
            conn.execute("UPDATE ledger_state SET version=version+1 WHERE company_id=%s", (self.company,))
        self.assert_error("BOM_LEDGER_VERSION_CHANGED", lambda: self.action("post", self.author, approved, "stale-base"))
        self.assertEqual(self.bom.get(self.author, self.company, approved["id"])["status"], "APPROVED")

    def test_revoked_approver_blocks_post_and_preserves_source(self):
        approved = self.approved()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active=false WHERE actor_id=%s", (self.reviewer.actor_id,))
        self.assert_error("APPROVER_PERMISSION_REVOKED", lambda: self.action("post", self.author, approved, "revoked-post"))
        self.assertEqual(self.bom.source_bytes(self.author, self.company, approved["id"])[1], self.raw)

    def test_concurrent_retries_have_one_effect_and_posted_content_is_immutable(self):
        approved = self.approved()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.action("post", self.author, approved, "same-post"), range(2)))
        self.assertEqual(results[0], results[1])
        posted = results[0]
        rows = [dict(row_id=row["row_id"], **row["decision"]) for row in posted["content"]["rows"]]
        body = BomRevise(expected_version=posted["version"], payload_hash=posted["payload_hash"], idempotency_key="post-revise", expected_ledger_version=2, review_note="Attempt to rewrite posted source document", rows=rows)
        self.assert_error("POSTED_DOCUMENT_IMMUTABLE", lambda: self.bom.revise(self.author, self.company, posted["id"], body))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events WHERE event_type='BomRegistered'").fetchone()["n"], 1)

    def test_symlink_manifest_source_is_rejected(self):
        self.raw_path.unlink()
        self.raw_path.symlink_to(self.root / BASELINE_PATH)
        self.assert_error("BOM_SOURCE_LOCATION_REJECTED", lambda: self.create())


if __name__ == "__main__":
    unittest.main()
