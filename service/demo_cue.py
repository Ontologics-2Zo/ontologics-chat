"""Scripted other-order reservation for the isolated MK-S2 demo only (DEC-H52).

The demo wants the other order's planner to reserve shared stock while the
presenter is working, without a button. This cue issues the same
ReservationService command as that planner's own account would, so the event
goes through version check, idempotency, audit, outbox and projection like any
other. It never runs unless dev mode and ONTO_DEMO_AUTO_RESERVATION=1 are both
set, fires at most once per company while no reservation exists, and never
changes the presenter's drafts, quotes or answers.
"""
from __future__ import annotations

import logging
import os
import threading

from service.errors import ServiceError
from service.reservations import ReservationCreate

log = logging.getLogger(__name__)

PLANNER_PROFILE = "dev-planner-h"
ITEM_ID, QUANTITY, DEMAND_REF = "IM-0016", 5, "ORD-26042-01"
NOTE = "시연 자동 사건 · 다른 주문 담당의 공용 재고 예약"
AFTER_ANSWER_SECONDS, AFTER_DRAFT_SECONDS = 20.0, 7.0


def enabled(dev_mode: bool) -> bool:
    return bool(dev_mode) and os.environ.get("ONTO_DEMO_AUTO_RESERVATION") == "1"


class DemoReservationCue:
    def __init__(self, db, identity, reservations, *, after_answer=AFTER_ANSWER_SECONDS, after_draft=AFTER_DRAFT_SECONDS):
        self.db, self.identity, self.reservations = db, identity, reservations
        self.after_answer, self.after_draft = after_answer, after_draft
        self._lock = threading.Lock()
        self._timers: dict[str, tuple[float, threading.Timer]] = {}

    # Triggers -------------------------------------------------------------
    def delivery_answered(self, company_id: str) -> None:
        self._schedule(company_id, self.after_answer)

    def purchase_request_drafted(self, company_id: str) -> None:
        self._schedule(company_id, self.after_draft)

    def _schedule(self, company_id: str, delay: float) -> None:
        if not self._eligible(company_id):
            return
        import time
        due = time.monotonic() + delay
        with self._lock:
            current = self._timers.get(company_id)
            if current and current[0] <= due:
                return  # An earlier cue is already waiting.
            if current:
                current[1].cancel()
            timer = threading.Timer(delay, self._fire, args=(company_id,))
            timer.daemon = True
            self._timers[company_id] = (due, timer)
            timer.start()

    def cancel(self) -> None:
        with self._lock:
            for _, timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    # Firing ---------------------------------------------------------------
    def _eligible(self, company_id: str) -> bool:
        with self.db.connect() as conn:
            if conn.execute("SELECT 1 FROM inventory_reservations WHERE company_id=%s LIMIT 1", (company_id,)).fetchone():
                return False
            row = conn.execute("SELECT s.payload->'bom'->>'bom_id' AS bom FROM service_snapshots s JOIN ledger_state l "
                               "ON l.company_id=s.company_id AND l.version=s.ledger_version WHERE s.company_id=%s",
                               (company_id,)).fetchone()
        return bool(row and row["bom"])

    def _lot(self, company_id: str):
        with self.db.connect() as conn:
            row = conn.execute("SELECT l.version, s.payload FROM ledger_state l JOIN service_snapshots s "
                               "ON s.company_id=l.company_id AND s.ledger_version=l.version WHERE l.company_id=%s",
                               (company_id,)).fetchone()
        if not row:
            return None, None
        payload = row["payload"]
        stock = payload.get("current_inventory") or payload["opening_balance"]["inventory"]
        lots = sorted((x for x in stock if x.get("item_id") == ITEM_ID and x.get("lot_id")
                       and (x.get("available_qty") or 0) >= QUANTITY), key=lambda x: (-x["available_qty"], x["lot_id"]))
        return row["version"], (lots[0]["lot_id"] if lots else None)

    def _fire(self, company_id: str) -> None:
        with self._lock:
            self._timers.pop(company_id, None)
        try:
            if not self._eligible(company_id):
                return
            planner = self.identity.login(PLANNER_PROFILE).principal
            if planner.company_id != company_id:
                return
            for _ in range(5):
                version, lot = self._lot(company_id)
                if lot is None:
                    return
                body = ReservationCreate(item_id=ITEM_ID, lot_id=lot, quantity=QUANTITY, demand_ref=DEMAND_REF, note=NOTE,
                                         expected_ledger_version=version,
                                         idempotency_key=f"demo-auto-reserve-{company_id}-{version}")
                try:
                    result = self.reservations.reserve(planner, company_id, body)
                    log.info("demo reservation committed at ledger v%s", result["ledger_version"])
                    return
                except ServiceError as error:
                    if error.code not in {"RESERVATION_LEDGER_VERSION_CHANGED", "RESERVATION_LEDGER_NOT_READY"}:
                        log.warning("demo reservation skipped: %s", error.code)
                        return
        except Exception:  # A demo cue must never take the service down.
            log.exception("demo reservation cue failed")
