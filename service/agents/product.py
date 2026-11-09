"""Typed Product specialist: only its Host-bound read tool is available."""

from service.errors import ServiceError
from service.gateway import ToolContext, ToolPort, ToolResult
from service.product_contracts import ProductReviewInput
from service.product import product_answer


class ProductReview:
    def run(self, request: ProductReviewInput, port: ToolPort, context: ToolContext) -> ToolResult:
        request = ProductReviewInput.model_validate(request)
        if request.ledger_version != context.ledger_version:
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        result = port.call("product.review", request.model_dump())
        meta = result.data.get("meta", {})
        if (result.mutating or result.tool_name != "product.review" or result.snapshot != context
                or meta.get("company_id") != context.company_id
                or meta.get("snapshot_id") != context.read_snapshot_id
                or meta.get("ledger_version") != context.ledger_version
                or meta.get("projection_version") != context.projection_version
                or result.data.get("bom_id") != request.bom_id
                or result.data.get("bom_version") != request.bom_version
                or result.data.get("payload_hash") != request.payload_hash
                or result.data.get("ledger_mutations") != 0 or result.data.get("model_calls") != 0):
            raise ServiceError("AGENT_RESULT_INVALID", 409)
        return result.model_copy(update={"data": product_answer(result.data)})
