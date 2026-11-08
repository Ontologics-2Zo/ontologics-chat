"""Question paraphrases and unsafe interpretation boundaries, without ledger facts."""

from decimal import Decimal
import unittest

from service.query_intents import interpret_question


class QueryIntentTests(unittest.TestCase):
    def test_colloquial_delivery_has_priority_over_materials(self):
        for question in ("68대 언제 다 보내?", "기한 안에 도착 가능해?", "납기 맞춰?", "자재가 모자라는데 납기 맞출까?", "내일까지 다 보낼 수 있어?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.intents[0], "delivery")
                self.assertIsNone(plan.clarification)
                self.assertIsNone(plan.target_good_qty)
        self.assertIn("materials", interpret_question("자재가 모자라는데 납기 맞출까?").intents)

    def test_production_timing_does_not_become_a_quantity_only_answer(self):
        for question in ("며칠이면 만들어?", "이번 달까지 생산할 수 있어?", "기간 안에 생산 가능해?", "양품 생산은 몇 시간 걸려?", "생산 완료 날짜 알려줘", "하루면 생산 가능?", "3일이면 생산 가능?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.intents[0], "delivery")
                self.assertIn("production", plan.intents)
                self.assertIsNone(plan.target_good_qty)
                self.assertEqual(plan.quantity_mentions, ())
        self.assertTrue(interpret_question("언제 날씨가 좋아져?").unsupported)

    def test_remaining_and_production_paraphrases(self):
        self.assertEqual(interpret_question("얼마나 남았어?").intents, ("fulfillment", "production"))
        for question in ("몇 대 더 만들어야 해?", "양품 얼마나 만들어?", "생산은 얼마나 남았어?"):
            with self.subTest(question=question):
                self.assertIn("production", interpret_question(question).intents)
        plan = interpret_question("왜 68인데 40이야?")
        self.assertEqual(plan.intents, ("fulfillment", "production"))
        self.assertEqual([mention.quantity for mention in plan.quantity_mentions], [Decimal(68), Decimal(40)])
        self.assertIsNone(plan.target_good_qty)

    def test_material_and_inventory_composites(self):
        for question in ("뭐 모자라?", "뭐가 모잘라?", "자재 얼마나 필요해?", "부품 소요량 알려줘"):
            with self.subTest(question=question):
                self.assertIn("materials", interpret_question(question).intents)
        plan = interpret_question("재고 빼면 몇 개 부족해?")
        self.assertEqual(plan.intents, ("materials", "inventory"))
        self.assertIsNone(plan.clarification)

    def test_yield_is_decimal_and_never_a_quantity_assertion(self):
        for question in ("수율94%면 몇개 투입?", "수율 94 퍼센트면 몇 대 넣어?", "수율94프로로 몇개 생산해?", "94% 수율로 몇개 투입?", "수율 0.94면 투입량은?", "94%면 몇개 넣어?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.yield_rate, Decimal("0.94"))
                self.assertEqual(plan.quantity_mentions, ())
                self.assertIsNone(plan.clarification)
                self.assertTrue(plan.is_what_if)
        self.assertEqual(interpret_question("수율 99.25%로 투입량 알려줘").yield_rate, Decimal("0.9925"))

    def test_invalid_or_ambiguous_yield_never_silently_defaults(self):
        for question in ("수율0%면 투입?", "수율-2%면?", "수율101%면?", "수율94면 투입?", "수율NaN으로 몇개?", "수율구십사퍼센트면?", "수율94%%면?", "수율94%와90%면?", "수율0.94와90%면 투입?", "불량률10%면 자재부족?", "불량률10%면 몇개 투입?", "생산 가능성이70%면?"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)
        self.assertIsNone(interpret_question("계획 수율 알려줘").clarification)

    def test_near_full_yield_does_not_round_to_a_perfect_yield(self):
        percent = "99." + "9" * 40
        plan = interpret_question(f"수율{percent}%면 몇대 투입?")
        self.assertEqual(plan.yield_rate, Decimal("0." + "9" * 42))
        self.assertLess(plan.yield_rate, Decimal(1))
        self.assertIsNone(plan.clarification)

    def test_explicit_yield_exclusion_does_not_reuse_a_default_rate(self):
        for question in ("수율 없이 몇대 투입해?", "수율을 제외하고 투입량은?", "수율 미적용으로 몇개 생산?", "수율을 무시하면 몇대 투입?", "수율을 적용하지 말고 자재 소요량은?", "수율94%를 반영하지 말고 투입량은?", "수율 적용 없이 몇대 만들어?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertIsNotNone(plan.clarification)
                self.assertIsNone(plan.yield_rate)
        self.assertIsNone(interpret_question("수율94%를 적용하고 재고를 빼고 자재부족 계산해줘").clarification)

    def test_only_explicit_good_target_changes_calculation(self):
        for question in ("양품 50대가 목표면 수율90%로 몇대투입?", "50대를 생산하려면 자재 얼마나 필요해?", "양품 50개라면 부족한 자재는?", "생산목표를 50대로 가정하면 투입량은?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.target_good_qty, 50)
                self.assertTrue(plan.is_what_if)
                self.assertIsNone(plan.clarification)
                self.assertEqual(plan.quantity_mentions[0].role, "target_good")
        for question in ("50대가 남았는데 몇개 투입?", "50대 언제 보내?", "왜 73인데 29야?"):
            with self.subTest(question=question):
                self.assertIsNone(interpret_question(question).target_good_qty)
        self.assertIsNotNone(interpret_question("50대면 자재 얼마 필요해?").clarification)

    def test_fractional_negative_unitless_and_multiple_targets_need_clarification(self):
        for question in ("양품2.5대가 목표면?", "양품-2대가 목표면?", "양품50이 목표면?", "양품50kg가 목표면?", "양품50개가 목표면 아니면 60개를 생산하려면?"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)
        self.assertEqual(interpret_question("양품0개가 목표면?").target_good_qty, 0)

    def test_good_target_and_yield_can_share_an_explicit_assumption(self):
        for question in (
            "양품 목표 50대와 수율 90%를 가정하면 자재가 얼마나 부족해?",
            "양품 목표를 50대로 가정하고 수율 90%면 자재 얼마나 부족해?",
            "양품 목표 50대 및 계획 수율 90%를 함께 가정하면 부족분은?",
        ):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.target_good_qty, 50)
                self.assertEqual(plan.yield_rate, Decimal("0.9"))
                self.assertTrue(plan.is_what_if)
                self.assertIsNone(plan.clarification)
                self.assertIn("materials", plan.intents)
                self.assertEqual(plan.quantity_mentions[0].role, "target_good")
                self.assertIsNone(plan.quantity_mentions[0].reference)
        for question in (
            "남은 약속68대와 수율94%를 가정하면 자재 얼마나 부족해?",
            "양품 목표50대와 수율90%는 맞아?",
        ):
            with self.subTest(question=question):
                self.assertIsNone(interpret_question(question).target_good_qty)

    def test_identifier_numbers_are_not_quantities(self):
        plan = interpret_question("ORD-001 JOB-AB-02 CFG-003 IM-0016과 IM-0016 재고 부족해?")
        self.assertEqual(plan.item_ids, ("IM-0016",))
        self.assertEqual(plan.target_references, ("ORD-001", "JOB-AB-02", "CFG-003"))
        self.assertEqual(plan.quantity_mentions, ())

    def test_unicode_and_spacing_do_not_change_meaning(self):
        plan = interpret_question("수 율 ９４％ 면 몇 개 투 입?")
        self.assertEqual(plan.yield_rate, Decimal("0.94"))
        self.assertIn("production", plan.intents)
        self.assertEqual(interpret_question("ＩＭ－００１６ 재고?").item_ids, ("IM-0016",))
        self.assertEqual(interpret_question("IM - 0016 재고?").item_ids, ("IM-0016",))
        self.assertIsNotNone(interpret_question("승\u200b인해줘").clarification)

    def test_dates_are_not_material_or_product_quantities(self):
        for question in ("2026-11-09까지 68대 도착 가능?", "11월 9일 안에 68대 납품 가능?", "3일 안에 68대 다 보내?"):
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual([mention.quantity for mention in plan.quantity_mentions], [Decimal(68)])
                self.assertEqual(plan.intents[0], "delivery")
                self.assertIsNone(plan.target_good_qty)

    def test_past_unknown_round_and_version_cannot_change_read_context(self):
        for question in ("지난달 재고 알려줘", "어제 부족분은?", "다른 회차 자재는?", "2회차 현황", "3차 납품은?", "원장 버전1 재고"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)

    def test_write_requests_do_not_become_successful_reads(self):
        for question in ("재고 삭제해줘", "BOM 승인해줘", "40개 생산해줘", "재고를 50개로 바꿔줘", "68대 보내줘", "BOM 원장에 반영해", "delete inventory"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)
        for question in ("BOM 승인 상태 알려줘", "승인하지 말고 재고만 조회해줘", "68대 언제 다 보내?"):
            with self.subTest(question=question):
                self.assertIsNone(interpret_question(question).clarification)

    def test_stock_and_bom_quantity_assumptions_are_not_silently_ignored(self):
        for question in ("재고가20개면 몇개 부족?", "재고20개로 가정하면 부족분?", "예약을10개로 바꾸면 부족분?", "대당2개로 가정하면 소요량?"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)

    def test_unsupported_does_not_match_possible_or_model_alone(self):
        for question in ("가능해?", "모델 알려줘", "날씨 어때?", ""):
            with self.subTest(question=question):
                self.assertTrue(interpret_question(question).unsupported)
        self.assertEqual(interpret_question("전체 온톨로지 클래스 관계 정의").intents, ("ontology",))

    def test_ambiguous_product_names_and_malformed_ids_need_binding(self):
        for question in ("IM0016 부족?", "다른 모델 자재 부족?", "WROVER 재고는?"):
            with self.subTest(question=question):
                self.assertIsNotNone(interpret_question(question).clarification)

    def test_quantity_roles_and_units_remain_available_for_snapshot_validation(self):
        cases = (("68대 언제 보내?", "delivery"), ("투입43대 맞아?", "planned_input"), ("43개 투입해야 해?", "planned_input"), ("73대 남았어?", "unspecified"))
        for question, role in cases:
            with self.subTest(question=question):
                plan = interpret_question(question)
                self.assertEqual(plan.quantity_mentions[0].role, role)
                self.assertIn(plan.quantity_mentions[0].unit, ("개", "대"))
                self.assertIsNone(plan.target_good_qty)
        self.assertEqual(interpret_question("양품1,250개가 목표면?").target_good_qty, 1250)
        self.assertIsNotNone(interpret_question("양품1,25개가 목표면?").clarification)

    def test_explicit_fact_references_cannot_match_a_different_ledger_count(self):
        cases = (
            ("남은60대 언제보내?", "remaining_promise"),
            ("60대 남았어?", "remaining_promise"),
            ("남은 약속60대 맞아?", "remaining_promise"),
            ("생산에 남은60대 맞아?", "production_remaining_qty"),
            ("생산목표60대 맞아?", "production_remaining_qty"),
            ("60대 더 만들어야 해?", "production_remaining_qty"),
            ("양품실적40대 맞아?", "good_qty"),
            ("이미 만든 양품40대 맞아?", "good_qty"),
            ("도착40대 맞아?", "arrived_qty"),
            ("40대가 도착했어?", "arrived_qty"),
            ("약속40대 맞아?", "promised_qty"),
            ("40대를 약속했어?", "promised_qty"),
            ("투입40대 맞아?", "planned_input"),
        )
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(interpret_question(question).quantity_mentions[0].reference, expected)
        plan = interpret_question("남은 68대인데 양품실적60대면 왜40대를 더 만들어야 해?")
        self.assertEqual([mention.reference for mention in plan.quantity_mentions], ["remaining_promise", "good_qty", "production_remaining_qty"])

    def test_hypothetical_and_unbound_numbers_do_not_claim_current_facts(self):
        plan = interpret_question("양품50대가 목표면 수율90%로 몇대 투입?")
        self.assertIsNone(plan.quantity_mentions[0].reference)
        self.assertEqual(plan.quantity_mentions[0].role, "target_good")
        for question in ("왜68인데40이야?", "68대 도착가능해?", "도착68대 언제 가능해?"):
            with self.subTest(question=question):
                self.assertTrue(all(mention.reference is None for mention in interpret_question(question).quantity_mentions))


if __name__ == "__main__":
    unittest.main()
