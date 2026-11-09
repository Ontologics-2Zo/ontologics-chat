"""Synthetic display-focus contracts, independent of private BOM originals."""

from copy import deepcopy
import unittest

from service.bom_focus import review_focus
from tests.test_bom_compare import source_document, source_row


def document(name, *, mapped=(), status="DRAFT"):
    doc = source_document(name, [source_row(2, "R1"), source_row(3, "R2"), source_row(4, "C1")], source_hash="a" * 64)
    doc.update(company_id="synthetic-company", status=status)
    for index in mapped:
        doc["content"]["rows"][index]["decision"] = {"application_status": "APPLICABLE", "item_id": "synthetic-item",
            "quantity_per_unit": "1", "unit": "piece", "review_basis": "DEMO_ASSUMPTION", "evidence_note": "Synthetic review only"}
    return doc


class BomReviewFocusTests(unittest.TestCase):
    def focus(self, target, reference=None):
        before = deepcopy((target, reference))
        result = review_focus(target, reference)
        self.assertEqual((target, reference), before)
        self.assertTrue(result["read_only"])
        self.assertTrue(result["selection_only"])
        self.assertEqual((result["model_calls"], result["ledger_mutations"]), (0, 0))
        self.assertFalse(result["decisions_copied"])
        self.assertFalse(result["approvals_copied"])
        self.assertEqual(result["technical_equivalence"], "UNKNOWN")
        self.assertEqual(result["focus_count"] + result["remaining_count"], len(target["content"]["rows"]))
        return result

    def test_current_mapping_precedes_historical_focus(self):
        target = document("target", mapped=(1,)); reference = document("old", mapped=(0, 2), status="POSTED")
        result = self.focus(target, reference)
        self.assertEqual(result["basis"], "CURRENT_DOCUMENT")
        self.assertIsNone(result["reference"])
        self.assertEqual(result["row_ids"], [target["content"]["rows"][1]["row_id"]])

    def test_new_draft_gets_only_row_ids_without_inheriting_values_or_approval(self):
        target = document("target"); reference = document("old", mapped=(0, 2), status="POSTED")
        result = self.focus(target, reference)
        self.assertEqual(result["basis"], "POSTED_SAME_SOURCE")
        self.assertEqual(result["reference_selection"], "LATEST_POSTED_WITH_MAPPINGS")
        self.assertEqual(result["current_mapped_count"], 0)
        self.assertEqual(result["status"], "FOCUSED")
        self.assertEqual(result["focus_count"], 2)
        self.assertEqual(result["reference"]["payload_hash"], reference["payload_hash"])
        self.assertTrue(all(row["decision"]["application_status"] == "UNRESOLVED" for row in target["content"]["rows"]))
        for key in ("item_id", "unit", "quantity_per_unit", "evidence_note", "approved_by", "rows"):
            self.assertNotIn(key, str(result))

    def test_no_reference_or_reference_without_mappings_cannot_invent_a_focus(self):
        target = document("target")
        self.assertEqual(self.focus(target)["status"], "NO_REFERENCE")
        result = self.focus(target, document("latest", status="POSTED"))
        self.assertEqual(result["status"], "NO_FOCUS")
        self.assertEqual(result["row_ids"], [])

    def test_target_itself_is_not_its_historical_reference(self):
        target = document("self", status="POSTED")
        result = self.focus(target, target)
        self.assertEqual(result["basis"], "CURRENT_DOCUMENT")
        self.assertIsNone(result["reference"])
        self.assertEqual(result["status"], "NO_FOCUS")

    def test_reference_scope_is_company_source_configuration_and_posted_status(self):
        for altered in ("company", "source", "configuration", "status"):
            with self.subTest(altered=altered):
                reference = document("old", mapped=(0,), status="POSTED")
                if altered == "company": reference["company_id"] = "foreign-company"
                if altered == "source": reference["content"]["source"]["sha256"] = "b" * 64
                if altered == "configuration": reference["content"]["configuration_id"] = "other-configuration"
                if altered == "status": reference["status"] = "APPROVED"
                result = self.focus(document("target"), reference)
                self.assertEqual(result["status"], "NEEDS_REVIEW")
                self.assertEqual(result["row_ids"], [])
                self.assertIsNone(result["reference"])

    def test_same_hash_and_row_id_do_not_hide_changed_original_evidence(self):
        for field, value in (("cells", ["different source cell"]), ("source_quantity", "3"),
                             ("locator", "Other!row=9"), ("designators", ["R9"])):
            reference = document("old", mapped=(0,), status="POSTED")
            reference["content"]["rows"][0][field] = value
            result = self.focus(document("target"), reference)
            self.assertEqual(result["issues"], ["SOURCE_ROW_EVIDENCE_MISMATCH"])
            self.assertEqual(result["row_ids"], [])

    def test_duplicate_or_missing_rows_are_not_silently_deduplicated(self):
        for side in ("target", "reference"):
            target = document("target"); reference = document("old", mapped=(0,), status="POSTED")
            changed = target if side == "target" else reference
            changed["content"]["rows"][1] = deepcopy(changed["content"]["rows"][0])
            result = self.focus(target, reference)
            self.assertEqual(result["status"], "NEEDS_REVIEW")
            self.assertEqual(result["row_ids"], [])
        target = document("target"); target["content"]["rows"].pop()
        self.assertEqual(self.focus(target, document("old", mapped=(0,), status="POSTED"))["status"], "NEEDS_REVIEW")

    def test_unlinked_applicable_and_excluded_rows_are_not_focus_mappings(self):
        reference = document("old", status="POSTED")
        for row, status in zip(reference["content"]["rows"], ("APPLICABLE", "NOT_MOUNTED", "SETTING")):
            row["decision"] = {"application_status": status, "item_id": None}
        self.assertEqual(self.focus(document("target"), reference)["row_ids"], [])


if __name__ == "__main__":
    unittest.main()
