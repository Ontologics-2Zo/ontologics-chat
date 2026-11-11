"""Suggested questions fit the state and are answerable by the read flow."""
import unittest

from service.agent_workflow import ROUTE_FEATURE
from service.agents.procurement_draft import draft_request
from service.agents.readers import FulfillmentRead, MaterialAnalysis
from service.query_intents import interpret_question
from service.views import suggested_questions

SUMMARY = {"promise_remaining_qty": 68, "item_id": "IM-0016"}
SHORT = {"items": [{"item_id": "IM-0016", "planned_shortage_qty": "23"}]}
FOLLOW_UPS = ["왜 부족이 28개로 늘었어?", "기존 23개 안이면 충분해? 어떤 안을 다시 검토할까?", "도착기한에는 어떤 영향이 있어?"]


DRAFT_WRITER = "procurement_draft"


def route(question):
    """Same deterministic routing as the agent flow, without a model."""
    request = draft_request(question)
    if request and not request["forbidden"]:
        return DRAFT_WRITER, None  # write agent behind the approval card
    plan = interpret_question(question)
    if plan.clarification or plan.unsupported:
        return None, plan
    features = {ROUTE_FEATURE[intent] for intent in plan.intents if intent in ROUTE_FEATURE}
    if set(plan.intents) == {"ontology"}:
        return FulfillmentRead, plan
    if features & {"materials", "inventory"}:
        return MaterialAnalysis, plan
    if features & {"fulfillment", "production", "delivery"}:
        return FulfillmentRead, plan
    return None, plan


class SuggestedQuestionTests(unittest.TestCase):
    def states(self):
        return {
            "no_bom": suggested_questions(SUMMARY, {"items": []}, None, {}, []),
            "bom_short": suggested_questions(SUMMARY, SHORT, {"bom_id": "b"}, {}, []),
            "after_reservation": suggested_questions(SUMMARY, SHORT, {"bom_id": "b"},
                {"procurement": {"documents": [{"kind": "PR"}]}}, FOLLOW_UPS),
            "received": suggested_questions(SUMMARY, SHORT, {"bom_id": "b"}, {"procurement": {"documents": [{"kind": "PR"}],
                "receipts": [{"item_id": "IM-0016", "lot_code": "LOT-0016-123456", "pending_qty": 30}]}}, []),
            "built": suggested_questions(SUMMARY, {"items": [{"item_id": "IM-0016", "planned_shortage_qty": "0"}]}, {"bom_id": "b"},
                {"procurement": {"documents": [{"kind": "PR"}], "receipts": [{"item_id": "IM-0016", "lot_code": "LOT-0016-123456", "pending_qty": 0}]},
                 "fulfillment": {"boards": {"B1": {"status": "SHIPPED", "lot_code": "LOT-0016-123456"}}}}, []),
            "all_arrived": suggested_questions({"promise_remaining_qty": 0, "arrived_qty": 100, "item_id": "IM-0016"},
                {"items": [{"item_id": "IM-0016", "planned_shortage_qty": "0"}]}, {"bom_id": "b"}, {"procurement": {"documents": [{"kind": "PR"}]}}, []),
        }

    def test_every_suggestion_routes_to_a_reader_that_accepts_it(self):
        for state, questions in self.states().items():
            for question in questions:
                with self.subTest(state=state, question=question):
                    reader, plan = route(question)
                    self.assertIsNotNone(reader, plan)
                    if reader == DRAFT_WRITER:
                        continue
                    self.assertTrue(set(plan.intents) & reader.allowed_intents, plan.intents)

    def test_suggestions_follow_the_state(self):
        states = self.states()
        self.assertEqual(states["no_bom"][0], "남은 68대, 납기 안에 고객에게 도착할 수 있어?")
        self.assertFalse(any("자재" in question for question in states["no_bom"]))
        self.assertIn("IM-0016 부족을 채울 견적을 비교해줘", states["bom_short"])
        self.assertEqual(states["after_reservation"][:3], FOLLOW_UPS)
        self.assertNotIn("IM-0016 부족을 채울 견적을 비교해줘", states["after_reservation"])  # a request exists
        self.assertIn("추천 견적으로 구매 요청 초안 만들어줘", states["bom_short"])
        self.assertNotIn("추천 견적으로 구매 요청 초안 만들어줘", states["after_reservation"])
        self.assertEqual(states["received"][0], "입하된 IM-0016 부품, 이제 가용 재고로 쓸 수 있어?")
        self.assertEqual(states["built"][0], "LOT-0016-123456는 어느 보드·출하·도착까지 연결됐어?")
        self.assertEqual(states["all_arrived"][0], "이번 회차 약속 100대가 모두 고객에게 도착했어?")
        self.assertTrue(all(len(questions) <= 7 for questions in states.values()))


if __name__ == "__main__":
    unittest.main()
