"""Independent numerical and uncertainty cases, without private BOM contents."""

from copy import deepcopy
import unittest

from service.materials import requirements_for_snapshot


def row(identifier, *, quantity="1", item="part-a", unit="piece", state="APPLICABLE", basis="DEMO_ASSUMPTION"):
    return {"row_id": identifier, "source_quantity": quantity, "locator": "sheet/row/" + identifier,
            "decision": {"application_status": state, "quantity_per_unit": quantity,
                         "item_id": item, "unit": unit, "review_basis": basis}}


def payload(rows=None):
    return {
        "opening_balance": {"delivery_round": {"remaining_good_target_qty": 11}, "inventory": [
            {"scope": "LOT", "lot_id": "lot-a", "item_id": "part-a", "unit": "piece",
             "on_hand_qty": 18, "reserved_other_qty": 4, "reserved_self_qty": 2, "held_qty": 2, "available_qty": 12}]},
        "entities": [{"id": "urn:part-a", "display_id": "part-a", "type": "InternalItem"},
                     {"id": "urn:part-b", "display_id": "part-b", "type": "InternalItem"}],
        "bom": {"bom_id": "sample-bom", "bom_version": 2, "source": {"sha256": "a"*64},
                "rows": rows if rows is not None else [row("one")]},
    }


class MaterialRequirementsTests(unittest.TestCase):
    def test_duplicate_item_rows_are_aggregated_before_stock_is_compared(self):
        result = requirements_for_snapshot(payload([row("one", quantity="1"), row("two", quantity="0.5")]))
        self.assertEqual(len(result["items"]), 1)
        item=result["items"][0]
        self.assertEqual(item["required_qty"], "16.5")
        self.assertEqual(item["planned_required_qty"], "18.0")
        self.assertEqual(item["available_qty"], "12")
        self.assertEqual(item["shortage_qty"], "4.5")
        self.assertEqual(item["planned_shortage_qty"], "6.0")
        self.assertEqual(item["row_ids"], ["one", "two"])

    def test_own_reservations_remain_available_but_other_and_held_do_not(self):
        result=requirements_for_snapshot(payload())
        self.assertEqual(result["items"][0]["available_qty"], "12")
        self.assertEqual(result["items"][0]["shortage_qty"], "0")
        self.assertEqual(result["basis"]["planned_input_qty"], 12)
        self.assertFalse(result["basis"]["good_output_guaranteed"])

    def test_unreviewed_source_quantity_is_conditional_arithmetic_only(self):
        unreviewed=row("one", quantity="3", basis="UNREVIEWED", state="UNRESOLVED")
        unreviewed["decision"].update(quantity_per_unit=None, item_id=None, unit=None)
        result=requirements_for_snapshot(payload([unreviewed]))
        self.assertEqual(result["rows"][0]["source_quantity_times_target"], "33")
        self.assertEqual(result["rows"][0]["source_quantity_times_plan"], "36")
        self.assertIsNone(result["rows"][0]["required_qty"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["unknown_shortage_count"], 1)

    def test_missing_stock_is_unknown_instead_of_full_shortage(self):
        result=requirements_for_snapshot(payload([row("one", item="part-b")]))
        item=result["items"][0]
        self.assertEqual(item["required_qty"], "11")
        self.assertIsNone(item["available_qty"])
        self.assertIsNone(item["shortage_qty"])
        self.assertIn("INVENTORY_UNKNOWN", item["issues"])
        self.assertFalse(result["full_coverage"])

    def test_missing_mapping_and_unit_do_not_become_zero(self):
        for change in ({"item_id": None}, {"unit": None}, {"quantity_per_unit": "NaN"}, {"quantity_per_unit": "-2"}):
            data=payload();data["bom"]["rows"][0]["decision"].update(change)
            with self.subTest(change=change):
                result=requirements_for_snapshot(data)
                self.assertEqual(result["items"], [])
                self.assertEqual(result["unknown_shortage_count"], 1)

    def test_unknown_source_quantity_is_not_parsed_as_zero(self):
        data=payload();data["bom"]["rows"][0]["source_quantity"]="not stated"
        self.assertIsNone(requirements_for_snapshot(data)["rows"][0]["source_quantity_times_target"])

    def test_item_and_lot_coverage_cannot_double_count_stock(self):
        data=payload()
        extra={**data["opening_balance"]["inventory"][0], "scope": "ITEM_AGGREGATE_LOTS_UNKNOWN"}
        data["opening_balance"]["inventory"].append(extra)
        item=requirements_for_snapshot(data)["items"][0]
        self.assertIsNone(item["available_qty"])
        self.assertIn("INVENTORY_COVERAGE_AMBIGUOUS",item["issues"])

    def test_incompatible_units_do_not_reuse_same_stock(self):
        data=payload([row("one"),row("two",unit="box")])
        result=requirements_for_snapshot(data)
        self.assertEqual(len(result["items"]),2)
        self.assertTrue(all(item["available_qty"] is None for item in result["items"]))
        self.assertTrue(all("ITEM_UNIT_CONFLICT" in item["issues"] for item in result["items"]))

    def test_excluded_rows_are_preserved_and_do_not_consume_stock(self):
        excluded=row("excluded",state="NOT_MOUNTED");excluded["decision"].update(quantity_per_unit=None,unit=None,item_id=None)
        setting=row("setting",state="SETTING");setting["decision"].update(quantity_per_unit=None,unit=None,item_id=None)
        result=requirements_for_snapshot(payload([row("one"),excluded,setting]))
        self.assertEqual(result["row_count"],3)
        self.assertEqual(result["not_mounted_count"],1)
        self.assertEqual(result["setting_count"],1)
        self.assertEqual(len(result["items"]),1)
        self.assertEqual(result["rows"][1]["calculation_state"],"EXCLUDED_BY_REVIEW")

    def test_plan_changes_do_not_mutate_source_or_opening_inventory(self):
        data=payload();before=deepcopy(data)
        result=requirements_for_snapshot(data,plan_yield="0.5")
        self.assertEqual(result["basis"]["planned_input_qty"],22)
        self.assertEqual(result["items"][0]["planned_shortage_qty"],"10")
        self.assertEqual(data,before)
        self.assertEqual(result["manufacturing_feasibility"],"UNKNOWN")

    def test_missing_bom_stays_blocked(self):
        data=payload();del data["bom"]
        result=requirements_for_snapshot(data)
        self.assertEqual(result["state"],"BLOCKED_BOM")
        self.assertEqual(result["rows"],[])

    def test_yield_close_to_one_still_requires_an_extra_input_unit(self):
        result=requirements_for_snapshot(payload(),plan_yield="0.999999999999999999999999999999")
        self.assertEqual(result["basis"]["planned_input_qty"],12)


if __name__ == "__main__":
    unittest.main()
