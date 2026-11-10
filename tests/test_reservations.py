"""Other-demand reservation contract (DEC-H47); TEST_DATABASE_URL must be disposable."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import os
import unittest
from uuid import uuid4

from pydantic import ValidationError

from service.errors import ServiceError
from service.ledger import ledger_event_view
from service.materials import requirements_for_snapshot
from service.procurement import quote_calculation
from service.procurement_contracts import QuoteContent
from service.reservations import ReservationCreate, ReservationService
import tests.test_bom as bom_fixtures


def offer(**changes):
    values = dict(quote_ref="cmp-offer", supplier_id="supplier-a", item_id="item-a", unit="piece", unit_price="1",
                  currency="KRW", moq=10, order_multiple=10, freight=None,
                  valid_until=(date.today() + timedelta(days=30)).isoformat(), note="Separate synthetic comparison offer")
    return QuoteContent(**{**values, **changes}).model_dump()


class SupplyLimitTests(unittest.TestCase):
    """Fixed realtime-request comparison: shortage 23 → 28 after five reserved."""

    def judge(self, shortage, **quote):
        result = quote_calculation(offer(**quote), str(shortage))
        return result["recommended_qty"], result["quantity_condition"], result["margin_qty"], result["limit_shortfall_qty"]

    def test_four_synthetic_offers_before_and_after_reservation(self):
        a = dict(moq=10, order_multiple=10, supply_limit_qty=40)
        b = dict(moq=10, order_multiple=10, supply_limit_qty=25)
        c = dict(moq=1, order_multiple=1, supply_limit_qty=25)
        d = dict(moq=10, order_multiple=10)
        self.assertEqual(self.judge(23, **a), (30, "MET", 7, 0))
        self.assertEqual(self.judge(28, **a), (30, "MET", 2, 0))
        self.assertEqual(self.judge(23, **b)[:2], (30, "NOT_MET"))
        self.assertEqual(self.judge(28, **b)[:2], (30, "NOT_MET"))
        self.assertEqual(self.judge(23, **c)[:2], (23, "MET"))
        self.assertEqual(self.judge(28, **c), (28, "NOT_MET", 0, 3))
        self.assertEqual(self.judge(23, **d)[:2], (30, "UNKNOWN"))
        self.assertEqual(self.judge(28, **d)[:2], (30, "UNKNOWN"))

    def test_unknown_limit_is_not_zero_or_unlimited(self):
        result = quote_calculation(offer(), "5")
        self.assertIsNone(result["supply_limit_qty"])
        self.assertEqual(result["quantity_condition_reason"], "SUPPLY_LIMIT_UNKNOWN")
        with self.assertRaises(ValidationError):
            offer(supply_limit_qty=-1)


class ReservationContractTests(unittest.TestCase):
    def test_command_rejects_unsafe_input(self):
        base = dict(item_id="item-a", lot_id="lot-a", quantity=5, demand_ref="other-order", note="Other demand reserve",
                    expected_ledger_version=1, idempotency_key="key")
        for change in ({"quantity": 0}, {"quantity": True}, {"demand_ref": "bad ref"}, {"note": "short"},
                       {"note": "line\nbreak inside"}, {"approved_by": "forged"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                ReservationCreate(**{**base, **change})


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class ReservationDomainTests(unittest.TestCase):
    def setUp(self):
        fixture = bom_fixtures.BomDomainTests("runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        doc = fixture.approved(fixture.revised())
        fixture.action("post", fixture.author, doc, "fixture-post-bom")
        fixture.ledger.advance_documents()
        for name in ("db", "identity", "author", "viewer", "other", "company", "ledger"):
            setattr(self, name, getattr(fixture, name))
        self.planner = self.identity.login("dev-planner-h").principal
        self.service = ReservationService(self.db, self.identity)

    def version(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT version FROM ledger_state WHERE company_id=%s", (self.company,)).fetchone()["version"]

    def body(self, **changes):
        values = dict(item_id="item-a", lot_id="lot-a", quantity=5, demand_ref="other-order-1",
                      note="Other synthetic demand reserves shared stock", expected_ledger_version=self.version(),
                      idempotency_key="reserve-" + uuid4().hex)
        return ReservationCreate(**{**values, **changes})

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def lot(self, snapshot):
        return next(x for x in snapshot["current_inventory"] if x.get("lot_id") == "lot-a")

    def test_reservation_changes_only_other_reserved_and_available(self):
        before = self.ledger.read(self.author)
        delivery = before["opening_balance"]["delivery_round"]
        need_before = requirements_for_snapshot(before)
        body = self.body()
        result = self.service.reserve(self.planner, self.company, body)
        self.assertEqual((result["before"]["available_qty"], result["after"]["available_qty"]), (12, 7))
        self.assertFalse(result["stock_moved"])
        # Exact retry returns the first result; same key with other content conflicts.
        self.assertEqual(self.service.reserve(self.planner, self.company, body)["reservation_id"], result["reservation_id"])
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: self.service.reserve(self.planner, self.company,
                         body.model_copy(update={"quantity": 1})))
        self.ledger.advance_documents()
        after = self.ledger.read(self.author)
        lot = self.lot(after)
        self.assertEqual([lot[k] for k in ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty", "available_qty")],
                         [17, 9, 2, 1, 7])
        self.assertEqual(after["opening_balance"]["delivery_round"], delivery)
        self.assertEqual(after["metadata"]["server_ledger_version"], result["ledger_version"])
        entity = next(e for e in after["entities"] if e["type"] == "ReservationEvent")
        self.assertTrue(any(r["source"] == entity["id"] and r["predicate"] == "reservationEventLot" for r in after["relationships"]))
        self.assertEqual(after["reservations"][0]["quantity"], 5)
        items = {x["item_id"]: x for x in requirements_for_snapshot(after)["items"]}
        old = {x["item_id"]: x for x in need_before["items"]}
        self.assertEqual(items["item-a"]["available_qty"], "7")
        self.assertNotEqual(items["item-a"]["available_qty"], old["item-a"]["available_qty"])
        view = next(v for v in after["events"] if v["event_type"] == "InventoryReserved")
        self.assertEqual(view["document_kind"], "RESERVATION")
        self.assertEqual(view["reservation"]["available_after"], 7)
        self.assertNotIn("note", str(view))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM outbox WHERE ledger_version=%s", (result["ledger_version"],)).fetchone()["n"], 1)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM inventory_reservation_audit").fetchone()["n"], 1)

    def test_permission_version_target_and_quantity_rejections_write_nothing(self):
        start = self.version()
        self.assert_code("PERMISSION_DENIED", lambda: self.service.reserve(self.author, self.company, self.body()))
        self.assert_code("RESERVATION_LEDGER_VERSION_CHANGED",
                         lambda: self.service.reserve(self.planner, self.company, self.body(expected_ledger_version=start - 1)))
        self.assert_code("RESERVATION_DEMAND_IS_TARGET",
                         lambda: self.service.reserve(self.planner, self.company, self.body(demand_ref="delivery-a")))
        # Own reservation (2) and held stock (1) cannot be taken: reservable is 10, not 12.
        self.assert_code("RESERVATION_EXCEEDS_AVAILABLE",
                         lambda: self.service.reserve(self.planner, self.company, self.body(quantity=11)))
        self.assert_code("RESERVATION_LOT_UNCONFIRMED",
                         lambda: self.service.reserve(self.planner, self.company, self.body(item_id="item-b")))
        self.assertEqual(self.version(), start)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM inventory_reservations").fetchone()["n"], 0)

    def test_concurrent_reservations_never_exceed_reservable(self):
        start = self.version()
        bodies = [self.body(quantity=6, expected_ledger_version=start) for _ in range(4)]
        def attempt(body):
            try:
                return self.service.reserve(self.planner, self.company, body)
            except ServiceError as error:
                return error.code
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(attempt, bodies))
        accepted = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(accepted), 1)
        self.assertTrue(all(r == "RESERVATION_LEDGER_VERSION_CHANGED" for r in results if not isinstance(r, dict)))
        self.ledger.advance_documents()
        self.assertEqual(self.lot(self.ledger.read(self.author))["reserved_other_qty"], 10)

    def test_event_view_of_other_company_is_unverified(self):
        self.service.reserve(self.planner, self.company, self.body())
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM ledger_events WHERE event_type='InventoryReserved'").fetchone()
        self.assertEqual(ledger_event_view(row, "DEMO-OTHER")["origin"], "unknown")
        self.assertEqual(ledger_event_view(row, self.company)["origin"], "internal_development")


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class MultiLotImpactTests(unittest.TestCase):
    def test_impact_uses_the_item_total_across_lots(self):
        from service.reservations import reservation_impacts
        f = ReservationDomainTests("runTest"); self.addCleanup(f.doCleanups); f.setUp()
        with f.db.connect() as conn:
            row = conn.execute("SELECT ledger_version, payload FROM service_snapshots ORDER BY ledger_version DESC LIMIT 1").fetchone()
            payload = row["payload"]
            inventory = payload.setdefault("current_inventory", payload["opening_balance"]["inventory"])
            inventory.append({"scope": "LOT", "lot_id": "lot-b", "item_id": "item-a", "unit": "piece", "on_hand_qty": 6,
                              "reserved_other_qty": 0, "reserved_self_qty": 0, "held_qty": 0, "available_qty": 6})
            from psycopg.types.json import Jsonb
            conn.execute("UPDATE service_snapshots SET payload=%s WHERE ledger_version=%s", (Jsonb(payload), row["ledger_version"]))
        result = f.service.reserve(f.planner, f.company, f.body(quantity=2))
        f.ledger.advance_documents()
        snapshot = f.ledger.read(f.author)
        requirements = requirements_for_snapshot(snapshot)
        impact = reservation_impacts(requirements, snapshot["reservations"])[0]
        self.assertEqual((impact["available_before"], impact["available_after"]), ("18", "16"))
        item = next(x for x in requirements["items"] if x["item_id"] == "item-a")
        self.assertEqual(impact["available_after"], item["available_qty"])
        self.assertEqual(impact["planned_shortage_after"], item["planned_shortage_qty"])
