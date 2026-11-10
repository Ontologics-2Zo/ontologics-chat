"""T5 server-side recovery for committed-event reflection; TEST_DATABASE_URL must be disposable."""
import os
import unittest

from service.ledger import LedgerReader
from service.reservations import ReservationService
import tests.test_reservations as reservation_fixtures


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class RealtimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = reservation_fixtures.ReservationDomainTests("runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def count(self, sql, *args):
        with self.f.db.connect() as conn:
            return conn.execute(sql, args).fetchone()["n"]

    def test_reconnect_with_an_old_cursor_returns_missed_events_once_and_in_order(self):
        start = self.f.version()
        first = self.f.service.reserve(self.f.planner, self.f.company, self.f.body(quantity=2))
        self.f.ledger.advance_documents()
        second = self.f.service.reserve(self.f.planner, self.f.company, self.f.body(quantity=3))
        events = self.f.ledger.events(self.f.author, start)
        self.assertEqual([e["ledger_version"] for e in events], [first["ledger_version"], second["ledger_version"]])
        self.assertEqual(len({e["event_id"] for e in events}), 2)
        self.assertEqual(self.f.ledger.events(self.f.author, second["ledger_version"]), [])
        self.assertEqual([e["ledger_version"] for e in self.f.ledger.events(self.f.author, first["ledger_version"])], [second["ledger_version"]])

    def test_commit_is_visible_before_its_snapshot_and_projection(self):
        before = self.f.ledger.read(self.f.author)["metadata"]["server_ledger_version"]
        result = self.f.service.reserve(self.f.planner, self.f.company, self.f.body())
        pending = self.f.ledger.read(self.f.author)
        # The UI must not refresh here: the current version is ahead of the readable snapshot.
        self.assertEqual(pending["metadata"]["server_ledger_version"], before)
        self.assertEqual(pending["metadata"]["current_ledger_version"], result["ledger_version"])
        self.f.ledger.advance_documents()
        ready = self.f.ledger.read(self.f.author)
        self.assertEqual(ready["metadata"]["server_ledger_version"], result["ledger_version"])
        self.assertIsNone(ready.get("projection"))

    def test_restart_keeps_reservation_audit_outbox_and_version_without_duplicates(self):
        result = self.f.service.reserve(self.f.planner, self.f.company, self.f.body())
        self.f.ledger.advance_documents()
        snapshots = self.count("SELECT count(*) AS n FROM service_snapshots")
        # A new process: fresh service objects over the same database.
        restarted = LedgerReader(self.f.db, self.f.identity)
        self.assertEqual(restarted.advance_documents(), 0)
        self.assertEqual(self.count("SELECT count(*) AS n FROM service_snapshots"), snapshots)
        after = restarted.read(self.f.author)
        self.assertEqual(after["metadata"]["server_ledger_version"], result["ledger_version"])
        self.assertEqual(len(after["reservations"]), 1)
        self.assertEqual(self.count("SELECT count(*) AS n FROM outbox WHERE ledger_version=%s", result["ledger_version"]), 1)
        self.assertEqual(self.count("SELECT count(*) AS n FROM inventory_reservation_audit"), 1)
        # The retried command after restart returns the original result, no second effect.
        restarted.advance_documents()
        again = ReservationService(self.f.db, self.f.identity)
        body = self.f.body()
        first = again.reserve(self.f.planner, self.f.company, body)
        repeat = ReservationService(self.f.db, self.f.identity).reserve(self.f.planner, self.f.company, body)
        self.assertEqual(first["reservation_id"], repeat["reservation_id"])
        self.assertEqual(self.count("SELECT count(*) AS n FROM inventory_reservations"), 2)

    def test_revoked_permission_is_rechecked_on_the_next_command(self):
        with self.f.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET permissions=%s WHERE actor_id=%s", (["ledger.read"], self.f.planner.actor_id))
        with self.assertRaises(Exception) as caught:
            self.f.service.reserve(self.f.planner, self.f.company, self.f.body())
        self.assertIn(getattr(caught.exception, "code", ""), {"PERMISSION_DENIED", "IDENTITY_CHANGED"})
        self.assertEqual(self.count("SELECT count(*) AS n FROM inventory_reservations"), 0)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class ProjectionRetryTests(unittest.TestCase):
    def test_graph_store_failures_are_retried_but_shacl_violations_are_not(self):
        from psycopg.types.json import Jsonb
        f = reservation_fixtures.ReservationDomainTests("runTest"); self.addCleanup(f.doCleanups); f.setUp()
        with f.db.connect() as conn:
            versions = [row["ledger_version"] for row in conn.execute("SELECT ledger_version FROM service_snapshots ORDER BY 1").fetchall()]
            for version, projection in zip(versions, (
                    {"state": "READY", "neo4j": {"state": "READY"}, "validation": {"state": "CONFORMS"}},
                    {"state": "NOT_READY", "neo4j": {"state": "ERROR"}, "validation": {"state": "CONFORMS"}})):
                conn.execute("UPDATE service_snapshots SET projection=%s WHERE ledger_version=%s", (Jsonb(projection), version))
        self.assertEqual([row["ledger_version"] for row in f.ledger.pending()], versions[1:2])
        with f.db.connect() as conn:
            conn.execute("UPDATE service_snapshots SET projection=%s WHERE ledger_version=%s",
                         (Jsonb({"state": "NOT_READY", "neo4j": {"state": "SKIPPED"}, "validation": {"state": "VIOLATIONS"}}), versions[1]))
        self.assertEqual(f.ledger.pending(), [])
