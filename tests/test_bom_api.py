"""BOM HTTP contracts using isolated PostgreSQL and an explicit parser double.

Real Docker parser and private source acceptance have separate execution records.
"""
from hashlib import sha256
import json
import os
import unittest
from unittest.mock import patch

import test_service_api as api_fixtures


class SyntheticBomRunner:
    def status(self):
        return {"ready": True, "parser_version": "synthetic-http-test"}

    def parse(self, data, filename):
        return {"parser_version": "synthetic-http-test", "format": "csv", "sheets": [
            {"name": "independent", "rows": [
                {"row_number": 1, "cells": ["Quantity", "Designation", "Package", "Designator"]},
                {"row_number": 2, "cells": ["2", "Synthetic capacitor", "CASE-X", "C1,C2"]},
            ]}
        ]}


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL required")
class BomApiTests(unittest.TestCase):
    setUpClass = classmethod(api_fixtures.ServiceApiIntegrationTests.setUpClass.__func__)
    tearDownClass = classmethod(api_fixtures.ServiceApiIntegrationTests.tearDownClass.__func__)
    drop_schema = api_fixtures.ServiceApiIntegrationTests.drop_schema
    headers = api_fixtures.ServiceApiIntegrationTests.headers
    select = api_fixtures.ServiceApiIntegrationTests.select
    error = api_fixtures.ServiceApiIntegrationTests.error

    def setUp(self):
        api_fixtures.ServiceApiIntegrationTests.setUp(self)
        self.app.state.bom.runner = SyntheticBomRunner()
        raw = b"Quantity,Designation,Package,Designator\n2,Synthetic capacitor,CASE-X,\"C1,C2\"\n"
        relative = "v1.7-data/data/raw/synthetic.csv"
        path = self.private / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        manifest_path = self.private / "SOURCE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [row for row in manifest["files"] if row["path"] != relative]
        manifest["files"].append({"path": relative, "slot": "v1.7-data", "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        manifest_path.write_text(json.dumps(manifest))

    def create(self, session):
        listing = self.client.get("/api/mks2/bom/sources", headers=self.headers(session))
        self.assertEqual(listing.status_code, 200, listing.text)
        sources = listing.json()["sources"]
        self.assertEqual(len(sources), 1)
        actor = self.identity.authenticate(self.client.cookies.get("onto_session"))
        payload = self.ledger.read(actor)
        args = {"source_id": sources[0]["source_id"],
                "configuration_id": payload["opening_balance"]["configuration"]["configuration_id"],
                "expected_ledger_version": 1, "idempotency_key": "http-create"}
        response = self.client.post("/api/mks2/bom/documents", json=args, headers=self.headers(session))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), args

    def test_product_candidates_in_existing_draft_flow_preserve_document_and_ledger(self):
        session = self.select()
        doc, _ = self.create(session)
        api_fixtures.ServiceApiIntegrationTests.mark_projection_ready(self)
        before = self.client.get("/api/mks2/snapshot").json()
        body = {"ledger_version": 1, "bom_id": doc["id"], "bom_version": doc["version"],
                "payload_hash": doc["payload_hash"], "row_id": doc["content"]["rows"][0]["row_id"]}
        response = self.client.post("/api/mks2/product/review", json=body, headers=self.headers(session))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertEqual(result["ledger_mutations"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["payload_hash"], doc["payload_hash"])
        changed = self.client.post("/api/mks2/product/review", json={**body, "payload_hash": "f" * 64}, headers=self.headers(session))
        self.error(changed, 409, "PRODUCT_BOM_VERSION_CHANGED")
        self.assertEqual(self.client.get("/api/mks2/bom/documents/" + doc["id"]).json(),
                         {key: value for key, value in doc.items() if key != "operation_id"})
        self.assertEqual(self.client.get("/api/mks2/snapshot").json(), before)

    @staticmethod
    def compare_ref(document):
        return {"document_id": document["id"], "expected_version": document["version"],
                "payload_hash": document["payload_hash"]}

    def test_compare_is_read_only_with_explicit_baseline_and_no_parser_call(self):
        author = self.select()
        left, args = self.create(author)
        second = self.client.post("/api/mks2/bom/documents", json={**args, "idempotency_key": "second-source-document"},
                                  headers=self.headers(author))
        self.assertEqual(second.status_code, 200, second.text)
        right = second.json()
        viewer = self.select("dev-viewer-f", author)

        def state():
            tables = ("bom_sources", "bom_documents", "bom_revisions", "bom_audit", "operations",
                      "ledger_state", "ledger_events", "audit_events", "outbox", "service_snapshots")
            with self.db.connect() as conn:
                # Both table names and SQL are fixed, independent of HTTP data.
                return {name: conn.execute("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) AS state FROM " + name + " t").fetchone()["state"]
                        for name in tables}

        before = state()
        with patch.object(self.app.state.bom.runner, "parse", side_effect=AssertionError("comparison must not parse")):
            no_baseline = self.client.post("/api/mks2/bom/compare", json={"right": self.compare_ref(right)}, headers=self.headers(viewer))
            self.assertEqual(no_baseline.status_code, 200, no_baseline.text)
            self.assertEqual(no_baseline.json()["status"], "NO_BASELINE")
            response = self.client.post("/api/mks2/bom/compare", json={"left": self.compare_ref(left), "right": self.compare_ref(right)},
                                        headers=self.headers(viewer))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "IDENTICAL_SOURCE")
        self.assertEqual(result["left"]["document_id"], left["id"])
        self.assertEqual(result["right"]["document_id"], right["id"])
        self.assertEqual(result["meta"]["identity_revision"], viewer["principal"]["identity_revision"])
        self.assertEqual((result["model_calls"], result["ledger_mutations"]), (0, 0))
        self.assertEqual(state(), before)

    def test_compare_requires_exact_versions_and_hashes_on_both_sides(self):
        session = self.select(); doc, _ = self.create(session)
        body = {"left": self.compare_ref(doc), "right": self.compare_ref(doc)}
        for side in ("left", "right"):
            for field, value in (("expected_version", 99), ("payload_hash", "f" * 64)):
                request = {**body, side: {**body[side], field: value}}
                self.error(self.client.post("/api/mks2/bom/compare", json=request, headers=self.headers(session)),
                           409, "DOCUMENT_VERSION_CHANGED")
        with self.db.connect() as conn:
            # Corruption is confined to this test's disposable schema.
            conn.execute("UPDATE bom_documents SET canonical_bytes=%s WHERE id=%s", (b"corrupt", doc["id"]))
        self.error(self.client.post("/api/mks2/bom/compare", json=body, headers=self.headers(session)),
                   409, "DOCUMENT_INTEGRITY_FAILURE")

    def test_compare_keeps_identity_company_and_typed_read_boundary(self):
        self.error(self.client.post("/api/mks2/bom/compare", json={"right": {"document_id": "unavailable", "expected_version": 1,
            "payload_hash": "a" * 64}}, headers=self.headers()), 401, "IDENTITY_REQUIRED")
        author = self.select(); doc, _ = self.create(author)
        body = {"right": self.compare_ref(doc)}
        self.error(self.client.post("/api/mks2/bom/compare", json=body, headers=self.headers()), 403, "CSRF_REJECTED")
        self.error(self.client.post("/api/mks2/bom/compare", json={**body, "company_id": "DEMO-B"}, headers=self.headers(author)), 422)
        other = self.identity.login("dev-other-g").principal
        from service.bom_contracts import BomCompare
        from service.errors import ServiceError
        with self.assertRaises(ServiceError) as denied:
            self.app.state.bom.compare(other, "DEMO-B", BomCompare.model_validate(body))
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(ServiceError) as denied:
            self.app.state.bom.compare(other, "DEMO-A", BomCompare.model_validate(body))
        self.assertEqual(denied.exception.status_code, 403)

    def _post_focus_reference(self, author, doc, *, mapped=False):
        endpoint = "/api/mks2/bom/documents/" + doc["id"]
        if mapped:
            row = doc["content"]["rows"][0]
            response = self.client.post(endpoint + "/revise", headers=self.headers(author), json={
                "expected_version": doc["version"], "payload_hash": doc["payload_hash"],
                "expected_ledger_version": doc["content"]["expected_ledger_version"],
                "idempotency_key": "focus-revise-" + doc["id"], "review_note": "Synthetic conditional focus test",
                "rows": [{"row_id": row["row_id"], "application_status": "APPLICABLE", "item_id": "item-a",
                          "quantity_per_unit": "2", "unit": "piece", "review_basis": "DEMO_ASSUMPTION",
                          "evidence_note": "Synthetic conditional item decision"}]})
            self.assertEqual(response.status_code, 200, response.text); doc = response.json()
        exact = {"expected_version": doc["version"], "payload_hash": doc["payload_hash"]}
        reviewer = self.select("dev-reviewer-b", author)
        response = self.client.post(endpoint + "/review", headers=self.headers(reviewer), json={**exact,
            "idempotency_key": "focus-review-" + doc["id"], "review_note": "Independent synthetic source review",
            "acknowledge_unresolved": True})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(endpoint + "/approve", headers=self.headers(reviewer), json={**exact,
            "idempotency_key": "focus-approve-" + doc["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        author = self.select("dev-operator-a", reviewer)
        response = self.client.post(endpoint + "/post", headers=self.headers(author), json={**exact,
            "idempotency_key": "focus-post-" + doc["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        # This isolated HTTP fixture has no background projection worker.
        # Publish its committed event to the read snapshot before another draft.
        self.assertEqual(self.ledger.advance_documents(), 1)
        return author, response.json()

    def _new_focus_draft(self, session, original_args, key):
        actor = self.identity.authenticate(self.client.cookies.get("onto_session"))
        version = self.ledger.read(actor)["metadata"]["current_ledger_version"]
        response = self.client.post("/api/mks2/bom/documents", headers=self.headers(session),
            json={**original_args, "idempotency_key": key, "expected_ledger_version": version})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_review_focus_uses_latest_mapped_source_without_overwriting_newer_unmapped_documents(self):
        author = self.select(); original, args = self.create(author)
        author, reference = self._post_focus_reference(author, original, mapped=True)
        target = self._new_focus_draft(author, args, "focus-new-target")
        body = self.compare_ref(target)

        def state():
            tables = ("bom_sources", "bom_documents", "bom_revisions", "bom_audit", "operations",
                      "ledger_state", "ledger_events", "audit_events", "outbox", "service_snapshots")
            with self.db.connect() as conn:
                return {name: conn.execute("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) AS state FROM " + name + " t").fetchone()["state"]
                        for name in tables}

        before = state()
        with patch.object(self.app.state.bom.runner, "parse", side_effect=AssertionError("focus must not parse")):
            response = self.client.post("/api/mks2/bom/review-focus", headers=self.headers(author), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["basis"], "POSTED_SAME_SOURCE")
        self.assertEqual(result["reference"]["document_id"], reference["id"])
        self.assertEqual(result["row_ids"], [target["content"]["rows"][0]["row_id"]])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["decisions_copied"])
        self.assertEqual(state(), before)
        self.assertEqual(target["content"]["rows"][0]["decision"]["application_status"], "UNRESOLVED")
        author, zero_mapping = self._post_focus_reference(author, target)
        new_target = self._new_focus_draft(author, args, "focus-after-zero")
        before = state()
        response = self.client.post("/api/mks2/bom/review-focus", headers=self.headers(author), json=self.compare_ref(new_target))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["reference"]["document_id"], reference["id"])
        self.assertEqual(response.json()["reference_selection"], "LATEST_POSTED_WITH_MAPPINGS")
        self.assertEqual(response.json()["status"], "FOCUSED")
        self.assertEqual(response.json()["current_mapped_count"], 0)
        self.assertEqual(response.json()["row_ids"], [new_target["content"]["rows"][0]["row_id"]])
        self.assertEqual(state(), before)
        newer = self.client.get("/api/mks2/bom/documents/" + zero_mapping["id"], headers=self.headers(author)).json()
        self.assertEqual(newer["status"], "POSTED")
        self.assertEqual(newer["content"]["rows"][0]["decision"]["application_status"], "UNRESOLVED")

    def test_review_focus_current_mapping_and_empty_self_are_not_historical_inheritance(self):
        author = self.select(); original, _ = self.create(author)
        author, posted = self._post_focus_reference(author, original, mapped=True)
        viewer = self.select("dev-viewer-f", author)
        response = self.client.post("/api/mks2/bom/review-focus", headers=self.headers(viewer), json=self.compare_ref(posted))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["basis"], "CURRENT_DOCUMENT")
        self.assertIsNone(response.json()["reference"])
        self.assertEqual(response.json()["meta"]["identity_revision"], viewer["principal"]["identity_revision"])

    def test_review_focus_exact_target_company_and_no_reference_boundaries(self):
        author = self.select(); doc, _ = self.create(author); body = self.compare_ref(doc)
        response = self.client.post("/api/mks2/bom/review-focus", headers=self.headers(author), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "NO_REFERENCE")
        for field, value in (("expected_version", 99), ("payload_hash", "f" * 64)):
            self.error(self.client.post("/api/mks2/bom/review-focus", headers=self.headers(author), json={**body, field: value}),
                       409, "DOCUMENT_VERSION_CHANGED")
        self.error(self.client.post("/api/mks2/bom/review-focus", headers=self.headers(), json=body), 403, "CSRF_REJECTED")
        self.error(self.client.post("/api/mks2/bom/review-focus", headers=self.headers(author), json={**body, "row_ids": []}), 422)
        from service.bom_contracts import BomReviewFocus
        from service.errors import ServiceError
        other = self.identity.login("dev-other-g").principal
        with self.assertRaises(ServiceError) as denied:
            self.app.state.bom.review_focus(other, "DEMO-B", BomReviewFocus.model_validate(body))
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(ServiceError) as denied:
            self.app.state.bom.review_focus(other, "DEMO-A", BomReviewFocus.model_validate(body))
        self.assertEqual(denied.exception.status_code, 403)

    def test_bom_read_requires_identity_and_csrf_write_boundary(self):
        for route in ("/api/mks2/bom/sources", "/api/mks2/bom/documents"):
            self.error(self.client.get(route), 401, "IDENTITY_REQUIRED")
        session = self.select()
        document, args = self.create(session)
        self.assertEqual(document["status"], "DRAFT")
        self.error(self.client.post("/api/mks2/bom/documents", json=args, headers=self.headers()), 403, "CSRF_REJECTED")
        self.assertEqual(self.client.post("/api/mks2/bom/documents", json=args, headers=self.headers(session)).json(), document)
        foreign = self.select("dev-other-g", session)
        self.error(self.client.get("/api/mks2/bom/documents/" + document["id"], headers=self.headers(foreign)), 404)

    def test_source_registration_http_exact_approval_and_single_ledger_effect(self):
        author = self.select()
        doc, _ = self.create(author)
        endpoint = "/api/mks2/bom/documents/" + doc["id"]
        exact = {"expected_version": doc["version"], "payload_hash": doc["payload_hash"]}
        review = {**exact, "idempotency_key": "http-review", "review_note": "Independent synthetic source-only verification; application unresolved", "acknowledge_unresolved": True}
        self.error(self.client.post(endpoint + "/review", json=review, headers=self.headers(author)), 403)
        reviewer = self.select("dev-reviewer-b", author)
        reviewed = self.client.post(endpoint + "/review", json=review, headers=self.headers(reviewer))
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        approved = self.client.post(endpoint + "/approve", json={**exact, "idempotency_key": "http-approve"}, headers=self.headers(reviewer))
        self.assertEqual(approved.status_code, 200, approved.text)
        author = self.select("dev-operator-a", reviewer)
        posted = self.client.post(endpoint + "/post", json={**exact, "idempotency_key": "http-post"}, headers=self.headers(author))
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertEqual(posted.json()["status"], "POSTED")
        again = self.client.post(endpoint + "/post", json={**exact, "idempotency_key": "http-post"}, headers=self.headers(author))
        self.assertEqual(again.json(), posted.json())
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events WHERE event_type='BomRegistered'").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT version FROM ledger_state WHERE company_id='DEMO-A'").fetchone()["version"], 2)


if __name__ == "__main__":
    unittest.main()
