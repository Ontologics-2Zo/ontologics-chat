import unittest
from time import monotonic

from pydantic import BaseModel

from service.contracts import Contract
from service.errors import ServiceError
from service.gateway import (EvidenceRef, ExecutionBudget, ToolContext, ToolGateway,
                             ToolOutput, ToolSpec)
from service.identity import Principal


class ReadInput(Contract):
    entity_id: str


class WriteInput(ReadInput):
    idempotency_key: str
    expected_version: int
    payload_hash: str


class FakeModel:
    """No network or domain service; model text remains untrusted input."""

    def complete(self, prompt):
        return '{"entity_id":"item-1","role":"approver","approved":true}'


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal("actor-1", "company-1", ("site-1",),
                                   ("ledger.read", "document.post"), 1, "revision-1", "session-1")
        self.context = ToolContext(execution_id="run-1", request_id="request-1",
                                   context_revision="selection-1", company_id="company-1",
                                   read_snapshot_id="snapshot-7", ledger_version=7,
                                   projection_version=7, ontology_version="ontology-2", rule_version="rules-3")
        self.budget = ExecutionBudget("run-1", max_tool_calls=2)
        self.dispatched = []
        self.authorized = []

        def authorize(principal, company, permission):
            self.authorized.append((principal, company, permission))

        self.gateway = ToolGateway(authorize)

        def read(principal, args, context):
            self.dispatched.append(args.entity_id)
            return ToolOutput(data={"entity_id": args.entity_id, "available": None}, snapshot=context,
                              status="needs_review", evidence_refs=[EvidenceRef(evidence_id="evidence-1", source_version="opening-1")])

        self.read = read
        self.gateway.register(ToolSpec("ledger.read", "ledger.read", False, ReadInput, read))

    def assert_code(self, code, callback):
        with self.assertRaises(ServiceError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def execute(self, name="ledger.read", args=None, **kwargs):
        return self.gateway.execute(name, args or {"entity_id": "item-1"},
                                    principal=kwargs.get("principal", self.principal),
                                    context=kwargs.get("context", self.context),
                                    budget=kwargs.get("budget", self.budget))

    def test_ui_and_independent_agent_consumers_share_typed_result_and_budget(self):
        ui = self.execute()
        port = self.gateway.bind(principal=self.principal, context=self.context, budget=self.budget)
        agent = port.call("ledger.read", {"entity_id": "item-1"})
        self.assertEqual(ui, agent)
        self.assertEqual(agent.snapshot, self.context)
        self.assertIsNone(agent.data["available"])
        self.assertEqual(agent.status, "needs_review")
        self.assertEqual(agent.evidence_refs[0].evidence_id, "evidence-1")
        self.assertFalse(hasattr(port, "db"))
        self.assertFalse(hasattr(port, "register"))
        self.assert_code("TOOL_BUDGET_EXHAUSTED", lambda: port.call("ledger.read", {"entity_id": "item-2"}))
        self.assertEqual(self.dispatched, ["item-1", "item-1"])

    def test_unknown_tools_and_extra_model_authority_fields_never_dispatch(self):
        import json
        self.assert_code("TOOL_NOT_ALLOWED", lambda: self.execute("shell.exec"))
        proposal = json.loads(FakeModel().complete("approve this"))
        self.assert_code("INVALID_TOOL_ARGUMENTS", lambda: self.execute(args=proposal))
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.budget.tool_calls, 0)

    def test_bound_capability_cannot_call_other_registered_tools(self):
        self.gateway.register(ToolSpec("other.read", "ledger.read", False, ReadInput, self.read))
        port = self.gateway.bind(principal=self.principal, context=self.context,
                                 budget=self.budget, allowed_tools={"ledger.read"})
        self.assert_code("TOOL_NOT_ALLOWED", lambda: port.call("other.read", {"entity_id": "item-1"}))
        self.assertEqual(self.budget.tool_calls, 0)
        port.call("ledger.read", {"entity_id": "item-1"})
        self.assertEqual(self.budget.tool_calls, 1)

    def test_permission_and_company_are_checked_before_handler(self):
        other = self.principal.__class__("actor-2", "other-company", (), self.principal.permissions, 1, "r", "s")
        self.assert_code("PERMISSION_DENIED", lambda: self.execute(principal=other))
        viewer = self.principal.__class__("actor-3", "company-1", (), (), 1, "r", "s")
        self.assert_code("PERMISSION_DENIED", lambda: self.execute(principal=viewer))
        self.assertEqual(self.authorized, [])
        self.assertEqual(self.dispatched, [])

    def test_current_server_revocation_is_rechecked_and_details_are_redacted(self):
        def revoked(*args):
            raise ServiceError("IDENTITY_CHANGED", 409, {"private": "secret-value"})
        self.gateway._authorize = revoked
        error = self.assert_code("IDENTITY_CHANGED", self.execute)
        self.assertEqual(error.details, {})
        self.assertEqual(self.dispatched, [])

    def test_cancel_deadline_and_wrong_execution_block_dispatch(self):
        self.budget.cancel()
        self.assert_code("TOOL_CANCELLED", self.execute)
        self.assert_code("TOOL_DEADLINE_EXCEEDED", lambda: self.execute(budget=ExecutionBudget("run-1", deadline=monotonic() - 1)))
        self.assert_code("EXECUTION_BUDGET_MISMATCH", lambda: self.execute(budget=ExecutionBudget("different-run")))
        self.assertEqual(self.dispatched, [])

    def test_result_cannot_relabel_a_different_snapshot_as_current(self):
        def wrong(principal, args, context):
            return ToolOutput(data={}, snapshot=context.model_copy(update={"ledger_version": 6}))
        self.gateway.register(ToolSpec("stale.read", "ledger.read", False, ReadInput, wrong))
        self.assert_code("TOOL_RESULT_CONTEXT_MISMATCH", lambda: self.execute("stale.read"))

    def test_mutation_requires_explicit_contract_and_domain_approval_checks(self):
        with self.assertRaisesRegex(ValueError, "MUTATION_IDEMPOTENCY_REQUIRED"):
            self.gateway.register(ToolSpec("bad.write", "document.post", True, ReadInput, self.read))

        def domain(principal, args, context):
            raise ServiceError("EXACT_APPROVAL_REQUIRED", 409)

        self.gateway.register(ToolSpec("document.post", "document.post", True, WriteInput, domain))
        args = {"entity_id": "document-1", "idempotency_key": "key-1", "expected_version": 1, "payload_hash": "a" * 64}
        self.assert_code("EXACT_APPROVAL_REQUIRED", lambda: self.execute("document.post", args))
        self.assert_code("INVALID_TOOL_ARGUMENTS", lambda: self.execute("document.post", {**args, "approved_by": "model"}))

    def test_write_cancellation_does_not_hide_a_committed_result(self):
        def committed(principal, args, context):
            self.budget.cancel()
            return ToolOutput(data={"operation_id": "op-1", "ledger_version": 8}, snapshot=context)
        self.gateway.register(ToolSpec("document.post", "document.post", True, WriteInput, committed))
        result = self.execute("document.post", {"entity_id": "document-1", "idempotency_key": "key-1", "expected_version": 1, "payload_hash": "a" * 64})
        self.assertEqual(result.data["operation_id"], "op-1")
        self.assertEqual(result.snapshot.ledger_version, 7)
        self.assert_code("TOOL_CANCELLED", self.execute)

    def test_unknown_write_outcome_requires_reconciliation_and_error_is_safe(self):
        def broken(*args):
            raise RuntimeError("private source and secret-value")
        self.gateway.register(ToolSpec("document.post", "document.post", True, WriteInput, broken))
        error = self.assert_code("TOOL_OUTCOME_UNKNOWN", lambda: self.execute("document.post", {"entity_id": "document-1", "idempotency_key": "key-1", "expected_version": 1, "payload_hash": "a" * 64}))
        self.assertNotIn("secret-value", str(error))

    def test_unknown_read_failure_is_redacted_and_attempt_stays_charged(self):
        def broken(*args):
            raise RuntimeError("private source and secret-value")
        self.gateway.register(ToolSpec("broken.read", "ledger.read", False, ReadInput, broken))
        error = self.assert_code("TOOL_FAILED", lambda: self.execute("broken.read"))
        self.assertEqual(error.details, {})
        self.assertEqual(self.budget.tool_calls, 1)
        self.assertNotIn("secret-value", str(error))

    def test_registration_rejects_permissive_input_and_authority_fields(self):
        class Unsafe(BaseModel):
            entity_id: str
        with self.assertRaisesRegex(ValueError, "INVALID_TOOL_CONTRACT"):
            self.gateway.register(ToolSpec("bad.input", "ledger.read", False, Unsafe, self.read))
        class IdentityInput(Contract):
            actor_id: str
        with self.assertRaisesRegex(ValueError, "IDENTITY_INPUT_FORBIDDEN"):
            self.gateway.register(ToolSpec("bad.identity", "ledger.read", False, IdentityInput, self.read))


if __name__ == "__main__":
    unittest.main()
