"""HTTP boundaries against isolated PostgreSQL and independent synthetic data.

Projection responses below are an explicit synthetic backend double. These tests
prove the host's version/auth/data contract, not actual RDF/Neo4j acceptance.
The latter has its own projection integration checks.
"""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql

from dev.preview_server import FIXTURE_FILES
from service.contracts import canonical_bytes
from service.db import Database
from service.errors import ServiceError
from service.identity import IdentityService
from service.imports import convert_baseline
from service.ledger import LedgerReader
from service.main import create_app
from service.ontology import OntologyCatalog, SchemaInput
from service.projections import ProjectionError
from test_imports import synthetic_baseline
from test_ontology import ADAPTERS, SHAPES, TBOX


ORIGIN = "http://127.0.0.1:8870"
REPO = Path(__file__).resolve().parents[1]


class SyntheticProjectionReader:
    """Explicit host-test double; never used by the application runner."""

    def __init__(self):
        self.calls = 0

    def read_projection(self, projection):
        self.calls += 1
        return deepcopy(projection["graph"])


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class ServiceApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Database(os.environ["TEST_DATABASE_URL"])
        cls.temp = tempfile.TemporaryDirectory()
        cls.private = Path(cls.temp.name)
        rows = []
        for name, content in (("tbox.ttl", TBOX), ("shapes.ttl", SHAPES),
                              ("predicate_adapters.json", json.dumps(ADAPTERS).encode())):
            (cls.private / name).write_bytes(content)
            rows.append({"path": name, "slot": "synthetic", "bytes": len(content), "sha256": sha256(content).hexdigest()})
        (cls.private / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": rows}))
        specs = [SchemaInput(name, role, "synthetic-v1") for name, role in (
            ("tbox.ttl", "tbox"), ("shapes.ttl", "shapes"), ("predicate_adapters.json", "adapters"))]
        cls.catalog = OntologyCatalog.load(cls.private, specs)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.schema = "test_api_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.db = Database(os.environ["TEST_DATABASE_URL"], schema=self.schema)
        self.db.migrate()
        self.identity = IdentityService(self.db, enabled=True, app_env="test")
        self.identity.provision_dev_accounts()
        self.ledger = LedgerReader(self.db, self.identity)
        initial = convert_baseline(synthetic_baseline(), company_id="DEMO-A", profile_id="synthetic-api",
                                   run_id="INITIAL", imported_at="2025-02-02T12:00:00+00:00", source_sha256="a" * 64)
        initial["metadata"]["source_manifest_sha256"] = sha256(b"independent-api-test-manifest").hexdigest()
        self.ledger.install_initial(initial, local_development=True)
        self.projector = SyntheticProjectionReader()
        self.app = create_app(self.db, private_root=str(self.private), origin=ORIGIN,
                              dev_mode=True, catalog=self.catalog, projector=self.projector)
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(self.client.close)

    def drop_schema(self):
        with self.base.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def headers(self, session=None):
        result = {"Origin": ORIGIN, "X-Requested-With": "MK-S2", "Content-Type": "application/json"}
        if session:
            result.update({"X-CSRF-Token": session["csrf_token"],
                           "X-Identity-Revision": session["principal"]["identity_revision"]})
        return result

    def select(self, profile="dev-operator-a", session=None):
        result = self.client.post("/api/mks2/session", json={"profile_id": profile}, headers=self.headers(session))
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code"})
        if code:
            self.assertEqual(payload["error"]["code"], code)
        self.assertNotIn(str(self.private), response.text)
        self.assertNotIn(os.environ["TEST_DATABASE_URL"], response.text)

    def mark_projection_ready(self):
        """Explicit test double only: no claim of Neo4j or SHACL execution."""
        actor = self.identity.login("dev-operator-a").principal
        payload = self.ledger.read(actor)
        with self.db.connect() as conn:
            stored = conn.execute("SELECT payload FROM service_snapshots WHERE company_id='DEMO-A'").fetchone()["payload"]
        projection = {
            "state": "READY", "company_id": "DEMO-A", "run_id": "INITIAL",
            "ledger_version": 1, "projection_version": 1, "ontology_version": self.catalog.version,
            "payload_sha256": sha256(canonical_bytes(stored)).hexdigest(),
            "validation": {"state": "CONFORMS", "test_double": True},
            "neo4j": {"state": "READY", "test_double": True},
            "graph": {"complete": True, "node_count": len(payload["entities"]),
                      "edge_count": len(payload["relationships"]), "nodes": deepcopy(payload["entities"]),
                      "edges": deepcopy(payload["relationships"])},
        }
        self.ledger.save_projection("DEMO-A", "INITIAL", 1, projection, "<urn:test:s> <urn:test:p> <urn:test:o> .")

    def test_health_is_live_db_status_without_credentials(self):
        result = self.client.get("/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["database"]["database"], "ready")
        self.assertNotIn(os.environ["TEST_DATABASE_URL"], result.text)
        with patch.object(self.db, "health", side_effect=RuntimeError("PRIVATE_DIAGNOSTIC_SENTINEL")):
            broken = self.client.get("/health")
        self.error(broken, 503, "SERVICE_UNAVAILABLE")
        self.assertNotIn("PRIVATE_DIAGNOSTIC_SENTINEL", broken.text)

    def test_observability_health_and_shutdown_failures_are_isolated(self):
        from service.agent_config import AgentSettings

        summary = {"mode": "fake", "live_calls": 0, "fake_calls": 0}
        for close_failure in (False, True):
            with self.subTest(close_failure=close_failure):
                observer = Mock(exported=3, failed=0, invalid=0)
                observer.langsmith_status.return_value = {
                    "enabled": True, "content_policy": "metadata_only",
                    "states": {"exported": 3, "failed_or_unknown": 1},
                }
                if close_failure:
                    observer.close.side_effect = RuntimeError("PRIVATE_OBSERVATION_FAILURE")
                with patch("service.agent_host.build_agent_host", return_value=(object(), lambda: summary, observer)):
                    app = create_app(self.db, private_root=str(self.private), origin=ORIGIN,
                        dev_mode=True, catalog=self.catalog, projector=self.projector,
                        agent_settings=AgentSettings())
                with TestClient(app, base_url=ORIGIN) as client:
                    response = client.get("/health")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["observability"]["langsmith"], observer.langsmith_status.return_value)
                    self.assertEqual(response.json()["model_calls"], 0)
                    self.assertNotIn("PRIVATE_OBSERVATION_FAILURE", response.text)
                    observer.close.assert_not_called()
                observer.close.assert_called_once_with()
                self.assertEqual(self.client.get("/health").status_code, 200)

    def test_retrieval_evidence_is_authorized_versioned_read_without_mutation(self):
        session = self.select()
        self.mark_projection_ready()
        before = self.client.get("/api/mks2/snapshot").json()
        query = before["entities"][0]["display_id"]
        body = {"ledger_version": 1, "query": query, "profile": "R0"}
        response = self.client.post("/api/mks2/retrieval/search", json=body, headers=self.headers(session))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "HIT")
        evidence = {"ledger_version": 1, "evidence_id": result["hits"][0]["evidence_id"],
                    "corpus_version": result["meta"]["corpus_version"]}
        response = self.client.post("/api/mks2/retrieval/evidence", json=evidence, headers=self.headers(session))
        self.assertEqual(response.status_code, 200, response.text)
        bad = self.client.post("/api/mks2/retrieval/search", json={**body, "ledger_version": 2}, headers=self.headers(session))
        self.assertEqual(bad.status_code, 404)
        missing_csrf = self.client.post("/api/mks2/retrieval/search", json=body, headers=self.headers())
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(self.client.get("/api/mks2/snapshot").json(), before)

    def test_product_requires_authorized_bom_and_procurement_read_does_not_create(self):
        session = self.select()
        self.mark_projection_ready()
        before = self.client.get("/api/mks2/snapshot").json()
        body = {"ledger_version": 1, "bom_id": "unregistered", "bom_version": 1, "payload_hash": "a" * 64}
        response = self.client.post("/api/mks2/product/review", json=body, headers=self.headers(session))
        self.error(response, 404, "BOM_NOT_FOUND")
        response = self.client.post("/api/mks2/procurement/options", json={"ledger_version": 1}, headers=self.headers(session))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["quotes"], [])
        self.assertEqual(response.json()["ledger_mutations"], 0)
        self.assertEqual(self.client.get("/api/mks2/procurement/documents").json()["documents"], [])
        self.assertEqual(self.client.get("/api/mks2/snapshot").json(), before)

    def test_service_origin_requires_explicit_loopback_without_credentials(self):
        for origin in ("https://127.0.0.1:8870", "http://example.invalid:8870", "http://127.0.0.1",
                       "http://user:secret@127.0.0.1:8870", "http://127.0.0.1:8870/path"):
            with self.subTest(origin=origin), self.assertRaises(ServiceError) as error:
                create_app(self.db, private_root=str(self.private), origin=origin,
                           dev_mode=True, catalog=self.catalog)
            self.assertEqual(error.exception.code, "LOCAL_ORIGIN_REQUIRED")

    def test_unselected_cannot_read_business_or_ontology(self):
        session = self.client.get("/api/mks2/session")
        self.assertEqual(session.status_code, 200)
        self.assertFalse(session.json()["authenticated"])
        for path in ("/api/mks2/snapshot", "/api/mks2/ontology", "/api/mks2/events", "/api/mks2/documents"):
            with self.subTest(path=path):
                self.error(self.client.get(path), 401, "IDENTITY_REQUIRED")

    def test_origin_host_fetch_site_and_non_json_boundary(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.error(self.client.get("/health", headers=headers), 403, "LOCAL_ORIGIN_REQUIRED")
        body = {"profile_id": "dev-operator-a"}
        for headers in ({}, {"Origin": ORIGIN}, {"X-Requested-With": "MK-S2"},
                        {"Origin": ORIGIN, "X-Requested-With": "MK-S2", "Content-Type": "text/plain"}):
            self.error(self.client.post("/api/mks2/session", json=body, headers=headers), 403, "SAME_ORIGIN_JSON_REQUIRED")
        self.error(self.client.options("/api/mks2/session", headers=self.headers()), 405, "METHOD_NOT_ALLOWED")

    def test_json_validation_never_echoes_input_or_accepts_identity_fields(self):
        malicious = {"profile_id": "dev-operator-a", "role": "PRIVATE_ROLE_SENTINEL"}
        result = self.client.post("/api/mks2/session", json=malicious, headers=self.headers())
        self.error(result, 422, "INVALID_REQUEST")
        self.assertNotIn("PRIVATE_ROLE_SENTINEL", result.text)
        invalid = self.client.post("/api/mks2/session", content=b'{"PRIVATE_BODY_SENTINEL":', headers=self.headers())
        self.error(invalid, 422, "INVALID_REQUEST")
        self.assertNotIn("PRIVATE_BODY_SENTINEL", invalid.text)

    def test_declared_and_actual_body_sizes_are_enforced(self):
        oversized = json.dumps({"profile_id": "x" * 140000})
        self.error(self.client.post("/api/mks2/session", content=oversized, headers=self.headers()), 413,
                   "REQUEST_SIZE_REJECTED")
        lying = {**self.headers(), "Content-Length": "2"}
        self.error(self.client.post("/api/mks2/session", content=oversized, headers=lying), 413,
                   "REQUEST_SIZE_REJECTED")

    def test_cookie_is_httponly_strict_scoped_and_reload_recovers_csrf(self):
        result = self.client.post("/api/mks2/session", json={"profile_id": "dev-operator-a"}, headers=self.headers())
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertIn("Path=/", cookie)
        session = self.client.get("/api/mks2/session").json()
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["csrf_token"], result.json()["csrf_token"])
        self.assertNotIn("token_hash", result.text)

    def test_session_end_requires_csrf_and_revokes_cookie_on_server(self):
        session = self.select()
        raw_token = self.client.cookies.get("onto_session")
        denied = self.client.request("DELETE", "/api/mks2/session", json={}, headers=self.headers())
        self.error(denied, 403, "CSRF_REJECTED")
        self.assertTrue(self.client.get("/api/mks2/session").json()["authenticated"])
        ended = self.client.request("DELETE", "/api/mks2/session", json={}, headers=self.headers(session))
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertIn("Max-Age=0", ended.headers["set-cookie"])
        self.assertIn("Path=/", ended.headers["set-cookie"])
        self.assertFalse(self.client.get("/api/mks2/session").json()["authenticated"])
        # Replaying a copied old cookie after logout cannot reopen the session.
        self.error(self.client.get("/api/mks2/snapshot", headers={"Cookie": "onto_session=" + raw_token}),
                   401, "IDENTITY_REQUIRED")
        with self.db.connect() as conn:
            events = conn.execute("SELECT event_type FROM identity_audit WHERE actor_id='actor-dev-a' ORDER BY recorded_at").fetchall()
            self.assertEqual([row["event_type"] for row in events], ["SESSION_SELECTED", "SESSION_ENDED"])

    def test_snapshot_uses_server_ledger_and_never_expected_fixture(self):
        self.select()
        self.error(self.client.get("/api/mks2/snapshot"), 409, "PROJECTION_NOT_READY")
        self.mark_projection_ready()
        result = self.client.get("/api/mks2/snapshot")
        self.assertEqual(result.status_code, 200, result.text)
        view = result.json()
        self.assertEqual(view["meta"]["ledger_version"], 1)
        self.assertEqual(view["meta"]["source_ledger_version"], 7)
        self.assertEqual(view["summary"]["promise_remaining_qty"], 17)
        self.assertEqual(view["summary"]["production_remaining_qty"], 11)
        self.assertEqual(view["summary"]["available_qty"], 12)
        self.assertEqual(view["meta"]["projection_state"], "READY")
        self.assertNotIn("EXPECTED_SENTINEL", result.text)
        self.assertEqual(len(view["graph"]["data"]["nodes"]), len(view["entities"]))
        self.assertGreater(self.projector.calls, 0)

    def test_full_ontology_catalog_not_scenario_pruned(self):
        self.select()
        result = self.client.get("/api/mks2/ontology")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["counts"]["classes"], 3)
        self.assertIn("UnseenFutureClass", result.text)
        self.assertEqual(len(result.json()["tbox_triples"]), len(self.catalog.export()["tbox_triples"]))

    def test_query_requires_csrf_identity_revision_and_exact_ready_snapshot(self):
        session = self.select()
        query = {"question": "현재 납품 이행 현황", "ledger_version": 1}
        self.error(self.client.post("/api/mks2/query", json=query, headers=self.headers()), 403, "CSRF_REJECTED")
        stale = {**self.headers(session), "X-Identity-Revision": "stale"}
        self.error(self.client.post("/api/mks2/query", json=query, headers=stale), 409, "IDENTITY_CHANGED")
        self.error(self.client.post("/api/mks2/query", json=query, headers=self.headers(session)), 409, "PROJECTION_NOT_READY")
        self.mark_projection_ready()
        success = self.client.post("/api/mks2/query", json=query, headers=self.headers(session))
        self.assertEqual(success.status_code, 200, success.text)
        self.assertEqual(success.json()["model_calls"], 0)
        self.assertEqual(success.json()["execution_kind"], "deterministic_domain_query")
        self.assertIn("17", success.json()["answer"])
        wrong = self.client.post("/api/mks2/query", json={**query, "ledger_version": 2}, headers=self.headers(session))
        self.assertIn(wrong.status_code, (404, 409))
        with self.db.connect() as conn:
            conn.execute("UPDATE ledger_state SET version=2 WHERE company_id='DEMO-A'")
        self.error(self.client.post("/api/mks2/query", json=query, headers=self.headers(session)), 409, "SNAPSHOT_CHANGED")

    def test_switch_company_and_old_browser_revision_do_not_leak(self):
        original = self.select()
        self.mark_projection_ready()
        self.assertEqual(self.client.get("/api/mks2/snapshot").status_code, 200)
        other = self.select("dev-other-g", session=original)
        self.assertEqual(other["principal"]["company_id"], "DEMO-B")
        old_revision = {"X-Identity-Revision": original["principal"]["identity_revision"]}
        new_revision = {"X-Identity-Revision": other["principal"]["identity_revision"]}
        for route in ("/api/mks2/snapshot", "/api/mks2/ontology", "/api/mks2/events", "/api/mks2/documents"):
            self.error(self.client.get(route, headers=old_revision), 409, "IDENTITY_CHANGED")
        ontology = self.client.get("/api/mks2/ontology", headers=new_revision)
        self.assertEqual(ontology.status_code, 200)
        self.assertEqual(ontology.json()["meta"]["identity_revision"], other["principal"]["identity_revision"])
        self.assertEqual(ontology.json()["meta"]["company_id"], "DEMO-B")
        denied = self.client.get("/api/mks2/snapshot")
        self.error(denied, 404, "LEDGER_NOT_INITIALIZED")
        self.assertNotIn("delivery-a", denied.text)
        stale = self.client.post("/api/mks2/query", json={"question": "납품", "ledger_version": 1}, headers=self.headers(original))
        self.assertIn(stale.status_code, (403, 409))

    def test_natural_language_delivery_and_what_if_are_read_only(self):
        session = self.select()
        self.mark_projection_ready()
        before = self.client.get("/api/mks2/snapshot").json()
        context = next(node["id"] for node in before["entities"] if node["type"] == "DeliveryRound")
        for question in ("남은 17대 납기 안에 도착 가능해?", "양품 19대가 목표면 수율 50%로 몇 대 투입해?"):
            result = self.client.post("/api/mks2/query", json={"question": question, "ledger_version": 1,
                                    "context_entity_id": context}, headers=self.headers(session))
            self.assertEqual(result.status_code, 200, result.text)
            data = result.json()
            self.assertIn(data["status"], ("PARTIAL", "ANSWERED"))
            self.assertTrue(data["sections"])
            self.assertTrue(data["execution_id"])
            self.assertEqual(data["ledger_mutations"], 0)
            self.assertEqual(data["meta"]["snapshot_id"], before["meta"]["snapshot_id"])
        after = self.client.get("/api/mks2/snapshot").json()
        self.assertEqual(before, after)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"], 0)

    def test_query_selection_is_checked_against_the_authorized_snapshot(self):
        session = self.select()
        self.mark_projection_ready()
        response = self.client.post("/api/mks2/query", json={"question": "남은 물량 알려줘", "ledger_version": 1,
                                    "context_entity_id": "another-company-private-id"}, headers=self.headers(session))
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(result["evidence_refs"], [])
        self.assertNotIn("another-company-private-id", response.text)

    def test_projection_backend_failure_never_uses_stored_graph_as_fallback(self):
        session = self.select()
        self.mark_projection_ready()
        with patch.object(self.projector, "read_projection", side_effect=ProjectionError("PRIVATE_DRIVER_SENTINEL")):
            snapshot = self.client.get("/api/mks2/snapshot")
            query = self.client.post("/api/mks2/query", json={"question": "납품 현황", "ledger_version": 1}, headers=self.headers(session))
        for response in (snapshot, query):
            self.error(response, 503, "PROJECTION_READ_UNAVAILABLE")
            self.assertNotIn("PRIVATE_DRIVER_SENTINEL", response.text)
            self.assertNotIn("delivery-a", response.text)

    def test_current_policy_revocation_is_rechecked_and_reselection_recovers(self):
        self.select()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET policy_version=2 WHERE actor_id='actor-dev-a'")
        self.error(self.client.get("/api/mks2/snapshot"), 409, "IDENTITY_CHANGED")
        self.assertFalse(self.client.get("/api/mks2/session").json()["authenticated"])
        recovered = self.client.post("/api/mks2/session", json={"profile_id": "dev-reviewer-b"}, headers=self.headers())
        self.assertEqual(recovered.status_code, 200, recovered.text)

    def test_business_write_does_not_trust_extra_actor_and_viewer_cannot_create(self):
        session = self.select("dev-viewer-f")
        content = {"kind": "evidence_note", "payload": {"note": "synthetic"}, "evidence_refs": ["urn:test:evidence"],
                   "source_version": "synthetic-v1", "ontology_version": "synthetic-v1", "rule_version": "test-v1"}
        body = {"content": content, "idempotency_key": "api-example"}
        self.error(self.client.post("/api/mks2/documents", json=body, headers=self.headers(session)), 403, "PERMISSION_DENIED")
        self.error(self.client.post("/api/mks2/documents", json={**body, "actor_id": "actor-dev-b"}, headers=self.headers(session)), 422, "INVALID_REQUEST")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"], 0)

    def test_static_routes_preserve_both_tabs_and_reject_private_paths(self):
        routes = {"/": "shell.html", "/current/": "index.html", "/app.js": "app.js", "/mks2/": "mks2/index.html", "/mks2/graph-layout.js": "mks2/graph-layout.js",
                  "/mks2/twin.js": "mks2/twin.js", "/mks2/twin.css": "mks2/twin.css",
                  "/mks2/twin2.js": "mks2/twin2.js", "/mks2/twin2.css": "mks2/twin2.css",
                  "/mks2/material_watch.js": "mks2/material_watch.js",
                  "/mks2/vendor/three/three.module.min.js": "mks2/vendor/three/three.module.min.js",
                  "/mks2/vendor/three/OrbitControls.js": "mks2/vendor/three/OrbitControls.js",
                  "/mks2/vendor/three/CSS2DRenderer.js": "mks2/vendor/three/CSS2DRenderer.js"}
        for route, filename in routes.items():
            result = self.client.get(route)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.content, (REPO / "web" / filename).read_bytes())
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'self'", result.headers["content-security-policy"])
        for route in ("/.env", "/AGENTS.md", "/SOURCE_MANIFEST.json", "/private_inputs/", "/mks2/%2e%2e/app.js", "/app.js?path=.env",
                      "/mks2/vendor/three/", "/mks2/vendor/three/unlisted.js", "/mks2/twin.js?path=.env"):
            with self.subTest(route=route):
                self.error(self.client.get(route), 404, "NOT_FOUND")

    def test_reference_fixture_api_disabled_and_debug_api_not_exposed(self):
        self.error(self.client.get("/api/preview"), 503, "REFERENCE_PREVIEW_DISABLED")
        for path in ("/docs", "/openapi.json", "/api/mks2/import", "/api/mks2/reset"):
            self.error(self.client.get(path), 404, "NOT_FOUND")

    def test_explicit_preserved_reference_mode_remains_distinct_from_service(self):
        root = self.private / "mk-s2" / "fixtures"
        root.mkdir(parents=True, exist_ok=True)
        for key, filename in FIXTURE_FILES.items():
            (root / filename).write_text(json.dumps({"synthetic_reference_only": key}))
        app = create_app(self.db, private_root=str(self.private), origin=ORIGIN, dev_mode=True,
                         reference_preview=True, catalog=self.catalog, projector=self.projector)
        with TestClient(app, base_url=ORIGIN) as client:
            result = client.get("/api/preview")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["mode"], "reference-preview")
            self.assertIs(result.json()["serviceConnected"], False)
            self.assertEqual(set(result.json()["bundle"]), set(FIXTURE_FILES))
            self.error(client.get("/api/mks2/snapshot"), 401, "IDENTITY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
