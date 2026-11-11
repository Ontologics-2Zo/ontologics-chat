"""Fixed-rule quote recommendation: suggests one quote, never selects or orders."""

import unittest

from service.procurement import quote_calculation, recommend_quote


def entry(ref, supplier, *, moq, multiple, limit, lead, price="1.00", expired=False, shortage=23):
    quote = {"moq": moq, "order_multiple": multiple, "unit_price": price, "freight": None, "currency": "USD",
             "supply_limit_qty": limit}
    return {"quote_id": f"q-{supplier}", "quote_ref": ref, "supplier_id": supplier, "lead_business_days": lead,
            "expired": expired, "calculation": quote_calculation(quote, shortage)}


class RecommendQuoteTests(unittest.TestCase):
    def scenario(self, shortage):
        # Same shapes as the demo comparison: A 10/10 limit 40, B 10/10 limit 25,
        # C 1/1 limit 25, D 10/10 limit unknown.
        return [entry("Q-A", "SUP-A", moq=10, multiple=10, limit=40, lead=7, shortage=shortage),
                entry("Q-B", "SUP-B", moq=10, multiple=10, limit=25, lead=3, shortage=shortage),
                entry("Q-C", "SUP-C", moq=1, multiple=1, limit=25, lead=5, shortage=shortage),
                entry("Q-D", "SUP-D", moq=10, multiple=10, limit=None, lead=2, shortage=shortage)]

    def test_shortest_lead_among_quantity_meeting_quotes(self):
        picked = recommend_quote(self.scenario(23))
        self.assertEqual(picked["quote_id"], "q-SUP-C")  # A and C meet; C has the shorter lead
        self.assertIn("리드타임이 가장 짧습니다(5영업일)", picked["reason"])
        self.assertIn("선택과 승인은 사람이 합니다", picked["reason"])

    def test_a_reservation_that_raises_the_shortage_changes_the_recommendation(self):
        picked = recommend_quote(self.scenario(28))
        self.assertEqual(picked["quote_id"], "q-SUP-A")  # C (limit 25) no longer meets 28
        self.assertIn("이것 하나", picked["reason"])

    def test_unknown_limits_expired_quotes_and_no_candidates(self):
        rows = self.scenario(23)
        rows[2]["expired"] = True
        self.assertEqual(recommend_quote(rows)["quote_id"], "q-SUP-A")
        self.assertIsNone(recommend_quote([rows[1], rows[3]]))  # NOT_MET and UNKNOWN only

    def test_equal_lead_falls_back_to_subtotal(self):
        rows = [entry("Q-A", "SUP-A", moq=10, multiple=10, limit=40, lead=5, price="2.00"),
                entry("Q-C", "SUP-C", moq=1, multiple=1, limit=40, lead=5, price="2.00")]
        picked = recommend_quote(rows)
        self.assertEqual(picked["quote_id"], "q-SUP-C")  # 23 × 2.00 < 30 × 2.00
        self.assertIn("소계가 가장 낮습니다", picked["reason"])


if __name__ == "__main__":
    unittest.main()
