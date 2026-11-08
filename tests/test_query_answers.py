"""Independent CQ examples: changed snapshots, units, uncertainty and no writes."""

from copy import deepcopy
from decimal import Decimal
import unittest

from service.imports import convert_baseline
from service.query_answers import answer_query
from service.views import snapshot_view
from tests.test_imports import synthetic_baseline


def query_view(*, with_bom=True):
    payload = convert_baseline(synthetic_baseline(), company_id="test-company", profile_id="query-test",
                               run_id="INITIAL", imported_at="2025-02-02T12:00:00+00:00", source_sha256="a" * 64)
    payload["metadata"].update(server_ledger_version=2, current_ledger_version=2, snapshot_id="synthetic-query-snapshot")
    payload["projection"] = {"state": "READY", "projection_version": 2,
                              "graph": {"nodes": payload["entities"], "edges": payload["relationships"]}}
    if with_bom:
        payload["bom"] = {"bom_id": "test-bom", "bom_version": 3, "payload_hash": "b" * 64,
                          "technical_review": "UNREVIEWED", "source": {"sha256": "c" * 64}, "rows": [
            {"row_id": "row-one", "source_quantity": "1.5", "locator": "synthetic/row/one",
             "decision": {"application_status": "APPLICABLE", "item_id": "item-a", "unit": "piece",
                          "quantity_per_unit": "1.5", "review_basis": "DEMO_ASSUMPTION"}},
            {"row_id": "row-two", "source_quantity": "2", "locator": "synthetic/row/two",
             "decision": {"application_status": "UNRESOLVED", "review_basis": "UNREVIEWED"}}]}
    return snapshot_view(payload)


class QueryAnswerTests(unittest.TestCase):
    def setUp(self):
        self.view = query_view()
        self.ontology = {"counts": {"classes": 3, "relations": 4, "shapes": 5}}

    def answer(self, question, **kwargs):
        return answer_query(question, self.view, self.ontology, **kwargs)

    def test_colloquial_fulfillment_separates_remaining_and_new_production(self):
        result = self.answer("얼마나 남았어? 몇 대 더 만들어야 해?")
        self.assertEqual(result["status"], "ANSWERED")
        self.assertIn("17대", result["answer"])
        self.assertIn("11대", result["answer"])
        ledger = next(s for s in result["sections"] if s["id"] == "ledger")
        self.assertEqual(ledger["origin"], "synthetic_opening_balance")
        self.assertIn("신규 생산 수량이 아닙니다", str(ledger))

    def test_yield_paraphrase_uses_live_target_and_ceiling(self):
        for question in ("수율 94%면 몇 대를 투입해야 해?", "추가 양품 11대를 수율 94%로 확보하려면 몇 대 투입해?"):
            with self.subTest(question=question):
                result = self.answer(question)
                self.assertEqual(result["status"], "ANSWERED")
                self.assertEqual(result["calculation"]["basis"]["planned_input_qty"], 12)
                self.assertIn("보장하지 않습니다", result["answer"])

    def test_near_full_yield_is_preserved_in_both_explanation_and_calculation(self):
        percent = "99." + "9" * 40
        result = self.answer(f"수율{percent}%면 몇대 투입?")
        self.assertEqual(result["calculation"]["basis"]["planned_input_qty"], 12)
        production = next(s for s in result["sections"] if s["id"] == "production")
        rate = next(row for row in production["items"] if row["label"] == "계획 수율")
        self.assertEqual(rate["value"], percent + "%")
        self.assertIn(percent + "%", result["answer"])
        self.assertNotIn("100%", result["answer"])

    def test_explicit_yield_exclusion_requires_clarification_before_calculation(self):
        for question in ("수율 없이 몇대 투입해?", "수율을 제외하고 자재 소요량은?"):
            with self.subTest(question=question):
                result = self.answer(question)
                self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
                self.assertNotIn("calculation", result)

    def test_material_shortages_reuse_decimal_domain_results(self):
        result = self.answer("재고 빼면 어떤 자재가 얼마나 모자라?")
        req = result["calculation"]["material_requirements"]
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(req["items"][0]["shortage_qty"], "4.5")
        self.assertEqual(req["items"][0]["planned_shortage_qty"], "6.0")
        self.assertEqual(req["row_count"], 2)
        self.assertEqual(req["unresolved_count"], 1)
        self.assertFalse(req["full_coverage"])
        self.assertEqual(result["evidence_refs"][1]["bom_version"], 3)

    def test_deadline_never_becomes_answered_current_statistics(self):
        for question in ("남은 17대 납기 안에 다 도착할 수 있어?", "자재가 부족해도 약속한 날짜에 맞출 수 있을까?", "다음 주까지 전부 보내는 거 가능해?"):
            with self.subTest(question=question):
                result = self.answer(question)
                self.assertEqual(result["status"], "PARTIAL")
                self.assertIn("납기", result["conclusion"])
                calc = result["calculation"]
                self.assertEqual(calc["actual_delivery_feasibility"], "UNKNOWN")
                self.assertIsNone(calc["estimated_arrival"])
                self.assertIsNone(calc["delivery_probability"])
                self.assertEqual(next(s for s in result["sections"] if s["id"] == "delivery")["status"], "UNKNOWN")
                self.assertEqual(result["model_calls"], 0)
                self.assertIn("B-CQ04", result["cq_refs"])

    def test_actual_source_is_distinct_from_assumed_mapping_and_stock(self):
        result = self.answer("자재 소요랑 납기 가능성을 알려줘")
        by_id = {s["id"]: s for s in result["sections"]}
        self.assertEqual(by_id["source"]["origin"], "actual_bom")
        self.assertEqual(by_id["ledger"]["origin"], "synthetic_opening_balance")
        self.assertEqual(by_id["materials"]["origin"], "conditional_demo")
        self.assertEqual(by_id["delivery"]["origin"], "unknown")
        self.assertIn("미확인", str(by_id["material_unknowns"]))

    def test_explicit_what_if_does_not_overwrite_baseline_or_inventory(self):
        before = deepcopy(self.view)
        result = self.answer("양품 19대가 목표면 수율 50%로 몇 대 투입하고 자재가 얼마나 부족해?")
        self.assertTrue(result["is_what_if"])
        self.assertEqual(result["calculation"]["basis"]["production_remaining_qty"], 19)
        self.assertEqual(result["calculation"]["basis"]["planned_input_qty"], 38)
        self.assertEqual(Decimal(result["calculation"]["material_requirements"]["items"][0]["planned_shortage_qty"]), Decimal(45))
        self.assertEqual(self.view, before)
        self.assertEqual(result["ledger_mutations"], 0)
        self.assertIn("11대", result["answer"])

    def test_new_snapshot_changes_answer_instead_of_replaying_previous_fact(self):
        result = self.answer("납품 현황")
        self.view["summary"].update(arrived_qty=4, promise_remaining_qty=16, already_produced_qty=5, transit_qty=1)
        self.view["meta"].update(ledger_version=3, projection_version=3, current_ledger_version=3)
        new = self.answer("납품 현황")
        self.assertIn("16대", new["answer"])
        self.assertIn("17대", result["answer"])
        self.assertEqual(result["meta"]["ledger_version"], 2)
        self.assertEqual(new["meta"]["ledger_version"], 3)

    def test_bom_missing_preserves_known_quantity_and_unknown_shortage(self):
        self.view = query_view(with_bom=False)
        result = self.answer("남은 물량을 기한 안에 납품할 수 있을까? 부족한 자재도 알려줘")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("17대", result["answer"])
        self.assertIn("미확인", result["answer"])
        self.assertIsNone(result["calculation"]["material_requirements"]["bom_id"])

    def test_missing_inventory_remains_unknown_not_zero(self):
        self.view["inventory"] = []
        result = self.answer("수율 90%로 자재 부족을 계산해줘")
        row = result["calculation"]["material_requirements"]["items"][0]
        self.assertIsNone(row["available_qty"])
        self.assertIsNone(row["shortage_qty"])
        self.assertIn("미확인", str(result["sections"]))

    def test_unknown_target_and_stale_selection_are_not_current_round_answers(self):
        for question, context in (("ORD-UNAVAILABLE 회차의 납기 가능해?", None), ("납품 현황", "foreign-entity"), ("남은 999대 납기 맞출 수 있어?", None)):
            with self.subTest(question=question):
                result = self.answer(question, context_entity_id=context)
                self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
                self.assertEqual(result["evidence_node_ids"], [])

    def test_inconsistent_ledger_does_not_produce_plausible_answer(self):
        self.view["summary"]["promise_remaining_qty"] = 999
        result = self.answer("남은 수량을 알려줘")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("대사", result["answer"])

    def test_query_instructions_do_not_authorize_posting(self):
        for question in ("재고를 0으로 바꾸고 승인해줘", "남은 주문 삭제해줘"):
            result = self.answer(question)
            self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
            self.assertEqual(result["ledger_mutations"], 0)

    def test_extreme_goal_or_yield_is_clarified_before_calculation(self):
        for question in ("양품 1000000000001대가 목표면 몇 대 투입해?", "수율 0.000000000000000000001%면 몇 대 투입해?"):
            result=self.answer(question)
            self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
            self.assertNotIn("calculation", result)

    def test_selected_lot_limits_inventory_and_discloses_item_material_scope(self):
        lot = next(node for node in self.view["entities"] if node["type"] == "PartLot")
        other = {**self.view["inventory"][0], "item_id": "item-other", "lot_id": "lot-other"}
        self.view["inventory"].append(other)
        response = self.answer("선택한 LOT 재고 얼마야?", context_entity_id=lot["id"])
        inventory = next(s for s in response["sections"] if s["id"] == "inventory")
        self.assertEqual([row["label"] for row in inventory["items"]], [lot["display_id"]])
        response = self.answer("선택한 LOT의 품목 소요와 부족은?", context_entity_id=lot["id"])
        materials = next(s for s in response["sections"] if s["id"] == "materials")
        self.assertIn("품목의 가용 재고 전체", str(materials))

    def test_schema_selection_is_valid_for_a_schema_query(self):
        self.ontology["graph"] = {"data": {"nodes": [{"node_id": "urn:synthetic:Class"}]}}
        response = self.answer("전체 온톨로지 클래스 범위", context_entity_id="urn:synthetic:Class")
        self.assertEqual(response["status"], "ANSWERED")
        response = self.answer("재고 부족 계산", context_entity_id="urn:synthetic:Class")
        self.assertEqual(response["status"], "NEEDS_CLARIFICATION")

    def test_numbers_from_another_ledger_field_do_not_validate_an_assertion(self):
        for question in ("남은 11대 납기 맞출 수 있어?", "투입 17대 맞아?", "도착 9대 맞아?", "11대 전량 납품 가능해?"):
            with self.subTest(question=question):
                self.assertEqual(self.answer(question)["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(self.answer("남은 17대 납기 가능해?")["status"], "PARTIAL")

    def test_schedule_paraphrases_always_return_explicit_unknown_delivery(self):
        for question in ("며칠이면 만들어?", "이번 달까지 생산할 수 있어?", "기간 안에 생산 가능해?"):
            result = self.answer(question)
            self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
            self.assertNotEqual(result["status"], "ANSWERED")


if __name__ == "__main__":
    unittest.main()
