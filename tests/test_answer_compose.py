"""Model-written summaries: scope-A packet, deterministic verification, server-filled numbers."""
from decimal import Decimal
import json
import unittest

from service.answer_compose import (AnswerPacket, ComposeGateway, ComposeRequest, FakeComposeProvider,
                                    OpenRouterComposeProvider, build_packet, compose_summary, verify)
from service.errors import ServiceError
from service.model_gateway import GatewayPolicy


def result(**overrides):
    base = {
        "status": "PARTIAL", "intent_label": "고객 도착·납기 판단",
        "conclusion": "현재 수량·계산 가능한 품목의 부족 확인 · 실제 납기는 판단 보류",
        "calculation": {"actual_delivery_feasibility": "UNKNOWN", "material_requirements": {"items": [
            {"item_id": "IM-0016", "planned_shortage_qty": "23", "unit": "EA"},
            {"item_id": "IM-0008", "planned_shortage_qty": "0", "unit": "EA"}]}},
        "sections": [
            {"id": "ledger", "status": "ANSWERED", "origin": "synthetic_opening_balance", "title": "현재 원장 수량 · 합성 기초 이관",
             "items": [{"label": "조회 회차", "value": "ORD-26041-01"}, {"label": "남은 고객 도착 약속", "value": "68대"}]},
            {"id": "production", "status": "PARTIAL", "origin": "conditional_demo", "title": "시연 가정 · 양품 목표와 계획 투입",
             "items": [{"label": "계획 수율", "value": "94%"}]},
            {"id": "delivery", "status": "UNKNOWN", "origin": "unknown", "title": "납기 가능 여부 · 판단 보류",
             "items": [{"label": "생산능력·착수·작업 달력", "value": "미확인"}]}]}
    base.update(overrides)
    return base


HITS = [{"evidence_id": "retrieval:abc", "kind": "source_row", "origin": "actual_source", "review_status": "SOURCE_ONLY",
         "node_ids": ["n1"], "text": "BL4054B-42TPRN(SOT23-5) U3 1"}]


def packet_and_maps():
    return build_packet(result(), HITS)


def fact(packet, needle):
    return next(f for f in packet.facts if needle in f.name)


class PacketTests(unittest.TestCase):
    def test_packet_carries_no_quantities_or_source_text(self):
        packet, values, cites = packet_and_maps()
        dumped = json.dumps(packet.model_dump(mode="json"), ensure_ascii=False)
        for secret in ("68", "23", "94", "BL4054B", "U3"):
            self.assertNotIn(secret, dumped.replace("ORD-26041-01", "").replace("IM-0016", "").replace("IM-0008", ""))
        self.assertEqual(fact(packet, "IM-0016").state, "부족 있음")
        self.assertEqual(fact(packet, "IM-0008").state, "부족 없음")
        self.assertEqual(values[fact(packet, "IM-0016").id], "23개")
        self.assertEqual(values[fact(packet, "남은 고객 도착 약속").id], "68대")
        self.assertTrue(packet.delivery_on_hold)
        self.assertEqual(cites["E1"]["evidence_id"], "retrieval:abc")
        self.assertEqual(set(packet.identifiers), {"ORD-26041-01", "IM-0016", "IM-0008"})

    def test_packet_contract_refuses_quantities(self):
        packet, _, _ = packet_and_maps()
        data = packet.model_dump(mode="json")
        data["verdict"] = "부족 23개"
        with self.assertRaises(ValueError):
            AnswerPacket.model_validate(data)

    def test_nothing_to_summarize(self):
        self.assertIsNone(build_packet(result(status="NEEDS_CLARIFICATION")))
        self.assertIsNone(build_packet({"status": "PARTIAL", "sections": []}))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.packet, self.values, self.cites = packet_and_maps()
        self.short = fact(self.packet, "IM-0016").id

    def check(self, *paragraphs):
        return verify(self.packet, self.values, self.cites, [{"text": t, "cites": c} for t, c in paragraphs])

    def test_placeholders_become_server_values(self):
        ok = self.check((f"IM-0016은 ⟦{self.short}⟧ 부족합니다. 실제 납기 판단은 보류합니다.", [self.short, "E1"]))
        self.assertEqual(ok.status, "VERIFIED")
        self.assertEqual(ok.paragraphs[0]["text"], "IM-0016은 23개 부족합니다. 실제 납기 판단은 보류합니다.")
        self.assertEqual(ok.paragraphs[0]["cites"][1]["evidence"]["evidence_id"], "retrieval:abc")

    def test_asking_about_delivery_is_not_deciding_it(self):
        for sentence in ("실제 납기와 고객 도착 가능 여부는 판단 보류입니다.", "도착기한 충족 여부는 판단하지 않습니다.",
                         "고객에게 도착할 수 있는지는 보류합니다.", "납기 충족한지는 판단 보류입니다."):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.check((sentence, [self.short])).status, "VERIFIED")
        for sentence in ("납기는 가능합니다. 다만 일부는 보류.", "고객에게 도착할 수 있습니다. 판단 보류.", "납기를 보장합니다. 보류."):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.check((sentence, [self.short])).reason, "SUMMARY_DECIDES_HELD_DELIVERY")

    def test_attacks_are_rejected(self):
        hold = " 실제 납기 판단은 보류합니다."
        cases = {
            "SUMMARY_NUMBER_UNPLACED": (f"IM-0016은 25개 부족합니다.{hold}", [self.short]),
            "SUMMARY_PLACEHOLDER_UNKNOWN": (f"⟦F99⟧ 부족.{hold}", [self.short]),
            "SUMMARY_CITE_UNKNOWN": (f"⟦{self.short}⟧ 부족.{hold}", ["F99"]),
            "SUMMARY_IDENTIFIER_UNLISTED": (f"IM-9999도 부족합니다.{hold}", [self.short]),
            "SUMMARY_DECIDES_HELD_DELIVERY": ("부족하지만 납기는 충족합니다. 판단은 보류합니다.", [self.short]),
            "SUMMARY_OMITS_HOLD": (f"IM-0016은 ⟦{self.short}⟧ 부족합니다.", [self.short]),
            "SUMMARY_SHAPE_INVALID": ("", [self.short]),
        }
        for code, paragraph in cases.items():
            with self.subTest(code=code):
                self.assertEqual(self.check(paragraph).reason, code)
        no_value = fact(self.packet, "생산능력").id
        self.assertEqual(self.check((f"⟦{no_value}⟧{hold}", [no_value])).reason, "SUMMARY_PLACEHOLDER_WITHOUT_VALUE")
        self.assertEqual(verify(self.packet, self.values, self.cites, "text").reason, "SUMMARY_SHAPE_INVALID")
        long = [{"text": "가" * 300 + hold, "cites": [self.short]}] * 4
        self.assertEqual(verify(self.packet, self.values, self.cites, long).reason, "SUMMARY_TOO_LONG")


class Store:
    """In-memory stand-in for the budget store's reserve/dispatch/finish."""
    def __init__(self):
        self.rows = {}

    def reserve(self, **kwargs):
        key = (kwargs["execution_id"], kwargs["call_id"])
        if key in self.rows:
            return {**self.rows[key], "new": False}
        row = {"attempt_id": "a" * 32, "status": "reserved", "execution_mode": kwargs["mode"], "result": None,
               "error_code": None, "reserved_usd": kwargs["reserved_usd"]}
        self.rows[key] = row
        return {**row, "new": True}

    def mark_dispatched(self, attempt_id):
        pass

    def finish(self, attempt_id, *, status, usage, result=None, error_code=None, actual_provider=None,
               actual_model=None, provider_request_id=None, not_dispatched=False):
        row = next(r for r in self.rows.values() if r["attempt_id"] == attempt_id)
        row.update(status=status, result=result, error_code=error_code, actual_provider=actual_provider, actual_model=actual_model)
        return dict(row)


class Principal:
    company_id, actor_id, permissions = "DEMO-A", "actor-a", ("query.read",)


def context():
    from datetime import datetime, timezone
    from service.answer_compose import ComposeContext
    now = datetime.now(timezone.utc)
    return ComposeContext(execution_id="exec-1", request_id="req-1", context_revision="rev", company_id="DEMO-A",
        read_snapshot_id="snap", ledger_version=9, projection_version=9, ontology_version="o", rule_version="r",
        as_of=now, known_as_of=now, segment_id="seg", parent_span_id="seg", budget_id="DEC-H18")


class GatewayTests(unittest.TestCase):
    def run_with(self, provider):
        import service.answer_compose as module
        original = module.Principal
        module.Principal = Principal  # the gateway checks the server Principal type
        self.addCleanup(setattr, module, "Principal", original)
        gateway = ComposeGateway(Store(), provider, lambda *a: None, GatewayPolicy())
        return compose_summary(gateway, result(), HITS, principal=Principal(), context=context())

    def test_fake_model_summary_is_verified_and_filled(self):
        summary = self.run_with(FakeComposeProvider())
        self.assertEqual(summary["status"], "VERIFIED", summary)
        self.assertIn("23개", " ".join(p["text"] for p in summary["paragraphs"]))
        self.assertTrue(any("보류" in p["text"] for p in summary["paragraphs"]))

    def test_a_model_that_writes_numbers_is_discarded(self):
        liar = FakeComposeProvider(override=lambda request: [{"text": "IM-0016은 5개만 부족합니다. 판단은 보류합니다.", "cites": ["F1"]}])
        summary = self.run_with(liar)
        self.assertEqual((summary["status"], summary["reason_code"]), ("REJECTED", "SUMMARY_NUMBER_UNPLACED"))
        self.assertNotIn("paragraphs", summary)

    def test_live_body_sends_only_the_packet_on_the_fixed_route(self):
        provider = OpenRouterComposeProvider("synthetic", provider_slug="azure/us", output_token_parameter="max_completion_tokens")
        provider.configure_price_caps(Decimal("0.0000022"), Decimal("0.000011"))
        packet, _, _ = packet_and_maps()
        body = provider._body(ComposeRequest(call_id="compose-v1", packet=packet))
        self.assertEqual(body["provider"]["only"], ["azure/us"])
        self.assertFalse(body["provider"]["allow_fallbacks"])
        self.assertTrue(body["provider"]["zdr"])
        sent = json.loads(body["messages"][1]["content"])
        self.assertEqual(set(sent), {"question_kind", "verdict", "delivery_on_hold", "facts", "evidence", "identifiers"})
        self.assertNotIn("BL4054B", body["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
