"""DEC-H47 read CQs: reservation change cause and sourcing re-comparison."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest
from uuid import uuid4

from service.contracts import content_hash
from service.imports import convert_baseline
from service.query_answers import answer_query
from service.query_intents import interpret_question
from service.reservations import apply_reservation_event
from service.views import snapshot_view
from tests.test_imports import synthetic_baseline


def base_payload():
    payload = convert_baseline(synthetic_baseline(), company_id="test-company", profile_id="query-test",
                               run_id="INITIAL", imported_at="2025-02-02T12:00:00+00:00", source_sha256="a" * 64)
    payload["metadata"]["server_ledger_version"] = 1
    payload["bom"] = {"bom_id": "test-bom", "bom_version": 3, "payload_hash": "b" * 64,
                      "technical_review": "UNREVIEWED", "source": {"sha256": "c" * 64}, "rows": [
        {"row_id": "row-one", "source_quantity": "1.5", "locator": "synthetic/row/one",
         "decision": {"application_status": "APPLICABLE", "item_id": "item-a", "unit": "piece",
                      "quantity_per_unit": "1.5", "review_basis": "DEMO_ASSUMPTION"}}]}
    return payload


def reserve(payload, quantity=5):
    lot = next(row for row in payload["opening_balance"]["inventory"] if row.get("lot_id") == "lot-a")
    before = {k: lot[k] for k in ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty", "available_qty")}
    after = {**before, "reserved_other_qty": before["reserved_other_qty"] + quantity, "available_qty": before["available_qty"] - quantity}
    item = next(e for e in payload["entities"] if e["type"] == "InternalItem" and e["display_id"] == "item-a")
    lot_entity = next(e for e in payload["entities"] if e["type"] == "PartLot" and e["display_id"] == "lot-a")
    version = payload["metadata"]["server_ledger_version"] + 1
    content = {"kind": "inventory_reservation", "item_id": "item-a", "item_entity_id": item["id"], "lot_id": "lot-a",
               "lot_entity_id": lot_entity["id"], "unit": "piece", "quantity": quantity, "demand_ref": "other-order-1",
               "demand_origin": "SYNTHETIC_OTHER_DEMAND_REFERENCE", "target_round_id": "delivery-a",
               "basis_ledger_version": version - 1, "before": before, "after": after, "note": "test reservation",
               "rule_version": "inventory-reservation/1"}
    reservation_id = str(uuid4())
    event = {"event_id": uuid4(), "event_type": "InventoryReserved", "ledger_version": version,
             "reservation_id": reservation_id, "recorded_at": datetime(2025, 2, 3, tzinfo=timezone.utc),
             "payload": {"event_type": "InventoryReserved", "company_id": "test-company", "run_id": "INITIAL",
                         "ledger_version": version, "reservation_id": reservation_id, "content": content,
                         "payload_hash": content_hash(content)}}
    return apply_reservation_event(payload, event)


def add_quotes(payload):
    until = (date.today() + timedelta(days=30)).isoformat()
    docs = []
    for ref, moq, multiple, limit in (("CMP-A", 5, 5, 40), ("CMP-B", 10, 10, 15), ("CMP-C", 1, 1, 10), ("CMP-D", 5, 5, None)):
        docs.append({"id": str(uuid4()), "kind": "QUOTE", "status": "POSTED", "cancelled_by": None, "version": 1,
                     "payload_hash": "d" * 64, "ledger_version": 1,
                     "content": {"kind": "QUOTE", "quote_ref": ref, "unit": "piece", "unit_price": "2", "currency": "KRW",
                                 "moq": moq, "order_multiple": multiple, "freight": None, "valid_until": until,
                                 "lead_business_days": 3, "supply_limit_qty": limit,
                                 "resolved": {"item_id": "item-a", "supplier_id": "supplier-" + ref[-1].lower()}}})
    payload["procurement"] = {"documents": docs, "receipts": [], "scope": "INTERNAL_DEVELOPMENT_ONLY"}
    return payload


def view_of(payload):
    payload = deepcopy(payload)
    version = payload["metadata"]["server_ledger_version"]
    payload["metadata"].update(current_ledger_version=version, snapshot_id="synthetic-reservation-snapshot")
    payload["projection"] = {"state": "READY", "projection_version": version,
                             "graph": {"nodes": payload["entities"], "edges": payload["relationships"]}}
    return snapshot_view(payload)


class ReservationAnswerTests(unittest.TestCase):
    ontology = {"counts": {"classes": 3, "relations": 4, "shapes": 5}}

    def setUp(self):
        self.before = view_of(add_quotes(base_payload()))
        self.after = view_of(add_quotes(reserve(base_payload())))

    def test_intents_are_read_only(self):
        self.assertIn("change", interpret_question("왜 부족이 28개로 늘었어?").intents)
        self.assertIn("sourcing", interpret_question("기존 23개 안이면 충분해? 어떤 안을 다시 검토할까?").intents)
        self.assertIn("sourcing", interpret_question("예약이 늘어난 지금, 부족 자재 확보안을 다시 비교해 줘.").intents)
        self.assertIsNotNone(interpret_question("23개 발주해줘").clarification)

    def test_change_cause_uses_event_before_after_and_keeps_delivery(self):
        # target 11, plan ceil(11/0.94)=12, 1.5/unit → need 16.5 / 18; available 12 → 7.
        result = answer_query("왜 부족이 11개로 늘었어?", self.after, self.ontology)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("other-order-1", result["answer"])
        self.assertIn("12→7", result["answer"])
        self.assertIn("6→11", result["answer"])
        self.assertIn("4.5→9.5", result["answer"])
        self.assertIn("남은 약속 17대", result["answer"])
        self.assertEqual(result["ledger_mutations"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertTrue(any(ref.get("record_type") == "inventory_reservation" for ref in result["evidence_refs"]))
        self.assertTrue(any(n.startswith("urn:ontologics:reservation:") for n in result["evidence_node_ids"]))

    def test_without_reservation_change_answer_says_none(self):
        result = answer_query("왜 부족이 6개로 늘었어?", self.before, self.ontology)
        self.assertIn("예약 변경 사건이 없습니다", result["answer"])

    def test_sourcing_compares_limits_without_selecting(self):
        result = answer_query("기존 6개 안이면 충분해? 어떤 안을 다시 검토할까?", self.after, self.ontology)
        sourcing = result["calculation"]["sourcing"]
        now = {q["quote_ref"]: q["calculation"] for q in sourcing["current"]}
        before = {q["quote_ref"]: q["calculation"] for q in sourcing["previous"]}
        self.assertEqual((now["CMP-A"]["recommended_qty"], now["CMP-A"]["quantity_condition"], now["CMP-A"]["margin_qty"]), (15, "MET", 4))
        self.assertEqual((before["CMP-A"]["recommended_qty"], before["CMP-A"]["margin_qty"]), (10, 4))
        self.assertEqual((now["CMP-B"]["recommended_qty"], now["CMP-B"]["quantity_condition"]), (20, "NOT_MET"))
        self.assertEqual((before["CMP-C"]["quantity_condition"], now["CMP-C"]["quantity_condition"]), ("MET", "NOT_MET"))
        self.assertEqual(now["CMP-C"]["limit_shortfall_qty"], 1)
        self.assertEqual(now["CMP-D"]["quantity_condition"], "UNKNOWN")
        self.assertIn("5개가 부족해 충분하지 않습니다", result["answer"].replace("piece", "개").replace(" 개", "개"))
        self.assertFalse(result["calculation"]["automatic_order"])
        self.assertIn("자동으로 하지 않으며", result["answer"])

    def test_delivery_answer_reports_material_change_but_stays_unknown(self):
        result = answer_query("남은 17대, 납기 안에 고객에게 도착할 수 있어?", self.after, self.ontology)
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
        self.assertTrue(any(s["id"] == "material_change" for s in result["sections"]))
        self.assertIn("재검토", result["answer"])
        self.assertIn("자재 조건 변경", str(result["answer_blocks"]))
        self.assertIn("개발 원장 사건", str(result["answer_blocks"]))

    def test_suggestions_follow_the_reservation(self):
        self.assertEqual(self.before["suggested_questions"][0], "남은 약속은 몇 대고, 얼마나 더 만들어야 해?")
        self.assertEqual(self.after["suggested_questions"][:2], ["왜 부족이 11개로 늘었어?", "기존 6개 안이면 충분해? 어떤 안을 다시 검토할까?"])


class OrderReferenceTests(unittest.TestCase):
    ontology = {"counts": {"classes": 3, "relations": 4, "shapes": 5}}

    def test_order_prefix_selects_only_the_current_round(self):
        view = view_of(base_payload())
        view["summary"]["delivery_round_id"] = "ORD-90001-01"
        result = answer_query("ORD-90001 남은 17대, 도착기한 안에 가능해?", view, self.ontology)
        self.assertNotEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertIn("ORD-90001의 선택 회차", str(result["sections"]))
        other = answer_query("ORD-90002 남은 17대, 도착기한 안에 가능해?", view, self.ontology)
        self.assertEqual(other["status"], "NEEDS_CLARIFICATION")


class UnchangedShortageTests(unittest.TestCase):
    def test_no_increase_suggestion_when_the_shortage_does_not_grow(self):
        payload = base_payload()
        lot = next(row for row in payload["opening_balance"]["inventory"] if row.get("lot_id") == "lot-a")
        lot.update(on_hand_qty=lot["on_hand_qty"] + 40, available_qty=lot["available_qty"] + 40)
        view = view_of(reserve(payload, quantity=1))
        self.assertEqual(view["reservation_impacts"][-1]["planned_shortage_after"], "0")
        self.assertFalse(any("늘었어" in q for q in view["suggested_questions"]))
