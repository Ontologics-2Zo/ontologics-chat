"""Synthetic internal workflow tests; TEST_DATABASE_URL must be disposable."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from service.contracts import DocumentAction
from service.errors import ServiceError
from service.procurement import ProcurementService, quote_calculation
from service.materials import requirements_for_snapshot
from service.procurement_contracts import (ProcurementCreate, ProcurementReject, ProcurementRevise, QuoteContent,
    RequestContent, OrderContent, ReceiptContent, SupplyConfirmationContent, InspectionContent, CancelContent)
import tests.test_bom as bom_fixtures


def offer(**changes):
    values = dict(quote_ref="test-offer-one", supplier_id="supplier-a", item_id="item-a", unit="piece",
                  unit_price="1.25", currency="USD", moq=2, order_multiple=1, freight=None,
                  valid_until=(date.today() + timedelta(days=30)).isoformat(), note="Explicit independent demo offer")
    return QuoteContent(**{**values, **changes})


class ProcurementContractTests(unittest.TestCase):
    def test_unknown_freight_is_not_zero_and_packaging_rounds_up(self):
        result = quote_calculation(offer(moq=7, order_multiple=4).model_dump(), "7.01")
        self.assertEqual(result["recommended_qty"], 8)
        self.assertEqual(result["subtotal"], "10.00")
        self.assertIsNone(result["total"])
        self.assertEqual(result["freight_status"], "UNKNOWN")
        known = quote_calculation(offer(freight="0").model_dump(), "3")
        self.assertEqual(Decimal(known["total"]), Decimal("3.75"))
        self.assertEqual(quote_calculation(offer().model_dump(), "0")["recommended_qty"], 0)

    def test_invalid_money_quantity_dates_and_forged_authority_rejected(self):
        for change in ({"unit_price": "NaN"}, {"unit_price": "-1"}, {"unit_price": 1.0},
                       {"freight": "1e-12"}, {"moq": True}, {"order_multiple": 0},
                       {"valid_until": "2025-02-30"}, {"approved_by": "forged"}, {"provenance": "ACTUAL_SUPPLIER"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                offer(**change)
        with self.assertRaises(ValidationError):
            InspectionContent(receipt_id="example", inspection_ref="inspect", passed_qty=0, failed_qty=0, note="No outcome is not a recorded inspection")


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class ProcurementDomainTests(unittest.TestCase):
    def setUp(self):
        fixture = bom_fixtures.BomDomainTests("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        doc = fixture.approved(fixture.revised())
        fixture.action("post", fixture.author, doc, "fixture-post-bom")
        fixture.ledger.advance_documents()
        for name in ("db", "identity", "author", "reviewer", "viewer", "other", "company", "ledger"):
            setattr(self, name, getattr(fixture, name))
        self.service = ProcurementService(self.db, self.identity)
        self.original = deepcopy(self.ledger.read(self.author)["opening_balance"])

    def version(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT version FROM ledger_state WHERE company_id=%s", (self.company,)).fetchone()["version"]

    def create(self, content, **updates):
        body = ProcurementCreate(expected_ledger_version=self.version(), content=content,
                                 idempotency_key="create-" + uuid4().hex, **updates)
        return self.service.create(self.author, self.company, body)

    def action(self, action, doc, actor=None, key=None):
        actor = actor or (self.author if action == "post" else self.reviewer)
        body = DocumentAction(expected_version=doc["version"], payload_hash=doc["payload_hash"],
                              idempotency_key=key or action + "-" + uuid4().hex)
        return getattr(self.service, action)(actor, self.company, doc["id"], body)

    def post(self, content):
        doc = self.create(content)
        doc = self.action("review", doc)
        doc = self.action("approve", doc)
        doc = self.action("post", doc)
        self.ledger.advance_documents()
        return doc

    def order(self):
        quote = self.post(offer())
        options = self.service.options(self.author, self.company, self.version())
        plan = next(x for x in options["quotes"] if x["material_basis"] == "planned")
        quantity = plan["calculation"]["recommended_qty"]
        request = self.post(RequestContent(quote_id=quote["id"], quantity=quantity, material_basis="planned",
            acknowledge_partial_bom=True, note="Selected independent planned shortage"))
        order = self.post(OrderContent(pr_id=request["id"], quantity=quantity, acknowledge_unknown_freight=True,
            note="Internal order with unconfirmed freight"))
        self.post(SupplyConfirmationContent(po_id=order["id"], confirmation_ref="simulated-confirmation", confirmed_qty=quantity,
            note="Internal simulated supply only, no vendor transmission"))
        return request, order

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_partial_receipts_iqc_and_restart_preserve_opening_and_delivery(self):
        request, order = self.order()
        self.assertEqual(order["quantity"], 12)
        receipt = self.post(ReceiptContent(po_id=order["id"], receipt_ref="delivery-part-one", lot_code="new-lot-one",
            quantity=7, note="Independent partial internal receipt"))
        before = self.ledger.read(self.author)
        self.assertEqual(sum(x["available_qty"] for x in before["current_inventory"]), 12)
        self.assertEqual(before["procurement"]["receipts"][0]["pending_qty"], 7)
        self.assertFalse(any(e["predicate"] == "lotAcceptedBy" for e in before["relationships"]))
        self.post(InspectionContent(receipt_id=receipt["id"], inspection_ref="inspect-part-one", passed_qty=4, failed_qty=1,
            note="Four accepted, one rejected, two still pending"))
        intermediate = self.ledger.read(self.author)
        self.assertEqual(sum(x["available_qty"] for x in intermediate["current_inventory"]), 16)
        balance = intermediate["procurement"]["receipts"][0]
        self.assertEqual([balance[k] for k in ("quantity", "passed_qty", "failed_qty", "pending_qty")], [7, 4, 1, 2])
        self.post(InspectionContent(receipt_id=receipt["id"], inspection_ref="inspect-part-two", passed_qty=2, failed_qty=0,
            note="Remaining two pieces explicitly accepted"))
        other = self.post(ReceiptContent(po_id=order["id"], receipt_ref="delivery-part-two", lot_code="new-lot-two",
            quantity=5, note="Remaining internal receipt quantity"))
        self.post(InspectionContent(receipt_id=other["id"], inspection_ref="inspect-part-three", passed_qty=5, failed_qty=0,
            note="Five pieces explicitly accepted after inspection"))
        final = self.ledger.read(self.author)
        self.assertEqual(sum(x["available_qty"] for x in final["current_inventory"]), 23)
        self.assertEqual(final["opening_balance"], self.original)
        self.assertEqual(final["opening_balance"]["delivery_round"]["remaining_promise_qty"], 17)
        if os.environ.get("PRIVATE_INPUT_ROOT"):
            from service.ontology import OntologyCatalog
            from service.projections import OntologyProjector
            projection = OntologyProjector(OntologyCatalog.from_private_root(os.environ["PRIVATE_INPUT_ROOT"])).project(
                final, self.version(), self.company, "INITIAL")
            self.assertEqual(projection["validation"]["state"], "CONFORMS")
            self.assertFalse(projection["validation"]["unmapped"])
        restarted = ProcurementService(self.db, self.identity)
        self.assertEqual(restarted.get(self.author, self.company, order["id"])["status"], "POSTED")
        with self.db.connect() as conn:
            events = conn.execute("SELECT event_id,procurement_document_id FROM ledger_events WHERE event_type='ProcurementPosted'").fetchall()
            self.assertEqual(len(events), 9)
            self.assertTrue(all(conn.execute("SELECT 1 FROM outbox WHERE event_id=%s", (x["event_id"],)).fetchone() for x in events))
        # Rejected stock leaves a real remaining shortage; a fully settled old
        # request does not prevent an explicitly reviewed replacement request.
        quote_id = request["content"]["quote_id"]
        replacement = self.create(RequestContent(quote_id=quote_id, quantity=2, material_basis="planned",
            acknowledge_partial_bom=True, note="Replacement for rejected inspected quantity"))
        self.assertEqual(replacement["quantity"], 2)

    def test_unknown_freight_requires_explicit_order_acknowledgement(self):
        quote = self.post(offer())
        request = self.post(RequestContent(quote_id=quote["id"], quantity=12, material_basis="planned",
            acknowledge_partial_bom=True, note="Independent planned material demand"))
        self.assert_code("PROCUREMENT_FREIGHT_ACK_REQUIRED", lambda: self.create(OrderContent(
            pr_id=request["id"], quantity=12, note="No acknowledgement of missing freight")))
        doc = self.create(OrderContent(pr_id=request["id"], quantity=12, acknowledge_unknown_freight=True,
            note="Missing freight explicitly remains unconfirmed"))
        self.assertIsNone(doc["content"]["resolved"]["calculation"]["total"])
        self.assertFalse(doc["content"]["resolved"]["supplier_confirmation"])

    def test_independent_review_exact_revision_and_stale_basis(self):
        doc = self.create(offer())
        self.assert_code("SELF_REVIEW_FORBIDDEN", lambda: self.action("review", doc, self.author))
        self.assert_code("PERMISSION_DENIED", lambda: self.action("approve", doc, self.author))
        own = self.service.create(self.reviewer, self.company, ProcurementCreate(
            expected_ledger_version=self.version(), content=offer(quote_ref="reviewer-owned"), idempotency_key="reviewer-create"))
        own = self.action("review", own, self.author)
        self.assert_code("SELF_APPROVAL_FORBIDDEN", lambda: self.action("approve", own, self.reviewer))
        reviewed = self.action("review", doc)
        approved = self.action("approve", reviewed)
        revised = self.service.revise(self.author, self.company, doc["id"], ProcurementRevise(
            expected_version=doc["version"], payload_hash=doc["payload_hash"], expected_ledger_version=self.version(),
            content=offer(unit_price="2.50"), idempotency_key="change-price"))
        self.assertEqual(revised["status"], "DRAFT")
        self.assertIsNone(revised["approved_hash"])
        self.assert_code("DOCUMENT_VERSION_CHANGED", lambda: self.action("post", approved))
        other = self.post(offer(quote_ref="different-offer"))
        self.assert_code("PROCUREMENT_LEDGER_VERSION_CHANGED", lambda: self.action("review", revised))

    def test_duplicate_idempotency_receipt_reference_over_receipt_and_iqc(self):
        _, order = self.order()
        receipt = self.post(ReceiptContent(po_id=order["id"], receipt_ref="one-reference", lot_code="one-lot", quantity=8,
            note="Independent receipt with unique reference"))
        self.assert_code("PROCUREMENT_REFERENCE_ALREADY_EXISTS", lambda: self.create(ReceiptContent(
            po_id=order["id"], receipt_ref="one-reference", lot_code="different-lot", quantity=1, note="Duplicate physical delivery reference")))
        self.assert_code("PROCUREMENT_OVER_RECEIPT", lambda: self.create(ReceiptContent(
            po_id=order["id"], receipt_ref="excess", lot_code="excess-lot", quantity=5, note="Quantity exceeds remaining order")))
        self.assert_code("PROCUREMENT_OVER_INSPECTION", lambda: self.create(InspectionContent(
            receipt_id=receipt["id"], inspection_ref="excess-inspect", passed_qty=9, failed_qty=0, note="Exceeds received quantity")))
        doc = self.create(InspectionContent(receipt_id=receipt["id"], inspection_ref="unique-inspect", passed_qty=3,
            failed_qty=2, note="Independent partial accepted and rejected counts"))
        doc = self.action("approve", self.action("review", doc))
        first = self.action("post", doc, key="stable-iqc-post")
        self.assertEqual(first, self.action("post", doc, key="stable-iqc-post"))
        self.assert_code("PROCUREMENT_STATE_CHANGED", lambda: self.action("post", doc))

    def test_partial_order_cancel_releases_only_unreceived_allocation(self):
        request, order = self.order()
        receipt = self.post(ReceiptContent(po_id=order["id"], receipt_ref="before-cancel", lot_code="cancel-lot", quantity=5,
            note="Received before order remainder cancellation"))
        cancel = self.post(CancelContent(target_id=order["id"], note="Cancel unreceived internal order remainder"))
        self.assertEqual(cancel["content"]["resolved"]["cancelled_remaining_qty"], 7)
        self.assert_code("PROCUREMENT_PARENT_CANCELLED", lambda: self.create(ReceiptContent(po_id=order["id"],
            receipt_ref="after-cancel", lot_code="after-lot", quantity=1, note="Cannot receive cancelled remainder")))
        self.post(InspectionContent(receipt_id=receipt["id"], inspection_ref="inspect-after-cancel", passed_qty=5,
            failed_qty=0, note="Existing physical receipt still requires inspection"))
        replacement = self.create(OrderContent(pr_id=request["id"], quantity=7, acknowledge_unknown_freight=True,
            note="Replacement internal order for released allocation"))
        self.assertEqual(replacement["quantity"], 7)
        self.assertEqual(self.ledger.read(self.author)["opening_balance"], self.original)

    def test_permission_company_and_approver_revocation(self):
        body = ProcurementCreate(expected_ledger_version=self.version(), content=offer(), idempotency_key="unauthorized")
        self.assert_code("PERMISSION_DENIED", lambda: self.service.create(self.viewer, self.company, body))
        doc = self.create(offer())
        with self.assertRaises(ServiceError):
            self.service.get(self.other, self.company, doc["id"])
        doc = self.action("approve", self.action("review", doc))
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET policy_version=policy_version+1 WHERE actor_id=%s", (self.reviewer.actor_id,))
        self.assert_code("APPROVAL_PERMISSION_CHANGED", lambda: self.action("post", doc))
        self.assertEqual(self.version(), 2)

    def test_concurrent_receipts_cannot_exceed_order(self):
        _, order = self.order()
        drafts = []
        for suffix in ("a", "b"):
            draft = self.create(ReceiptContent(po_id=order["id"], receipt_ref="concurrent-" + suffix,
                lot_code="concurrent-lot-" + suffix, quantity=8, note="Concurrent receipt against same remaining order"))
            drafts.append(self.action("approve", self.action("review", draft)))
        def attempt(doc):
            try:
                return self.action("post", doc)["status"]
            except ServiceError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, drafts))
        self.assertEqual(sorted(results), sorted(["POSTED", "PROCUREMENT_LEDGER_VERSION_CHANGED"]))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT sum(quantity) AS n FROM procurement_documents WHERE kind='RECEIPT' AND status='POSTED'").fetchone()["n"], 8)

    def test_partial_supply_confirmation_limits_receipts_and_remains_simulated(self):
        quote = self.post(offer())
        request = self.post(RequestContent(quote_id=quote["id"], quantity=12, material_basis="planned",
            acknowledge_partial_bom=True, note="Independent shortage for supply confirmation"))
        order = self.post(OrderContent(pr_id=request["id"], quantity=12, acknowledge_unknown_freight=True,
            note="Internal order requires separate simulated confirmation"))
        receipt = ReceiptContent(po_id=order["id"], receipt_ref="confirmed-receipt", lot_code="confirmed-lot",
            quantity=5, note="Receive only explicitly confirmed quantity")
        self.assert_code("PROCUREMENT_SUPPLY_CONFIRMATION_REQUIRED", lambda: self.create(receipt))
        confirmation = self.post(SupplyConfirmationContent(po_id=order["id"], confirmation_ref="partial-confirmation",
            confirmed_qty=4, note="Four pieces internally simulated, no actual supplier response"))
        self.assertFalse(confirmation["content"]["resolved"]["supplier_confirmation"])
        self.assertIsNone(confirmation["content"]["resolved"]["promised_arrival"])
        self.assert_code("PROCUREMENT_SUPPLY_CONFIRMATION_REQUIRED", lambda: self.create(receipt))
        self.assert_code("PROCUREMENT_OVER_CONFIRMATION", lambda: self.create(SupplyConfirmationContent(
            po_id=order["id"], confirmation_ref="excess-confirmation", confirmed_qty=9, note="Exceeds remaining unconfirmed order quantity")))
        self.post(SupplyConfirmationContent(po_id=order["id"], confirmation_ref="remaining-confirmation",
            confirmed_qty=8, note="Remaining quantity internally simulated only"))
        self.post(receipt)
        payload = self.ledger.read(self.author)
        self.assertNotEqual(payload["metadata"]["effective_at"], payload["metadata"]["opening_effective_at"])
        self.assertEqual(payload["opening_balance"], self.original)

    def test_expired_quote_wrong_quantity_partial_ack_and_duplicate_demand_blocked(self):
        self.assert_code("PROCUREMENT_QUOTE_EXPIRED", lambda: self.create(offer(valid_until="2020-01-01")))
        quote = self.post(offer())
        self.assert_code("PROCUREMENT_PARTIAL_BOM_ACK_REQUIRED", lambda: self.create(RequestContent(
            quote_id=quote["id"], quantity=12, material_basis="planned", note="Partial BOM acknowledgement is missing")))
        self.assert_code("PROCUREMENT_QUANTITY_CHANGED", lambda: self.create(RequestContent(
            quote_id=quote["id"], quantity=11, material_basis="planned", acknowledge_partial_bom=True,
            note="Incorrect quantity differs from computed recommendation")))
        content = RequestContent(quote_id=quote["id"], quantity=12, material_basis="planned", acknowledge_partial_bom=True,
                                 note="Explicit selected planned material shortage")
        self.post(content)
        self.assert_code("PROCUREMENT_DEMAND_ALREADY_REQUESTED", lambda: self.create(content))
        revised_basis = requirements_for_snapshot(self.ledger.read(self.author))
        revised_basis["bom_id"] = "a-later-approved-bom"
        with patch("service.procurement.requirements_for_snapshot", return_value=revised_basis):
            self.assert_code("PROCUREMENT_DEMAND_ALREADY_REQUESTED", lambda: self.create(content))

    def test_aggregate_opening_and_known_new_lot_are_never_double_counted(self):
        source = bom_fixtures.synthetic_baseline()
        source["inventory"] = {"lots": [], "reservations": [], "other_item_snapshots": [{
            "item_id": "item-a", "baseline_on_hand_qty": 17, "baseline_reserved_other_qty": 4,
            "baseline_reserved_self_qty": 2, "baseline_held_qty": 1, "unit_assumption": "piece",
            "technical_mapping_status": "DEMO_ASSUMPTION"}]}
        with patch.object(bom_fixtures, "synthetic_baseline", return_value=source):
            self.setUp()
        _, order = self.order()
        receipt = self.post(ReceiptContent(po_id=order["id"], receipt_ref="aggregate-new", lot_code="known-new-lot",
            quantity=6, note="Known new lot alongside unknown historical LOT membership"))
        self.post(InspectionContent(receipt_id=receipt["id"], inspection_ref="aggregate-inspect", passed_qty=4,
            failed_qty=1, note="Four accepted, one rejected, one pending"))
        payload = self.ledger.read(self.author)
        inventory = payload["current_inventory"]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["scope"], "ITEM_AGGREGATE_LOTS_UNKNOWN")
        self.assertEqual(inventory[0]["on_hand_qty"], 23)
        self.assertEqual(inventory[0]["available_qty"], 16)
        self.assertEqual(inventory[0]["held_qty"], 3)
        self.assertEqual(payload["procurement"]["receipts"][0]["lot_code"], "known-new-lot")
        self.assertEqual(payload["opening_balance"], self.original)

    def test_independent_rejection_revokes_grant_and_cannot_reject_posted(self):
        draft = self.create(offer())
        approved = self.action("approve", self.action("review", draft))
        body = ProcurementReject(expected_version=approved["version"], payload_hash=approved["payload_hash"],
            idempotency_key="reject-reviewed-quote", reason="The hypothetical freight condition needs another review")
        self.assert_code("SELF_REVIEW_FORBIDDEN", lambda: self.service.reject(self.author, self.company, approved["id"], body))
        rejected = self.service.reject(self.reviewer, self.company, approved["id"], body)
        self.assertEqual(rejected["status"], "DRAFT")
        self.assertIsNone(rejected["approved_hash"])
        self.assertEqual(rejected["rejection_reason"], body.reason)
        self.assert_code("PROCUREMENT_STATE_CHANGED", lambda: self.action("post", approved))
        posted = self.post(offer(quote_ref="posted-immutable"))
        body = ProcurementReject(expected_version=posted["version"], payload_hash=posted["payload_hash"],
            idempotency_key="cannot-reject-posted", reason="Posted records require separate business cancellation")
        self.assert_code("POSTED_DOCUMENT_IMMUTABLE", lambda: self.service.reject(self.reviewer, self.company, posted["id"], body))
