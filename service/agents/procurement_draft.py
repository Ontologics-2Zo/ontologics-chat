"""Write agent for one purchase-request draft, with a human approval before the write (T07/T14).

The agent only proposes a draft from the current quote options (a read), waits for
the requesting user's explicit approval, then saves exactly that draft through the
shared write tool and reads it back to reconcile. Saving a draft is not a business
approval: another account still reviews, approves and posts it. Nothing is sent to
a supplier and no ledger version changes here. No model is called.
"""

from copy import deepcopy
import re
import unicodedata

from service.contracts import content_hash
from service.errors import ServiceError


_DRAFT_WORDS = ("초안", "만들어", "만들자", "작성", "올려", "등록해", "저장해", "넣어")
_REQUEST_WORDS = ("구매요청", "구매요청서", "구매신청", "pr초안", "pr만들", "pr작성")


def draft_request(question):
    """{'supplier_id': 'SUP-A' | None} when the question asks for a purchase-request draft."""
    text = unicodedata.normalize("NFKC", question or "").casefold()
    compact = re.sub(r"\s+", "", text)
    if not any(word in compact for word in _REQUEST_WORDS) or not any(word in compact for word in _DRAFT_WORDS):
        return None
    if any(word in compact for word in ("발주해", "송신", "보내줘", "확정해", "승인해", "반영해")):
        return {"supplier_id": None, "forbidden": True}
    supplier = None
    match = re.search(r"sup-([a-z0-9]+)", compact) or re.search(r"(?:공급사|견적)([a-z])(?![a-z0-9])", compact)
    if match:
        supplier = "SUP-" + match.group(1).upper()
    return {"supplier_id": supplier, "forbidden": False}


def _check_read(result, context, tool):
    meta = result.data.get("meta", {})
    if (result.mutating or result.tool_name != tool or result.snapshot != context
            or meta.get("company_id") != context.company_id or meta.get("snapshot_id") != context.read_snapshot_id
            or meta.get("ledger_version") != context.ledger_version
            or result.data.get("ledger_mutations") != 0 or result.data.get("model_calls") != 0):
        raise ServiceError("AGENT_RESULT_INVALID", 409)


def _result(status, answer, *, conclusion, sections=(), meta=None, extra=None):
    return {"status": status, "answer": answer, "conclusion": conclusion, "intent_label": "구매 요청 초안 작성",
            "sections": list(sections), "meta": deepcopy(meta or {}), "model_calls": 0, "ledger_mutations": 0,
            "steps": [], "evidence_node_ids": [], "evidence_edge_ids": [], "graph_path": [], "evidence_refs": [],
            **(extra or {})}


def propose(question, port, context, principal_permissions):
    """Read the quote options and return a proposal, a clarification or a refusal."""
    request = draft_request(question)
    if request is None:
        return _result("NEEDS_CLARIFICATION", "구매 요청 초안을 만들 견적을 지정해 주세요. 예: '견적 A로 구매 요청 초안 만들어줘'.",
                       conclusion="요청 확인 필요")
    if request["forbidden"]:
        return _result("UNKNOWN", "Agent는 구매 요청 초안 저장까지만 합니다. 승인·확정·발주·공급사 송신은 각 담당 계정이 화면에서 직접 합니다.",
                       conclusion="쓰기 범위 밖 · 저장 없음")
    if "document.approve" in principal_permissions:
        return _result("UNKNOWN", "승인 권한이 있는 계정이 초안을 작성하면 승인할 다른 계정이 없습니다. 작성자 계정으로 요청해 주세요.",
                       conclusion="작성자 계정 필요 · 저장 없음")
    if "document.create" not in principal_permissions:
        return _result("UNKNOWN", "이 계정에는 문서 작성 권한이 없어 초안을 저장할 수 없습니다.", conclusion="작성 권한 없음 · 저장 없음")
    result = port.call("procurement.options", {"ledger_version": context.ledger_version})
    _check_read(result, context, "procurement.options")
    data, meta = result.data, result.data.get("meta", {})
    usable = [quote for quote in data.get("quotes", []) if quote.get("material_basis") == "planned"
              and quote.get("calculation") and quote["calculation"].get("recommended_qty")]
    if not usable:
        return _result("UNKNOWN", "현재 원장에 계획 투입 기준 부족을 채울 견적이 없어 초안을 제안하지 않았습니다.",
                       conclusion="제안할 견적 없음 · 저장 없음", meta=meta)
    choices = ", ".join(f"{quote['supplier_id']} {quote['calculation']['recommended_qty']}개"
                        f"({ {'MET': '충족', 'NOT_MET': '공급한도 미충족'}.get(quote['calculation'].get('quantity_condition'), '한도 미확인') })"
                        for quote in usable)
    if request["supplier_id"] is None:
        # No supplier named: propose the server's fixed-rule recommendation.
        # The user still approves the exact draft before anything is saved.
        picked = [quote for quote in usable if quote.get("recommended")]
    else:
        picked = [quote for quote in usable if quote["supplier_id"] == request["supplier_id"]]
    if len(picked) != 1:
        return _result("NEEDS_CLARIFICATION", f"{'수량 조건을 충족하는 추천 견적이 없습니다. ' if request['supplier_id'] is None else ''}어느 견적으로 초안을 만들지 지정해 주세요. 현재 견적: {choices}. 예: '견적 A로 구매 요청 초안 만들어줘'.",
                       conclusion="견적 지정 필요 · 저장 없음", meta=meta)
    quote = picked[0]
    calculation = quote["calculation"]
    if quote.get("expired"):
        return _result("UNKNOWN", f"{quote['supplier_id']} 견적은 유효기간이 지나 초안을 만들지 않습니다.", conclusion="만료 견적 · 저장 없음", meta=meta)
    if calculation.get("quantity_condition") == "NOT_MET":
        return _result("UNKNOWN", f"{quote['supplier_id']} 견적은 주문량 {calculation['recommended_qty']}개가 공급한도 {calculation.get('supply_limit_qty')}개를 넘어 초안을 만들지 않습니다. 다른 견적을 지정해 주세요.",
                       conclusion="공급한도 미충족 · 저장 없음", meta=meta)
    content = {"kind": "PR", "quote_id": quote["quote_id"], "quantity": calculation["recommended_qty"], "material_basis": "planned",
               "acknowledge_partial_bom": bool(quote.get("partial_bom")),
               "note": f"Agent 제안 · 사용자 승인 후 저장 · {quote['supplier_id']} 견적 {quote.get('quote_ref') or ''} 계획 투입 기준 부족 {calculation['shortage_qty']}개 → MOQ·배수 적용 {calculation['recommended_qty']}개"}
    proposal = {"content": content, "ledger_version": context.ledger_version, "snapshot_id": context.read_snapshot_id}
    display = [["견적", f"{quote['supplier_id']} · {quote.get('quote_ref') or quote['quote_id'][:8]}{' · 추천' if quote.get('recommended') else ''}"],
               ["품목", quote["item_id"]],
               ["부족 → 주문 수량", f"{calculation['shortage_qty']} → {calculation['recommended_qty']}개 (MOQ {quote.get('moq')} · 배수 {quote.get('order_multiple')})"],
               ["공급한도", "충족" if calculation.get("quantity_condition") == "MET" else "미확인"],
               ["총액", f"{calculation['total']} {calculation.get('currency') or ''}" if calculation.get("total") is not None else "운임 미확인 · 총액 미확인"],
               ["기준 원장", f"v{context.ledger_version}"]]
    if quote.get("recommended") and quote.get("recommendation_reason"):
        display.insert(1, ["추천 이유", quote["recommendation_reason"]])
    lead = "추천 견적인 " if request["supplier_id"] is None else ""
    return _result("PROPOSED", f"{lead}{quote['supplier_id']} 견적으로 {quote['item_id']} {calculation['recommended_qty']}개 구매 요청 초안을 제안합니다. 승인하면 초안만 저장합니다.",
                   conclusion="초안 저장 승인 대기", meta=meta,
                   extra={"proposal": proposal, "proposal_hash": content_hash(proposal), "proposal_rows": display})


def idempotency_key(principal, proposal_hash):
    """Same user, same ledger version and same draft → same key, across retries and executions."""
    return "agent-draft-" + content_hash({"actor": principal.actor_id, "company": principal.company_id, "proposal": proposal_hash})[:48]


def write(principal, port, context, proposed):
    proposal = proposed["proposal"]
    if proposal["ledger_version"] != context.ledger_version or content_hash(proposal) != proposed["proposal_hash"]:
        raise ServiceError("AGENT_PROPOSAL_STALE", 409)
    result = port.call("procurement.create", {"expected_ledger_version": context.ledger_version,
                                              "content": deepcopy(proposal["content"]),
                                              "idempotency_key": idempotency_key(principal, proposed["proposal_hash"])})
    document = result.data
    if not result.mutating or result.tool_name != "procurement.create" or not isinstance(document, dict) or not document.get("id"):
        raise ServiceError("TOOL_OUTCOME_UNKNOWN", 409)
    return {"document_id": str(document["id"]), "version": document.get("version"), "payload_hash": document.get("payload_hash")}


def reconcile(principal, port, context, proposed, receipt):
    """Read the saved document back; every field the user approved must match."""
    result = port.call("procurement.document", {"document_id": receipt["document_id"]})
    if result.mutating or result.tool_name != "procurement.document" or result.snapshot != context:
        raise ServiceError("AGENT_RESULT_INVALID", 409)
    document = result.data
    approved = proposed["proposal"]["content"]
    content = document.get("content") or {}
    checks = {
        "document": str(document.get("id")) == receipt["document_id"] and document.get("kind") == "PR",
        "author": document.get("created_by") == principal.actor_id,
        "content": {key: content.get(key) for key in approved} == approved,
        "basis_version": content.get("basis_ledger_version") == context.ledger_version,
        "hash": document.get("payload_hash") == receipt["payload_hash"],
        "not_posted": document.get("status") in {"DRAFT", "REVIEWED", "APPROVED"},
    }
    if not all(checks.values()):
        raise ServiceError("AGENT_WRITE_RECONCILIATION_FAILED", 409)
    label = "PR-" + re.sub(r"[^0-9a-z]", "", receipt["document_id"], flags=re.IGNORECASE)[:4].upper()
    rows = [{"label": label, "value": f"구매 요청 초안 · {approved['quantity']}개 · {document.get('status')}",
             "detail": "작성자는 요청한 계정입니다. 다른 계정의 검토·승인·확정 전에는 원장에 반영되지 않습니다."}] + [
            {"label": name, "value": value} for name, value in proposed.get("proposal_rows", [])]
    return _result("ANSWERED", f"승인한 내용 그대로 구매 요청 초안 {label}({approved['quantity']}개)를 저장했고, 저장된 문서를 다시 읽어 내용·작성자·기준 원장 v{context.ledger_version}이 일치함을 확인했습니다. "
                   "원장은 바뀌지 않았으며 검토자 계정의 승인·확정이 필요합니다.",
                   conclusion="초안 저장 · 대사 일치 · 원장 미반영", meta=proposed.get("meta"),
                   sections=[{"id": "agent-write", "title": "Agent 쓰기 결과 · 대사", "origin": "development_workflow", "status": "ANSWERED", "items": rows}],
                   extra={"document_writes": [{**receipt, "kind": "PR", "status": document.get("status")}],
                          "reconciliation": {"status": "MATCHED", "checks": checks}})


def declined(proposed):
    return _result("ANSWERED", "승인하지 않아 초안을 저장하지 않았습니다. 원장과 문서는 그대로입니다.",
                   conclusion="사용자 취소 · 저장 없음", meta=proposed.get("meta"), extra={"document_writes": []})
