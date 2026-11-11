"""Persistent bounded read workflow, separate from business approval and writes.

Only opaque references and routing state enter LangGraph checkpoints. Authenticated
inputs/results live in the local artifact store; secrets/Principals never enter
graph configuration. Explicit metadata observation replaces automatic cloud traces.
"""

from datetime import datetime, timezone
from importlib.metadata import version
import re
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from service.agent_contracts import (AgentRequest, AgentResume, CHECKPOINT_VERSION,
                                     SpecialistInput, WORKFLOW_VERSION)
from service.agent_store import AgentStore, DurableAgentBudget, TERMINAL
from service.agents import FulfillmentRead, MaterialAnalysis
from service.agents.product import ProductReview
from service.agents.procurement import ProcurementReview
from service.contracts import content_hash
from service.errors import ServiceError
from service.query_intents import interpret_question
from service.product_contracts import ProductReviewInput
from service.observability import (EXECUTION_ROOT_SPAN, WorkflowObservationInput,
                                   WorkflowObservationOutput)


CAPABILITIES = {"fulfillment_read": FulfillmentRead, "material_analysis": MaterialAnalysis,
                "product_review": ProductReview, "procurement_review": ProcurementReview}
CAPABILITY_TOOLS = {"fulfillment_read": "ledger.query", "material_analysis": "ledger.query",
                    "product_review": "product.review", "procurement_review": "procurement.options"}
MODEL_CAPABILITIES = frozenset({"fulfillment_read", "material_analysis"})
PINNED_FIELDS = ("company_id", "read_snapshot_id", "ledger_version", "projection_version",
                 "ontology_version", "rule_version", "as_of", "known_as_of")


class TaskState(TypedDict, total=False):
    execution_id: str
    capability: str | None
    result_ref: str | None
    needs_clarification: bool
    checkpoint_version: str


class AgentWorkflow:
    def __init__(self, db, identity, gateway, context_provider, *, route_model=None,
                 compose_model=None, observer=None, max_tool_calls=8, deadline_seconds=3600,
                 retrieval_profile="R3"):
        if type(max_tool_calls) is not int or not 1 <= max_tool_calls <= 100:
            raise ValueError("INVALID_AGENT_BUDGET")
        if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 86400:
            raise ValueError("INVALID_AGENT_DEADLINE")
        self.db, self.identity, self.gateway = db, identity, gateway
        self.context_provider, self.route_model, self.observer = context_provider, route_model, observer
        self.compose_model = compose_model
        if retrieval_profile not in {"R3", "R4"}:
            raise ValueError("INVALID_RETRIEVAL_PROFILE")
        self.retrieval_profile = retrieval_profile
        self.max_tool_calls, self.deadline_seconds = max_tool_calls, deadline_seconds
        self.store = AgentStore(db)
        self.runtime_versions = {name: version(name) for name in
                                 ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-postgres")}

    def setup(self):
        # Library migrations are isolated checkpoint tables in the configured
        # database/schema. Existing ledger tables are never altered by setup.
        with self.db.connect() as conn:
            conn.autocommit = True
            conn.execute("SELECT pg_advisory_lock(%s)", (674958218,))
            try:
                PostgresSaver(conn).setup()
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (674958218,))

    def _owner(self, principal, execution_id):
        self.identity.authorize(principal, principal.company_id, "query.read")
        row = self.store.get(execution_id)
        if row["company_id"] != principal.company_id or row["owner_actor_id"] != principal.actor_id:
            raise ServiceError("AGENT_EXECUTION_NOT_FOUND", 404)
        return row

    def _revalidate(self, principal, row):
        self.identity.authorize(principal, row["company_id"], "query.read")
        if principal.permission_profile_version != row["policy_version"]:
            raise ServiceError("AGENT_POLICY_CHANGED", 409)
        if row["workflow_version"] != WORKFLOW_VERSION or row["checkpoint_version"] != CHECKPOINT_VERSION:
            raise ServiceError("AGENT_WORKFLOW_CHANGED", 409)
        stored_runtime = row["request"].get("_runtime_versions")
        if stored_runtime != self.runtime_versions:
            raise ServiceError("AGENT_CHECKPOINT_INCOMPATIBLE", 409)
        fresh = self.context_provider(principal, str(row["execution_id"]))
        current = fresh.model_dump(mode="json")
        if any(current.get(field) != row["pinned_context"].get(field) for field in PINNED_FIELDS):
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        if fresh.projection_version != fresh.ledger_version:
            raise ServiceError("PROJECTION_NOT_READY", 409)
        if fresh.execution_id != str(row["execution_id"]):
            raise ServiceError("EXECUTION_BUDGET_MISMATCH", 409)
        return fresh

    def create(self, principal, request):
        request = AgentRequest.model_validate(request)
        self.identity.authorize(principal, principal.company_id, "query.read")
        execution_id = str(uuid4())
        context = self.context_provider(principal, execution_id)
        if context.company_id != principal.company_id or context.execution_id != execution_id:
            raise ServiceError("SERVER_CONTEXT_REQUIRED", 403)
        if context.ledger_version != request.ledger_version or context.projection_version != context.ledger_version:
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        execution_id = self.store.create(principal, request, context, max_tool_calls=self.max_tool_calls,
                                         deadline_seconds=self.deadline_seconds, runtime_versions=self.runtime_versions)
        return self.status(principal, execution_id)

    def status(self, principal, execution_id):
        row = self._owner(principal, execution_id)
        result = self.store.result(str(row["execution_id"]), row["result_ref"])
        if result is not None:
            # Identity revision describes this authenticated response, not the
            # historical business snapshot. Keep the stored artifact immutable.
            result = {**result, "meta": {**result.get("meta", {}),
                                        "identity_revision": principal.identity_revision}}
        return {"execution_id": str(row["execution_id"]), "revision": row["revision"],
                "status": row["status"], "capability": row["capability"],
                "segment_count": row["segment_count"], "tool_calls": row["tool_calls"],
                "max_tool_calls": row["max_tool_calls"], "snapshot": row["pinned_context"],
                "result": result, "interrupt": row["interrupt"], "error_code": row["error_code"],
                "ledger_mutations": 0, "execution_kind": "langgraph_readonly_agent",
                "deadline_at": row["deadline_at"].isoformat(),
                "events": self.store.events(str(row["execution_id"]))}

    def events(self, principal, execution_id, after=0):
        row = self._owner(principal, execution_id)
        return self.store.events(str(row["execution_id"]), after)

    def cancel(self, principal, execution_id, expected_revision):
        row = self._owner(principal, execution_id)
        self.store.cancel(str(row["execution_id"]), expected_revision)
        if row["status"] not in TERMINAL:
            # RUNNING cancellation is summarized by run() after its lease exits.
            # CREATED/WAITING cancellation has no further segment to emit it.
            choice = row.get("pending_resume") or (row["request"] if row["segment_count"] <= 1 else None)
            self._observe_snapshot(str(row["execution_id"]), "execution", EXECUTION_ROOT_SPAN,
                                   None, row["created_at"], choice=choice, terminal=True)
        return self.status(principal, execution_id)

    def resume(self, principal, execution_id, request):
        request = AgentResume.model_validate(request)
        row = self._owner(principal, execution_id)
        self._revalidate(principal, row)
        if request.product is not None and request.product.ledger_version != row["pinned_context"]["ledger_version"]:
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        changed = self.store.resume(str(row["execution_id"]), request)
        if not changed:
            return self.status(principal, execution_id)
        return self.run(principal, execution_id)

    def _observe(self, event):
        if self.observer is not None:
            try:
                self.observer(event)
            except Exception:
                # Observability never changes a completed domain result.
                pass

    def _observe_snapshot(self, execution_id, stage, span_id, parent_span_id, started_at,
                          *, choice=None, state=None, status=None, error_code=None,
                          terminal=False):
        """Capture selected actual values only; never export an artifact wholesale."""
        if self.observer is None:
            return
        try:
            row = self.store.get(execution_id)
            if terminal and row["status"] not in TERMINAL:
                return
            state = state or {}
            ref = state.get("result_ref", row.get("result_ref"))
            try:
                result = self.store.result(execution_id, ref) if ref else None
            except Exception:
                result = None
            result_available = isinstance(result, dict)
            result = result if isinstance(result, dict) else {}
            selected = choice or {}
            pinned = row["pinned_context"]
            status = status or row["status"].lower()
            clarification = row.get("interrupt") or {}
            # A terminal execution root is immutable and exported once by its
            # stable receipt ID. WAITING remains on the segment observation.
            self._observe({"execution_id": execution_id, "segment_id": str(row["segment_id"]) if row.get("segment_id") else None,
                "span_id": span_id, "parent_span_id": parent_span_id,
                "name": "agent_execution_summary" if terminal else ("agent_segment" if stage == "segment" else stage),
                "kind": "agent" if terminal else "chain", "status": status,
                "started_at": row["created_at"] if terminal else started_at,
                "ended_at": datetime.now(timezone.utc),
                "terminal_summary": terminal,
                "input": WorkflowObservationInput(stage=stage,
                    requested_capability=selected.get("capability"),
                    ledger_version=pinned["ledger_version"], projection_version=pinned["projection_version"],
                    question_scope="current_segment" if choice else "unavailable",
                    question=selected.get("question"),
                    initial_question=row["request"].get("question") if terminal else None),
                "output": WorkflowObservationOutput(stage=stage, status=status,
                    capability=state.get("capability", row.get("capability")),
                    tool_calls=row["tool_calls"], segment_count=row["segment_count"], result_available=result_available,
                    result_status=result.get("status"), error_code=error_code or row.get("error_code"),
                    answer=result.get("answer") if isinstance(result.get("answer"), str) else None,
                    conclusion=result.get("conclusion") if isinstance(result.get("conclusion"), str) else None,
                    clarification=clarification.get("message") if isinstance(clarification.get("message"), str) else None),
                "metadata": {"workflow_version": WORKFLOW_VERSION,
                    "ledger_version": pinned["ledger_version"], "projection_version": pinned["projection_version"],
                    "tool_calls": row["tool_calls"]}})
        except Exception:
            # Neither an unavailable optional artifact nor tracing may change
            # canonical execution/result/usage or recover a failed provider call.
            pass

    def _build(self, principal, row, context, budget, saver):
        execution_id, segment_id, lease = str(row["execution_id"]), row["segment_id"], row["lease_token"]

        def effective_choice():
            current = self.store.get(execution_id)
            initial = current["request"]
            return current["pending_resume"] or initial

        def effective_input():
            choice = effective_choice()
            return SpecialistInput(question=choice["question"], ledger_version=context.ledger_version,
                                   context_entity_id=row["request"].get("context_entity_id")), choice.get("capability")

        def observed(name, fn):
            def call(state):
                budget.check()
                start = datetime.now(timezone.utc)
                span_id = str(uuid4())
                self.store.event(execution_id, lease, segment_id, name, "RUNNING")
                try:
                    result = fn(state)
                except Exception as error:
                    code = error.code if isinstance(error, ServiceError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code) else "AGENT_EXECUTION_FAILED"
                    self._observe_snapshot(execution_id, name, span_id, segment_id, start,
                        choice=row.get("pending_resume") or row["request"], state=state, status="failed", error_code=code)
                    raise
                self.store.event(execution_id, lease, segment_id, name, "COMPLETED")
                self._observe_snapshot(execution_id, name, span_id, segment_id, start,
                    choice=row.get("pending_resume") or row["request"], state={**state, **result}, status="completed")
                return result
            return call

        def route(state):
            request, explicit = effective_input()
            if explicit:
                return {"capability": explicit, "needs_clarification": False}
            plan = interpret_question(request.question)
            if plan.clarification or plan.unsupported:
                capability = None
            elif self.route_model:
                features = tuple(plan.intents) or ("unknown",)
                capability = self.route_model(principal, context, segment_id, features, budget)
            elif set(plan.intents) & {"materials", "inventory"}:
                capability = "material_analysis"
            elif set(plan.intents) & {"fulfillment", "production", "delivery"}:
                capability = "fulfillment_read"
            else:
                capability = None
            if capability == "needs_clarification":
                capability = None
            if capability is not None and capability not in MODEL_CAPABILITIES:
                raise ServiceError("AGENT_ROUTE_FORBIDDEN", 403)
            return {"capability": capability, "needs_clarification": capability is None}

        def clarify(state):
            # Re-execution starts at this node; no model/tool side effect precedes
            # interrupt inside the node, and the Host already validated resume.
            initial_request, _ = effective_input()
            plan = interpret_question(initial_request.question)
            previous = self.store.result(execution_id, state.get("result_ref"))
            message = plan.clarification or (previous.get("answer") if previous else None) or "현재 회차의 이행 또는 자재 분석에 필요한 질문과 업무를 지정해 주세요. 이 재개는 승인이나 원장 반영이 아닙니다."
            choice = interrupt({"kind": "clarification", "message": message,
                                "allowed_capabilities": list(CAPABILITIES)})
            request, persisted_capability = effective_input()
            expected = {"capability": persisted_capability, "question": request.question}
            product = effective_choice().get("product")
            if product is not None:
                expected["product"] = product
            if not isinstance(choice, dict) or choice != expected:
                raise ServiceError("AGENT_RESUME_INVALID", 409)
            return {"capability": persisted_capability, "result_ref": None, "needs_clarification": False}

        def specialist_node(capability):
            def call(state):
                request, _ = effective_input()
                if capability == "product_review":
                    request = ProductReviewInput.model_validate(effective_choice().get("product"))
                key = content_hash({"capability": capability, "input": request.model_dump(mode="json")})
                prior = self.store.artifact(execution_id, key)
                if prior:
                    # Read result cache is safe only after the fresh Host check.
                    self.store.result(execution_id, prior["artifact_id"])
                    return {"result_ref": str(prior["artifact_id"])}
                port = self.gateway.bind(principal=principal, context=context, budget=budget,
                                         allowed_tools={CAPABILITY_TOOLS[capability]})
                result = CAPABILITIES[capability]().run(request, port, context)
                ref = self.store.put_artifact(execution_id, lease, key, result.data)
                return {"result_ref": ref}
            return call

        def validate(state):
            result = self.store.result(execution_id, state.get("result_ref"))
            if result is None or result.get("status") not in {"ANSWERED", "PARTIAL", "UNKNOWN", "NEEDS_CLARIFICATION"}:
                raise ServiceError("AGENT_RESULT_INVALID", 409)
            return {"needs_clarification": result["status"] == "NEEDS_CLARIFICATION"}

        def extend(state, stage, field, make):
            """Adds one field to the result as a new immutable artifact (cached per input)."""
            base = state.get("result_ref")
            result = self.store.result(execution_id, base)
            if result is None or state.get("capability") not in MODEL_CAPABILITIES:
                return {}
            key = content_hash({"stage": stage, "base": base})
            prior = self.store.artifact(execution_id, key)
            if prior:
                self.store.result(execution_id, prior["artifact_id"])
                return {"result_ref": str(prior["artifact_id"])}
            ref = self.store.put_artifact(execution_id, lease, key, {**result, field: make(result)})
            return {"result_ref": ref}

        def retrieve(state):
            # Snapshot-local hybrid search (R3) as supporting evidence. A search
            # failure never fails the answer; it is recorded as unavailable.
            def search(result):
                request, _ = effective_input()
                try:
                    port = self.gateway.bind(principal=principal, context=context, budget=budget,
                                             allowed_tools={"retrieval.search"})
                    # The host picks R4 (local rerank, best top-5 order on the P0
                    # cases) when a reranker is loaded, otherwise R3.
                    found = port.call("retrieval.search", {"query": request.question, "profile": self.retrieval_profile,
                                                           "limit": 8, "ledger_version": context.ledger_version}).data
                except ServiceError as error:
                    return {"status": "UNAVAILABLE", "reason_code": error.code, "hits": []}
                except ValueError:
                    return {"status": "UNAVAILABLE", "reason_code": "RETRIEVAL_TOOL_UNAVAILABLE", "hits": []}
                hits = [{"evidence_id": hit["evidence_id"], "kind": hit["kind"], "origin": hit["origin"],
                         "review_status": hit["review_status"], "node_ids": hit["node_ids"][:4],
                         "match_modes": hit["match_modes"], "text": hit["text"][:120]} for hit in found.get("hits", [])]
                return {"status": found.get("status"), "profile": found.get("profile", "R3"),
                        "corpus_version": (found.get("meta") or {}).get("corpus_version"), "hits": hits}
            return extend(state, "retrieve", "retrieval", search)

        def compose(state):
            if self.compose_model is None:
                return {}
            def summarize(result):
                hits = (result.get("retrieval") or {}).get("hits") or []
                return self.compose_model(principal, context, segment_id, result, hits[:5], budget)
            return extend(state, "compose", "ai_summary", summarize)

        graph = StateGraph(TaskState)
        graph.add_node("route", observed("route", route))
        graph.add_node("clarify", clarify)
        for capability in CAPABILITIES:
            child = StateGraph(TaskState)
            child.add_node("read", observed(capability, specialist_node(capability)))
            child.add_edge(START, "read")
            child.add_edge("read", END)
            graph.add_node(capability, child.compile(name=capability))
            graph.add_edge(capability, "validate")
        graph.add_node("validate", observed("validate", validate))
        graph.add_node("retrieve", observed("retrieve", retrieve))
        graph.add_node("compose", observed("compose", compose))
        choices = {**{name: name for name in CAPABILITIES}, "clarify": "clarify"}
        graph.add_edge(START, "route")
        graph.add_conditional_edges("route", lambda state: state.get("capability") or "clarify", choices)
        graph.add_conditional_edges("clarify", lambda state: state["capability"], {name: name for name in CAPABILITIES})
        graph.add_conditional_edges("validate", lambda state: "clarify" if state["needs_clarification"] else "retrieve", {"clarify": "clarify", "retrieve": "retrieve"})
        graph.add_edge("retrieve", "compose")
        graph.add_edge("compose", END)
        return graph.compile(checkpointer=saver, name=WORKFLOW_VERSION)

    def run(self, principal, execution_id, *, expected_revision=None):
        row = self._owner(principal, execution_id)
        self._revalidate(principal, row)
        execution_id = str(row["execution_id"])
        with self.store.claim(execution_id, expected_revision=expected_revision) as claimed:
            if claimed is None:
                return self.status(principal, execution_id)
            lease, segment_id = claimed["lease_token"], claimed["segment_id"]
            start = datetime.now(timezone.utc)
            try:
                context = self._revalidate(principal, claimed)
                budget = DurableAgentBudget(self.store, execution_id, lease,
                                            lambda: self._revalidate(principal, claimed))
                budget.check()
                with self.db.connect() as conn, tracing_context(enabled=False):
                    conn.autocommit = True
                    compiled = self._build(principal, claimed, context, budget, PostgresSaver(conn))
                    config = {"configurable": {"thread_id": str(claimed["thread_id"])},
                              "callbacks": [], "recursion_limit": 24}
                    saved = compiled.get_state(config)
                    if saved.values and saved.values.get("checkpoint_version") != CHECKPOINT_VERSION:
                        raise ServiceError("AGENT_CHECKPOINT_INCOMPATIBLE", 409)
                    has_interrupt = any(task.interrupts for task in saved.tasks)
                    if has_interrupt and claimed["pending_resume"]:
                        argument = Command(resume=claimed["pending_resume"])
                    elif not saved.values:
                        argument = {"execution_id": execution_id, "checkpoint_version": CHECKPOINT_VERSION}
                    else:
                        argument = None
                    result = compiled.invoke(argument, config, durability="sync")
                    stopped = result.get("__interrupt__", ())
                    state = compiled.get_state(config).values
                budget.check()
                self.store.finish(execution_id, lease, segment_id, "WAITING" if stopped else "COMPLETED",
                                  result_ref=state.get("result_ref"), capability=state.get("capability"),
                                  interrupt=stopped[0].value if stopped else None)
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error.code) else "AGENT_EXECUTION_FAILED"
                self.store.finish(execution_id, lease, segment_id, "CANCELLED" if code == "AGENT_CANCELLED" else "FAILED", error_code=code)
            finally:
                choice = claimed.get("pending_resume") or claimed["request"]
                self._observe_snapshot(execution_id, "segment", segment_id, EXECUTION_ROOT_SPAN,
                                       start, choice=choice)
                self._observe_snapshot(execution_id, "execution", EXECUTION_ROOT_SPAN,
                                       None, start, choice=choice, terminal=True)
        return self.status(principal, execution_id)
