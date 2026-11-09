"""Read-only procurement advice; drafts/approval/sending are separate UI actions."""

from copy import deepcopy

from service.agent_contracts import SpecialistInput
from service.errors import ServiceError
from service.gateway import ToolContext, ToolPort, ToolResult


def procurement_answer(data):
    quotes = data.get("quotes", [])
    sections = [{"id": "procurement-scope", "title": "구매 검토 범위", "origin": "conditional_demo", "items": [
        {"label": "검토 선택지", "value": f"{len(quotes)}개", "detail": "기본 목표와 수율 계획을 별도로 비교합니다."},
        {"label": "전체 제조 가능성", "value": "미확인", "detail": "일부 자재의 구매 조건만으로 전체 BOM·재고·생산·납기를 확정하지 않습니다."},
        {"label": "공급사 확약·송신", "value": "미확인 · 미송신", "detail": "현재 견적은 개발용 가정입니다. 내부 초안과 별도 검토·승인은 실제 공급사 송신이나 납품 확약이 아닙니다."}]}]
    for index, quote in enumerate(quotes[:12]):
        calculation = quote.get("calculation")
        basis = "계획 투입" if quote.get("material_basis") == "planned" else "기본 목표"
        rows = [{"label": "품목·기준", "value": f"{quote.get('item_id', '미확인')} · {basis}"},
                {"label": "견적 유효성", "value": "만료" if quote.get("expired") else "별도 확인 필요"}]
        if calculation is None:
            rows.append({"label": "주문 수량·금액", "value": "미확인", "detail": "부족량·견적 조건을 확정할 수 없습니다."})
        else:
            rows.extend([{"label": "부족 / MOQ·배수 적용 제안", "value": f"{calculation.get('shortage_qty')} / {calculation.get('recommended_qty')} {quote.get('unit') or ''}"},
                         {"label": "총액", "value": "미확인" if calculation.get("total") is None else f"{calculation['total']} {calculation.get('currency') or ''}",
                          "detail": "운임 미확인" if calculation.get("freight") is None else f"운임 {calculation['freight']}"}])
        sections.append({"id": f"procurement-option-{index}", "title": f"견적 {quote.get('quote_id') or index + 1}",
                         "origin": "conditional_demo", "status": "UNKNOWN" if calculation is None else "PARTIAL", "items": rows})
    return {"status": "PARTIAL" if quotes else "UNKNOWN", "intent_label": "구매 검토", "conclusion": "구매 후보 비교 · 주문·송신 없음",
            "answer": f"현재 원장의 견적과 자재 부족을 기준으로 {len(quotes)}개 선택지를 조회했습니다. 미확인 운임·총액은 확정하지 않았으며 구매 초안 생성·승인·발주·공급사 송신은 수행하지 않았습니다.",
            "sections": sections, "meta": deepcopy(data["meta"]), "model_calls": 0, "ledger_mutations": 0,
            "steps": ["현재 원장 견적·부족 조회", "MOQ·주문배수와 유효성 비교", "미확인 비용·송신 상태 유지"],
            "evidence_node_ids": [], "evidence_edge_ids": [], "graph_path": [], "evidence_refs": [],
            "procurement_review": deepcopy(data)}


class ProcurementReview:
    def run(self, request: SpecialistInput, port: ToolPort, context: ToolContext) -> ToolResult:
        request = SpecialistInput.model_validate(request)
        if request.ledger_version != context.ledger_version:
            raise ServiceError("AGENT_SNAPSHOT_CHANGED", 409)
        result = port.call("procurement.options", {"ledger_version": request.ledger_version})
        meta = result.data.get("meta", {})
        if (result.mutating or result.tool_name != "procurement.options" or result.snapshot != context
                or meta.get("company_id") != context.company_id or meta.get("snapshot_id") != context.read_snapshot_id
                or meta.get("ledger_version") != context.ledger_version or meta.get("projection_version") != context.projection_version
                or result.data.get("ledger_mutations") != 0 or result.data.get("model_calls") != 0):
            raise ServiceError("AGENT_RESULT_INVALID", 409)
        return result.model_copy(update={"data": procurement_answer(result.data)})
