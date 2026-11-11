"""Deterministic specialist boundaries; no database, model or provider access."""

from service.agent_contracts import SpecialistInput
from service.errors import ServiceError
from service.gateway import ToolContext, ToolPort, ToolResult
from service.query_intents import interpret_question


class _ReadSpecialist:
    allowed_intents = frozenset()

    def run(self, request: SpecialistInput, port: ToolPort, context: ToolContext) -> ToolResult:
        plan = interpret_question(request.question)
        if not set(plan.intents).intersection(self.allowed_intents):
            raise ServiceError("AGENT_CAPABILITY_INPUT_MISMATCH", 422)
        if request.ledger_version != context.ledger_version:
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        result = port.call("ledger.query", request.model_dump())
        if result.mutating or result.tool_name != "ledger.query" or result.snapshot != context:
            raise ServiceError("AGENT_RESULT_INVALID", 409)
        meta = result.data.get("meta", {})
        if (meta.get("company_id") != context.company_id
                or meta.get("snapshot_id") != context.read_snapshot_id
                or meta.get("ledger_version") != context.ledger_version
                or meta.get("projection_version") != context.projection_version):
            raise ServiceError("AGENT_RESULT_INVALID", 409)
        if result.data.get("ledger_mutations", 0) != 0:
            raise ServiceError("AGENT_RESULT_INVALID", 409)
        return result


class FulfillmentRead(_ReadSpecialist):
    # The ontology scope question is answered by the same read-only ledger query.
    allowed_intents = frozenset({"fulfillment", "production", "delivery", "ontology", "quality", "transit"})


class MaterialAnalysis(_ReadSpecialist):
    # Sourcing comparison, shortage-change reasons and LOT tracing are material
    # reads answered by the same ledger query.
    allowed_intents = frozenset({"materials", "inventory", "sourcing", "change", "trace", "position", "substitute"})
