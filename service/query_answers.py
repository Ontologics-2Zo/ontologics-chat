"""Read-only CQ analysis: interpreted requests, snapshot tools, then explanation.

No private reference answers, future scenario events, model calls or database
writes are used here. Interpretations choose capabilities; their numbers come
from the same authorized snapshot and the shared material calculation tool.
"""

from copy import deepcopy
from decimal import Decimal

from service.materials import requirements_for_snapshot
from service.procurement import quote_calculation
from service.reservations import reservation_impacts
from service.query_contracts import (PrerequisiteCounts, QueryAnswerBlock, QueryBomReference, QueryQuoteCard,
                                     QueryNextAction, QueryPrerequisite)
from service.query_intents import interpret_question


RULE_VERSION = "cq-read-analysis/2"
LABELS = {"trace": "LOT 추적", "change": "수량 변경 원인", "sourcing": "부족 자재 확보안 비교", "fulfillment": "납품 이행", "production": "추가 생산·투입",
          "materials": "자재 소요·부족", "inventory": "가용 재고",
          "delivery": "고객 도착·납기 판단", "ontology": "전체 온톨로지"}
SYNTHETIC = "synthetic_opening_balance"
DEMO = "conditional_demo"
# DEC-H47 branches explain existing material CQs; no new official CQ IDs.
CQ_REFS = {"trace": ("DEMO-CQ-03",), "change": ("B-CQ01", "B-CQ04"), "sourcing": ("CQ09", "B-CQ01", "B-CQ04"),
           "fulfillment": ("DEMO-CQ-01", "B-CQ02"), "production": ("DEMO-CQ-02", "B-CQ03"),
           "materials": ("CQ09", "B-CQ04"), "inventory": ("B-CQ01", "B-CQ04"),
           "delivery": ("DEMO-CQ-01", "B-CQ01", "B-CQ04"), "ontology": ()}


def number(value):
    if value is None:
        return "미확인"
    value = format(Decimal(str(value)), "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


def percentage(value):
    """Render the exact ratio as percent without Decimal context rounding."""
    sign, digits, exponent = Decimal(str(value)).as_tuple()
    return number(Decimal((sign, digits, exponent + 2)))


def item(label, value, detail="", origin=None):
    result = {"label": label, "value": str(value), "detail": detail}
    if origin:
        result["origin"] = origin
    return result


def section(identifier, title, origin, items, status="ANSWERED"):
    return {"id": identifier, "title": title, "origin": origin,
            "status": status, "items": items}


def inventory_origin(rows):
    origins = set()
    for row in rows:
        origin = row.get("inventory_origin")
        if origin == "DEVELOPMENT_RECEIPT":
            origins.add("development_workflow")
        elif origin == "OPENING_AGGREGATE_PLUS_DEVELOPMENT_RECEIPTS":
            origins.update((SYNTHETIC, "development_workflow"))
        else:
            origins.add(SYNTHETIC)
        if row.get("reservation_ids"):
            origins.add("development_workflow")
    return next(iter(origins)) if len(origins) == 1 else "mixed_sources" if origins else "unknown"


def _prerequisite(view, req, selected):
    """Locate the first unsupported BOM prerequisite, across the full BOM.

    A selected item with no mapping is not a globally unreviewed BOM. Likewise,
    reviewed exclusions do not mean that the preserved source is missing. The
    current registration service cannot certify manufacturing technical review.
    """
    counts = PrerequisiteCounts(source_rows=req["row_count"], applicable_rows=req["applicable_count"],
        unresolved_rows=req["unresolved_count"], calculation_items=len(req["items"]))
    bom = None
    if req["bom_id"] is None:
        status, reason = "BLOCKED", "REGISTERED_BOM_MISSING"
    else:
        active = view["bom"]
        bom = QueryBomReference(document_id=active["bom_id"], version=active["bom_version"],
                                payload_hash=active["payload_hash"])
        if req["applicable_count"] == 0 and req["unresolved_count"] > 0:
            status, reason = "BLOCKED", "NO_REVIEWED_APPLICABLE_ROWS"
        elif req["unresolved_count"] or any(row.get("issues") for row in req["rows"]) or any(
                "ITEM_UNIT_CONFLICT" in row.get("issues", []) for row in req["items"]):
            status, reason = "PARTIAL", "APPLICATION_REVIEW_INCOMPLETE"
        elif req["applicable_count"] == 0:
            status, reason = "UNKNOWN", "NO_APPLICABLE_COMPONENTS"
        else:
            status, reason = "UNKNOWN", "TECHNICAL_REVIEW_UNCONFIRMED"
    action = "REGISTER_BOM" if bom is None else "REVIEW_BOM_APPLICATION"
    return QueryPrerequisite(status=status, reason=reason, configuration_id=view["summary"]["configuration_id"],
        bom=bom, counts=counts, selected_item_ids=list(selected),
        selected_calculation_items=sum(row["item_id"] in selected for row in req["items"]) if selected else None,
        next_action=QueryNextAction(type=action, label="BOM 원본 등록" if bom is None else "BOM 적용 검토"))


def _prerequisite_text(prerequisite):
    config = prerequisite.configuration_id
    reason = prerequisite.reason
    if reason == "REGISTERED_BOM_MISSING":
        return (f"제품 구성 {config}에 반영된 BOM이 없어 자재 소요·부족은 미확인입니다.",
                "BOM 원본을 등록하고 검토·승인·원장 반영을 마친 뒤 다시 조회해 주세요.")
    if reason == "NO_REVIEWED_APPLICABLE_ROWS":
        return (f"제품 구성 {config}의 BOM 원본은 등록됐지만, 검토된 적용 행이 없어 자재 소요·부족을 계산할 수 없습니다.",
                "기존 BOM의 적용 대상·사내 품목 연결·수량·단위를 먼저 검토해 주세요.")
    if reason == "APPLICATION_REVIEW_INCOMPLETE":
        return (f"제품 구성 {config}의 BOM 적용 검토가 일부만 되어 전체 자재 소요·부족은 아직 미확인입니다.",
                "기존 BOM의 남은 적용 판단·품목 연결·수량·단위를 먼저 검토해 주세요.")
    if reason == "NO_APPLICABLE_COMPONENTS":
        return (f"제품 구성 {config}의 BOM 원본은 등록됐지만, 현재 검토 결과에 생산 적용 행이 없습니다.",
                "비실장·설정 판단과 이 구성의 실제 적용 대상을 확인해 주세요.")
    return (f"제품 구성 {config}의 검토된 BOM으로 조건부 계산은 가능하지만 제조 기술검수는 확인되지 않았습니다.",
            "등록 근거와 실제 제조 적용 조건을 먼저 확인해 주세요.")


def _prerequisite_steps(view, prerequisite):
    if prerequisite.bom is None:
        next_step = "BOM 원본 접수·검토·승인·반영 후 소요를 다시 조회해야 합니다."
    elif prerequisite.selected_item_ids and prerequisite.selected_calculation_items == 0:
        next_step = "선택 품목이 적용 BOM과 연결되지 않아 해당 소요·부족 계산을 보류했습니다."
    elif prerequisite.counts.calculation_items == 0:
        next_step = "계산 가능한 적용 품목이 없어 자재 소요·부족 계산을 보류했습니다."
    else:
        next_step = "계산 가능한 적용 품목의 수량과 제조·납기 미확인 범위를 구분했습니다."
    return [f"납품 회차 {view['summary']['delivery_round_id']}와 조회 버전을 확인했습니다.",
            f"회차의 제품 구성 {prerequisite.configuration_id}를 확인했습니다.",
            "연결된 반영 BOM이 없어 소요·부족 계산을 보류했습니다." if prerequisite.bom is None else
            "BOM 원본 등록과 적용 행·품목 연결 검토 상태를 구분했습니다.",
            next_step]


def _ratio_text(numerator, denominator):
    value = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"))
    return format(value.normalize(), "f")


def _designators(requirements, row):
    by_id = {entry["row_id"]: entry for entry in requirements["rows"]}
    names = [name for row_id in row["row_ids"] for name in (by_id.get(row_id, {}).get("designators") or [])]
    if not names:
        return "위치 미확인"
    if len(names) > 3 and all(name[:1] == names[0][:1] for name in names):
        # R10, R11, R12, R13 -> R10–R13 only when the run is contiguous.
        digits = [name[1:] for name in names]
        if all(d.isdigit() for d in digits) and [int(d) for d in digits] == list(range(int(digits[0]), int(digits[0]) + len(digits))):
            return f"{names[0]}–{names[-1]}"
    return "·".join(names)


def _quote_cards(view, item, planned_shortage):
    cards = []
    for row in _quote_rows(view, item["item_id"], item["unit"]):
        entry = _quote_view(row, planned_shortage)
        c = entry["calculation"]
        cards.append(QueryQuoteCard(
            quote_id=entry["quote_id"], quote_ref=entry["quote_ref"], supplier_id=entry["supplier_id"],
            material_basis="planned", quantity_condition=c["quantity_condition"],
            recommended_qty=c.get("recommended_qty"), margin_qty=c.get("margin_qty"),
            supply_limit_qty=entry["supply_limit_qty"], moq=entry["moq"], order_multiple=entry["order_multiple"],
            unit_price=None if entry["unit_price"] is None else str(entry["unit_price"]), currency=entry["currency"],
            freight=None if entry["freight"] is None else str(entry["freight"]), total=c.get("total"),
            lead_business_days=entry["lead_business_days"], valid_until=entry["valid_until"],
            reason=_quote_sentence(entry, item["unit"])))
    return cards


def _delivery_blocks(view, plan, sections, prerequisite, requirements):
    """Delivery answer in the shared MK-S2 order, filled only from this query.

    Cards: what the remaining quantity means → defect/yield basis → material
    shortage → schedule (assumption, no ledger evidence) → quotes for a human
    choice → verdict. All cards belong to the same ledger.query; their labels
    name the data behind each card, not separate tool executions.
    """
    s, basis = view["summary"], requirements["basis"]
    remaining, production = number(s["promise_remaining_qty"]), number(s["production_remaining_qty"])
    due = s.get("arrival_due_date")
    blocks = [QueryAnswerBlock(id="delivery_balance", title=f"“{remaining}대”가 무엇인지 확인", kind="normal", paragraphs=[
        f"{remaining}대는 남은 생산이 아니라 남은 약속(고객 미도착)입니다. 회차 {s['delivery_round_id']}, 도착기한 {due or '미확인'}.",
        f"{remaining}대 중 {number(s['already_produced_qty'])}대는 이미 생산(입고 대기 {number(s['good_not_received_qty'])} · 창고 {number(s['warehouse_remaining_qty'])} · 운송 {number(s['transit_qty'])})했고, 더 생산할 수량은 {production}대입니다."])]
    judged = (s.get("good_qty") or 0) + (s.get("defect_qty") or 0)
    yield_text = percentage(basis["plan_yield"])
    production_lines = ([f"판정된 {number(judged)}대 중 불량 {number(s['defect_qty'])}대 → {_ratio_text(s['defect_qty'], judged)}% (판정대기 {number(s.get('pending_judgement_qty') or 0)}대 제외)."]
                        if judged else ["판정 실적이 없어 불량률을 계산하지 않았습니다."])
    production_lines.append(f"양품 {number(basis['production_remaining_qty'])}대에 계획 수율 {yield_text}%를 가정하면 {number(basis['planned_input_qty'])}대 투입이 필요합니다. 가정 계산이며 양품을 보장하지 않습니다.")
    blocks.append(QueryAnswerBlock(id="defect_basis", title="불량률 계산", tool="원장 계산 · 생산실적", kind="normal", paragraphs=production_lines))

    items = [row for row in requirements["items"] if not prerequisite.selected_item_ids or row["item_id"] in prerequisite.selected_item_ids]
    shortage_items = [row for row in items if row.get("planned_shortage_qty") not in (None, "0")]
    focus = max(shortage_items, key=lambda row: Decimal(row["planned_shortage_qty"])) if shortage_items else None
    missing = list(view.get("delivery_unknowns") or [])
    if prerequisite.bom is None:
        blocks.append(QueryAnswerBlock(id="bom_missing", title="BOM 전개 불가", kind="attention", action="REGISTER_BOM", paragraphs=[
            f"{s.get('production_job_id') or '생산 작업'}의 조립 구성 {prerequisite.configuration_id}에 반영된 BOM이 없어 BOM 라인이 없습니다.",
            "어떤 부품이 몇 개 필요한지 근거가 없어 부족을 추정하지 않고 여기서 멈춥니다. BOM 원본을 등록하고 검토·승인·반영한 뒤 다시 물어보세요."]))
        verdict_title = "BOM 미등록 · 납기 판단 보류"
        verdict_text = "자재 소요를 계산할 근거가 없어 도착기한 충족 여부를 판단하지 않습니다."
    elif items:
        rows, alerts = [], []
        # Short items first, largest planned shortage first; the rest keep their order.
        ordered = sorted(items, key=lambda row: -Decimal(row["planned_shortage_qty"] or "0"))
        for index, row in enumerate(ordered):
            short = row.get("planned_shortage_qty")
            rows.append([_designators(requirements, row), row["item_id"], number(row["planned_required_qty"]),
                         number(row["available_qty"]), "미확인" if short is None else ("—" if short == "0" else number(short))])
            if short not in (None, "0"):
                alerts.append([index, 4])
        notes = []
        if focus:
            reserved = sum(entry.get("reserved_other_qty") or 0 for entry in view["inventory"] if entry["item_id"] == focus["item_id"])
            notes.append(f"{focus['item_id']}은 다른 수요 예약 {number(reserved)}개를 빼면 가용 {number(focus['available_qty'])}개 → {number(focus['planned_shortage_qty'])}개 부족 (수율 미반영 시 {number(focus['shortage_qty'])}개).")
        else:
            notes.append("계산한 품목에는 계획 투입 기준 부족이 없습니다.")
        substitutes = [row for row in view.get("substitutes") or [] if focus and row.get("original_item_id") == focus["item_id"]
                       and row.get("status") != "APPROVED"]
        if substitutes:
            notes.append(f"대체 후보 {', '.join(row['candidate_item_id'] for row in substitutes)}은 검토 전이라 확보량에서 제외했습니다.")
        stock_origin = inventory_origin([entry for entry in view["inventory"] if any(entry["item_id"] == row["item_id"] for row in items)])
        stock_label = {SYNTHETIC: "합성 기초 원장", "development_workflow": "개발 원장 사건",
                       "mixed_sources": "합성 기초 원장과 개발 원장 사건(입하·검사·예약)", "unknown": "출처 미확인"}[stock_origin]
        mapping = "시연 가정" if all(row["conditional"] for row in items) else "검토된 적용 판단과 시연 가정"
        scope = f"원문 {number(requirements['row_count'])}행 중 적용 판단 {number(requirements['applicable_count'])}행만 계산했습니다."
        if requirements["unresolved_count"]:
            scope += f" 적용 미확인 {number(requirements['unresolved_count'])}행은 부족 0으로 보지 않습니다."
        notes.append(f"{scope} 계획 투입 {number(basis['planned_input_qty'])}대 기준이며 적용 매핑·수량은 {mapping}, 재고는 {stock_label}입니다.")
        change = {row["id"]: row for row in sections}.get("material_change")
        if change:
            entries = {row["label"]: row for row in change["items"]}
            event = entries["변경 사건"]
            available = next(row for label, row in entries.items() if label.endswith("가용"))
            notes.append(f"자재 조건 변경: {event['value']} 이후 {available['label']} {available['value']}, 계획 투입 기준 부족 {entries['계획 투입 기준 부족']['value']}입니다. 기존 확보안은 재검토가 필요하며 공급사·초안은 자동으로 바뀌지 않습니다.")
        blocks.append(QueryAnswerBlock(id="material_shortage", title="부품 부족 예측", tool="원장 계산 · BOM 전개", kind="normal",
            paragraphs=notes, table={"columns": ["위치", "품목", "필요", "가용", "부족"], "rows": rows, "alert_cells": alerts}))
        # Backward from the due date, one row per stage. Durations without
        # ledger evidence stay "근거 없음"; nothing is filled in.
        steps = [["고객 도착", f"도착기한 {due}" if due else "도착기한", "원장 확인" if due else "미확인"],
                 ["운송", "운송 기간", "근거 없음"], ["출하 준비", "출하 준비 기간", "근거 없음"],
                 [f"생산 {number(basis['planned_input_qty'])}대", "생산능력·작업 달력", "근거 없음"],
                 ["수입검사", "검사 기간", "근거 없음"], ["부품 입하 필요일", "위 기간의 합", "계산 불가"]]
        blocks.append(QueryAnswerBlock(id="schedule_backward", title="일정 역산", tool="시연 가정 · 일정", kind="assumption",
            table={"columns": ["단계 (도착부터 거꾸로)", "필요한 근거", "상태"], "rows": steps,
                   "alert_cells": [[r, 2] for r, row in enumerate(steps) if row[2] != "원장 확인"]},
            paragraphs=["단계별 기간 근거가 원장에 없어 부품 입하 필요일을 거꾸로 계산하지 않았습니다. 근거가 들어오면 같은 순서로 계산합니다."]))
        if focus:
            quotes = _quote_cards(view, focus, focus["planned_shortage_qty"])
            if quotes:
                blocks.append(QueryAnswerBlock(id="supply_candidates", title="공급 후보 비교", tool="원장 조회 · 견적 → 공급사", kind="normal", quotes=quotes, paragraphs=[
                    f"{focus['item_id']}에 연결된 유효 견적 {len(quotes)}건입니다. 가장 싼 곳을 자동으로 고르지 않습니다. 아래에서 사람이 선택하세요.",
                    "수량 조건만 판정했습니다. 납기·비용·공급 확약은 별도이며 공급사에 보내지 않습니다."]))
            verdict_title = f"자재 부족 {number(focus['planned_shortage_qty'])}개 · 납기 판단 보류"
            verdict_text = f"{focus['item_id']}이 {number(focus['planned_shortage_qty'])}개 부족합니다. 확보 경로를 정하고 생산·검사·출하·운송 근거가 원장에 들어오기 전에는 도착기한 충족 여부를 판단하지 않습니다."
        else:
            verdict_title = "계산 품목 부족 없음 · 납기 판단 보류"
            verdict_text = "계산한 품목은 부족이 없지만 적용 미확인 행과 생산·검사·출하·운송 근거가 없어 도착기한 충족 여부를 판단하지 않습니다."
    else:
        first, action = _prerequisite_text(prerequisite)
        blocks.append(QueryAnswerBlock(id="bom_next_action", title=prerequisite.next_action.label + " 필요", kind="attention",
                                       action=prerequisite.next_action.type, paragraphs=[first, action]))
        verdict_title = "자재 소요 미확인 · 납기 판단 보류"
        verdict_text = "계산 가능한 적용 품목이 없어 도착기한 충족 여부를 판단하지 않습니다."
    bullets = []
    if prerequisite.bom is not None and requirements["unresolved_count"]:
        bullets.append(f"BOM 적용 미확인 {number(requirements['unresolved_count'])}행")
    bullets += ["생산능력·작업 달력 근거 없음", "수입검사·출하 준비 기간 근거 없음", "운송·고객 수령 근거 없음",
                "견적은 수량 조건만 확인 · 공급 확약 없음"] + missing
    blocks.append(QueryAnswerBlock(id="verdict", title=verdict_title, tool="판정", kind="verdict",
                                   paragraphs=[verdict_text, "조건부 판단입니다. 새 납기 약속으로 저장하지 않습니다."], bullets=bullets))
    return [block.model_dump(mode="json") for block in blocks]


def _result(view, plan, *, answer, status="ANSWERED", sections=(), kinds=(),
            include_bom=False, selected_items=(), selected_lot=None, conclusion=None, offer_documents=None):
    """Bind claims to the current BOM only, while preserving graph history."""
    active = view.get("bom")
    selected_items = set(selected_items)
    ids = set()
    procurement = view.get("procurement", {})
    # Material shortages use all stock for the selected item, even when the
    # question starts from a LOT. Their evidence must cover that same scope.
    lot_scope = selected_lot if not set(plan.intents) & {"materials", "delivery"} else None
    receipts = [row for row in procurement.get("receipts", [])
                if (not selected_items or row["item_id"] in selected_items)
                and (not lot_scope or row["lot_code"] == lot_scope)]
    receipt_ids = {row["receipt_id"] for row in receipts}
    development_docs = receipt_ids | {row["id"] for row in procurement.get("documents", [])
        if row["kind"] == "IQC" and row["content"]["resolved"]["parent"]["id"] in receipt_ids}
    if offer_documents is not None:
        development_docs |= set(offer_documents)
    if receipts and set(plan.intents) & {"materials", "inventory", "delivery"}:
        kinds = set(kinds) | {"PartsReceiptLine", "InspectionResult", "DocumentVersion", "ApprovalGrant"}
    for node in view["entities"]:
        if node["type"] not in kinds:
            continue
        evidence = node.get("evidence", {})
        if evidence.get("record_type") == "development_procurement" and evidence.get("document_id") not in development_docs:
            continue
        # A sourcing comparison cites only the offers it compared.
        if offer_documents is not None and node["type"] == "SupplierOffer" and evidence.get("document_id") not in development_docs:
            continue
        if evidence.get("record_type") == "bom_registration":
            if not include_bom or not active or (evidence.get("bom_id"), evidence.get("bom_version")) != (active["bom_id"], active["bom_version"]):
                continue
        if selected_items and node["type"] == "InternalItem" and node["display_id"] not in selected_items:
            continue
        if node["type"] == "PartLot":
            relevant_lots = {row.get("lot_id") for row in view["inventory"] if not selected_items or row["item_id"] in selected_items}
            relevant_lots.update(row["lot_code"] for row in receipts)
            if node["display_id"] not in relevant_lots or (lot_scope and node["display_id"] != lot_scope):
                continue
        ids.add(node["id"])
    edges = [edge for edge in view["relationships"] if edge["source"] in ids and edge["target"] in ids]
    ready = view["meta"]["projection_state"] == "READY"
    refs = []
    if kinds:
        refs.append({"source_sha256": view["meta"]["source_sha256"], "record_type": "opening_balance",
                     "origin": SYNTHETIC, "snapshot_id": view["meta"]["snapshot_id"]})
    if include_bom and active:
        refs.append({"source_sha256": active["source"]["sha256"], "record_type": "bom_registration",
                     "origin": "actual_bom", "bom_id": active["bom_id"], "bom_version": active["bom_version"],
                     "payload_hash": active["payload_hash"], "snapshot_id": view["meta"]["snapshot_id"]})
    for node in view["entities"]:
        evidence = node.get("evidence", {})
        if node["id"] in ids and evidence.get("record_type") in {"development_procurement", "inventory_reservation"}:
            ref = {**deepcopy(evidence), "origin": "development_workflow", "snapshot_id": view["meta"]["snapshot_id"]}
            if ref not in refs:
                refs.append(ref)
    return {"status": status, "answer": answer, "conclusion": conclusion,
            "intent": list(plan.intents), "intent_label": " · ".join(LABELS[k] for k in plan.intents),
            "sections": list(sections), "meta": deepcopy(view["meta"]),
            "steps": ["회사·회차·선택 대상과 조회 버전을 확인했습니다.",
                      "같은 스냅샷의 수량과 등록 BOM·재고 도구로 계산했습니다.",
                      "원문·합성 기초 원장·가정과 미확인 조건을 나누어 답했습니다."] if kinds else [],
            "graph_path": [[edge["source"], edge["target"]] for edge in edges] if ready else [],
            "evidence_edge_ids": [edge["id"] for edge in edges] if ready else [],
            "evidence_node_ids": sorted(ids) if ready else [], "evidence_refs": refs,
            "model_calls": 0, "execution_kind": "deterministic_domain_query",
            "query_rule_version": RULE_VERSION, "ledger_mutations": 0,
            "cq_refs": sorted({ref for intent in plan.intents for ref in CQ_REFS[intent]}),
            "is_what_if": plan.is_what_if,
            "support_scope": "bounded_readonly_CQ; full_natural_language_agent_not_implemented"}


def _clarify(view, plan, text):
    return _result(view, plan, status="NEEDS_CLARIFICATION", answer=text,
                   conclusion="질문 범위 확인 필요",
                   sections=[section("clarification", "확인이 필요한 질문 범위", "unknown",
                                     [item("현재 조회 회차", view["summary"]["delivery_round_id"]),
                                      item("확인할 내용", text)], "NEEDS_CLARIFICATION")])


def _scope_error(plan, view, context_entity_id, ontology):
    entities = view["entities"]
    known = {node["display_id"].casefold() for node in entities}
    known.update(str(view["summary"].get(k, "")).casefold() for k in
                 ("delivery_round_id", "production_job_id", "configuration_id"))
    # The selected round ORD-xxxxx-NN belongs to order ORD-xxxxx; that order ID
    # alone selects this same round, never another round or order.
    round_id = str(view["summary"].get("delivery_round_id", ""))
    if "-" in round_id and round_id.upper().startswith("ORD-"):
        known.add(round_id.rsplit("-", 1)[0].casefold())
    if any(ref.casefold() not in known for ref in plan.target_references):
        return "질문의 대상이 현재 조회 회차·작업·구성과 일치하지 않습니다. 조회할 대상을 확인해 주세요."
    schema_ids = {node["node_id"] for node in ontology.get("graph", {}).get("data", {}).get("nodes", [])}
    schema_selection = set(plan.intents) == {"ontology"} and context_entity_id in schema_ids
    if context_entity_id and not schema_selection and not any(node["id"] == context_entity_id for node in entities):
        return "선택한 객체가 현재 회사·원장 스냅샷에 없습니다. 최신 원장을 조회하고 대상을 다시 선택해 주세요."
    valid_items = {node["display_id"].casefold() for node in entities if node["type"] == "InternalItem"}
    if any(ref.casefold() not in valid_items for ref in plan.item_ids):
        return "질문의 품목을 현재 원장에서 확인하지 못했습니다. 사내 품목코드와 조회 회차를 확인해 주세요."
    s = view["summary"]
    current = {Decimal(str(s[k])) for k in ("promised_qty", "good_qty", "fg_received_qty", "shipped_qty",
               "arrived_qty", "promise_remaining_qty", "production_remaining_qty", "already_produced_qty")}
    basis = view["material_requirements"]["basis"]
    if plan.target_good_qty is not None and plan.target_good_qty > 10**12:
        return "가정 양품 목표가 현재 계산 도구의 수량 범위를 초과합니다. 1조 이하의 수량을 지정해 주세요."
    target = plan.target_good_qty if plan.target_good_qty is not None else basis["production_remaining_qty"]
    rate = plan.yield_rate if plan.yield_rate is not None else Decimal(basis["plan_yield"])
    numerator, denominator = rate.as_integer_ratio()
    planned = (int(target) * denominator + numerator - 1) // numerator
    if planned > 10**12:
        return "목표와 수율로 계산한 투입이 현재 계산 도구의 수량 범위를 초과합니다. 목표·수율을 확인해 주세요."
    current.add(Decimal(str(basis["planned_input_qty"])))
    if set(plan.intents) & {"change", "sourcing"}:
        requirements = view["material_requirements"]
        for row in requirements.get("items", []):
            current.update(Decimal(str(row[k])) for k in ("available_qty", "required_qty", "planned_required_qty",
                           "shortage_qty", "planned_shortage_qty") if row.get(k) is not None)
        for impact in reservation_impacts(requirements, view.get("reservations", [])):
            current.update(Decimal(str(impact[k])) for k in ("quantity", "available_before", "available_after",
                           "shortage_before", "shortage_after", "planned_shortage_before", "planned_shortage_after",
                           "reserved_other_before", "reserved_other_after"))
    for mention in plan.quantity_mentions:
        if mention.role == "target_good" and plan.target_good_qty is not None:
            continue
        if mention.reference:
            field = {"remaining_promise": "promise_remaining_qty"}.get(mention.reference, mention.reference)
            expected = planned if field == "planned_input" else s[field]
            if mention.quantity != Decimal(str(expected)):
                return "질문에 제시한 수량이 해당 원장 항목과 다릅니다. 잔여 약속·양품 실적·추가 양품 목표·계획 투입을 구분해 확인해 주세요."
            continue
        if mention.role == "delivery" and mention.quantity not in {Decimal(str(s['promised_qty'])), Decimal(str(s['promise_remaining_qty']))}:
            return "질문의 납품 수량이 현재 회차의 약속 또는 잔여 약속과 다릅니다. 현재 회차인지 별도 수요인지 확인해 주세요."
        if mention.unit == "행":
            counts = {basis_value for key, basis_value in view["material_requirements"].items() if key.endswith("count") and isinstance(basis_value, int)}
            if mention.quantity in counts:
                continue
        if mention.unit == "품목" and mention.quantity == len(view["material_requirements"]["items"]):
            continue
        if mention.quantity not in current:
            return "질문의 수량이 현재 회차 수량과 다릅니다. 현재 회차 조회인지, 다른 양품 목표를 가정한 계산인지 명시해 주세요."
    return None


def _consistent(summary):
    """Do not publish a plausible explanation if the ledger disagrees with itself."""
    keys = ("promised_qty", "good_qty", "fg_received_qty", "shipped_qty", "arrived_qty")
    values = [Decimal(str(summary[k])) for k in keys]
    if any(not v.is_finite() or v < 0 for v in values) or values != sorted(values, reverse=True):
        return False
    expected = {"promise_remaining_qty": values[0] - values[4],
                "production_remaining_qty": values[0] - values[1],
                "already_produced_qty": values[1] - values[4],
                "good_not_received_qty": values[1] - values[2],
                "warehouse_remaining_qty": values[2] - values[3], "transit_qty": values[3] - values[4]}
    return all(Decimal(str(summary[key])) == value for key, value in expected.items())


def _requirements(view, plan):
    if plan.yield_rate is None and plan.target_good_qty is None:
        return deepcopy(view["material_requirements"])
    # Reuse the same Domain calculation as the dashboard. Only a private copy
    # of the selected calculation basis changes; no snapshot or stock is edited.
    basis = view["material_requirements"]["basis"]
    target = plan.target_good_qty if plan.target_good_qty is not None else basis["production_remaining_qty"]
    return requirements_for_snapshot({
        "opening_balance": {"delivery_round": {"remaining_good_target_qty": target}, "inventory": view["inventory"]},
        "bom": view.get("bom"), "entities": view["entities"],
    }, plan_yield=str(plan.yield_rate if plan.yield_rate is not None else basis["plan_yield"]))


def _unit_word(unit):
    return "개" if unit == "EA" else " " + unit


def _quote_rows(view, item_id, unit):
    documents = view.get("procurement", {}).get("documents", [])
    return sorted((row for row in documents if row["kind"] == "QUOTE" and row["status"] == "POSTED"
                   and not row.get("cancelled_by") and row["content"]["resolved"]["item_id"] == item_id
                   and row["content"]["unit"] == unit), key=lambda row: row["content"]["quote_ref"])


def _quote_view(row, shortage):
    quote = row["content"]
    calculation = quote_calculation(quote, shortage)
    return {"quote_id": row["id"], "quote_ref": quote["quote_ref"], "supplier_id": quote["resolved"]["supplier_id"],
            "moq": quote["moq"], "order_multiple": quote["order_multiple"], "supply_limit_qty": quote.get("supply_limit_qty"),
            "unit_price": quote["unit_price"], "currency": quote["currency"], "freight": quote["freight"],
            "lead_business_days": quote.get("lead_business_days"), "valid_until": quote["valid_until"],
            "calculation": calculation, "schedule_status": "UNCONFIRMED", "supplier_confirmation": False,
            "transmission_status": "NOT_SENT", "ledger_version": row["ledger_version"], "payload_hash": row["payload_hash"]}


def _quote_sentence(entry, unit):
    c, word = entry["calculation"], _unit_word(unit)
    label = f"{entry['quote_ref']}({entry['supplier_id']})"
    if c["quantity_condition"] == "MET":
        return f"{label}는 주문 {c['recommended_qty']}{word}로 수량 조건을 충족합니다(여유 {c['margin_qty']}{word})."
    if c["quantity_condition"] == "NOT_MET":
        if c["recommended_qty"] > c["required_qty"]:
            return f"{label}는 MOQ·주문배수로 {c['recommended_qty']}{word}를 주문해야 하지만 공급한도 {c['supply_limit_qty']}{word}를 넘어 미충족입니다."
        return f"{label}는 필요 {c['required_qty']}{word}가 공급한도 {c['supply_limit_qty']}{word}보다 {c['limit_shortfall_qty']}{word} 많아 단독으로는 미충족입니다."
    return f"{label}의 주문 {c['recommended_qty']}{word}는 산술값이며 공급한도가 확인되지 않아 판단을 보류합니다."


def _material_change_answer(view, plan, req):
    """Explain a recorded reservation's effect and re-compare recorded offers.

    Every number is recomputed from the same snapshot: the shared requirement
    calculation, the reservation event's exact before/after balance and posted
    synthetic quotes. Quantity conditions never imply schedule, cost approval,
    supplier commitment, selection or ordering.
    """
    s, intents = view["summary"], set(plan.intents)
    calculated = [row for row in req["items"] if row["state"] == "CALCULATED"]
    selected = [row["item_id"] for row in calculated if row["item_id"].casefold() in {v.casefold() for v in plan.item_ids}]
    impacts = reservation_impacts(req, view.get("reservations", []))
    focus_item = selected[0] if selected else (impacts[-1]["item_id"] if impacts else
                  next((row["item_id"] for row in calculated if row.get("planned_shortage_qty") not in (None, "0")), None))
    item_row = next((row for row in calculated if row["item_id"] == focus_item), None)
    kinds = {"InternalItem", "PartLot", "BOMApplication", "BOMLine", "ProductConfiguration", "DeliveryRound", "ProductionJob"}
    sections, answers, result_quotes, offer_documents = [], [], None, None
    status, conclusion = "PARTIAL", "같은 원장 버전의 자재 변경·확보안 조회"
    if item_row is None:
        answer = "현재 BOM에서 소요·부족이 계산된 품목이 없어 변경 원인과 확보안을 비교할 수 없습니다. BOM 적용 검토가 먼저 필요합니다."
        return _result(view, plan, answer=answer, status="UNKNOWN", conclusion="계산 가능한 품목 없음", kinds=kinds, include_bom=True)
    unit, word = item_row["unit"], _unit_word(item_row["unit"])
    item_impacts = [row for row in impacts if row["item_id"] == focus_item]
    first, latest = (item_impacts[0], item_impacts[-1]) if item_impacts else (None, None)
    basis = req["basis"]
    current_planned = item_row["planned_shortage_qty"]
    if "change" in intents:
        kinds.add("ReservationEvent")
        if latest:
            answers.append(f"부족이 늘어난 원인은 다른 수요 {latest['demand_ref']}가 {focus_item} {latest['quantity']}{word}를 예약한 원장 사건(v{latest['ledger_version']})입니다.")
            answers.append(f"보유 {latest['on_hand_qty']}{word}는 그대로이고 다른 수요 예약이 {first['reserved_other_before']}→{latest['reserved_other_after']}{word}로 늘어 가용이 {number(first['available_before'])}→{number(latest['available_after'])}{word}가 됐습니다.")
            answers.append(f"그래서 계획 투입 {basis['planned_input_qty']}대 기준 부족은 {number(first['planned_shortage_before'])}→{number(latest['planned_shortage_after'])}{word}, 양품 목표 {basis['production_remaining_qty']}대 기준은 {number(first['shortage_before'])}→{number(latest['shortage_after'])}{word}입니다.")
            answers.append(f"예약은 재고 이동·소비가 아니며 고객 도착 {number(s['arrived_qty'])}대와 남은 약속 {number(s['promise_remaining_qty'])}대는 바뀌지 않았습니다. 이전 답변은 당시 원장 버전 기준으로 남고, 기존 확보안은 재검토가 필요합니다.")
            sections.append(section("change", "변경 사건 · 원장 기록", "development_workflow", [
                item("사건", f"다른 수요 {row['demand_ref']} 예약 {row['quantity']}{word}", f"원장 v{row['ledger_version']} · LOT {row['lot_id']} · 재고 이동·소비 아님")
                for row in item_impacts]))
            sections.append(section("before_after", f"{focus_item} 전후 수량", DEMO, [
                item("보유", f"{latest['on_hand_qty']}{word}", "변하지 않았습니다.", SYNTHETIC),
                item("다른 수요 예약", f"{first['reserved_other_before']} → {latest['reserved_other_after']}{word}", origin="development_workflow"),
                item("가용", f"{number(first['available_before'])} → {number(latest['available_after'])}{word}", "가용 = 보유 − 다른 수요 예약 − 보류·검사대기"),
                item(f"부족 · 양품 목표 {basis['production_remaining_qty']}대", f"{number(first['shortage_before'])} → {number(latest['shortage_after'])}{word}"),
                item(f"부족 · 계획 투입 {basis['planned_input_qty']}대", f"{number(first['planned_shortage_before'])} → {number(latest['planned_shortage_after'])}{word}", "계획 수율은 가정이며 한 번만 적용합니다."),
            ], "PARTIAL"))
        else:
            answers.append(f"현재 조회 기준에는 {focus_item}의 다른 수요 예약 변경 사건이 없습니다. 계획 부족은 {number(current_planned)}{word}입니다.")
    if "sourcing" in intents:
        quotes = _quote_rows(view, focus_item, unit)
        kinds.update({"SupplierOffer", "Supplier"})
        if not quotes:
            answers.append(f"{focus_item}에 등록된 견적이 없어 확보안을 비교할 수 없습니다.")
        else:
            now = [_quote_view(row, current_planned) for row in quotes]
            previous = [_quote_view(row, first["planned_shortage_before"]) for row in quotes] if first else []
            if first and Decimal(first["planned_shortage_before"]) < Decimal(current_planned):
                gap = Decimal(current_planned) - Decimal(first["planned_shortage_before"])
                answers.append(f"현재 계획 부족은 {number(current_planned)}{word}입니다. 기존 {number(first['planned_shortage_before'])}{word} 확보안만으로는 {number(gap)}{word}가 부족해 충분하지 않습니다.")
            else:
                answers.append(f"현재 계획 부족은 {number(current_planned)}{word}입니다.")
            answers.extend(_quote_sentence(entry, unit) for entry in now)
            answers.append("수량 조건은 납기·비용·공급 확약과 별개입니다. 공급사 선택·초안 변경·분할 주문·발주는 자동으로 하지 않으며 사람이 비교 후 선택합니다.")
            entries = []
            for index, entry in enumerate(now):
                c = entry["calculation"]
                change = ""
                if previous:
                    p = previous[index]["calculation"]
                    change = f" · 기존 부족 {number(first['planned_shortage_before'])} 기준: 주문 {p['recommended_qty']} · {p['quantity_condition']}"
                cost = (f"{number(c['total'])} {c['currency']}" if c["total"] is not None else f"상품 {number(c['subtotal'])} {c['currency']} · 운송비 미확인")
                entries.append(item(f"{entry['quote_ref']} · {entry['supplier_id']}",
                    f"주문 {c['recommended_qty']}{word} · {dict(MET='수량 충족', NOT_MET='수량 미충족', UNKNOWN='판단 보류')[c['quantity_condition']]}",
                    f"MOQ {entry['moq']} · 배수 {entry['order_multiple']} · 공급한도 {entry['supply_limit_qty'] if entry['supply_limit_qty'] is not None else '미확인'} · 여유 {c['margin_qty']}{change} · 비용 {cost} · 리드타임 {entry['lead_business_days'] if entry['lead_business_days'] is not None else '미확인'}영업일(가정) · 일정·공급 확약 미확인",
                    "development_workflow"))
            sections.append(section("sourcing", f"{focus_item} 확보안 비교 · 별도 합성 견적", "development_workflow", entries, "PARTIAL"))
            sections.append(section("sourcing_bounds", "판단 경계", "unknown", [
                item("수량 조건", "MOQ·주문배수·공급한도", "부족 → 주문 가능 수량 → 공급한도 순서로 계산합니다. 부족·주문량에 수율을 다시 적용하지 않습니다."),
                item("납기·비용·확약", "별도 확인", "리드타임은 견적 가정이며 공급 확약·입하·검사 합격이 아닙니다."),
                item("자동 처리", "없음", "공급사 선택·초안 수량 변경·분할·발주는 사람이 명시적으로 진행합니다."),
            ], "UNKNOWN"))
            result_quotes = {"current": now, "previous": previous}
            offer_documents = [row["id"] for row in quotes]
    result = _result(view, plan, answer=" ".join(answers), status=status, sections=sections, kinds=kinds,
                     include_bom=True, selected_items=[focus_item], conclusion=conclusion,
                     offer_documents=offer_documents)
    result["calculation"] = {"basis": deepcopy(basis), "scope_item_ids": [focus_item],
                             "material_requirements": req, "reservation_impacts": item_impacts,
                             "sourcing": result_quotes, "actual_delivery_feasibility": None,
                             "estimated_arrival": None, "delivery_probability": None,
                             "automatic_selection": False, "automatic_order": False}
    result["steps"] = ["회사·회차·선택 품목과 조회 버전을 확인했습니다.",
                       "같은 스냅샷의 BOM 소요·재고·예약 사건으로 전후 수량을 다시 계산했습니다." if "change" in intents else "같은 스냅샷의 BOM 소요·재고로 현재 부족을 계산했습니다.",
                       *(["등록된 견적의 MOQ·주문배수·공급한도로 수량 조건을 비교했습니다."] if "sourcing" in intents else []),
                       "수량 조건과 납기·비용·확약·자동 처리를 분리해 답했습니다."]
    return result


def _lot_trace_answer(question, view, plan):
    """Follow only recorded relations from a LOT: consumption → installation on
    boards → production report → finished goods → shipment → arrival → round."""
    import re
    lots = {row.get("lot_id") for row in view["inventory"] if row.get("lot_id")}
    asked = [lot for lot in lots if re.search(re.escape(lot.casefold()), question.casefold())]
    kinds = {"PartLot", "InternalItem", "ConsumptionEvent", "InstallationEvent", "BoardUnit", "ProductionReport",
             "FinishedGoodsReceipt", "Allocation", "Shipment", "ArrivalRecord", "DeliveryRound", "ProductionJob"}
    if len(asked) != 1:
        return _clarify(view, plan, "추적할 LOT를 현재 원장의 LOT 코드 하나로 지정해 주세요.")
    lot = asked[0]
    stock = next(row for row in view["inventory"] if row.get("lot_id") == lot)
    boards = {serial: value for serial, value in view.get("fulfillment", {}).get("boards", {}).items() if value.get("lot_code") == lot}
    documents = view.get("procurement", {}).get("documents", [])
    by_id = {row["id"]: row for row in documents}
    receipts = [row for row in documents if row["kind"] == "RECEIPT" and row["content"].get("lot_code") == lot]
    related = {row["id"] for row in receipts} | {row["id"] for row in documents if row["kind"] == "IQC" and row["content"]["resolved"]["parent"]["id"] in {r["id"] for r in receipts}}
    for value in boards.values():
        related.update(value[key] for key in ("production_id", "fg_receipt_id", "shipment_id", "arrival_id") if value.get(key))
    s = view["summary"]
    counts = {state: sorted(serial for serial, value in boards.items() if value["status"] == state) for state in ("PRODUCED", "RECEIVED", "SHIPPED", "ARRIVED")}
    sections, answers = [], []
    origin = "development_workflow" if receipts else SYNTHETIC
    answers.append(f"{lot}({stock['item_id']})의 현재 보유 {number(stock.get('on_hand_qty'))} · 가용 {number(stock.get('available_qty'))} {stock.get('unit') or ''}입니다."
                   + (f" 소비 기록 {number(stock.get('consumed_qty', 0))}개." if stock.get("consumed_qty") else ""))
    if not boards:
        answers.append("이 LOT의 소비·장착·생산 기록이 아직 없어 연결된 보드·출하·납품회차가 없습니다. 예약·계획만으로 연결하지 않습니다.")
        status = "ANSWERED"
    else:
        produced = sorted(boards)
        production_ids = sorted({value["production_id"] for value in boards.values()})
        per_board = next((by_id[pid]["content"]["resolved"]["quantity_per_board"] for pid in production_ids if pid in by_id), "미확인")
        answers.append(f"{lot}에서 소비된 부품이 보드 {len(produced)}대({', '.join(produced)})에 장착 기록됐고, 작업 {s['production_job_id']}의 생산 실적으로 회차 {s['delivery_round_id']}에 귀속됩니다(보드당 {number(per_board)}{'개' if stock.get('unit') == 'EA' else ''}).")
        reached_fg = len(counts["RECEIVED"]) + len(counts["SHIPPED"]) + len(counts["ARRIVED"])
        reached_ship = len(counts["SHIPPED"]) + len(counts["ARRIVED"])
        answers.append(f"그중 완성품 입고 {reached_fg}대, 출하 {reached_ship}대, 고객 도착 {len(counts['ARRIVED'])}대입니다"
                       + (f"(도착 {', '.join(counts['ARRIVED'])})" if counts["ARRIVED"] else "") + ".")
        answers.append(f"회차 {s['delivery_round_id']}의 누적 고객 도착은 {number(s['arrived_qty'])}대, 남은 약속은 {number(s['promise_remaining_qty'])}대입니다. 장착은 BOM 적용 시연 가정에 근거한 기록이며 제조 기술검수가 아닙니다.")
        sections.append(section("trace", f"{lot} 추적 · 기록된 관계", "development_workflow", [
            item(serial, {"PRODUCED": "생산", "RECEIVED": "완성품 입고", "SHIPPED": "출하", "ARRIVED": "고객 도착"}[value["status"]],
                 "생산 → 입고 → 출하 → 도착 중 현재 단계")
            for serial, value in sorted(boards.items())]))
        status = "ANSWERED"
    sections.insert(0, section("lot", f"{lot} 재고", origin, [
        item("보유 / 가용", f"{number(stock.get('on_hand_qty'))} / {number(stock.get('available_qty'))} {stock.get('unit') or ''}"),
        item("입하 문서", f"{len(receipts)}건", "입하·수입검사 합격 후 가용으로 전환됩니다."),
    ]))
    result = _result(view, plan, answer=" ".join(answers), status=status, sections=sections, kinds=kinds,
                     selected_items=[stock["item_id"]], conclusion="기록된 LOT 추적 관계", offer_documents=sorted(related))
    result["calculation"] = {"lot_code": lot, "boards": {serial: value["status"] for serial, value in boards.items()},
                             "counts": {key: len(value) for key, value in counts.items()},
                             "actual_delivery_feasibility": None, "estimated_arrival": None, "delivery_probability": None}
    result["steps"] = ["LOT와 회차·조회 버전을 확인했습니다.", "소비·장착·생산·입고·출하·도착 기록을 원장 관계로 따라갔습니다.",
                       "기록이 없는 단계는 추정하지 않고 비워 두었습니다."]
    return result


def answer_query(question, view, ontology, *, context_entity_id=None):
    plan = interpret_question(question)
    if plan.clarification:
        return _clarify(view, plan, plan.clarification)
    if plan.unsupported or not plan.intents:
        return _result(view, plan, status="UNKNOWN", answer="현재 회차의 잔여 약속·추가 생산·수율 투입·자재 부족·납기 근거를 질문해 주세요. 이 질문에 필요한 업무 기능은 아직 연결되지 않았습니다.", conclusion="지원 범위 밖 또는 의도 미확인")
    scope_error = _scope_error(plan, view, context_entity_id, ontology)
    if scope_error:
        return _clarify(view, plan, scope_error)
    if not _consistent(view["summary"]):
        return _result(view, plan, status="UNKNOWN", answer="원장 수량과 단계별 잔량이 일치하지 않아 계산을 보류했습니다. 원장 대사가 필요합니다.", conclusion="원장 수량 대사 필요")

    intents = set(plan.intents)
    s = view["summary"]
    req = _requirements(view, plan)
    if "trace" in intents:
        return _lot_trace_answer(question, view, plan)
    if intents & {"change", "sourcing"}:
        return _material_change_answer(view, plan, req)
    basis = req["basis"]
    sections, answers, kinds = [], [], set()
    status, conclusion = "ANSWERED", "같은 원장 버전의 조회 결과"
    selected = [node["display_id"] for node in view["entities"]
                if node["type"] == "InternalItem" and node["display_id"].casefold() in {v.casefold() for v in plan.item_ids}]
    selected_lot = None
    if not selected and context_entity_id and intents & {"materials", "inventory"}:
        node = next(node for node in view["entities"] if node["id"] == context_entity_id)
        if node["type"] == "InternalItem":
            selected = [node["display_id"]]
        elif node["type"] == "PartLot":
            selected_lot = node["display_id"]
            selected = list({row["item_id"] for row in view["inventory"] if row.get("lot_id") == selected_lot})
            if not selected:
                return _clarify(view, plan, "선택 LOT의 품목·재고 근거를 현재 스냅샷에서 확인하지 못했습니다.")
    need_materials = bool(intents & {"materials", "delivery"})
    include_bom = need_materials or "production" in intents

    if intents & {"fulfillment", "production", "delivery", "materials"}:
        kinds.update({"DeliveryRound", "ContractLine", "ProductionJob", "Customer", "ProductConfiguration"})
        sections.append(section("ledger", "현재 원장 수량 · 합성 기초 이관", SYNTHETIC, [
            item("조회 회차", s["delivery_round_id"], f"질문의 주문 {', '.join(r for r in plan.target_references if r.upper() == s['delivery_round_id'].rsplit('-', 1)[0].upper())}의 선택 회차입니다." if any(r.upper() == s['delivery_round_id'].rsplit('-', 1)[0].upper() for r in plan.target_references) else ""),
            item("약속 / 고객 도착", f"{number(s['promised_qty'])}대 / {number(s['arrived_qty'])}대"),
            item("남은 고객 도착 약속", f"{number(s['promise_remaining_qty'])}대", f"{number(s['promised_qty'])} − {number(s['arrived_qty'])}; 신규 생산 수량이 아닙니다."),
            item("양품 / 완성품 입고 / 출하", f"{number(s['good_qty'])}대 / {number(s['fg_received_qty'])}대 / {number(s['shipped_qty'])}대"),
            item("이미 생산됐으나 미도착", f"{number(s['already_produced_qty'])}대", f"양품 미입고 {number(s['good_not_received_qty'])} + 창고 {number(s['warehouse_remaining_qty'])} + 운송 중 {number(s['transit_qty'])}; 서로 다른 단계입니다."),
            item("추가 양품 목표", f"{number(s['production_remaining_qty'])}대", f"약속 {number(s['promised_qty'])} − 양품 {number(s['good_qty'])}; 현장 실측으로 확정한 수량은 아닙니다."),
        ]))
        answers.append(f"남은 고객 도착 약속은 {number(s['promise_remaining_qty'])}대이고, 추가 양품 목표는 {number(s['production_remaining_qty'])}대입니다.")

    if intents & {"production", "materials", "delivery"}:
        origin = "user_assumption" if plan.is_what_if else DEMO
        sections.append(section("production", "시연 가정 · 양품 목표와 계획 투입", origin, [
            item("계산 양품 목표", f"{basis['production_remaining_qty']}대", "질문에 명시한 별도 가정입니다. 현재 원장 목표는 바꾸지 않습니다." if plan.target_good_qty is not None else "현재 회차의 추가 양품 목표를 사용합니다."),
            item("계획 수율", f"{percentage(basis['plan_yield'])}%", "명시한 계산 가정이며 관측 수율이나 양품 보장이 아닙니다."),
            item("계획 투입", f"{basis['planned_input_qty']}대", f"ceil({basis['production_remaining_qty']} ÷ {number(basis['plan_yield'])}); 계획이며 완료 실적이 아닙니다."),
        ], "PARTIAL"))
        answers.append(f"계산 양품 목표 {basis['production_remaining_qty']}대에 수율 {percentage(basis['plan_yield'])}%를 가정한 투입은 {basis['planned_input_qty']}대이며 양품을 보장하지 않습니다.")
        conclusion = "계획 수율을 적용한 조건부 계산"

    if need_materials:
        kinds.update({"SourceDocument", "SourceSnapshot", "SourceRow", "BOMSnapshot", "BOMLine", "BOMApplication",
                      "InternalItem", "PartLot", "ApprovalGrant", "DocumentVersion"})
        if req["bom_id"]:
            sections.append(section("source", "실제 BOM 원문 · 등록 근거", "actual_bom", [
                item("원본 전체 행", f"{req['row_count']}행", f"문서 v{req['bom_version']}의 원문·행·hash를 보존합니다."),
                item("적용 판단 / 미확인", f"{req['applicable_count']}행 / {req['unresolved_count']}행", "원문 등록 승인과 제조 기술검수는 별개입니다."),
            ]))
            rows = [row for row in req["items"] if not selected or row["item_id"] in selected]
            entries = []
            for row in rows:
                stock_origin = inventory_origin([entry for entry in view["inventory"] if entry["item_id"] == row["item_id"]])
                stock_label = {SYNTHETIC: "합성 기초 원장", "development_workflow": "개발 입하·수입검사 원장",
                               "mixed_sources": "합성 기초 원장과 개발 입하·수입검사 원장", "unknown": "출처 미확인"}[stock_origin]
                entries.append(item(row["item_id"],
                    f"소요 {number(row['required_qty'])} / {number(row['planned_required_qty'])} {row['unit']} · 가용 {number(row['available_qty'])} {row['unit']} · 부족 {number(row['shortage_qty'])} / {number(row['planned_shortage_qty'])} {row['unit']}",
                    f"앞은 양품 목표 {basis['production_remaining_qty']}대, 뒤는 계획 투입 {basis['planned_input_qty']}대 기준. 대당 {number(row['quantity_per_unit'])} {row['unit']}, 원문 {len(row['row_ids'])}행 합산. 적용 매핑·수량은 {'시연 가정' if row['conditional'] else '검토된 적용 판단'}, 재고는 {stock_label}입니다.",
                    DEMO if row["conditional"] else "reviewed_application"))
            if not entries:
                entries.append(item("선택 품목 소요·부족", "미확인", "현재 BOM의 검토된 적용 행과 선택 품목 연결이 필요합니다.", "unknown"))
            material_origin = DEMO if rows and all(row["conditional"] for row in rows) else "mixed_sources"
            sections.append(section("materials", f"전체 BOM 중 일부 품목 기준 · {len(rows)}개 품목", material_origin, entries, "PARTIAL"))
            if selected_lot:
                sections[-1]["items"].insert(0, item("선택 LOT와 계산 범위", selected_lot,
                    "소요·부족은 선택 LOT가 속한 품목의 가용 재고 전체와 비교합니다. 아래 LOT 재고 조회와 범위를 구분합니다.", SYNTHETIC))
            if rows:
                answers.append(f"등록된 실제 원문 {req['row_count']}행 중 조회한 {len(rows)}개 품목의 소요·부족을 아래에 구분했습니다. 전체 자재 확보 판정은 보류합니다.")
            status = "PARTIAL"
        else:
            sections.append(section("source", "BOM 근거 부족", "unknown", [item("적용 BOM", "미등록", "원본 접수·검토·승인·반영 후 소요와 부족을 조회할 수 있습니다.")], "UNKNOWN"))
            answers.append("적용 BOM이 없어 자재 소요·부족량은 미확인입니다.")
            status = "PARTIAL"
        sections.append(section("material_unknowns", "전체 자재 판단에 남은 확인", "unknown", [
            item("부족량 미확인", f"{req['unknown_shortage_count']}행" if req["bom_id"] else "전체 BOM", "미검수·미매핑·단위 또는 재고 미확인은 부족 0을 뜻하지 않습니다."),
            item("제조 기술검수", s["technical_review"], "제품 구성·개정·품목 동일성·단위·실장 여부를 확인해야 합니다."),
            item("현장 재고", "미확인", "합성 초기 재고와 개발 원장에 반영된 입하·검사 기록으로 계산합니다. 실제 현장 재고 확정이 아닙니다."),
        ], "UNKNOWN"))

    if "inventory" in intents:
        kinds.update({"InternalItem", "PartLot"})
        rows = [row for row in view["inventory"] if (not selected or row["item_id"] in selected)
                and (selected_lot is None or row.get("lot_id") == selected_lot)]
        origin = inventory_origin(rows)
        label = {SYNTHETIC: "합성 기초 이관", "development_workflow": "개발 입하·수입검사",
                 "mixed_sources": "기초 이관 + 개발 입하·수입검사", "unknown": "미확인"}[origin]
        sections.append(section("inventory", "현재 가용 재고 · " + label, origin, [
            item(row.get("lot_id") or row["item_id"], f"{number(row.get('available_qty'))} {row.get('unit') or '단위 미확인'}",
                 f"보유 {number(row.get('on_hand_qty'))} − 타 수요 예약 {number(row.get('reserved_other_qty'))} − 보류 {number(row.get('held_qty'))}. 자기 예약은 다시 차감하지 않습니다.")
            for row in rows] or [item("재고", "미확인", "선택 품목의 재고 근거가 없습니다.", "unknown")]))
        answers.append("가용 재고는 보유량에서 타 수요 예약과 검사대기·보류량을 제외했습니다.")

    if "delivery" in intents:
        # Current ledger contracts contain neither a complete operational
        # schedule nor verified calendar/capacity/transport evidence. No future
        # reference scenario or assumed 0-duration is a substitute for it.
        blockers = [
            item("전체 BOM·제조 적용", "미확인", f"적용 미확인 {req['unresolved_count']}행 및 기술검수가 남아 있습니다." if req["bom_id"] else "적용 BOM이 미등록입니다."),
            item("실제 가용 재고·확정 조달", "미확인", "견적 후보·발주 예정은 공급 확약이나 입하·검사 합격 재고가 아닙니다."),
            item("수입검사·양품 판정 일정", "미확인", "입하와 합격, 생산 완료와 양품 판정을 나누어 확인해야 합니다."),
            item("생산능력·착수·작업 달력", "미확인", "능력의 투입/양품 단위, 영업일·휴일과 작업 시작 근거가 필요합니다."),
            item("기존 미도착분의 진행", "미확인", f"미입고 {number(s['good_not_received_qty'])}대·창고 {number(s['warehouse_remaining_qty'])}대·운송 중 {number(s['transit_qty'])}대의 후속 일정도 필요합니다."),
            item("출하 준비·운송·고객 도착", "미확인", "출하를 고객 도착으로 간주하지 않으며 운송 약속·수령 근거가 필요합니다."),
            item("고객 도착 기한", str(s.get("arrival_due_date") or "미확인"), "합성 초기 원장의 기한 표기입니다. 실제 약속·마감 시각·시간대와 달력을 확인해야 합니다.", SYNTHETIC),
        ]
        sections.append(section("delivery", "납기 가능 여부 · 판단 보류", "unknown", blockers, "UNKNOWN"))
        impacts = reservation_impacts(req, view.get("reservations", []))
        if impacts:
            latest = impacts[-1]
            first = next(row for row in impacts if row["item_id"] == latest["item_id"])
            kinds.update({"ReservationEvent", "PartLot", "InternalItem"})
            sections.append(section("material_change", "자재 조건 변경 영향 · 원장 사건", "development_workflow", [
                item("변경 사건", f"다른 수요 {latest['demand_ref']} 예약 {latest['quantity']} {latest['unit']}", f"원장 v{latest['ledger_version']} · 재고 이동·소비가 아닙니다."),
                item(f"{latest['item_id']} 가용", f"{number(first['available_before'])} → {number(latest['available_after'])} {latest['unit']}"),
                item("계획 투입 기준 부족", f"{number(first['planned_shortage_before'])} → {number(latest['planned_shortage_after'])} {latest['unit']}", "이전 확보안은 재검토가 필요합니다. 공급사·초안은 자동으로 바꾸지 않습니다."),
                item("고객 도착 / 남은 약속", f"{number(s['arrived_qty'])}대 / {number(s['promise_remaining_qty'])}대", "예약 사건은 도착·잔여 약속을 바꾸지 않습니다."),
            ], "PARTIAL"))
        sections.append(section("next", "조건부 납기 계산에 필요한 다음 확인", "unknown", [
            item("실제 판단", "필수 근거 확인 후 재조회", "전체 적용 자재·실재고·조달/검사·능력·달력·운송·고객 도착 기한을 같은 기준으로 확인합니다."),
            item("시연 일정", "제안 가정은 아직 적용하지 않음", "문서의 일정 제안은 선택한 프로필·단위·시작점·달력과 함께 명시해야 합니다. 미래 시연 결말을 현재 원장으로 사용하지 않습니다.", "proposed_demo_assumption"),
        ], "UNKNOWN"))
        answers.insert(0, "현재 근거로는 남은 물량의 납기 내 고객 도착 가능 여부를 확정할 수 없습니다.")
        status = "PARTIAL"
        scoped_materials = [row for row in req["items"] if not selected or row["item_id"] in selected]
        known_shortages = [row for row in scoped_materials if row.get("shortage_qty") is not None]
        conclusion = ("현재 수량·계산 가능한 품목의 부족 확인 · 실제 납기는 판단 보류"
                      if known_shortages else "현재 수량 확인 · 자재 소요·부족 미확인 · 납기 판단 보류")
        if req["state"] == "BLOCKED_BOM" and req["bom_id"] is None:
            conclusion = "현재 수량 확인 · BOM 등록 후 소요·부족 계산 · 납기 판단 보류"
            answers.insert(1, "BOM 원본을 등록하고 검토·승인·원장 반영을 마친 뒤 소요·부족을 계산해야 합니다.")

    if "ontology" in intents:
        counts = ontology["counts"]
        sections.append(section("ontology", "전체 온톨로지 계약", "actual_source", [
            item("클래스 / 관계 / SHACL shape", f"{counts['classes']} / {counts['relations']} / {counts['shapes']}", "전체 스키마를 보존하며 전체 CQ 인수와 구분합니다.")]))
        answers.append(f"전체 스키마는 클래스 {counts['classes']}개, 관계 {counts['relations']}개, SHACL shape {counts['shapes']}개입니다.")

    prerequisite = _prerequisite(view, req, selected) if need_materials else None
    if prerequisite:
        first, action = _prerequisite_text(prerequisite)
        blocked_calculation = prerequisite.counts.calculation_items == 0 or (
            bool(selected) and prerequisite.selected_calculation_items == 0)
        if "delivery" in intents and prerequisite.bom is not None and not blocked_calculation:
            # Calculable items exist: lead with the computed shortage, keep the review boundary.
            scoped = [row for row in req["items"] if not selected or row["item_id"] in selected]
            short = [row for row in scoped if row.get("planned_shortage_qty") not in (None, "0")]
            focus = max(short, key=lambda row: Decimal(row["planned_shortage_qty"])) if short else None
            answers = [f"남은 고객 도착 약속은 {number(s['promise_remaining_qty'])}대이고, 추가 양품 목표는 {number(s['production_remaining_qty'])}대입니다.",
                       (f"계획 투입 {basis['planned_input_qty']}대 기준 {focus['item_id']}이 {number(focus['planned_shortage_qty'])}개 부족합니다."
                        if focus else f"계산한 {len(scoped)}개 품목은 계획 투입 {basis['planned_input_qty']}대 기준 부족이 없습니다."),
                       f"BOM 적용 미확인 {number(req['unresolved_count'])}행은 부족 0으로 보지 않습니다." if req["unresolved_count"] else first]
        elif "delivery" in intents or blocked_calculation:
            answers = [first, action,
                f"합성 기초 원장 기준 남은 고객 도착 약속은 {number(s['promise_remaining_qty'])}대이고, 추가 양품 목표는 {number(s['production_remaining_qty'])}대입니다."]
        # A deadline question may acquire a production intent merely because
        # it says 'remaining'. Show the yield assumption in prose only when
        # actually requested; its exact calculation remains in sections/DTO.
        if ("delivery" in intents or blocked_calculation) and (plan.explicit_production or plan.is_what_if):
            answers.append(f"계산 양품 목표 {basis['production_remaining_qty']}대에 수율 {percentage(basis['plan_yield'])}%를 가정한 투입은 {basis['planned_input_qty']}대이며 양품을 보장하지 않습니다.")
        if selected and prerequisite.selected_calculation_items == 0 and prerequisite.counts.calculation_items:
            answers.append("선택 품목은 현재 BOM의 계산 가능한 품목에 연결되지 않았습니다. 다른 적용 품목의 계산과 구분합니다.")
        if "delivery" in intents:
            impacts = reservation_impacts(req, view.get("reservations", []))
            if impacts:
                latest = impacts[-1]
                first = next(row for row in impacts if row["item_id"] == latest["item_id"])
                answers.append(f"다른 수요의 예약으로 {latest['item_id']} 계획 부족이 {number(first['planned_shortage_before'])}→{number(latest['planned_shortage_after'])}{latest['unit'] == 'EA' and '개' or ' ' + latest['unit']}로 늘어 기존 확보안은 재검토가 필요합니다.")
            answers.append("실제 납기 판단은 보류합니다.")
            if prerequisite.reason == "NO_REVIEWED_APPLICABLE_ROWS":
                conclusion = "원본 등록 완료 · BOM 적용 검토 필요 · 납기 판단 보류"
            elif prerequisite.reason == "NO_APPLICABLE_COMPONENTS":
                conclusion = "원본 등록 완료 · 생산 적용 대상 확인 필요 · 납기 판단 보류"
        if "ontology" in intents and ("delivery" in intents or blocked_calculation):
            counts = ontology["counts"]
            answers.append(f"전체 스키마는 클래스 {counts['classes']}개, 관계 {counts['relations']}개, SHACL shape {counts['shapes']}개입니다.")
        sections.insert(0, section("prerequisite", "가장 먼저 필요한 확인", "unknown", [
            item("회차 / 제품 구성", f"{s['delivery_round_id']} / {s['configuration_id']}"),
            item("현재 선행 조건", first), item("다음 행동", prerequisite.next_action.label, action),
            item("승인·반영 경계", "사용자 명시 절차 필요",
                 "반영된 문서는 변경하지 않습니다. 적용 판단을 보완하려면 작성 중인 초안을 선택하거나 보관 원본으로 새 검토 초안을 접수한 뒤 검토·독립 승인·반영해야 합니다. 원문 등록 승인은 제조 기술검수가 아닙니다."
                 if prerequisite.bom else "버튼은 원본 접수 화면만 엽니다. 등록·검토·독립 승인·반영은 별도 명시 절차입니다."),
        ], "UNKNOWN"))

    result = _result(view, plan, answer=" ".join(answers), status=status, sections=sections, kinds=kinds,
                     include_bom=include_bom, selected_items=selected,
                     selected_lot=selected_lot if not need_materials else None, conclusion=conclusion)
    result["calculation"] = {"basis": deepcopy(basis), "scope_item_ids": selected,
                             "material_requirements": req if need_materials else None,
                             "actual_delivery_feasibility": "UNKNOWN" if "delivery" in intents else None,
                             "estimated_arrival": None, "delivery_probability": None}
    if prerequisite:
        result["prerequisite"] = prerequisite.model_dump(mode="json")
        result["steps"] = _prerequisite_steps(view, prerequisite)
        if "delivery" in intents:
            result["answer_blocks"] = _delivery_blocks(view, plan, sections, prerequisite, req)
    return result
