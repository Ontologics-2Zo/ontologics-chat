"""Deterministic BOM requirements from one committed, versioned snapshot.

Source rows, reviewed application decisions and synthetic opening inventory
have separate provenance. A missing mapping, unit or inventory record never
means zero stock. Repeated BOM rows are aggregated before stock is compared.
"""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation


RULE_VERSION = "bom-material-requirements/1"


CALCULATION_POLICY = {
    "id": "material-calculation/1", "label": "자재 계산 기준 v1",
    "status": "CURRENT_IMPLEMENTATION", "company_policy_approval": "NOT_RECORDED",
    "default_basis": "production_remaining",
    "rules": [
        {"id": "REQUIREMENT", "text": "같은 품목·단위의 대당 수량을 합산한 뒤 기준 생산 대수를 곱합니다."},
        {"id": "AVAILABLE", "text": "가용 = 보유 − 다른 수요 예약 − 보류·검사대기. 이 수요의 자기예약은 다시 빼지 않습니다."},
        {"id": "SHORTAGE", "text": "부족 = max(소요 − 가용, 0). 단위·재고·품목 연결 미확인은 0 대신 미확인으로 둡니다."},
        {"id": "YIELD", "text": "수율 반영 투입 = 올림(추가 양품 목표 ÷ 계획 수율). 계획 수율은 실적이나 양품 보장이 아닙니다."},
        {"id": "COVERAGE", "text": "미검수 BOM 행을 전체 판정에서 제외하지 않으며, 부분 충족을 전체 제조 가능으로 확정하지 않습니다."},
    ],
}


def _decimal(value, *, positive=False):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal("1e12"):
            return None
        return result
    except (InvalidOperation, ValueError):
        return None


def _number(value):
    """Use decimal strings at the API boundary, avoiding floating point drift."""
    return format(value, "f") if value is not None else None


def _inventory_for_item(inventories, item_id, unit):
    records = [row for row in inventories if row.get("item_id") == item_id]
    if not records:
        return None, "UNKNOWN", ["INVENTORY_UNKNOWN"]
    lot_rows = [row for row in records if row.get("scope") == "LOT"]
    aggregate_rows = [row for row in records if row.get("scope") == "ITEM_AGGREGATE_LOTS_UNKNOWN"]
    # Aggregate and LOT records may overlap. Neither summing nor silently
    # choosing one establishes that the stock coverage is complete.
    if len(lot_rows) + len(aggregate_rows) != len(records) or (lot_rows and aggregate_rows) or len(aggregate_rows) > 1:
        return None, "UNKNOWN", ["INVENTORY_COVERAGE_AMBIGUOUS"]
    if lot_rows and len({row.get("lot_id") for row in lot_rows}) != len(lot_rows):
        return None, "UNKNOWN", ["INVENTORY_COVERAGE_AMBIGUOUS"]
    if any(not row.get("unit") or row["unit"] != unit for row in records):
        return None, "UNKNOWN", ["INVENTORY_UNIT_UNCONFIRMED"]
    available = Decimal(0)
    for row in records:
        fields = [_decimal(row.get(key)) for key in ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty")]
        if any(value is None for value in fields):
            return None, "UNKNOWN", ["INVENTORY_BALANCE_UNKNOWN"]
        on_hand, other, own, held = fields
        if other + own + held > on_hand:
            return None, "UNKNOWN", ["INVENTORY_BALANCE_INVALID"]
        # Own reservations remain available to this demand. Held/inspection
        # stock and other demands' reservations are excluded exactly once.
        actual_available = on_hand - other - held
        if _decimal(row.get("available_qty")) != actual_available:
            return None, "UNKNOWN", ["INVENTORY_BALANCE_INVALID"]
        available += actual_available
    return available, "LOT" if lot_rows else "ITEM_AGGREGATE_LOTS_UNKNOWN", []


def requirements_for_snapshot(payload, *, plan_yield="0.94"):
    """Compute full registered-row coverage; only reviewed facts drive totals."""
    balance = payload["opening_balance"]
    # Posted production records (DEC-H49) reduce the remaining good target.
    round_ = payload.get("current_delivery_round") or balance["delivery_round"]
    target = _decimal(round_.get("remaining_good_target_qty"))
    yield_fraction = _decimal(plan_yield, positive=True)
    if target is None or yield_fraction is None or yield_fraction > 1:
        raise ValueError("MATERIAL_BASIS_INVALID")
    # Exact rational ceiling avoids Decimal context rounding a yield very close
    # to 1 up to 1 before the extra input unit is accounted for.
    tn, td = target.as_integer_ratio()
    yn, yd = yield_fraction.as_integer_ratio()
    numerator, denominator = tn * yd, td * yn
    planned = Decimal((numerator + denominator - 1) // denominator)
    result = {
        "state": "BLOCKED_BOM", "rule_version": RULE_VERSION,
        "bom_id": None, "bom_version": None, "source": None,
        "row_count": 0, "applicable_count": 0, "not_mounted_count": 0,
        "setting_count": 0, "unresolved_count": 0, "reviewed_count": 0,
        "unknown_shortage_count": 0, "full_coverage": False,
        "basis": {"production_remaining_qty": int(target), "planned_input_qty": int(planned),
                  "plan_yield": _number(yield_fraction), "planned_is_assumption": True,
                  "good_output_guaranteed": False},
        "rows": [], "items": [], "has_demo_assumptions": False,
        "inventory_is_synthetic": True, "manufacturing_feasibility": "UNKNOWN",
        "calculation_policy": deepcopy(CALCULATION_POLICY),
    }
    bom = payload.get("bom")
    if not bom:
        return result
    source_rows = bom["rows"]
    result.update(bom_id=bom["bom_id"], bom_version=bom["bom_version"],
                  source=deepcopy(bom["source"]), row_count=len(source_rows))
    groups = {}
    unresolved = 0
    mapped_by_id = defaultdict(list)
    for entity in payload["entities"]:
        if entity["type"] == "InternalItem":
            for key in {entity["id"], entity["display_id"]}:
                mapped_by_id[key].append(entity)
    for source_row in source_rows:
        row = deepcopy(source_row)
        decision = row.get("decision", {})
        application = decision.get("application_status", "UNRESOLVED")
        reviewed = decision.get("review_basis") in {"SOURCE_VERIFIED", "DEMO_ASSUMPTION"}
        source_quantity = _decimal(row.get("source_quantity"))
        row.update(required_qty=None, planned_required_qty=None, calculation_state="UNKNOWN", issues=[],
                   source_quantity_times_target=_number(source_quantity * target) if source_quantity is not None else None,
                   source_quantity_times_plan=_number(source_quantity * planned) if source_quantity is not None else None,
                   source_arithmetic_is_conditional=True)
        if decision.get("review_basis") == "DEMO_ASSUMPTION":
            result["has_demo_assumptions"] = True
        if reviewed:
            result["reviewed_count"] += 1
        if not reviewed or application == "UNRESOLVED":
            result["unresolved_count"] += 1
            unresolved += 1
            row["issues"].append("APPLICATION_UNREVIEWED")
        elif application in {"NOT_MOUNTED", "SETTING"}:
            result["not_mounted_count" if application == "NOT_MOUNTED" else "setting_count"] += 1
            row["calculation_state"] = "EXCLUDED_BY_REVIEW"
        elif application == "APPLICABLE":
            result["applicable_count"] += 1
            quantity = _decimal(decision.get("quantity_per_unit"), positive=True)
            unit = decision.get("unit")
            if quantity is None:
                row["issues"].append("QUANTITY_UNCONFIRMED")
            if not isinstance(unit, str) or not unit:
                row["issues"].append("UNIT_UNCONFIRMED")
            if quantity is not None and isinstance(unit, str) and unit:
                row["required_qty"] = _number(quantity * target)
                row["planned_required_qty"] = _number(quantity * planned)
            item_candidates = mapped_by_id.get(decision.get("item_id"), [])
            if len(item_candidates) != 1:
                row["issues"].append("ITEM_MAPPING_UNCONFIRMED")
            if row["issues"]:
                unresolved += 1
            else:
                item = item_candidates[0]
                item_id = item["display_id"]
                key = (item_id, unit)
                group = groups.setdefault(key, {"item_id": item_id, "item_entity_id": item["id"], "unit": unit,
                    "item_name": item.get("attributes", {}).get("display_name") or item.get("label") or item_id,
                    "row_ids": [], "quantity_per_unit": Decimal(0), "review_bases": set(), "evidence_refs": []})
                group["quantity_per_unit"] += quantity
                group["row_ids"].append(row["row_id"])
                group["review_bases"].add(decision["review_basis"])
                group["evidence_refs"].append({"source_sha256": bom["source"]["sha256"], "locator": row["locator"],
                                               "bom_id": bom["bom_id"], "bom_version": bom["bom_version"]})
                row["calculation_state"] = "REQUIREMENT_CALCULATED"
        else:
            unresolved += 1
            result["unresolved_count"] += 1
            row["issues"].append("APPLICATION_UNREVIEWED")
        result["rows"].append(row)
    units_by_item = defaultdict(set)
    for item_id, unit in groups:
        units_by_item[item_id].add(unit)
    for key in sorted(groups):
        item = groups[key]
        quantity = item.pop("quantity_per_unit")
        bases = item.pop("review_bases")
        required, planned_required = quantity * target, quantity * planned
        inventories = payload.get("current_inventory", balance["inventory"])
        available, scope, issues = _inventory_for_item(inventories, item["item_id"], item["unit"])
        if len(units_by_item[item["item_id"]]) != 1:
            available, scope, issues = None, "UNKNOWN", ["ITEM_UNIT_CONFLICT"]
        item.update(quantity_per_unit=_number(quantity), required_qty=_number(required),
                    planned_required_qty=_number(planned_required), available_qty=_number(available),
                    shortage_qty=_number(max(required - available, Decimal(0))) if available is not None else None,
                    planned_shortage_qty=_number(max(planned_required - available, Decimal(0))) if available is not None else None,
                    state="CALCULATED" if available is not None else "UNKNOWN", issues=issues,
                    inventory_scope=scope, inventory_is_synthetic=True,
                    review_bases=sorted(bases), conditional="DEMO_ASSUMPTION" in bases)
        # Presentation exposes the existing arithmetic and its evidence status
        # separately. A readable rule is not a manufacturing approval.
        item["unit_label"] = "개 (EA)" if item["unit"] == "EA" else item["unit"]
        item["inventory_scope_label"] = {
            "LOT": "LOT별 재고 근거 있음",
            "ITEM_AGGREGATE_LOTS_UNKNOWN": "품목 합계 · LOT별 내역 미확인",
            "UNKNOWN": "재고 범위 미확인",
        }[scope]
        item["quantity_status"] = ("UNKNOWN" if available is None else
                                   "SHORTAGE" if required > available else "COVERED")
        item["planned_quantity_status"] = ("UNKNOWN" if available is None else
                                           "SHORTAGE" if planned_required > available else "COVERED")
        if available is None:
            unresolved += len(item["row_ids"])
        result["items"].append(item)
    result["unknown_shortage_count"] = unresolved
    result["full_coverage"] = unresolved == 0 and len(source_rows) > 0
    result["state"] = "CALCULATED" if result["full_coverage"] else "PARTIAL"
    return result
