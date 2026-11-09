"""Read-only CQ analysis: interpreted requests, snapshot tools, then explanation.

No private reference answers, future scenario events, model calls or database
writes are used here. Interpretations choose capabilities; their numbers come
from the same authorized snapshot and the shared material calculation tool.
"""

from copy import deepcopy
from decimal import Decimal

from service.materials import requirements_for_snapshot
from service.query_intents import interpret_question


RULE_VERSION = "cq-read-analysis/1"
LABELS = {"fulfillment": "납품 이행", "production": "추가 생산·투입",
          "materials": "자재 소요·부족", "inventory": "가용 재고",
          "delivery": "고객 도착·납기 판단", "ontology": "전체 온톨로지"}
SYNTHETIC = "synthetic_opening_balance"
DEMO = "conditional_demo"
CQ_REFS = {"fulfillment": ("DEMO-CQ-01", "B-CQ02"), "production": ("DEMO-CQ-02", "B-CQ03"),
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
    return next(iter(origins)) if len(origins) == 1 else "mixed_sources" if origins else "unknown"


def _result(view, plan, *, answer, status="ANSWERED", sections=(), kinds=(),
            include_bom=False, selected_items=(), selected_lot=None, conclusion=None):
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
    if receipts and set(plan.intents) & {"materials", "inventory", "delivery"}:
        kinds = set(kinds) | {"PartsReceiptLine", "InspectionResult", "DocumentVersion", "ApprovalGrant"}
    for node in view["entities"]:
        if node["type"] not in kinds:
            continue
        evidence = node.get("evidence", {})
        if evidence.get("record_type") == "development_procurement" and evidence.get("document_id") not in development_docs:
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
        if node["id"] in ids and evidence.get("record_type") == "development_procurement":
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
            item("조회 회차", s["delivery_round_id"]),
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
        sections.append(section("next", "조건부 납기 계산에 필요한 다음 확인", "unknown", [
            item("실제 판단", "필수 근거 확인 후 재조회", "전체 적용 자재·실재고·조달/검사·능력·달력·운송·고객 도착 기한을 같은 기준으로 확인합니다."),
            item("시연 일정", "제안 가정은 아직 적용하지 않음", "문서의 일정 제안은 선택한 프로필·단위·시작점·달력과 함께 명시해야 합니다. 미래 시연 결말을 현재 원장으로 사용하지 않습니다.", "proposed_demo_assumption"),
        ], "UNKNOWN"))
        answers.insert(0, "현재 근거로는 남은 물량의 납기 내 고객 도착 가능 여부를 확정할 수 없습니다.")
        status, conclusion = "PARTIAL", "현재 수량·조건부 부족은 확인 · 실제 납기는 판단 보류"
        if req["state"] == "BLOCKED_BOM" and req["bom_id"] is None:
            conclusion = "현재 수량 확인 · BOM 등록 후 소요·부족 계산 · 납기 판단 보류"
            answers.insert(1, "BOM 원본을 등록하고 검토·승인·원장 반영을 마친 뒤 소요·부족을 계산해야 합니다.")

    if "ontology" in intents:
        counts = ontology["counts"]
        sections.append(section("ontology", "전체 온톨로지 계약", "actual_source", [
            item("클래스 / 관계 / SHACL shape", f"{counts['classes']} / {counts['relations']} / {counts['shapes']}", "전체 스키마를 보존하며 전체 CQ 인수와 구분합니다.")]))
        answers.append(f"전체 스키마는 클래스 {counts['classes']}개, 관계 {counts['relations']}개, SHACL shape {counts['shapes']}개입니다.")

    result = _result(view, plan, answer=" ".join(answers), status=status, sections=sections, kinds=kinds,
                     include_bom=include_bom, selected_items=selected,
                     selected_lot=selected_lot if not need_materials else None, conclusion=conclusion)
    result["calculation"] = {"basis": deepcopy(basis), "scope_item_ids": selected,
                             "material_requirements": req if need_materials else None,
                             "actual_delivery_feasibility": "UNKNOWN" if "delivery" in intents else None,
                             "estimated_arrival": None, "delivery_probability": None}
    return result
