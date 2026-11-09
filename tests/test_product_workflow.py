"""Explicit Product/Procurement subgraphs on disposable PostgreSQL schemas."""

import os
import unittest
from uuid import uuid4

from psycopg import sql

from service.agent_contracts import AgentRequest
from service.contracts import Contract
from service.db import Database
from service.errors import ServiceError
from service.gateway import ToolOutput, ToolSpec
from service.product import review_product
from service.product_contracts import ProductReviewInput
from tests.test_agent_workflow import build_host
from tests.test_product import fixture, row_id


class Options(Contract):
    ledger_version: int


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class ProductWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.base = Database(os.environ["TEST_DATABASE_URL"])
        self.schema = "test_product_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(os.environ["TEST_DATABASE_URL"], schema=self.schema); self.db.migrate()
        self.model_calls = []
        self.workflow, self.identity = build_host(self.db, route_model=lambda *args: self.model_calls.append(args))
        self.identity.provision_dev_accounts(); self.actor = self.identity.login("dev-operator-a").principal
        self.workflow.setup()

        def product(actor, args, ctx):
            payload = fixture(); payload["metadata"].update(company_id=ctx.company_id, snapshot_id=ctx.read_snapshot_id,
                server_ledger_version=1, current_ledger_version=1); payload["projection"]["projection_version"] = 1
            return ToolOutput(snapshot=ctx, data=review_product(payload, args, ctx))

        def procurement(actor, args, ctx):
            return ToolOutput(snapshot=ctx, data={"ledger_version": 1, "quotes": [{"quote_id": "synthetic-quote", "item_id": "part-a",
                "material_basis": "base", "unit": "piece", "expired": False, "partial_bom": True,
                "calculation": {"shortage_qty": "2", "recommended_qty": 4, "subtotal": "12", "freight": None, "total": None,
                                "currency": "USD", "freight_status": "UNKNOWN"}}], "model_calls": 0, "ledger_mutations": 0,
                "meta": {"company_id": ctx.company_id, "snapshot_id": ctx.read_snapshot_id,
                         "ledger_version": ctx.ledger_version, "projection_version": ctx.projection_version}})

        self.workflow.gateway.register(ToolSpec("product.review", "ledger.read", False, ProductReviewInput, product))
        self.workflow.gateway.register(ToolSpec("procurement.options", "ledger.read", False, Options, procurement))

    def cleanup_schema(self):
        with self.base.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def product(self, version=1):
        return {"ledger_version": version, "bom_id": "independent-bom", "bom_version": 3,
                "payload_hash": "b" * 64, "row_id": row_id(0)}

    def test_explicit_product_and_procurement_have_isolated_read_ports_and_no_model(self):
        for capability in ("product_review", "procurement_review"):
            args = {"question": "독립 합성 검토", "ledger_version": 1, "capability": capability, "idempotency_key": str(uuid4())}
            if capability == "product_review": args["product"] = self.product()
            created = self.workflow.create(self.actor, AgentRequest.model_validate(args))
            final = self.workflow.run(self.actor, created["execution_id"])
            self.assertEqual(final["status"], "COMPLETED", final["error_code"])
            self.assertEqual(final["capability"], capability)
            self.assertEqual(final["tool_calls"], 1)
            self.assertEqual(final["result"]["status"], "PARTIAL")
            self.assertEqual(final["result"]["model_calls"], 0)
            self.assertEqual(final["result"]["ledger_mutations"], 0)
            self.assertIn(capability, final["result"])
            if capability == "procurement_review":
                self.assertIsNone(final["result"][capability]["quotes"][0]["calculation"]["total"])
                self.assertIn("미확인", str(final["result"]["sections"]))
        self.assertEqual(self.model_calls, [])

    def test_clarification_resume_preserves_typed_product_input_and_rejects_other_version(self):
        created = self.workflow.create(self.actor, AgentRequest(question="무엇을 확인할지 모르겠어", ledger_version=1,
                                        idempotency_key=str(uuid4())))
        waiting = self.workflow.run(self.actor, created["execution_id"])
        self.assertEqual(waiting["status"], "WAITING")
        resume = {"expected_revision": waiting["revision"], "idempotency_key": str(uuid4()), "capability": "product_review",
                  "question": "이 BOM 행의 품목 연결 검토", "product": self.product(2)}
        with self.assertRaises(ServiceError) as error:
            self.workflow.resume(self.actor, created["execution_id"], resume)
        self.assertEqual(error.exception.code, "AGENT_SNAPSHOT_CHANGED")
        resume["product"] = self.product()
        final = self.workflow.resume(self.actor, created["execution_id"], resume)
        self.assertEqual(final["status"], "COMPLETED", final["error_code"])
        self.assertEqual(final["result"]["product_review"]["rows"][0]["row_id"], row_id(0))
        self.assertEqual(self.model_calls, [])
