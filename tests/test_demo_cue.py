"""Isolated-demo reservation cue (DEC-H52): opt-in, earliest trigger wins, at most once."""
import os
import threading
import unittest
from unittest import mock

from service import demo_cue
from service.errors import ServiceError


class Principal:
    company_id = "DEMO-A"


class Identity:
    def login(self, profile):
        assert profile == demo_cue.PLANNER_PROFILE
        return mock.Mock(principal=Principal())


class Reservations:
    def __init__(self, failures=()):
        self.calls, self.failures, self.done = [], list(failures), threading.Event()

    def reserve(self, principal, company_id, body):
        self.calls.append(body)
        if self.failures:
            raise ServiceError(self.failures.pop(0), 409)
        self.done.set()
        return {"ledger_version": body.expected_ledger_version + 1}


def cue(reservations, eligible=True, versions=(9,)):
    instance = demo_cue.DemoReservationCue(None, Identity(), reservations, after_answer=0.2, after_draft=0.05)
    state = {"eligible": eligible, "versions": list(versions)}
    instance._eligible = lambda company: state["eligible"] and not reservations.done.is_set()
    instance._lot = lambda company: (state["versions"].pop(0) if len(state["versions"]) > 1 else state["versions"][0], "LOT-X")
    return instance


class DemoCueTests(unittest.TestCase):
    def test_disabled_unless_dev_mode_and_flag(self):
        with mock.patch.dict(os.environ, {"ONTO_DEMO_AUTO_RESERVATION": "1"}):
            self.assertTrue(demo_cue.enabled(True))
            self.assertFalse(demo_cue.enabled(False))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(demo_cue.enabled(True))

    def test_draft_trigger_replaces_a_later_answer_trigger_and_fires_once(self):
        reservations = Reservations()
        instance = cue(reservations)
        instance.delivery_answered("DEMO-A")
        instance.purchase_request_drafted("DEMO-A")
        self.assertTrue(reservations.done.wait(2))
        instance.delivery_answered("DEMO-A")  # Already reserved: not eligible any more.
        threading.Event().wait(0.4)
        self.assertEqual(len(reservations.calls), 1)
        body = reservations.calls[0]
        self.assertEqual((body.item_id, body.lot_id, body.quantity, body.demand_ref), ("IM-0016", "LOT-X", 5, "ORD-26042-01"))
        self.assertEqual(body.idempotency_key, "demo-auto-reserve-DEMO-A-9")

    def test_version_race_retries_with_the_new_version(self):
        reservations = Reservations(failures=["RESERVATION_LEDGER_VERSION_CHANGED"])
        instance = cue(reservations, versions=(9, 10))
        instance.purchase_request_drafted("DEMO-A")
        self.assertTrue(reservations.done.wait(2))
        self.assertEqual([b.expected_ledger_version for b in reservations.calls], [9, 10])

    def test_not_eligible_never_reserves(self):
        reservations = Reservations()
        instance = cue(reservations, eligible=False)
        instance.purchase_request_drafted("DEMO-A")
        threading.Event().wait(0.2)
        self.assertEqual(reservations.calls, [])
        instance.cancel()


if __name__ == "__main__":
    unittest.main()
