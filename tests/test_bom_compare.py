"""Independent source comparisons; no private input, parser or database needed."""

from copy import deepcopy
from hashlib import sha256
import unittest

from pydantic import ValidationError

from service.bom_compare import compare_bom_documents
from service.bom_contracts import BomCompare


def source_row(index, positions, *, quantity="1", text="Synthetic component", sheet="Board", package="CASE-X"):
    tokens = positions.split(",") if positions else []
    return {"row_id": sha256(f"independent-row-{index}-{sheet}".encode()).hexdigest(),
            "source_row": index, "ordinal": index, "sheet": sheet, "locator": f"{sheet}!row={index}",
            "designators": tokens, "source_quantity": quantity, "source_quantity_text": quantity or "?",
            "source_description": text, "cells": [quantity or "?", text, package, positions],
            "decision": {"application_status": "UNRESOLVED"}}


def source_document(label, rows, *, source_hash=None, configuration="synthetic-configuration"):
    return {"id": label, "version": 1, "payload_hash": sha256((label + "-content").encode()).hexdigest(),
            "status": "DRAFT", "content": {"configuration_id": configuration,
                "source": {"filename": label + ".csv", "sha256": source_hash or sha256(label.encode()).hexdigest()},
                "source_conflicts": [], "rows": rows}}


class BomCompareTests(unittest.TestCase):
    def compare(self, left, right):
        originals = deepcopy((left, right))
        result = compare_bom_documents(left, right)
        self.assertEqual((left, right), originals)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["ledger_mutations"], 0)
        self.assertFalse(result["automatic_application"])
        self.assertFalse(result["lineage_inferred"])
        self.assertFalse(result["quantity_aggregation"])
        self.assertEqual(result["technical_equivalence"], "UNKNOWN")
        if left:
            for side, document in (("left", left), ("right", right)):
                refs = [entry[side] for entry in result["rows"] if entry[side]]
                self.assertCountEqual([ref["row_id"] for ref in refs],
                                      [row["row_id"] for row in document["content"]["rows"]])
                self.assertTrue(all(ref["source_sha256"] == document["content"]["source"]["sha256"] for ref in refs))
        return result

    def test_first_intake_has_no_implicit_baseline(self):
        result = self.compare(None, source_document("right", [source_row(2, "R1")]))
        self.assertEqual(result["status"], "NO_BASELINE")
        self.assertIsNone(result["left"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["summary"]["right_rows"], 1)
        self.assertEqual(result["summary"]["added"], 0)

    def test_same_original_is_identical_despite_different_application_decisions(self):
        left = source_document("left", [source_row(2, "Ref**"), source_row(3, "")])
        right = deepcopy(left); right.update(id="right", version=2, payload_hash="f" * 64)
        right["content"]["rows"][0]["decision"] = {"application_status": "NOT_MOUNTED"}
        right["content"]["source_conflicts"] = ["SOURCE_ROW_REQUIRES_REVIEW"]
        result = self.compare(left, right)
        self.assertEqual(result["status"], "IDENTICAL_SOURCE")
        self.assertTrue(result["same_source"])
        self.assertEqual(result["summary"]["unchanged"], 2)
        self.assertEqual(result["source_warnings"]["right"], ["SOURCE_ROW_REQUIRES_REVIEW"])
        self.assertTrue(all(row["match_basis"] == "SAME_SOURCE_ROW" for row in result["rows"]))

    def test_same_hash_does_not_hide_divergent_extracted_rows(self):
        left = source_document("left", [source_row(2, "R1")], source_hash="a" * 64)
        right = source_document("right", [source_row(2, "R1", quantity="2")], source_hash="a" * 64)
        result = self.compare(left, right)
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("SAME_SOURCE_EXTRACTION_DIFFERS", result["warnings"])

    def test_positions_match_across_changed_row_numbers_and_row_ids(self):
        left = source_document("left", [source_row(2, "R1"), source_row(3, "C1,C2")])
        right = source_document("right", [source_row(9, "C1,C2"), source_row(6, "R1")])
        result = self.compare(left, right)
        self.assertEqual(result["summary"]["unchanged"], 2)
        self.assertEqual(result["rows"][0]["left"]["source_row"], 2)
        self.assertEqual(result["rows"][0]["right"]["source_row"], 6)

    def test_added_removed_and_source_value_changes_are_independent_rows(self):
        left = source_document("left", [source_row(2, "R1"), source_row(3, "C1")])
        right = source_document("right", [source_row(2, "R1", package="CASE-Y"), source_row(3, "U1")])
        result = self.compare(left, right)
        self.assertEqual(result["status"], "COMPARED")
        self.assertEqual({key: result["summary"][key] for key in ("added", "removed", "changed", "source_value_changed", "quantity_changed")},
                         {"added": 1, "removed": 1, "changed": 1, "source_value_changed": 1, "quantity_changed": 0})

    def test_decimal_quantity_change_is_not_rounded_or_aggregated(self):
        left = source_document("left", [source_row(2, "R1", quantity="0.000001")])
        right = source_document("right", [source_row(2, "R1", quantity="0.000002")])
        result = self.compare(left, right)
        self.assertEqual(result["rows"][0]["changes"], ["QUANTITY_CHANGED", "SOURCE_VALUE_CHANGED"])
        self.assertEqual(result["summary"]["quantity_changed"], 1)
        self.assertNotIn("quantity_delta", result)

    def test_numeric_equivalence_and_original_spelling_are_distinct(self):
        result = self.compare(source_document("left", [source_row(2, "R1", quantity="1.0")]),
                              source_document("right", [source_row(2, "R1", quantity="1")]))
        self.assertEqual(result["summary"]["quantity_changed"], 0)
        self.assertEqual(result["summary"]["source_value_changed"], 1)

    def test_split_and_merged_position_groups_are_reviewed_without_invented_changes(self):
        group = source_document("group", [source_row(2, "R1,R2", quantity="2")])
        split = source_document("split", [source_row(2, "R1"), source_row(3, "R2")])
        for left, right in ((group, split), (split, group)):
            with self.subTest(left=left["id"]):
                result = self.compare(left, right)
                self.assertEqual(result["summary"]["needs_review"], 3)
                self.assertEqual(result["summary"]["added"] + result["summary"]["removed"], 0)
                self.assertEqual(len({row["review_group_id"] for row in result["rows"]}), 1)

    def test_duplicate_position_blocks_its_counterpart_but_not_unrelated_rows(self):
        left = source_document("left", [source_row(2, "R1"), source_row(3, "R1"), source_row(4, "C1")])
        right = source_document("right", [source_row(5, "R1"), source_row(6, "C1")])
        result = self.compare(left, right)
        self.assertEqual(result["summary"]["needs_review"], 3)
        self.assertEqual(result["summary"]["unchanged"], 1)
        self.assertTrue(all("DUPLICATE_DESIGNATOR" in row["review_reasons"] for row in result["rows"] if row["change_type"] == "NEEDS_REVIEW"))

    def test_duplicate_within_one_row_is_not_lost_by_set_conversion(self):
        result = self.compare(source_document("left", [source_row(2, "R1,R1")]),
                              source_document("right", [source_row(2, "R1")]))
        self.assertEqual(result["summary"]["needs_review"], 2)

    def test_empty_wildcard_and_unknown_quantity_stay_unconfirmed(self):
        for positions, quantity, reason in (("", "1", "MISSING_DESIGNATOR"),
                                            ("Ref**", "1", "UNRESOLVED_DESIGNATOR"),
                                            ("R1", None, "QUANTITY_UNCONFIRMED")):
            with self.subTest(reason=reason):
                result = self.compare(source_document("left", [source_row(2, positions, quantity=quantity)]),
                                      source_document("right", [source_row(3, positions, quantity=quantity)]))
                self.assertEqual(result["status"], "NEEDS_REVIEW")
                self.assertEqual(result["summary"]["needs_review"], 2)
                self.assertTrue(all(reason in row["review_reasons"] for row in result["rows"]))

    def test_position_case_or_partial_set_change_is_not_guessed(self):
        for positions in ("r1", "R1,R2"):
            result = self.compare(source_document("left", [source_row(2, "R1")]),
                                  source_document("right", [source_row(3, positions)]))
            self.assertEqual(result["summary"]["needs_review"], 2)

    def test_multiple_sheets_require_exact_sheet_correspondence(self):
        left = source_document("left", [source_row(2, "R1", sheet="A"), source_row(2, "R1", sheet="B")])
        right = source_document("right", [source_row(3, "R1", sheet="A"), source_row(3, "R1", sheet="C")])
        result = self.compare(left, right)
        self.assertEqual(result["summary"]["unchanged"], 1)
        self.assertEqual(result["summary"]["needs_review"], 2)
        self.assertEqual(result["summary"]["added"] + result["summary"]["removed"], 0)
        self.assertEqual(result["sheet_policy"], "EXACT_SHEET_NAME")

    def test_one_sheet_per_document_can_compare_positions_with_explicit_context(self):
        result = self.compare(source_document("left", [source_row(2, "R1", sheet="CSV")]),
                              source_document("right", [source_row(3, "R1", sheet="Board")]))
        self.assertEqual(result["summary"]["unchanged"], 1)
        self.assertEqual(result["sheet_policy"], "SINGLE_SHEET_CONTEXT")
        self.assertIn("SINGLE_SHEET_NAMES_DIFFER", result["warnings"])

    def test_explicit_other_configuration_does_not_create_revision_lineage(self):
        result = self.compare(source_document("left", [source_row(2, "R1")], configuration="board-A"),
                              source_document("right", [source_row(3, "R1")], configuration="board-B"))
        self.assertIn("DIFFERENT_CONFIGURATION_EXPLICIT_BASELINE", result["warnings"])
        self.assertEqual(result, compare_bom_documents(source_document("left", [source_row(2, "R1")], configuration="board-A"),
                         source_document("right", [source_row(3, "R1")], configuration="board-B")))

    def test_strict_compare_contract_does_not_accept_authority_or_invent_versions(self):
        right = {"document_id": "synthetic", "expected_version": 1, "payload_hash": "a" * 64}
        self.assertIsNone(BomCompare(right=right).left)
        for body in ({"right": right, "actor_id": "someone"},
                     {"right": {**right, "expected_version": 0}},
                     {"right": {**right, "payload_hash": "not-a-hash"}},
                     {"right": {"document_id": "synthetic", "payload_hash": "a" * 64}}):
            with self.assertRaises(ValidationError):
                BomCompare.model_validate(body)


if __name__ == "__main__":
    unittest.main()
