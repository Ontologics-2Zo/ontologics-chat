"""DEC-H49 production → finished goods → shipment → arrival; TEST_DATABASE_URL must be disposable."""
import os
import unittest

from service.errors import ServiceError
from service.materials import requirements_for_snapshot
from service.procurement_contracts import (ArrivalContent, FinishedGoodsContent, InspectionContent, ProductionContent,
                                           ReceiptContent, ShipmentContent)
import tests.test_procurement as procurement_fixtures


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class FulfillmentTests(unittest.TestCase):
    def setUp(self):
        self.p = procurement_fixtures.ProcurementDomainTests("runTest")
        self.addCleanup(self.p.doCleanups)
        self.p.setUp()
        request, order = self.p.order()
        receipt = self.p.post(ReceiptContent(po_id=order["id"], receipt_ref="lot-receipt", lot_code="LOT-NEW-1",
                                             quantity=order["quantity"], note="Received lot for fulfillment test"))
        self.iqc = self.p.post(InspectionContent(receipt_id=receipt["id"], inspection_ref="lot-iqc", passed_qty=order["quantity"],
                                                 failed_qty=0, note="All received parts accepted"))

    def read(self):
        return self.p.ledger.read(self.p.author)

    def round(self):
        snapshot = self.read()
        return snapshot.get("current_delivery_round") or snapshot["opening_balance"]["delivery_round"]

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_named_boards_move_through_each_step_and_round_counts_follow(self):
        before = self.round()
        lot_before = next(x for x in self.read()["current_inventory"] if x.get("lot_id") == "LOT-NEW-1")
        production = self.p.post(ProductionContent(iqc_id=self.iqc["id"], production_ref="prod-1", board_serials=["B-001", "B-002", "B-003"],
                                                   note="Three boards produced from the new lot"))
        after = self.read()
        lot = next(x for x in after["current_inventory"] if x.get("lot_id") == "LOT-NEW-1")
        per_board = int(production["content"]["resolved"]["quantity_per_board"].split(".")[0])
        self.assertEqual(lot["on_hand_qty"], lot_before["on_hand_qty"] - 3 * per_board)
        self.assertEqual(lot["available_qty"], lot_before["available_qty"] - 3 * per_board)
        r = after["current_delivery_round"]
        self.assertEqual((r["good_qty"], r["remaining_good_target_qty"], r["arrived_qty"]),
                         (before["good_qty"] + 3, before["remaining_good_target_qty"] - 3, before["arrived_qty"]))
        self.assertEqual(requirements_for_snapshot(after)["basis"]["production_remaining_qty"], before["remaining_good_target_qty"] - 3)
        types = {e["type"] for e in after["entities"]}
        self.assertTrue({"ProductionReport", "ConsumptionEvent", "InstallationEvent", "BoardUnit"} <= types)
        predicates = {e["predicate"] for e in after["relationships"]}
        self.assertTrue({"consumesLot", "installedOn", "installationForLine", "reportedBoard", "boardForRound"} <= predicates)
        # The opening balance is preserved; posted records change only the current round overlay.
        self.assertEqual(after["opening_balance"]["delivery_round"], self.p.original["delivery_round"])

        fg = self.p.post(FinishedGoodsContent(production_id=production["id"], receipt_ref="fg-1", board_serials=["B-001", "B-002", "B-003"],
                                              note="Three boards received to warehouse"))
        shipment = self.p.post(ShipmentContent(fg_receipt_id=fg["id"], shipment_ref="ship-1", board_serials=["B-001", "B-002"],
                                               note="Two boards shipped to customer"))
        self.p.post(ArrivalContent(shipment_id=shipment["id"], arrival_ref="arr-1", board_serials=["B-001"],
                                   note="One board arrived at customer"))
        r = self.round()
        self.assertEqual((r["fg_received_qty"], r["shipped_qty"], r["arrived_qty"], r["remaining_promise_qty"]),
                         (before["fg_received_qty"] + 3, before["shipped_qty"] + 2, before["arrived_qty"] + 1, before["remaining_promise_qty"] - 1))
        self.assertEqual((r["warehouse_qty"], r["in_transit_qty"]), (before["warehouse_qty"] + 1, before["in_transit_qty"] + 1))
        final = self.read()
        boards = final["fulfillment"]["boards"]
        self.assertEqual([boards[s]["status"] for s in ("B-001", "B-002", "B-003")], ["ARRIVED", "SHIPPED", "RECEIVED"])
        self.assertTrue({"shipsBoard", "arrivalOfShipment", "arrivedBoard", "allocatedToRound"} <= {e["predicate"] for e in final["relationships"]})
        views = [v for v in final["events"] if v["document_kind"] == "ARRIVAL"]
        self.assertEqual(len(views), 1)
        from service.query_answers import answer_query
        from tests.test_reservation_answers import view_of
        view = view_of(final)
        answer = answer_query("LOT-NEW-1는 어느 보드·작업·출하·납품회차에 연결되어 있어?", view, {"counts": {"classes": 1, "relations": 1, "shapes": 1}})
        self.assertIn("calculation", answer, (answer.get("status"), answer.get("answer")))
        self.assertEqual(answer["calculation"]["counts"], {"PRODUCED": 0, "RECEIVED": 1, "SHIPPED": 1, "ARRIVED": 1})
        self.assertIn("보드 3대(B-001, B-002, B-003)", answer["answer"])
        self.assertIn("고객 도착 1대", answer["answer"])
        self.assertEqual(view["summary"]["arrived_qty"], before["arrived_qty"] + 1)
        self.assertEqual(answer["ledger_mutations"], 0)

    def test_steps_reject_unknown_boards_skipped_states_and_reuse(self):
        production = self.p.post(ProductionContent(iqc_id=self.iqc["id"], production_ref="prod-2", board_serials=["C-001"],
                                                   note="One board produced from the new lot"))
        self.assert_code("FULFILLMENT_BOARD_ALREADY_RECORDED", lambda: self.p.create(ProductionContent(
            iqc_id=self.iqc["id"], production_ref="prod-3", board_serials=["C-001"], note="Same serial cannot be produced twice")))
        self.assert_code("PROCUREMENT_POSTED_PARENT_REQUIRED", lambda: self.p.create(ShipmentContent(
            fg_receipt_id=production["id"], shipment_ref="ship-x", board_serials=["C-001"], note="Shipment must follow a receipt")))
        fg = self.p.post(FinishedGoodsContent(production_id=production["id"], receipt_ref="fg-2", board_serials=["C-001"],
                                              note="One board received to warehouse"))
        self.assert_code("FULFILLMENT_BOARD_STATE_INVALID", lambda: self.p.create(FinishedGoodsContent(
            production_id=production["id"], receipt_ref="fg-3", board_serials=["C-001"], note="Already received board cannot repeat")))
        self.assert_code("FULFILLMENT_BOARD_STATE_INVALID", lambda: self.p.create(ShipmentContent(
            fg_receipt_id=fg["id"], shipment_ref="ship-y", board_serials=["Z-999"], note="Unknown board cannot be shipped")))

    def test_production_cannot_exceed_lot_or_remaining_target(self):
        target = self.round()["remaining_good_target_qty"]
        self.assert_code("FULFILLMENT_EXCEEDS_REMAINING_TARGET", lambda: self.p.create(ProductionContent(
            iqc_id=self.iqc["id"], production_ref="too-many", board_serials=[f"T-{i}" for i in range(target + 1)],
            note="More boards than the remaining good target")))
