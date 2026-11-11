"""Write agent behind a human approval interrupt, on disposable PostgreSQL schemas (T07/T14)."""

import os
import unittest
from uuid import uuid4

from psycopg import sql

from service.agent_contracts import AgentRequest
from service.agents.procurement_draft import draft_request
from service.contracts import Contract, content_hash
from service.db import Database
from service.errors import ServiceError
from service.gateway import ToolOutput, ToolSpec
from service.procurement_api import DocumentRead
from service.procurement_contracts import ProcurementCreate
from tests.test_agent_workflow import build_host


class Options(Contract):
    ledger_version: int


QUESTION = "견적 A로 구매 요청 초안 만들어줘"


class DraftRequestTests(unittest.TestCase):
    def test_draft_requests_are_recognized_without_a_model(self):
        self.assertEqual(draft_request(QUESTION), {"supplier_id": "SUP-A", "forbidden": False})
        self.assertEqual(draft_request("SUP-C 견적으로 구매요청 작성해줘")["supplier_id"], "SUP-C")
        self.assertIsNone(draft_request("구매 요청 상태 알려줘"))
        self.assertIsNone(draft_request("남은 68대 납기 가능해?"))
        self.assertTrue(draft_request("구매 요청 초안 만들고 발주해")["forbidden"])


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class WriteAgentTests(unittest.TestCase):
    def setUp(self):
        self.base = Database(os.environ["TEST_DATABASE_URL"])
        self.schema = "test_write_agent_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(os.environ["TEST_DATABASE_URL"], schema=self.schema); self.db.migrate()
        self.model_calls, self.writes, self.documents, self.tamper = [], [], {}, {}
        self.version = [1]
        self.workflow, self.identity = build_host(self.db, route_model=lambda *args: self.model_calls.append(args),
                                                  selected_version=self.version)
        self.identity.provision_dev_accounts()
        self.author = self.identity.login("dev-operator-a").principal
        self.reviewer = self.identity.login("dev-reviewer-b").principal
        self.workflow.setup()

        def options(actor, args, ctx):
            quote = lambda supplier, qty, condition: {"quote_id": f"quote-{supplier}", "quote_ref": f"Q-{supplier}", "supplier_id": supplier,
                "item_id": "item-x", "material_basis": "planned", "unit": "EA", "moq": 10, "order_multiple": 10, "expired": False,
                "partial_bom": True, "calculation": {"shortage_qty": "23", "recommended_qty": qty, "quantity_condition": condition,
                "supply_limit_qty": 25 if condition == "NOT_MET" else 40, "total": None, "currency": "USD"}}
            return ToolOutput(snapshot=ctx, data={"quotes": [quote("SUP-A", 30, "MET"), quote("SUP-B", 30, "NOT_MET")],
                "model_calls": 0, "ledger_mutations": 0, "meta": {"company_id": ctx.company_id, "snapshot_id": ctx.read_snapshot_id,
                "ledger_version": ctx.ledger_version, "projection_version": ctx.projection_version}})

        def create(actor, args, ctx):
            # Domain idempotency: the same key returns the same document.
            self.writes.append(args.idempotency_key)
            if args.idempotency_key not in self.documents:
                content = {**args.content.model_dump(mode="json"), "basis_ledger_version": args.expected_ledger_version, "resolved": {}}
                self.documents[args.idempotency_key] = {"id": str(uuid4()), "kind": "PR", "version": 1, "status": "DRAFT",
                    "created_by": actor.actor_id, "content": content, "payload_hash": content_hash(content)}
            return ToolOutput(snapshot=ctx, data=dict(self.documents[args.idempotency_key]))

        def document(actor, args, ctx):
            found = next(doc for doc in self.documents.values() if doc["id"] == args.document_id)
            return ToolOutput(snapshot=ctx, data={**found, "content": {**found["content"], **self.tamper}})

        gateway = self.workflow.gateway
        gateway.register(ToolSpec("procurement.options", "ledger.read", False, Options, options))
        gateway.register(ToolSpec("procurement.create", "document.create", True, ProcurementCreate, create))
        gateway.register(ToolSpec("procurement.document", "document.read", False, DocumentRead, document))

    def cleanup_schema(self):
        with self.base.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def start(self, question=QUESTION, actor=None):
        actor = actor or self.author
        created = self.workflow.create(actor, AgentRequest(question=question, ledger_version=self.version[0], idempotency_key=str(uuid4())))
        return self.workflow.run(actor, created["execution_id"])

    def decide(self, waiting, decision, proposal_hash=None, actor=None):
        return self.workflow.resume(actor or self.author, waiting["execution_id"], {
            "expected_revision": waiting["revision"], "idempotency_key": str(uuid4()), "capability": "procurement_draft",
            "question": QUESTION, "decision": decision, "proposal_hash": proposal_hash or waiting["interrupt"]["proposal_hash"]})

    def test_nothing_is_written_before_approval_and_the_approved_draft_is_reconciled(self):
        waiting = self.start()
        self.assertEqual(waiting["status"], "WAITING", waiting["error_code"])
        self.assertEqual(waiting["capability"], "procurement_draft")
        self.assertEqual(waiting["interrupt"]["kind"], "write_approval")
        self.assertIn("30개", waiting["interrupt"]["message"])
        self.assertEqual((self.writes, waiting["tool_calls"], self.model_calls), ([], 1, []))
        final = self.decide(waiting, "approve")
        self.assertEqual(final["status"], "COMPLETED", final["error_code"])
        result = final["result"]
        self.assertEqual(len(self.writes), 1)
        self.assertEqual((result["reconciliation"]["status"], result["ledger_mutations"], result["model_calls"]), ("MATCHED", 0, 0))
        self.assertEqual(final["document_writes"], 1)
        written = next(iter(self.documents.values()))
        self.assertEqual((written["created_by"], written["content"]["quantity"], written["content"]["quote_id"]),
                         (self.author.actor_id, 30, "quote-SUP-A"))
        self.assertIn("검토자 계정의 승인·확정이 필요합니다", result["answer"])
        self.assertEqual(self.model_calls, [])

    def test_decline_and_a_wrong_proposal_write_nothing(self):
        declined = self.decide(self.start(), "decline")
        self.assertEqual(declined["status"], "COMPLETED")
        self.assertEqual(declined["result"]["document_writes"], [])
        failed = self.decide(self.start(), "approve", proposal_hash="0" * 64)
        self.assertEqual((failed["status"], failed["error_code"]), ("FAILED", "AGENT_RESUME_INVALID"))
        self.assertEqual(self.writes, [])

    def test_a_ledger_change_while_waiting_refuses_the_old_approval(self):
        waiting = self.start()
        self.version[0] = 2
        with self.assertRaises(ServiceError) as error:
            self.decide(waiting, "approve")
        self.assertEqual(error.exception.code, "AGENT_SNAPSHOT_CHANGED")
        self.assertEqual(self.writes, [])

    def test_unsafe_or_unclear_requests_never_reach_the_write_tool(self):
        unclear = self.start("구매 요청 초안 만들어줘")
        self.assertEqual((unclear["status"], unclear["interrupt"]["kind"]), ("WAITING", "clarification"))
        self.assertIn("SUP-A 30개(충족)", unclear["interrupt"]["message"])
        over = self.start("견적 B로 구매 요청 초안 만들어줘")
        self.assertEqual((over["status"], over["result"]["status"]), ("COMPLETED", "UNKNOWN"))
        self.assertIn("공급한도", over["result"]["answer"])
        approver = self.start(actor=self.reviewer)
        self.assertEqual(approver["result"]["conclusion"], "작성자 계정 필요 · 저장 없음")
        beyond = self.start("견적 A로 구매 요청 초안 만들고 발주해")
        self.assertEqual(beyond["result"]["conclusion"], "쓰기 범위 밖 · 저장 없음")
        self.assertEqual(self.writes, [])

    def test_a_clarified_request_continues_to_the_same_approval_and_write(self):
        unclear = self.start("구매 요청 초안 만들어줘")
        waiting = self.workflow.resume(self.author, unclear["execution_id"], {"expected_revision": unclear["revision"],
            "idempotency_key": str(uuid4()), "capability": "procurement_draft", "question": QUESTION})
        self.assertEqual((waiting["status"], waiting["interrupt"]["kind"]), ("WAITING", "write_approval"))
        self.assertEqual(self.writes, [])
        final = self.decide(waiting, "approve")
        self.assertEqual((final["status"], final["result"]["reconciliation"]["status"]), ("COMPLETED", "MATCHED"))
        self.assertEqual(len(self.writes), 1)

    def test_a_retried_identical_proposal_reuses_the_same_idempotency_key(self):
        self.decide(self.start(), "approve")
        again = self.decide(self.start(), "approve")
        self.assertEqual(again["status"], "COMPLETED", again["error_code"])
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(len(set(self.writes)), 1)  # same key → the domain returns the same document
        self.assertEqual(len(self.documents), 1)

    def test_a_saved_document_that_differs_from_the_approval_fails_reconciliation(self):
        self.tamper = {"quantity": 40}
        final = self.decide(self.start(), "approve")
        self.assertEqual((final["status"], final["error_code"]), ("FAILED", "AGENT_WRITE_RECONCILIATION_FAILED"))


if __name__ == "__main__":
    unittest.main()
