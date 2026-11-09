"""Independent post-receipt read models: quantities, time and relevant evidence."""

from copy import deepcopy
from decimal import Decimal
import unittest

from service.imports import convert_baseline
from service.query_answers import answer_query
from service.views import snapshot_view
from tests.test_imports import synthetic_baseline


OPENING_TIME = "2025-02-01T10:00:00+00:00"
INVENTORY_TIME = "2025-02-03T12:00:00+00:00"
LATER_DOCUMENT_TIME = "2025-02-04T12:00:00+00:00"


def inventory_payload(*, received=False, inspected=False, later_document=False):
    payload = convert_baseline(synthetic_baseline(), company_id="inventory-test", profile_id="inventory-test",
                               run_id="INITIAL", imported_at="2025-02-02T12:00:00+00:00", source_sha256="a" * 64)
    payload["metadata"].update(server_ledger_version=2, current_ledger_version=2, snapshot_id="snapshot-opening")
    payload["projection"] = {"state": "READY", "projection_version": 2, "graph": {"nodes": [], "edges": []}}
    payload["bom"] = {"bom_id": "independent-bom", "bom_version": 1, "payload_hash": "b" * 64,
                      "source": {"sha256": "c" * 64}, "technical_review": "UNREVIEWED", "rows": [
        {"row_id": "row-a", "source_quantity": "1.5", "locator": "Independent!row=2", "decision": {
            "application_status": "APPLICABLE", "item_id": "item-a", "unit": "piece", "quantity_per_unit": "1.5", "review_basis": "DEMO_ASSUMPTION"}},
        {"row_id": "row-b", "source_quantity": "1", "locator": "Independent!row=3", "decision": {
            "application_status": "UNRESOLVED", "review_basis": "UNREVIEWED"}}]}
    if not received:
        return payload
    payload["metadata"].update(server_ledger_version=4, current_ledger_version=4, snapshot_id="snapshot-inspection",
        opening_effective_at=OPENING_TIME, effective_at=LATER_DOCUMENT_TIME if later_document else INVENTORY_TIME,
        known_as_of=LATER_DOCUMENT_TIME if later_document else INVENTORY_TIME, current_inventory_effective_at=INVENTORY_TIME)
    payload["projection"]["projection_version"] = 4
    payload["current_inventory"] = deepcopy(payload["opening_balance"]["inventory"])
    payload["procurement"] = {"documents": [], "receipts": [], "scope": "INTERNAL_DEVELOPMENT_ONLY"}
    masters = {node["display_id"]: node["id"] for node in payload["entities"] if node["type"] == "InternalItem"}
    for suffix, item, amount in [("a", "item-a", 5), ("b", "alternative-a", 7)]:
        passed = (3 if suffix == "a" else 7) if inspected else 0
        failed = 1 if inspected and suffix == "a" else 0
        receipt_id, inspection_id, lot_code = "receipt-" + suffix, "inspection-" + suffix, "new-lot-" + suffix
        receipt = {"receipt_id": receipt_id, "po_id": "order-" + suffix, "lot_code": lot_code,
                   "item_id": item, "unit": "piece", "quantity": amount, "passed_qty": passed,
                   "failed_qty": failed, "pending_qty": amount - passed - failed, "provenance": "DEVELOPMENT_EVENT"}
        payload["procurement"]["receipts"].append(receipt)
        payload["current_inventory"].append({"scope": "LOT", "lot_id": lot_code, "item_id": item, "unit": "piece",
            "on_hand_qty": amount, "reserved_other_qty": 0, "reserved_self_qty": 0, "held_qty": amount - passed,
            "available_qty": passed, "unreserved_qty": passed, "pending_inspection_qty": amount - passed - failed,
            "failed_inspection_qty": failed, "inventory_origin": "DEVELOPMENT_RECEIPT", "receipt_ids": [receipt_id]})

        def evidence(document_id):
            return {"record_type": "development_procurement", "event_id": "event-" + document_id,
                    "document_id": document_id, "document_version": 1, "payload_hash": "d" * 64,
                    "ledger_version": 4, "registered_at": INVENTORY_TIME, "synthetic": True}

        def node(identifier, kind, display_id, attributes, ref):
            payload["entities"].append({"id": identifier, "type": kind, "source_type": kind, "display_id": display_id,
                "label": display_id, "attributes": attributes, "origin": "development_workflow", "evidence": ref})

        def edge(source, predicate, target, ref):
            payload["relationships"].append({"id": source + "/" + predicate, "source": source,
                                              "target": target, "predicate": predicate, "evidence": ref})

        for document_id, kind, type_ in [(receipt_id, "RECEIPT", "PartsReceiptLine"),
                                         *([(inspection_id, "IQC", "InspectionResult")] if inspected else [])]:
            resolved = {"item_id": item, "item_entity_id": masters[item], "unit": "piece"}
            if kind == "IQC": resolved["parent"] = {"id": receipt_id, "kind": "RECEIPT"}
            payload["procurement"]["documents"].append({"id": document_id, "kind": kind,
                "content": {"kind": kind, "receipt_id": receipt_id, "resolved": resolved}})
            ref = evidence(document_id)
            node("urn:" + document_id, type_, document_id, {"document_id": document_id, "kind": kind}, ref)
            node("urn:" + document_id + ":version", "DocumentVersion", document_id + " version", {}, ref)
            node("urn:" + document_id + ":approval", "ApprovalGrant", document_id + " approval", {}, ref)
            edge("urn:" + document_id, "recordedByDocumentVersion", "urn:" + document_id + ":version", ref)
            edge("urn:" + document_id + ":approval", "approvalForDocumentVersion", "urn:" + document_id + ":version", ref)
        ref = evidence(inspection_id if inspected else receipt_id)
        ref["receipt_document_id"] = receipt_id
        node("urn:" + lot_code, "PartLot", lot_code, {"item_id": item, "available_qty": passed}, ref)
        edge("urn:" + lot_code, "lotOf", masters[item], ref)
        if inspected:
            edge("urn:" + inspection_id, "inspectionOfReceiptLine", "urn:" + receipt_id, ref)
            edge("urn:" + lot_code, "lotAcceptedBy", "urn:" + inspection_id, ref)
    return payload


def answer(payload, question, selected=None):
    return answer_query(question, snapshot_view(payload), {"counts": {"classes": 1, "relations": 1, "shapes": 1}},
                        context_entity_id=selected)


class CurrentInventoryQueryTests(unittest.TestCase):
    def test_receipt_pending_and_failed_inspection_never_become_available(self):
        opening = inventory_payload(); received = inventory_payload(received=True); inspected = inventory_payload(received=True, inspected=True)
        for data, available, shortage in [(opening, "12", "4.5"), (received, "12", "4.5"), (inspected, "15", "1.5")]:
            result = answer(data, "자재 소요와 부족을 알려줘")
            row = result["calculation"]["material_requirements"]["items"][0]
            self.assertEqual(Decimal(row["available_qty"]), Decimal(available))
            self.assertEqual(Decimal(row["shortage_qty"]), Decimal(shortage))
        self.assertEqual(opening["opening_balance"], inspected["opening_balance"])

    def test_after_inspection_material_explanation_does_not_claim_opening_inventory_only(self):
        result = answer(inventory_payload(received=True, inspected=True), "자재 소요와 부족을 알려줘")
        materials = next(section for section in result["sections"] if section["id"] == "materials")
        self.assertIn("개발", materials["items"][0]["detail"])
        self.assertIn("검사", materials["items"][0]["detail"])
        self.assertNotIn("재고는 합성 기초 원장입니다", materials["items"][0]["detail"])

    def test_new_iqc_evidence_excludes_other_item_and_unrelated_approvals(self):
        payload = inventory_payload(received=True, inspected=True)
        selected = next(node["id"] for node in payload["entities"] if node["type"] == "InternalItem" and node["display_id"] == "item-a")
        result = answer(payload, "선택 품목의 가용 재고와 자재 부족", selected)
        docs = {ref["document_id"] for ref in result["evidence_refs"] if ref.get("record_type") == "development_procurement"}
        self.assertEqual(docs, {"receipt-a", "inspection-a"})
        ids = set(result["evidence_node_ids"])
        self.assertIn("urn:inspection-a:approval", ids)
        self.assertFalse(any(identifier.startswith(("urn:receipt-b", "urn:inspection-b", "urn:new-lot-b")) for identifier in ids))

    def test_opening_lot_does_not_inherit_new_receipt_evidence(self):
        payload = inventory_payload(received=True, inspected=True)
        selected = next(node["id"] for node in payload["entities"] if node["type"] == "PartLot" and node["display_id"] == "lot-a")
        result = answer(payload, "선택 LOT의 재고를 알려줘", selected)
        self.assertFalse(any(ref.get("record_type") == "development_procurement" for ref in result["evidence_refs"]))
        rows = next(section for section in result["sections"] if section["id"] == "inventory")["items"]
        self.assertEqual([row["label"] for row in rows], ["lot-a"])
        section = next(section for section in result["sections"] if section["id"] == "inventory")
        self.assertEqual(section["origin"], "synthetic_opening_balance")

    def test_selected_lot_material_calculation_includes_other_lot_inspection_evidence_for_same_item(self):
        payload = inventory_payload(received=True, inspected=True)
        selected = next(node["id"] for node in payload["entities"] if node["type"] == "PartLot" and node["display_id"] == "lot-a")
        for question in ("선택 LOT의 품목 소요와 부족은?", "선택 LOT 재고와 납기 가능 여부"):
            with self.subTest(question=question):
                result = answer(payload, question, selected)
                row = result["calculation"]["material_requirements"]["items"][0]
                self.assertEqual(Decimal(row["available_qty"]), Decimal(15))
                docs = {ref["document_id"] for ref in result["evidence_refs"] if ref.get("record_type") == "development_procurement"}
                self.assertEqual(docs, {"receipt-a", "inspection-a"})
                self.assertIn("urn:new-lot-a", result["evidence_node_ids"])
                self.assertNotIn("urn:new-lot-b", result["evidence_node_ids"])

    def test_new_lot_inventory_origin_is_development_only_and_evidence_stays_selected(self):
        result = answer(inventory_payload(received=True, inspected=True), "선택 LOT의 재고를 알려줘", "urn:new-lot-a")
        section = next(section for section in result["sections"] if section["id"] == "inventory")
        self.assertEqual(section["origin"], "development_workflow")
        self.assertEqual([row["label"] for row in section["items"]], ["new-lot-a"])
        docs = {ref["document_id"] for ref in result["evidence_refs"] if ref.get("record_type") == "development_procurement"}
        self.assertEqual(docs, {"receipt-a", "inspection-a"})

    def test_opening_and_inventory_times_are_separate_from_later_document_time(self):
        payload = inventory_payload(received=True, inspected=True, later_document=True)
        before = deepcopy(payload); view = snapshot_view(payload)
        self.assertEqual(view["summary"]["effective_at"], OPENING_TIME)
        self.assertEqual(view["meta"]["opening_effective_at"], OPENING_TIME)
        self.assertEqual(view["meta"]["inventory_effective_at"], INVENTORY_TIME)
        self.assertEqual(view["meta"]["effective_at"], LATER_DOCUMENT_TIME)
        self.assertEqual(payload, before)

    def test_what_if_uses_current_inspected_stock_and_prior_answer_stays_unchanged(self):
        opening = inventory_payload(); original = answer(opening, "자재 소요와 부족을 알려줘")
        preserved = deepcopy(original)
        current = inventory_payload(received=True, inspected=True); before = deepcopy(current)
        updated = answer(current, "양품 19대 목표로 자재 부족 계산해줘")
        row = updated["calculation"]["material_requirements"]["items"][0]
        self.assertEqual(Decimal(row["available_qty"]), Decimal(15))
        self.assertEqual(Decimal(row["shortage_qty"]), Decimal("13.5"))
        self.assertEqual(current, before)
        self.assertEqual(original, preserved)
        self.assertEqual(original["meta"]["ledger_version"], 2)
        self.assertEqual(updated["meta"]["ledger_version"], 4)


if __name__ == "__main__":
    unittest.main()
