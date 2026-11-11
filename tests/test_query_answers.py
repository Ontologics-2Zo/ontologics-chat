"""Independent CQ examples: changed snapshots, units, uncertainty and no writes."""

from copy import deepcopy
from decimal import Decimal
import unittest

from service.imports import convert_baseline
from service.query_answers import number, answer_query
from service.query_contracts import QueryAnswerBlock, QueryPrerequisite
from service.materials import requirements_for_snapshot
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

    def test_detail_reads_answer_from_recorded_values_only(self):
        quality = self.answer("불량률은 얼마야?")
        self.assertEqual((quality["status"], quality["intent"]), ("ANSWERED", ["quality"]))
        self.assertRegex(quality["answer"], r"불량률은 [\d.]+%")
        self.assertIn("가정", quality["answer"])  # planned yield is kept apart from the actual rate
        transit = self.answer("운송 중인 보드는 몇 대야?")
        self.assertEqual((transit["status"], transit["intent"]), ("ANSWERED", ["transit"]))
        self.assertIn("근거는 원장에 없습니다", transit["answer"])
        substitute = self.answer("대체 후보는 있어?")
        self.assertEqual(substitute["intent"], ["substitute"])
        self.assertIn(substitute["status"], {"ANSWERED", "UNKNOWN"})
        if substitute["status"] == "ANSWERED":
            self.assertIn("확보량에 넣지 않습니다", substitute["answer"])
        position = self.answer("어디에 들어가?")
        self.assertEqual(position["intent"], ["position"])
        unknown = self.answer("IM-9999는 어디에 들어가?")
        self.assertIn(unknown["status"], {"UNKNOWN", "NEEDS_CLARIFICATION"})  # never guessed

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
        before = deepcopy(self.view)
        result = self.answer("남은 물량을 기한 안에 납품할 수 있을까? 부족한 자재도 알려줘")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("17대", result["answer"])
        self.assertIn("11대", result["answer"])
        self.assertIn("미확인", result["answer"])
        self.assertIsNone(result["calculation"]["material_requirements"]["bom_id"])
        self.assertEqual(result["calculation"]["material_requirements"]["state"], "BLOCKED_BOM")
        self.assertEqual(result["conclusion"], "현재 수량 확인 · BOM 등록 후 소요·부족 계산 · 납기 판단 보류")
        self.assertIn("BOM 원본을 등록하고 검토·승인·원장 반영을 마친 뒤", result["answer"])
        self.assertNotIn("조건부 부족은 확인", result["conclusion"])
        self.assertEqual(result["calculation"]["basis"]["planned_input_qty"], 12)
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
        self.assertIsNone(result["calculation"]["estimated_arrival"])
        self.assertIsNone(result["calculation"]["delivery_probability"])
        delivery = next(section for section in result["sections"] if section["id"] == "delivery")
        self.assertTrue({"실제 가용 재고·확정 조달", "생산능력·착수·작업 달력", "출하 준비·운송·고객 도착"}
                        <= {row["label"] for row in delivery["items"]})
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["ledger_mutations"], 0)
        self.assertEqual(self.view, before)

    def test_registered_bom_delivery_limits_conclusion_to_calculated_items(self):
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        self.assertEqual(result["conclusion"], "현재 수량·계산 가능한 품목의 부족 확인 · 실제 납기는 판단 보류")
        self.assertNotIn("BOM 원본을 등록하고 검토·승인·원장 반영을 마친 뒤", result["answer"])
        self.assertEqual(result["calculation"]["material_requirements"]["bom_id"], "test-bom")

    def test_registered_bom_with_no_known_shortages_does_not_claim_confirmation(self):
        # Registration does not imply that any item application is resolved.
        self.view["material_requirements"]["items"] = []
        self.view["material_requirements"].update(applicable_count=0, unresolved_count=2,
                                                  unknown_shortage_count=2)
        before = deepcopy(self.view)
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        self.assertEqual(result["conclusion"], "원본 등록 완료 · BOM 적용 검토 필요 · 납기 판단 보류")
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
        self.assertIn("17대", result["answer"])
        self.assertEqual(self.view, before)

    def test_mapped_bom_without_stock_evidence_still_has_unknown_shortage_summary(self):
        self.view["inventory"] = []
        result = self.answer("수율 90%로 자재 부족과 납기 가능성을 알려줘")
        self.assertTrue(result["calculation"]["material_requirements"]["items"])
        self.assertIsNone(result["calculation"]["material_requirements"]["items"][0]["shortage_qty"])
        self.assertIn("소요·부족 미확인", result["conclusion"])
        self.assertEqual(result["status"], "PARTIAL")

    def test_calculated_zero_shortage_is_known_but_delivery_remains_unknown(self):
        self.view["material_requirements"]["items"][0]["shortage_qty"] = "0"
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        self.assertIn("계산 가능한 품목의 부족 확인", result["conclusion"])
        self.assertEqual(result["calculation"]["material_requirements"]["items"][0]["shortage_qty"], "0")
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")

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

    def recalculate_bom(self):
        self.view["material_requirements"] = requirements_for_snapshot({
            "opening_balance": {"delivery_round": {"remaining_good_target_qty": self.view["summary"]["production_remaining_qty"]},
                                "inventory": self.view["inventory"]},
            "bom": self.view["bom"], "entities": self.view["entities"]})

    def test_unregistered_bom_action_is_distinct_from_registered_unreviewed_source(self):
        self.view = query_view(with_bom=False)
        missing = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        first = QueryPrerequisite.model_validate(missing["prerequisite"])
        self.assertEqual(first.reason, "REGISTERED_BOM_MISSING")
        self.assertEqual(first.next_action.type, "REGISTER_BOM")
        self.assertIsNone(first.bom)
        self.assertIn("계산을 보류", str(missing["steps"]))
        self.assertNotIn("계산했습니다", str(missing["steps"]))

        self.view = query_view()
        for row in self.view["bom"]["rows"]:
            row["decision"] = {"application_status": "UNRESOLVED", "review_basis": "UNREVIEWED"}
        self.recalculate_bom()
        before = deepcopy(self.view)
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        gate = QueryPrerequisite.model_validate(result["prerequisite"])
        self.assertEqual((gate.status, gate.reason), ("BLOCKED", "NO_REVIEWED_APPLICABLE_ROWS"))
        self.assertEqual(gate.next_action.type, "REVIEW_BOM_APPLICATION")
        self.assertEqual(gate.bom.model_dump(), {"document_id": "test-bom", "version": 3, "payload_hash": "b" * 64})
        self.assertEqual((gate.counts.source_rows, gate.counts.applicable_rows, gate.counts.calculation_items), (2, 0, 0))
        self.assertIn("원본은 등록됐지만", result["answer"])
        self.assertIn("적용 대상·사내 품목 연결·수량·단위", result["answer"])
        self.assertNotIn("0개 품목", result["answer"])
        self.assertNotIn("94%", result["answer"])
        self.assertNotIn("12대", result["answer"])
        self.assertIn("17대", result["answer"]); self.assertIn("11대", result["answer"])
        self.assertEqual(result["calculation"]["basis"]["planned_input_qty"], 12)
        self.assertEqual(result["calculation"]["material_requirements"]["rows"], before["material_requirements"]["rows"])
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
        self.assertEqual(result["meta"], before["meta"])
        self.assertEqual(result["evidence_refs"][1]["payload_hash"], gate.bom.payload_hash)
        self.assertIn("새 검토 초안", str(result["sections"]))
        self.assertEqual(self.view, before)

    def test_partial_application_preserves_calculated_items_and_all_remaining_rows(self):
        before = deepcopy(self.view)
        result = self.answer("자재 부족과 납기 가능성을 알려줘")
        gate = QueryPrerequisite.model_validate(result["prerequisite"])
        self.assertEqual((gate.status, gate.reason), ("PARTIAL", "APPLICATION_REVIEW_INCOMPLETE"))
        self.assertEqual((gate.counts.source_rows, gate.counts.applicable_rows, gate.counts.unresolved_rows, gate.counts.calculation_items), (2, 1, 1, 1))
        self.assertEqual(result["calculation"]["material_requirements"], before["material_requirements"])
        self.assertIn("4.5", str(next(s for s in result["sections"] if s["id"] == "materials")))
        self.assertEqual(gate.next_action.type, "REVIEW_BOM_APPLICATION")
        self.assertEqual(result["ledger_mutations"], 0)

    def test_selected_unmapped_item_does_not_make_the_whole_bom_unapplied(self):
        self.view["entities"].append({"id": "unmapped-item", "type": "InternalItem", "display_id": "IM-SYNTHETIC", "attributes": {}})
        result = self.answer("선택 품목 자재 소요와 부족은?", context_entity_id="unmapped-item")
        gate = QueryPrerequisite.model_validate(result["prerequisite"])
        self.assertEqual(gate.reason, "APPLICATION_REVIEW_INCOMPLETE")
        self.assertEqual(gate.counts.calculation_items, 1)
        self.assertEqual(gate.selected_calculation_items, 0)
        self.assertEqual(gate.selected_item_ids, ["IM-SYNTHETIC"])
        self.assertIn("선택 품목은", result["answer"])
        self.assertNotIn("검토된 적용 행이 없어", result["answer"])

    def test_all_reviewed_exclusions_are_not_an_absent_or_unreviewed_source(self):
        for row, application in zip(self.view["bom"]["rows"], ("NOT_MOUNTED", "SETTING")):
            row["decision"] = {"application_status": application, "review_basis": "SOURCE_VERIFIED"}
        self.recalculate_bom()
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        gate = QueryPrerequisite.model_validate(result["prerequisite"])
        self.assertEqual(gate.reason, "NO_APPLICABLE_COMPONENTS")
        self.assertEqual(gate.counts.unresolved_rows, 0)
        self.assertIsNotNone(gate.bom)
        self.assertEqual(gate.next_action.type, "REVIEW_BOM_APPLICATION")
        self.assertIn("비실장·설정 판단", result["answer"])
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")

    def test_complete_source_application_is_not_manufacturing_approval(self):
        self.view["bom"]["rows"][1]["decision"] = {"application_status": "SETTING", "review_basis": "SOURCE_VERIFIED"}
        self.recalculate_bom()
        result = self.answer("자재가 부족한지 알려줘")
        self.assertEqual(result["prerequisite"]["reason"], "TECHNICAL_REVIEW_UNCONFIRMED")
        self.assertIn("제조 기술검수는 확인되지 않았습니다", str(result["sections"]))
        self.assertEqual(result["calculation"]["material_requirements"]["manufacturing_feasibility"], "UNKNOWN")

    def test_explicit_production_or_what_if_keeps_exact_assumption_in_primary_answer(self):
        for question in ("남은 17대 납기와 추가 생산 투입은?", "양품 19대가 목표면 수율 50%로 납기 가능해?"):
            result = self.answer(question)
            basis = result["calculation"]["basis"]
            self.assertIn(f"{basis['planned_input_qty']}대", result["answer"])
            self.assertIn("보장하지 않습니다", result["answer"])
        normal = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        self.assertNotIn("94%", normal["answer"])
        self.assertEqual(next(s for s in normal["sections"] if s["id"] == "production")["items"][1]["value"], "94%")

    def test_known_material_query_keeps_its_numeric_detail_and_does_not_become_only_a_review_prompt(self):
        result = self.answer("재고 빼면 어떤 자재가 얼마나 모자라?")
        self.assertIn("조회한 1개 품목", result["answer"])
        self.assertNotIn("먼저 검토해 주세요", result["answer"])
        rows = next(s for s in result["sections"] if s["id"] == "materials")["items"]
        self.assertIn("부족 4.5 / 6", rows[0]["value"])
        self.assertEqual(result["prerequisite"]["counts"]["calculation_items"], 1)

    def test_blocked_delivery_cards_stop_at_application_review_with_snapshot_quantities(self):
        for row in self.view["bom"]["rows"]:
            row["decision"] = {"application_status": "UNRESOLVED", "review_basis": "UNREVIEWED"}
        self.recalculate_bom()
        before = deepcopy(self.view)
        result = self.answer("남은 17대 납기 안에 도착할 수 있어?")
        blocks = [QueryAnswerBlock.model_validate(block) for block in result["answer_blocks"]]
        self.assertEqual([block.id for block in blocks], ["delivery_balance", "defect_basis", "bom_next_action", "verdict"])
        self.assertEqual([block.kind for block in blocks], ["normal", "normal", "attention", "verdict"])
        self.assertEqual(blocks[2].action, "REVIEW_BOM_APPLICATION")
        balance = " ".join(blocks[0].paragraphs)
        for field in ("promise_remaining_qty", "production_remaining_qty", "already_produced_qty"):
            self.assertIn(f"{self.view['summary'][field]}대", balance)
        self.assertIn(self.view["summary"]["delivery_round_id"], balance)
        self.assertIn("검토된 적용 행이 없어", " ".join(blocks[2].paragraphs))
        self.assertIn("납기 판단 보류", blocks[3].title)
        self.assertIn("새 납기 약속으로 저장하지 않습니다", " ".join(blocks[3].paragraphs))
        self.assertNotIn("schedule_backward", [block.id for block in blocks])
        self.assertEqual(result["calculation"]["material_requirements"], before["material_requirements"])
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")
        self.assertEqual(result["meta"], before["meta"])
        self.assertEqual(self.view, before)

    def test_missing_source_card_requests_registration_without_claiming_registered_rows(self):
        self.view = query_view(with_bom=False)
        result = self.answer("남은 물량을 납기 안에 도착시킬 수 있어?")
        blocks = result["answer_blocks"]
        self.assertEqual([b["id"] for b in blocks], ["delivery_balance", "defect_basis", "bom_missing", "verdict"])
        self.assertIn("반영된 BOM이 없어", " ".join(blocks[2]["paragraphs"]))
        self.assertEqual(blocks[2]["title"], "BOM 전개 불가")
        self.assertEqual(blocks[2]["action"], "REGISTER_BOM")
        self.assertEqual(blocks[3]["title"], "BOM 미등록 · 납기 판단 보류")
        self.assertNotIn("등록 원문은", str(blocks))
        self.assertIsNone(result["prerequisite"]["bom"])

    def test_partial_delivery_cards_follow_reference_order_with_exact_quantities(self):
        result = self.answer("자재 부족과 납기 가능성을 알려줘")
        blocks = [QueryAnswerBlock.model_validate(block) for block in result["answer_blocks"]]
        self.assertEqual([b.id for b in blocks][:4], ["delivery_balance", "defect_basis", "material_shortage", "schedule_backward"])
        self.assertEqual(blocks[-1].id, "verdict")
        defect = " ".join(blocks[1].paragraphs)
        summary = self.view["summary"]
        judged = summary["good_qty"] + summary["defect_qty"]
        self.assertIn(f"판정된 {judged}대 중 불량 {summary['defect_qty']}대", defect)
        self.assertIn("가정 계산이며 양품을 보장하지 않습니다", defect)
        table = blocks[2].table
        self.assertEqual(table.columns, ["위치", "품목", "필요", "가용", "부족"])
        item = result["calculation"]["material_requirements"]["items"][0]
        # Planned-input basis: the same numbers as the shared calculation, not a recomputation.
        self.assertEqual(table.rows[0][1:], [item["item_id"], number(item["planned_required_qty"]), "12", "6"])
        self.assertEqual(table.alert_cells, [[0, 4]])
        self.assertIn("부족 0으로 보지 않습니다", " ".join(blocks[2].paragraphs))
        schedule = blocks[3]
        self.assertEqual((schedule.kind, schedule.tool), ("assumption", "원장 조회 · 일정 · 견적"))
        # One row per stage backward from the due date, filled from the ledger where it can be.
        rows = schedule.table.rows
        self.assertEqual([row[0] for row in rows][1:6], ["운송", "출하 준비", f"생산 {number(result['calculation']['basis']['planned_input_qty'])}대", "수입검사", "부품 입하 필요일"])
        self.assertEqual([row[2] for row in rows][1:6], ["근거 없음"] * 4 + ["계산 불가"])
        self.assertTrue(all(cell[1] == 2 for cell in schedule.table.alert_cells))
        # Durations for stages without evidence are never filled in.
        self.assertNotRegex(" ".join(" ".join(row) for row in rows[1:6]), r"\d+\s*(영업)?일")
        # Parts arrival comes from a quote's lead time; the gap is calendar days to the due date.
        parts = rows[6]
        self.assertTrue(parts[0].startswith("부품 입하 예상"))
        if parts[2] == "시연 견적 가정":
            self.assertRegex(parts[1], r"견적 \d+영업일 → \d{4}-\d{2}-\d{2}$")
            self.assertTrue(any("공급사 확약이 아닙니다" in note for note in schedule.paragraphs))
        else:
            self.assertEqual(parts[1:], ["수량을 충족하는 견적 없음", "미확인"])
        self.assertEqual(blocks[-1].title, "자재 부족 6개 · 납기 판단 보류")
        self.assertEqual(item["planned_shortage_qty"], "6.0")
        direct = self.answer("재고 빼면 어떤 자재가 얼마나 모자라?")
        self.assertNotIn("answer_blocks", direct)
        self.assertIn("부족 4.5 / 6", str(next(s for s in direct["sections"] if s["id"] == "materials")))

    def test_explicit_delivery_what_if_cards_preserve_exact_yield_and_planned_comparison(self):
        result = self.answer("양품 19대가 목표면 수율 50%로 납기 가능해?")
        defect = " ".join(result["answer_blocks"][1]["paragraphs"])
        self.assertIn("50%", defect); self.assertIn("38대", defect)
        self.assertIn("양품을 보장하지 않습니다", defect)
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")


class BusinessDayTests(unittest.TestCase):
    def test_scenario_schedule_runs_forward_from_parts_and_backward_from_the_due_date(self):
        from service.query_answers import _scenario_schedule
        scenario = {"content": {"scenario_ref": "SCN-1", "inspection_business_days": 1, "daily_capacity": 20,
                                "ship_prep_business_days": 1, "transit_days": 2}}
        plan = _scenario_schedule(scenario, {"date": "2026-11-13"}, "2026-11-27", "43")
        self.assertEqual((plan["inspected"], plan["production_days"], plan["produced"], plan["shipped"], plan["arrives"]),
                         ("2026-11-16", 3, "2026-11-19", "2026-11-20", "2026-11-22"))
        self.assertEqual((plan["slack"], plan["latest_parts"]), (5, "2026-11-18"))
        late = _scenario_schedule({"content": {**scenario["content"], "daily_capacity": 5}}, {"date": "2026-11-13"}, "2026-11-27", "43")
        self.assertLess(late["slack"], 0)  # a slower assumption shows the overrun, not a hidden pass

    def test_weekends_are_skipped_like_the_ledger_quote_dates(self):
        from datetime import date
        from service.query_answers import _add_business_days
        start = date(2026, 11, 6)  # Friday
        self.assertEqual([_add_business_days(start, days).isoformat() for days in (3, 5, 10)],
                         ["2026-11-11", "2026-11-13", "2026-11-20"])


if __name__ == "__main__":
    unittest.main()
