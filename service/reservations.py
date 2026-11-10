"""Other-demand stock reservations as committed ledger events (DEC-H47).

A reservation records that another synthetic demand holds part of one existing
LOT. It never moves, consumes, receives or releases stock and it never changes
delivery quantities. The command checks permission, exact ledger version and
reservable quantity, then writes the reservation, audit, ledger event and outbox
in one transaction. Idempotent retries return the first result.
"""

from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator
from psycopg.types.json import Jsonb

from service.contracts import Contract, content_hash
from service.domain import DomainService
from service.errors import ServiceError


RULE_VERSION = "inventory-reservation/1"
_BALANCE_KEYS = ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty", "available_qty")


class ReservationCreate(Contract):
    item_id: str = Field(min_length=1, max_length=100)
    lot_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=10**9)
    demand_ref: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    note: str = Field(min_length=8, max_length=500)
    expected_ledger_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("note")
    @classmethod
    def plain_note(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("INVALID_NOTE")
        return value


def _balance(row):
    values = {key: row.get(key) for key in _BALANCE_KEYS}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ServiceError("RESERVATION_INVENTORY_UNKNOWN", 409)
    if values["available_qty"] != values["on_hand_qty"] - values["reserved_other_qty"] - values["held_qty"]:
        raise ServiceError("RESERVATION_INVENTORY_INVALID", 409)
    return values


def _reservable(balance):
    # Another demand cannot take this demand's own reservation or held stock.
    return balance["on_hand_qty"] - balance["reserved_other_qty"] - balance["reserved_self_qty"] - balance["held_qty"]


def _lot_row(inventory, item_id, lot_id):
    rows = [row for row in inventory if row.get("scope") == "LOT" and row.get("lot_id") == lot_id]
    if len(rows) != 1 or rows[0].get("item_id") != item_id:
        raise ServiceError("RESERVATION_LOT_UNCONFIRMED", 409)
    return rows[0]


class ReservationService(DomainService):
    def reserve(self, principal, company_id, body: ReservationCreate):
        body = ReservationCreate.model_validate(body)
        request = body.model_dump(mode="json", exclude={"idempotency_key"})

        def work(conn):
            state = conn.execute("SELECT version FROM ledger_state WHERE company_id=%s FOR UPDATE", (company_id,)).fetchone()
            if not state or state["version"] != body.expected_ledger_version:
                raise ServiceError("RESERVATION_LEDGER_VERSION_CHANGED", 409)
            row = conn.execute("SELECT run_id,payload FROM service_snapshots WHERE company_id=%s AND ledger_version=%s ORDER BY run_id",
                               (company_id, body.expected_ledger_version)).fetchall()
            if len(row) != 1:
                raise ServiceError("RESERVATION_LEDGER_NOT_READY", 409)
            run_id, payload = row[0]["run_id"], row[0]["payload"]
            target_round = payload["opening_balance"]["delivery_round"].get("round_id")
            if body.demand_ref == target_round:
                # This command records another demand. The selected round's
                # own reservation would be reserved_self, a different contract.
                raise ServiceError("RESERVATION_DEMAND_IS_TARGET", 409)
            inventory = payload.get("current_inventory", payload["opening_balance"]["inventory"])
            lot = _lot_row(inventory, body.item_id, body.lot_id)
            before = _balance(lot)
            if body.quantity > _reservable(before):
                raise ServiceError("RESERVATION_EXCEEDS_AVAILABLE", 409)
            after = {**before, "reserved_other_qty": before["reserved_other_qty"] + body.quantity,
                     "available_qty": before["available_qty"] - body.quantity}
            # The shortage is per item: every LOT of the same item and unit counts.
            item_rows = [row for row in inventory if row.get("item_id") == body.item_id and row.get("unit") == lot["unit"]]
            item_before = sum(_balance(row)["available_qty"] for row in item_rows)
            items = [e for e in payload["entities"] if e["type"] == "InternalItem" and body.item_id in {e["id"], e["display_id"]}]
            lots = [e for e in payload["entities"] if e["type"] == "PartLot" and e["display_id"] == body.lot_id]
            if len(items) != 1 or len(lots) != 1:
                raise ServiceError("RESERVATION_ENTITY_UNCONFIRMED", 409)
            version = body.expected_ledger_version + 1
            reservation_id, event_id = uuid4(), uuid4()
            content = {"kind": "inventory_reservation", "item_id": items[0]["display_id"], "item_entity_id": items[0]["id"],
                       "lot_id": body.lot_id, "lot_entity_id": lots[0]["id"], "unit": lot["unit"],
                       "quantity": body.quantity, "demand_ref": body.demand_ref,
                       "demand_origin": "SYNTHETIC_OTHER_DEMAND_REFERENCE", "target_round_id": target_round,
                       "basis_ledger_version": body.expected_ledger_version, "before": before, "after": after,
                       "item_available_before": item_before, "item_available_after": item_before - body.quantity,
                       "note": body.note, "rule_version": RULE_VERSION}
            payload_hash = content_hash(content)
            conn.execute("INSERT INTO inventory_reservations(id,company_id,run_id,item_id,lot_id,unit,quantity,demand_ref,"
                         "basis_ledger_version,ledger_version,payload_hash,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (reservation_id, company_id, run_id, content["item_id"], body.lot_id, lot["unit"], body.quantity,
                          body.demand_ref, body.expected_ledger_version, version, payload_hash, principal.actor_id))
            event = {"event_type": "InventoryReserved", "company_id": company_id, "run_id": run_id,
                     "ledger_version": version, "reservation_id": str(reservation_id),
                     "content": content, "payload_hash": payload_hash}
            conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,event_type,payload,actor_id,reservation_id) "
                         "VALUES (%s,%s,%s,NULL,'InventoryReserved',%s,%s,%s)",
                         (event_id, company_id, version, Jsonb(event), principal.actor_id, reservation_id))
            conn.execute("INSERT INTO outbox(event_id,company_id,ledger_version,payload) VALUES (%s,%s,%s,%s)",
                         (event_id, company_id, version, Jsonb({"event_type": "InventoryReserved", "reservation_id": str(reservation_id),
                                                               "ledger_version": version, "payload_hash": payload_hash})))
            conn.execute("INSERT INTO inventory_reservation_audit(audit_id,company_id,actor_id,action,reservation_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
                         (uuid4(), company_id, principal.actor_id, "inventory.reserve", reservation_id,
                          Jsonb({"ledger_version": version, "payload_hash": payload_hash, "identity_revision": principal.identity_revision})))
            conn.execute("UPDATE ledger_state SET version=%s WHERE company_id=%s", (version, company_id))
            return {"reservation_id": str(reservation_id), "event_id": str(event_id), "ledger_version": version,
                    "payload_hash": payload_hash, "item_id": content["item_id"], "lot_id": body.lot_id,
                    "quantity": body.quantity, "demand_ref": body.demand_ref, "before": before, "after": after,
                    "stock_moved": False, "delivery_changed": False}

        return self._run(principal, company_id, "inventory.reserve", body.idempotency_key, request, work)

    def list(self, principal, company_id, limit=100):
        with self.db.connect() as conn:
            self.identity.require(principal, "ledger.read", conn, company_id=company_id)
            rows = conn.execute("SELECT id,item_id,lot_id,unit,quantity,demand_ref,basis_ledger_version,ledger_version,payload_hash,created_at "
                                "FROM inventory_reservations WHERE company_id=%s ORDER BY ledger_version DESC LIMIT %s",
                                (company_id, limit)).fetchall()
        return [{**row, "id": str(row["id"]), "created_at": row["created_at"].isoformat()} for row in rows]


def apply_reservation_event(previous, event):
    """Reduce one committed reservation into a new immutable snapshot."""
    try:
        value = event["payload"]
        content = value["content"]
        meta = previous["metadata"]
        version = event["ledger_version"]
        recorded = event["recorded_at"]
        recorded = recorded if isinstance(recorded, datetime) else datetime.fromisoformat(recorded)
        if (event["event_type"] != "InventoryReserved" or value["event_type"] != "InventoryReserved"
                or value["company_id"] != meta["company_id"] or value["run_id"] != meta["run_id"]
                or value["ledger_version"] != version or version != meta["server_ledger_version"] + 1
                or content["kind"] != "inventory_reservation" or content["basis_ledger_version"] != version - 1
                or content_hash(content) != value["payload_hash"]
                or str(UUID(str(event["reservation_id"]))) != value["reservation_id"]
                or recorded.utcoffset() is None
                or type(content["quantity"]) is not int or content["quantity"] < 1):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ServiceError("RESERVATION_EVENT_CONTRACT_INVALID", 409) from None
    snapshot = deepcopy(previous)
    inventory = snapshot.setdefault("current_inventory", deepcopy(snapshot["opening_balance"]["inventory"]))
    row = _lot_row(inventory, content["item_id"], content["lot_id"])
    if _balance(row) != content["before"] or content["quantity"] > _reservable(content["before"]):
        raise ServiceError("RESERVATION_EVENT_BASIS_MISMATCH", 409)
    row["reserved_other_qty"] = content["after"]["reserved_other_qty"]
    row["available_qty"] = content["after"]["available_qty"]
    if "unreserved_qty" in row:
        row["unreserved_qty"] -= content["quantity"]
    row.setdefault("reservation_ids", []).append(value["reservation_id"])
    lot = next((e for e in snapshot["entities"] if e["id"] == content["lot_entity_id"] and e["type"] == "PartLot"), None)
    if lot is None:
        raise ServiceError("RESERVATION_EVENT_BASIS_MISMATCH", 409)
    attributes = lot.setdefault("attributes", {})
    attributes["reserved_other_qty"] = row["reserved_other_qty"]
    attributes["available_qty"] = row["available_qty"]
    if "unreserved_qty" in attributes:
        attributes["unreserved_qty"] = row.get("unreserved_qty", attributes["unreserved_qty"] - content["quantity"])
    evidence = {"record_type": "inventory_reservation", "event_id": str(event["event_id"]),
                "reservation_id": value["reservation_id"], "payload_hash": value["payload_hash"],
                "ledger_version": version, "registered_at": recorded.isoformat(), "synthetic": True}
    identifier = "urn:ontologics:reservation:" + content_hash([meta["company_id"], meta["run_id"], value["reservation_id"]])
    if any(e["id"] == identifier for e in snapshot["entities"]):
        raise ServiceError("RESERVATION_GRAPH_DUPLICATE", 409)
    label = f"RSV {content['demand_ref']} · {content['item_id']} {content['quantity']}"
    snapshot["entities"].append({"id": identifier, "type": "ReservationEvent", "source_type": "ReservationEvent",
        "display_id": label, "logicalId": label, "label": label, "origin": "development_workflow", "run_id": meta["run_id"],
        "attributes": {"reservation_id": value["reservation_id"], "item_id": content["item_id"], "lot_id": content["lot_id"],
                       "unit": content["unit"], "quantity": content["quantity"], "demand_ref": content["demand_ref"],
                       "demand_origin": content["demand_origin"], "reserved_other_before": content["before"]["reserved_other_qty"],
                       "reserved_other_after": content["after"]["reserved_other_qty"],
                       "available_before": content["before"]["available_qty"], "available_after": content["after"]["available_qty"]},
        "evidence": deepcopy(evidence)})
    snapshot["relationships"].append({"id": "urn:ontologics:reservation:" + content_hash([identifier, "reservationEventLot", lot["id"]]),
        "source": identifier, "predicate": "reservationEventLot", "target": lot["id"], "evidence": deepcopy(evidence)})
    snapshot.setdefault("reservations", []).append({"reservation_id": value["reservation_id"], "event_id": str(event["event_id"]),
        "ledger_version": version, "item_id": content["item_id"], "lot_id": content["lot_id"], "unit": content["unit"],
        "quantity": content["quantity"], "demand_ref": content["demand_ref"], "demand_origin": content["demand_origin"],
        "before": deepcopy(content["before"]), "after": deepcopy(content["after"]), "recorded_at": recorded.isoformat(),
        "item_available_before": content.get("item_available_before"), "item_available_after": content.get("item_available_after"),
        "entity_id": identifier, "lot_entity_id": lot["id"]})
    snapshot["metadata"].setdefault("opening_effective_at", meta["effective_at"])
    snapshot["metadata"].update(server_ledger_version=version, recorded_at=recorded.isoformat(),
        effective_at=max(datetime.fromisoformat(meta["effective_at"]), recorded).isoformat(),
        known_as_of=max(datetime.fromisoformat(meta["known_as_of"]), recorded).isoformat(),
        current_inventory_effective_at=max(datetime.fromisoformat(meta["effective_at"]), recorded).isoformat(),
        last_reservation_event=evidence)
    if RULE_VERSION not in snapshot["metadata"]["rule_version"]:
        snapshot["metadata"]["rule_version"] += "+" + RULE_VERSION
    return snapshot


def reservation_impacts(requirements, reservations):
    """Before/after shortage of each calculated item that a reservation touched.

    Uses the same requirement arithmetic: shortage = max(required − available, 0).
    Only items with a calculated requirement and an exact before/after balance
    are reported; unknown requirement never becomes a zero shortage.
    """
    from decimal import Decimal
    items = {row["item_id"]: row for row in requirements.get("items", []) if row.get("state") == "CALCULATED"}
    impacts = []
    for reservation in sorted(reservations, key=lambda row: row["ledger_version"]):
        item = items.get(reservation["item_id"])
        if not item or item.get("unit") != reservation.get("unit"):
            continue
        required, planned = Decimal(item["required_qty"]), Decimal(item["planned_required_qty"])
        # Item totals across all LOTs when the event recorded them; older events
        # without totals fall back to the reserved LOT's own balance.
        if reservation.get("item_available_before") is not None:
            before, after = Decimal(reservation["item_available_before"]), Decimal(reservation["item_available_after"])
        else:
            before, after = Decimal(reservation["before"]["available_qty"]), Decimal(reservation["after"]["available_qty"])
        shortage = lambda need, available: format(max(need - available, Decimal(0)).normalize(), "f")
        impacts.append({"reservation_id": reservation["reservation_id"], "ledger_version": reservation["ledger_version"],
            "item_id": item["item_id"], "unit": item["unit"], "lot_id": reservation["lot_id"],
            "quantity": reservation["quantity"], "demand_ref": reservation["demand_ref"],
            "on_hand_qty": reservation["after"]["on_hand_qty"],
            "reserved_other_before": reservation["before"]["reserved_other_qty"],
            "reserved_other_after": reservation["after"]["reserved_other_qty"],
            "available_before": format(before.normalize(), "f"), "available_after": format(after.normalize(), "f"),
            "shortage_before": shortage(required, before), "shortage_after": shortage(required, after),
            "planned_shortage_before": shortage(planned, before), "planned_shortage_after": shortage(planned, after),
            "entity_id": reservation.get("entity_id"), "lot_entity_id": reservation.get("lot_entity_id")})
    return impacts
