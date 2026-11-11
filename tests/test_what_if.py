"""T12 what-if: supply schedule and substitute assumptions compared with the ledger basis."""

from copy import deepcopy
import unittest

from service.query_answers import answer_query
from service.query_contracts import QueryAnswerBlock
from service.query_intents import interpret_question
from tests.test_query_answers import query_view


class WhatIfIntentTests(unittest.TestCase):
    def test_supply_shift_is_read_in_business_days(self):
        cases = {"부품 입하가 3영업일 늦어지면 결과는?": 3, "공급이 1주 늦어지면?": 5,
                 "입하가 2일 빨라지면 부족은?": -2, "견적 리드타임이 4일 지연되면?": 4}
        for question, days in cases.items():
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.supply_delay_days, days)
                self.assertTrue(plan.is_what_if)
                self.assertIn("materials", plan.intents)
                self.assertIsNone(plan.clarification)
                self.assertEqual(plan.quantity_mentions, ())  # the shift is not a ledger quantity claim

    def test_vague_or_excessive_conditions_ask_for_values(self):
        for question in ("공급이 늦어지면 어떻게 돼?", "입하가 90영업일 늦어지면?", "수율·공급 일정·대체품 조건을 바꾸면 결과는?"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)

    def test_substitute_assumption_differs_from_a_substitute_lookup(self):
        self.assertTrue(interpret_question("대체품까지 쓰면 부족은?").substitute_assumed)
        lookup = interpret_question("대체품 재고를 활용할 수 있어?")
        self.assertFalse(lookup.substitute_assumed)
        self.assertFalse(lookup.is_what_if)


class WhatIfAnswerTests(unittest.TestCase):
    def setUp(self):
        self.view = query_view()
        self.ontology = {"counts": {"classes": 3, "relations": 4, "shapes": 5}}

    def answer(self, question, view=None):
        return answer_query(question, view or self.view, self.ontology)

    def test_supply_delay_compares_without_touching_the_ledger(self):
        before = deepcopy(self.view)
        result = self.answer("부품 입하가 3영업일 늦어지면 결과는?")
        self.assertEqual(self.view, before)
        self.assertEqual((result["status"], result["ledger_mutations"], result["model_calls"]), ("PARTIAL", 0, 0))
        self.assertTrue(result["is_what_if"])
        blocks = [QueryAnswerBlock.model_validate(block) for block in result["answer_blocks"]]
        self.assertEqual([block.id for block in blocks][:2], ["what_if_scope", "what_if_compare"])
        self.assertEqual(blocks[-1].title, "가정 비교 · 납기 판단 보류")
        compare = blocks[1].table.rows
        self.assertEqual(compare[-1], ["납기 판단", "보류", "보류"])
        # No approved schedule scenario in this ledger: reported as missing data, not guessed.
        gaps = next(block for block in blocks if block.id == "what_if_gaps").paragraphs
        self.assertTrue(any("일정 가정" in text for text in gaps))
        self.assertEqual(result["calculation"]["what_if"]["supply_delay_business_days"], 3)
        self.assertEqual(result["calculation"]["actual_delivery_feasibility"], "UNKNOWN")

    def test_yield_with_delay_changes_the_planned_shortage_only_in_the_copy(self):
        result = self.answer("수율 50%에 입하가 2영업일 늦어지면?")
        what_if = result["calculation"]["what_if"]
        self.assertNotEqual(what_if["planned_shortage_before"], what_if["planned_shortage_after"])
        self.assertEqual(result["calculation"]["basis"]["plan_yield"], self.view["material_requirements"]["basis"]["plan_yield"])
        self.assertIn("계획 수율", result["answer"])

    def test_unreviewed_substitutes_are_not_counted(self):
        view = deepcopy(self.view)
        view["substitutes"] = [{"original_item_id": "item-a", "candidate_item_id": "item-sub", "candidate_on_hand_qty": 100,
                                "unit": "piece", "status": "NOT_REVIEWED", "counted_qty_for_selected_demand": 0}]
        result = self.answer("대체품까지 쓰면 부족은?", view)
        what_if = result["calculation"]["what_if"]
        self.assertEqual(what_if["planned_shortage_before"], what_if["planned_shortage_after"])
        self.assertEqual(what_if["substitute_counted_qty"], "0")
        gaps = next(block for block in result["answer_blocks"] if block["id"] == "what_if_gaps")["paragraphs"]
        self.assertTrue(any("item-sub" in text and "승인 전" in text for text in gaps))

    def test_an_approved_substitute_is_already_in_the_basis_and_not_counted_twice(self):
        view = deepcopy(self.view)
        view["substitutes"] = [{"original_item_id": "item-a", "candidate_item_id": "item-sub", "candidate_on_hand_qty": 100,
                                "unit": "piece", "status": "APPROVED", "counted_qty_for_selected_demand": 2}]
        result = self.answer("대체품까지 쓰면 부족은?", view)
        what_if = result["calculation"]["what_if"]
        self.assertEqual(what_if["planned_shortage_before"], what_if["planned_shortage_after"])
        gaps = next(block for block in result["answer_blocks"] if block["id"] == "what_if_gaps")["paragraphs"]
        self.assertTrue(any("이미 승인·반영" in text for text in gaps))

    def test_delivery_answer_names_an_approved_substitute_in_the_available_stock(self):
        view = deepcopy(self.view)
        row = next(row for row in view["material_requirements"]["items"] if row["item_id"] == "item-a")
        row.update(stock_available_qty=row["available_qty"], substitute_qty="100",
                   available_qty=str(float(row["available_qty"]) + 100), shortage_qty="0", planned_shortage_qty="0")
        result = self.answer("남은 물량 납기 안에 도착 가능해?", view)
        self.assertIn("승인된 대체 100개를 포함했습니다", result["answer"])
        shortage = next(block for block in result["answer_blocks"] if block["id"] == "material_shortage")
        self.assertTrue(any("승인된 대체" in text and "기술검수가 아닙니다" in text for text in shortage["paragraphs"]))

    def test_no_bom_means_no_comparison(self):
        result = answer_query("입하가 3영업일 늦어지면?", query_view(with_bom=False), self.ontology)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertNotIn("answer_blocks", result)


if __name__ == "__main__":
    unittest.main()


class SubstituteReservationTests(unittest.TestCase):
    def test_reservation_impact_counts_the_current_approved_substitute(self):
        from service.reservations import reservation_impacts
        requirements = {"items": [{"item_id": "IM-1", "unit": "EA", "state": "CALCULATED", "required_qty": "40",
                                   "planned_required_qty": "43", "substitute_qty": "23"}]}
        reservation = {"reservation_id": "r1", "ledger_version": 11, "item_id": "IM-1", "unit": "EA", "lot_id": "LOT-1",
                       "quantity": 5, "demand_ref": "other", "item_available_before": 20, "item_available_after": 15,
                       "before": {"reserved_other_qty": 30, "available_qty": 20}, "after": {"on_hand_qty": 50, "reserved_other_qty": 35, "available_qty": 15}}
        impact = reservation_impacts(requirements, [reservation])[0]
        # 43 needed − (stock 20 + substitute 23) = 0 → after the reservation 43 − (15 + 23) = 5.
        self.assertEqual((impact["planned_shortage_before"], impact["planned_shortage_after"], impact["substitute_qty"]), ("0", "5", "23"))
