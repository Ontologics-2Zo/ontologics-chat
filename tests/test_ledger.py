"""Opening-balance and projection persistence boundaries on real PostgreSQL.

Inputs reuse the independently authored synthetic importer fixture. Projection
envelopes here test storage invariants, not SHACL or Neo4j engine acceptance.
"""

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
from psycopg.types.json import Jsonb

from service.contracts import canonical_bytes
from service.db import Database
from service.domain import DomainService
from service.errors import ServiceError
from service.identity import IdentityService
from service.imports import BASELINE_PATH, load_initial
from service.ledger import LedgerReader
from tests.test_imports import synthetic_baseline
from tests.test_domain import content


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is required for actual PostgreSQL tests")
class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["TEST_DATABASE_URL"]
        self.schema = "ledger_test_" + uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.remove_schema)
        self.db = Database(self.dsn, schema=self.schema)
        self.db.migrate()
        self.identity = IdentityService(self.db, enabled=True, app_env="test")
        self.identity.provision_dev_accounts()
        self.actor = self.identity.login("dev-operator-a").principal
        self.other = self.identity.login("dev-other-g").principal
        self.ledger = LedgerReader(self.db, self.identity)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write_source(synthetic_baseline())

    def remove_schema(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def write_source(self, baseline):
        raw = json.dumps(baseline).encode()
        target = self.root / BASELINE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        (self.root / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": [
            {"path": BASELINE_PATH, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
        ]}))

    def payload(self, *, company="DEMO-A", run="INITIAL", profile="test-profile", recorded="2025-02-02T12:00:00+00:00"):
        return load_initial(self.root, company_id=company, run_id=run, profile_id=profile, imported_at=recorded)

    def install(self, **kwargs):
        return self.ledger.install_initial(self.payload(**kwargs), local_development=True)

    def assert_error(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def projection(self, payload, state="READY"):
        return {"company_id": payload["metadata"]["company_id"], "run_id": payload["metadata"]["run_id"],
                "ledger_version": 1, "projection_version": 1, "ontology_version": "independent-test-schema",
                "payload_sha256": sha256(canonical_bytes(payload)).hexdigest(), "state": state,
                "graph": {"nodes": deepcopy(payload["entities"]), "edges": deepcopy(payload["relationships"]),
                          "complete": True, "node_count": len(payload["entities"]), "edge_count": len(payload["relationships"])},
                "validation": {"state": "CONFORMS"}, "neo4j": {"state": "READY"}}

    def test_explicit_local_scope_and_verified_initial_contract_required(self):
        self.assert_error("INITIAL_IMPORT_FORBIDDEN", lambda: self.ledger.install_initial(self.payload()))
        self.assert_error("INITIAL_IMPORT_FORBIDDEN", lambda: self.ledger.install_initial(self.payload(company="OPERATING-A"), local_development=True))
        mutations = [
            lambda p: p["metadata"].update(mode="EXPECTED"),
            lambda p: p["metadata"].pop("source_manifest_sha256"),
            lambda p: p.update(expected={"final_answer": "forbidden"}),
            lambda p: p["entities"][0]["evidence"].update(record_type="completed_event"),
            lambda p: p["entities"][0].update(type="BoardUnit"),
            lambda p: p["relationships"].pop(),
        ]
        for mutate in mutations:
            payload = self.payload()
            mutate(payload)
            self.assert_error("INITIAL_CONTRACT_INVALID", lambda: self.ledger.install_initial(payload, local_development=True))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 0)

    def test_import_retry_preserves_version_and_content_conflicts_are_not_ignored(self):
        self.assertEqual(self.install(), 1)
        self.assertEqual(self.install(recorded="2025-02-03T12:00:00+00:00"), 1)
        self.assert_error("OPENING_IMPORT_CONFLICT", lambda: self.install(profile="different-profile"))
        changed = synthetic_baseline()
        changed["ledger_version"] = 8
        self.write_source(changed)
        self.assert_error("OPENING_IMPORT_CONFLICT", self.install)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM opening_imports").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT version FROM ledger_state").fetchone()["version"], 1)

    def test_requires_empty_company_ledger_and_does_not_silently_merge_runs(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO ledger_state(company_id,version) VALUES ('DEMO-A',9)")
        self.assert_error("OPENING_IMPORT_REQUIRES_EMPTY_LEDGER", self.install)
        self.install(company="DEMO-B")
        self.assert_error("OPENING_IMPORT_REQUIRES_EMPTY_LEDGER", lambda: self.install(company="DEMO-B", run="ANOTHER-RUN"))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT version FROM ledger_state WHERE company_id='DEMO-A'").fetchone()["version"], 9)

    def test_company_scope_historical_snapshot_and_source_server_versions(self):
        self.install()
        read = self.ledger.read(self.actor)
        self.assertEqual(read["metadata"]["server_ledger_version"], 1)
        self.assertEqual(read["metadata"]["source_ledger_version"], 7)
        self.assertIsNone(read["projection"])
        self.assert_error("LEDGER_NOT_INITIALIZED", lambda: self.ledger.read(self.other))
        self.assertEqual(self.ledger.events(self.other), [])
        self.install(company="DEMO-B")
        other = self.ledger.read(self.other)
        self.assertNotEqual(read["metadata"]["snapshot_id"], other["metadata"]["snapshot_id"])
        self.assertFalse({e["id"] for e in read["entities"]} & {e["id"] for e in other["entities"]})
        a_events, b_events = self.ledger.events(self.actor), self.ledger.events(self.other)
        self.assertEqual((len(a_events), len(b_events)), (1, 1))
        self.assertNotEqual(a_events[0]["event_id"], b_events[0]["event_id"])
        self.assertEqual(self.ledger.events(self.actor, after=1), [])
        self.assert_error("LEDGER_NOT_INITIALIZED", lambda: self.ledger.read(self.actor, version=2))
        with self.db.connect() as conn:
            conn.execute("UPDATE ledger_state SET version=2 WHERE company_id='DEMO-A'")
        historical = self.ledger.read(self.actor, version=1)
        self.assertEqual(historical["metadata"]["server_ledger_version"], 1)
        self.assertEqual(historical["metadata"]["current_ledger_version"], 2)

    def test_projection_requires_exact_scope_payload_version_and_complete_graph(self):
        self.install()
        payload = self.ledger.pending()[0]["payload"]
        original = self.projection(payload)
        cases = [
            ("PROJECTION_VERSION_MISMATCH", lambda p: p.update(ledger_version=2)),
            ("PROJECTION_SCOPE_MISMATCH", lambda p: p.update(company_id="DEMO-B")),
            ("PROJECTION_SCOPE_MISMATCH", lambda p: p.update(run_id="OTHER")),
            ("PROJECTION_PAYLOAD_MISMATCH", lambda p: p.update(payload_sha256="0"*64)),
            ("PROJECTION_COVERAGE_MISMATCH", lambda p: p["graph"]["nodes"].pop()),
            ("PROJECTION_COVERAGE_MISMATCH", lambda p: p["graph"].update(complete=False)),
            ("PROJECTION_VALIDATION_REQUIRED", lambda p: p["validation"].update(state="VIOLATIONS")),
            ("PROJECTION_VALIDATION_REQUIRED", lambda p: p["neo4j"].update(state="NOT_READY")),
        ]
        for code, mutate in cases:
            projected = deepcopy(original)
            mutate(projected)
            self.assert_error(code, lambda: self.ledger.save_projection("DEMO-A", "INITIAL", 1, projected, "<urn:a> <urn:p> <urn:b> ."))
        self.assertIsNone(self.ledger.read(self.actor)["projection"])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM outbox").fetchone()["status"], "PENDING")

    def test_failed_projection_does_not_claim_ready_and_ready_is_immutable(self):
        self.install()
        payload = self.ledger.pending()[0]["payload"]
        blocked = self.projection(payload, "NOT_READY")
        blocked["validation"]["state"] = "VIOLATIONS"
        self.ledger.save_projection("DEMO-A", "INITIAL", 1, blocked, "<urn:a> <urn:p> <urn:b> .")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM outbox").fetchone()["status"], "PENDING")
        ready = self.projection(payload)
        self.ledger.save_projection("DEMO-A", "INITIAL", 1, ready, "<urn:a> <urn:p> <urn:b> .")
        self.ledger.save_projection("DEMO-A", "INITIAL", 1, ready, "<urn:a> <urn:p> <urn:b> .")
        changed = deepcopy(ready)
        changed["ontology_version"] = "different-schema"
        self.assert_error("READY_PROJECTION_IMMUTABLE", lambda: self.ledger.save_projection("DEMO-A", "INITIAL", 1, changed, "<urn:a> <urn:p> <urn:b> ."))
        self.assert_error("READY_PROJECTION_IMMUTABLE", lambda: self.ledger.save_projection("DEMO-A", "INITIAL", 1, blocked, "<urn:a> <urn:p> <urn:b> ."))
        self.assertEqual(self.ledger.read(self.actor)["projection"], ready)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM outbox").fetchone()["status"], "PROJECTED")

    def test_database_rejects_null_document_events_without_opening_record_type(self):
        for event_type, payload in (("ARBITRARY_EVENT", {}), ("EvidenceDocumentPosted", {}), ("OPENING_BALANCE_IMPORTED", {})):
            with self.subTest(event_type=event_type), self.assertRaises(psycopg.errors.CheckViolation):
                with self.db.connect() as conn:
                    conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,event_type,payload,actor_id) VALUES (%s,'DEMO-A',1,NULL,%s,%s,'test')", (uuid4(), event_type, Jsonb(payload)))

    def post_note(self):
        service = DomainService(self.db, self.identity)
        reviewer = self.identity.login("dev-reviewer-b").principal
        document = service.create(self.actor, self.actor.company_id, content(), "note-create")
        args = (document["id"], document["version"], document["payload_hash"])
        service.review(reviewer, reviewer.company_id, *args, "note-review")
        service.approve(reviewer, reviewer.company_id, *args, "note-approve")
        return service.post(self.actor, self.actor.company_id, *args, "note-post")

    def test_document_events_advance_exact_snapshot_without_changing_business_facts(self):
        self.install()
        before = self.ledger.read(self.actor)
        posted = self.post_note()
        self.assertEqual(posted["ledger_version"], 2)
        self.assertEqual(self.ledger.read(self.actor)["metadata"]["server_ledger_version"], 1)
        self.assertEqual(self.ledger.advance_documents(), 1)
        self.assertEqual(self.ledger.advance_documents(), 0)
        after = self.ledger.read(self.actor)
        self.assertEqual(after["metadata"]["server_ledger_version"], 2)
        self.assertEqual(after["metadata"]["source_ledger_version"], 7)
        self.assertFalse(after["metadata"]["last_document_event"]["business_facts_changed"])
        self.assertEqual(after["metadata"]["last_document_event"]["event_id"], posted["event_id"])
        for field in ("entities", "relationships", "opening_balance"):
            self.assertEqual(before[field], after[field])
        self.assertIsNone(after["projection"])
        self.assertEqual(self.ledger.read(self.actor, version=1)["metadata"]["server_ledger_version"], 1)

    def test_unsupported_event_rolls_back_all_new_snapshots_for_its_company(self):
        self.install()
        self.post_note()
        with self.db.connect() as conn:
            conn.execute("UPDATE ledger_state SET version=3 WHERE company_id='DEMO-A'")
            conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,event_type,payload,actor_id) VALUES (%s,'DEMO-A',3,NULL,'OPENING_BALANCE_IMPORTED',%s,'test')", (uuid4(), Jsonb({"record_type": "opening_balance"})))
        self.assert_error("UNSUPPORTED_LEDGER_EVENT", self.ledger.advance_documents)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM service_snapshots").fetchone()["n"], 1)

    def test_missing_event_sequence_does_not_silently_mark_snapshot_current(self):
        self.install()
        with self.db.connect() as conn:
            conn.execute("UPDATE ledger_state SET version=2 WHERE company_id='DEMO-A'")
        self.assert_error("LEDGER_EVENT_GAP", self.ledger.advance_documents)
        self.assertEqual(self.ledger.read(self.actor)["metadata"]["server_ledger_version"], 1)


if __name__ == "__main__":
    unittest.main()
