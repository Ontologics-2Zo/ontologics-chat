"""Durable workflow tests against disposable PostgreSQL schemas and synthetic tools.

No operating ledger is modified. Subprocess crash tests use the actual PostgreSQL
checkpointer and a separate interpreter, not an in-memory persistence double.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from service.agent_contracts import AgentRequest, WORKFLOW_VERSION
from service.agent_workflow import AgentWorkflow
from service.contracts import Contract
from service.db import Database
from service.errors import ServiceError
from service.gateway import ToolContext, ToolGateway, ToolOutput, ToolSpec
from service.identity import IdentityService


class SyntheticQuery(Contract):
    question: str
    ledger_version: int
    context_entity_id: str | None = None


def build_host(db, *, observer=None, handler_hook=None, route_model=None, max_tool_calls=8, selected_version=None):
    identity = IdentityService(db, enabled=True, app_env="test")
    gateway = ToolGateway(identity.authorize)
    selected_version = selected_version or [1]

    def context(principal, execution_id):
        number = selected_version[0]
        return ToolContext(execution_id=execution_id, request_id=str(uuid4()),
                           context_revision=principal.identity_revision, company_id=principal.company_id,
                           read_snapshot_id=f"synthetic-snapshot-{number}", ledger_version=number,
                           projection_version=number, ontology_version="synthetic-ontology-v1",
                           rule_version="synthetic-rules-v1", as_of=datetime(2001, 1, 1, tzinfo=timezone.utc),
                           known_as_of=datetime(2001, 1, 1, tzinfo=timezone.utc))

    def query(principal, arguments, ctx):
        if handler_hook:
            handler_hook()
        return ToolOutput(snapshot=ctx, data={"answer": "독립 합성 조회 결과", "status": "PARTIAL",
            "model_calls": 0, "ledger_mutations": 0,
            "meta": {"company_id": ctx.company_id, "snapshot_id": ctx.read_snapshot_id,
                     "ledger_version": ctx.ledger_version, "projection_version": ctx.projection_version},
            "sections": [{"id": "unknown", "title": "합성 검증", "origin": "unknown", "items": []}],
            "graph_path": [], "evidence_node_ids": [], "evidence_edge_ids": []})

    gateway.register(ToolSpec("ledger.query", "query.read", False, SyntheticQuery, query))
    return AgentWorkflow(db, identity, gateway, context, observer=observer, route_model=route_model,
                         max_tool_calls=max_tool_calls), identity


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class AgentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.base = Database(os.environ["TEST_DATABASE_URL"])
        self.schema = "test_agent_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(os.environ["TEST_DATABASE_URL"], schema=self.schema)
        self.db.migrate()
        self.workflow, self.identity = build_host(self.db)
        self.identity.provision_dev_accounts()
        self.session = self.identity.login("dev-operator-a")
        self.actor = self.session.principal
        self.workflow.setup()

    def cleanup_schema(self):
        with self.base.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def request(self, question="남은 약속은 얼마나?", capability=None, key=None):
        return AgentRequest(question=question, ledger_version=1, capability=capability,
                            idempotency_key=key or str(uuid4()))

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_two_actual_subgraphs_explicit_route_has_no_model_call(self):
        called = []
        host, _ = build_host(self.db, route_model=lambda *args: called.append(args))
        for capability, question in [("fulfillment_read", "납품 현황"), ("material_analysis", "가용 재고")]:
            created = host.create(self.actor, self.request(question, capability))
            result = host.run(self.actor, created["execution_id"])
            self.assertEqual(result["status"], "COMPLETED", result["error_code"])
            self.assertEqual(result["capability"], capability)
            self.assertEqual(result["tool_calls"], 1)
            self.assertEqual(result["result"]["status"], "PARTIAL")
            self.assertEqual(result["ledger_mutations"], 0)
            self.assertTrue(any(e["stage"] == capability and e["status"] == "COMPLETED" for e in result["events"]))
        self.assertEqual(called, [])

    def test_route_model_receives_only_bounded_features_and_rejects_invalid_route(self):
        seen = []
        def route(actor, ctx, segment, features, budget):
            seen.append(features)
            return "material_analysis"
        host, _ = build_host(self.db, route_model=route)
        result = host.run(self.actor, host.create(self.actor, self.request("자재 소요와 부족"))["execution_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(seen, [("materials",)])
        bad, _ = build_host(self.db, route_model=lambda *args: "bom.post")
        result = bad.run(self.actor, bad.create(self.actor, self.request("납품 현황"))["execution_id"])
        self.assertEqual(result["error_code"], "AGENT_ROUTE_FORBIDDEN")
        self.assertEqual(result["tool_calls"], 0)

    def test_unknown_and_write_questions_interrupt_without_model_or_tool(self):
        called = []
        host, _ = build_host(self.db, route_model=lambda *args: called.append(args))
        for question in ("오늘 날씨 어때?", "재고를 삭제해줘", "수율 0%로 생산해?"):
            result = host.run(self.actor, host.create(self.actor, self.request(question))["execution_id"])
            self.assertEqual(result["status"], "WAITING")
            self.assertEqual(result["tool_calls"], 0)
            self.assertEqual(result["interrupt"]["kind"], "clarification")
            self.assertIsNone(result["result"])
        self.assertEqual(called, [])

    def test_interrupt_resume_uses_fresh_session_and_same_execution(self):
        pending = self.workflow.run(self.actor, self.workflow.create(self.actor, self.request("무슨 질문?"))["execution_id"])
        second = self.identity.login("dev-operator-a").principal
        restarted, _ = build_host(self.db)
        payload = {"expected_revision": pending["revision"], "idempotency_key": "resume-one",
                   "capability": "material_analysis", "question": "가용 재고 알려줘"}
        result = restarted.resume(second, pending["execution_id"], payload)
        self.assertEqual(result["execution_id"], pending["execution_id"])
        self.assertEqual(result["status"], "COMPLETED", result["error_code"])
        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["result"]["meta"]["identity_revision"], second.identity_revision)
        self.assertEqual(restarted.resume(second, pending["execution_id"], payload)["revision"], result["revision"])
        self.assert_code("AGENT_IDEMPOTENCY_CONFLICT", lambda: restarted.resume(second, pending["execution_id"], {**payload, "question": "납품 현황"}))

    def test_dedupe_strict_revision_and_scoped_status_events(self):
        request = self.request(key="same-input")
        initial = self.workflow.create(self.actor, request)
        self.assertEqual(self.workflow.create(self.actor, request)["execution_id"], initial["execution_id"])
        self.assert_code("AGENT_IDEMPOTENCY_CONFLICT", lambda: self.workflow.create(self.actor, self.request("재고", key="same-input")))
        self.assert_code("AGENT_REVISION_CHANGED", lambda: self.workflow.run(self.actor, initial["execution_id"], expected_revision=99))
        for profile in ("dev-reviewer-b", "dev-other-g"):
            actor = self.identity.login(profile).principal
            self.assert_code("AGENT_EXECUTION_NOT_FOUND", lambda: self.workflow.status(actor, initial["execution_id"]))
            self.assert_code("AGENT_EXECUTION_NOT_FOUND", lambda: self.workflow.events(actor, initial["execution_id"]))
        events = self.workflow.events(self.actor, initial["execution_id"])
        self.assertEqual(self.workflow.events(self.actor, initial["execution_id"], events[-1]["sequence"]), [])

    def test_resume_rejects_changed_snapshot_permissions_policy_and_workflow(self):
        pending = self.workflow.run(self.actor, self.workflow.create(self.actor, self.request("무슨 질문?"))["execution_id"])
        payload = {"expected_revision": pending["revision"], "idempotency_key": "resume",
                   "capability": "fulfillment_read", "question": "납품 현황"}
        changed, _ = build_host(self.db, selected_version=[2])
        self.assert_code("AGENT_SNAPSHOT_CHANGED", lambda: changed.resume(self.actor, pending["execution_id"], payload))
        with self.db.connect() as conn:
            conn.execute("UPDATE agent_executions SET workflow_version='incompatible' WHERE execution_id=%s", (pending["execution_id"],))
        self.assert_code("AGENT_WORKFLOW_CHANGED", lambda: self.workflow.resume(self.actor, pending["execution_id"], payload))
        with self.db.connect() as conn:
            conn.execute("UPDATE agent_executions SET workflow_version=%s WHERE execution_id=%s", (WORKFLOW_VERSION, pending["execution_id"]))
            conn.execute("UPDATE identity_accounts SET policy_version=policy_version+1 WHERE actor_id=%s", (self.actor.actor_id,))
        fresh = self.identity.login("dev-operator-a").principal
        self.assert_code("AGENT_POLICY_CHANGED", lambda: self.workflow.resume(fresh, pending["execution_id"], payload))
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active=false WHERE actor_id=%s", (self.actor.actor_id,))
        self.assert_code("IDENTITY_INACTIVE", lambda: self.workflow.resume(fresh, pending["execution_id"], payload))

    def test_concurrent_run_is_exclusive_and_cancel_preserves_consumed_call(self):
        entered, release = Event(), Event()
        def pause():
            entered.set()
            if not release.wait(10):
                raise RuntimeError("synthetic timeout")
        host, _ = build_host(self.db, handler_hook=pause)
        created = host.create(self.actor, self.request())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(host.run, self.actor, created["execution_id"])
            try:
                self.assertTrue(entered.wait(10))
                self.assert_code("AGENT_EXECUTION_BUSY", lambda: self.workflow.run(self.actor, created["execution_id"]))
                current = self.workflow.status(self.actor, created["execution_id"])
                self.workflow.cancel(self.actor, created["execution_id"], current["revision"])
            finally:
                release.set()
            result = future.result(timeout=10)
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(result["tool_calls"], 1)

    def test_process_kill_after_artifact_commit_recovers_without_reexecuting_tool(self):
        created = self.workflow.create(self.actor, self.request(capability="fulfillment_read"))
        code = '''
import os
from tests.test_agent_workflow import build_host
from service.db import Database
def die(event):
    if event.get('name') == 'fulfillment_read' and event.get('status') == 'completed':
        os._exit(86)
db=Database(os.environ['TEST_DATABASE_URL'],schema=os.environ['AGENT_TEST_SCHEMA'])
host,identity=build_host(db,observer=die)
actor=identity.authenticate(os.environ['AGENT_TEST_TOKEN'])
host.run(actor,os.environ['AGENT_TEST_EXECUTION'])
'''
        environment = {**os.environ, "AGENT_TEST_SCHEMA": self.schema, "AGENT_TEST_TOKEN": self.session.token,
                       "AGENT_TEST_EXECUTION": created["execution_id"]}
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                               env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        self.assertEqual(child.returncode, 86)
        before = self.workflow.status(self.actor, created["execution_id"])
        self.assertEqual(before["tool_calls"], 1)
        restarted, _ = build_host(self.db)
        result = restarted.run(self.actor, created["execution_id"])
        self.assertEqual(result["status"], "COMPLETED", result["error_code"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["segment_count"], 2)

    def test_durable_budget_is_not_restored_from_checkpoint(self):
        pending = self.workflow.run(self.actor, self.workflow.create(self.actor, self.request("무슨 질문?"))["execution_id"])
        with self.db.connect() as conn:
            conn.execute("UPDATE agent_executions SET tool_calls=max_tool_calls WHERE execution_id=%s", (pending["execution_id"],))
        restarted, _ = build_host(self.db)
        result = restarted.resume(self.actor, pending["execution_id"], {
            "expected_revision": pending["revision"], "idempotency_key": "budget-resume",
            "capability": "material_analysis", "question": "가용 재고"})
        self.assertEqual(result["error_code"], "TOOL_BUDGET_EXHAUSTED")
        self.assertEqual(result["tool_calls"], result["max_tool_calls"])

    def test_process_kill_at_interrupt_recovers_wait_and_resumes_with_fresh_session(self):
        created = self.workflow.create(self.actor, self.request("무슨 질문?"))
        code = '''
import os
from tests.test_agent_workflow import build_host
from service.db import Database
db=Database(os.environ['TEST_DATABASE_URL'],schema=os.environ['AGENT_TEST_SCHEMA'])
host,identity=build_host(db)
actor=identity.authenticate(os.environ['AGENT_TEST_TOKEN'])
original=host.store.finish
def die(*args,**kwargs):
    if args[3]=='WAITING':
        os._exit(87)
    return original(*args,**kwargs)
host.store.finish=die
host.run(actor,os.environ['AGENT_TEST_EXECUTION'])
'''
        environment = {**os.environ, "AGENT_TEST_SCHEMA": self.schema, "AGENT_TEST_TOKEN": self.session.token,
                       "AGENT_TEST_EXECUTION": created["execution_id"]}
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                               env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        self.assertEqual(child.returncode, 87)
        fresh = self.identity.login("dev-operator-a").principal
        restarted, _ = build_host(self.db)
        waiting = restarted.run(fresh, created["execution_id"])
        self.assertEqual(waiting["status"], "WAITING")
        self.assertEqual(waiting["tool_calls"], 0)
        result = restarted.resume(fresh, created["execution_id"], {
            "expected_revision": waiting["revision"], "idempotency_key": "process-resume",
            "capability": "material_analysis", "question": "현재 가용 재고"})
        self.assertEqual(result["status"], "COMPLETED", result["error_code"])
        self.assertEqual(result["execution_id"], created["execution_id"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["segment_count"], 3)

    def test_cached_result_reauthorizes_fresh_session_without_mutating_artifact(self):
        completed = self.workflow.run(self.actor, self.workflow.create(self.actor, self.request())["execution_id"])
        row = self.workflow.store.get(completed["execution_id"])
        before = self.workflow.store.result(completed["execution_id"], row["result_ref"])
        fresh = self.identity.login("dev-operator-a").principal
        fetched = self.workflow.status(fresh, completed["execution_id"])
        self.assertEqual(fetched["result"]["meta"]["identity_revision"], fresh.identity_revision)
        self.assertEqual(fetched["result"]["meta"]["snapshot_id"], completed["result"]["meta"]["snapshot_id"])
        self.assertEqual(self.workflow.store.result(completed["execution_id"], row["result_ref"]), before)

    def test_workflow_metadata_observer_accepts_parent_child_spans(self):
        from service.observability import MetadataObserver
        observer = MetadataObserver()
        self.addCleanup(observer.close)
        host, _ = build_host(self.db, observer=observer)
        result = host.run(self.actor, host.create(self.actor, self.request())["execution_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(observer.invalid, 0)
        self.assertEqual(observer.failed, 0)

    def test_checkpoint_contains_references_not_question_result_or_credentials(self):
        question = "납품 현황 SYNTHETIC_PRIVATE_QUESTION_SENTINEL"
        result = self.workflow.run(self.actor, self.workflow.create(self.actor, self.request(question))["execution_id"])
        with self.db.connect() as conn:
            checkpoints = str(conn.execute("SELECT checkpoint,metadata FROM checkpoints").fetchall())
            blobs = b"".join(bytes(row["blob"]) for row in conn.execute("SELECT blob FROM checkpoint_blobs WHERE blob IS NOT NULL").fetchall())
        for private in (question, self.session.token, "독립 합성 조회 결과"):
            self.assertNotIn(private, checkpoints)
            self.assertNotIn(private.encode(), blobs)
        self.assertEqual(result["status"], "COMPLETED")

    def test_inherited_cloud_tracing_disabled_and_observer_failure_nonfatal(self):
        def broken(_):
            raise RuntimeError("synthetic observer failure")
        host, _ = build_host(self.db, observer=broken)
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": "synthetic"}), \
                patch("langsmith.Client.create_run", side_effect=AssertionError("automatic export forbidden")) as create_run:
            result = host.run(self.actor, host.create(self.actor, self.request(capability="fulfillment_read"))["execution_id"])
        self.assertEqual(result["status"], "COMPLETED")
        create_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
