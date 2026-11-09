"""Pure reduction of approved internal procurement events into new snapshots."""
from copy import deepcopy
from datetime import datetime

from service.contracts import content_hash
from service.errors import ServiceError
from service.procurement_contracts import CONTENT_TYPES


def apply_procurement_event(previous, event):
    try:
        value = event["payload"]
        content = value["content"]
        kind = content["kind"]
        typed = CONTENT_TYPES[kind].model_validate({k: v for k, v in content.items() if k not in {"resolved", "basis_ledger_version"}})
        meta = previous["metadata"]
        approval = value["approval"]
        version = event["ledger_version"]
        identifier = value["document_id"]
        if (event["event_type"] != "ProcurementPosted" or value["event_type"] != "ProcurementPosted"
                or value["company_id"] != meta["company_id"] or value["ledger_version"] != version
                or version != meta["server_ledger_version"] + 1 or content["basis_ledger_version"] != version - 1
                or str(event["procurement_document_id"]) != identifier
                or content_hash(content) != value["payload_hash"]
                or approval["approved_hash"] != value["payload_hash"]
                or approval["approved_version"] != value["document_version"]
                or approval["created_by"] in {approval["reviewed_by"], approval["approved_by"]}
                or not approval["reviewed_by"] or not approval["approved_by"]):
            raise ValueError()
        recorded = event["recorded_at"]
        recorded = recorded if isinstance(recorded, datetime) else datetime.fromisoformat(recorded)
        if recorded.utcoffset() is None:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ServiceError("PROCUREMENT_EVENT_CONTRACT_INVALID", 409) from None

    snapshot = deepcopy(previous)
    state = snapshot.setdefault("procurement", {"documents": [], "receipts": [], "scope": "INTERNAL_DEVELOPMENT_ONLY"})
    documents = {row["id"]: row for row in state["documents"]}
    if identifier in documents:
        raise ServiceError("PROCUREMENT_EVENT_DUPLICATE", 409)
    resolved = content["resolved"]
    parent = None
    if kind != "QUOTE":
        parent_ref = resolved.get("parent", {})
        parent = documents.get(parent_ref.get("id"))
        if (not parent or any(parent[key] != parent_ref[ref] for key, ref in
                (("kind", "kind"), ("version", "version"), ("payload_hash", "payload_hash"), ("ledger_version", "ledger_version")))):
            raise ServiceError("PROCUREMENT_EVENT_PARENT_INVALID", 409)
        expected_kind = {"PR": "QUOTE", "PO": "PR", "SUPPLY_CONFIRMATION": "PO", "RECEIPT": "PO", "IQC": "RECEIPT"}.get(kind)
        if expected_kind and (parent["kind"] != expected_kind or parent.get("cancelled_by")):
            raise ServiceError("PROCUREMENT_EVENT_PARENT_INVALID", 409)
        if kind in {"PO", "RECEIPT"}:
            if typed.quantity > parent["remaining_qty"]:
                raise ServiceError("PROCUREMENT_EVENT_QUANTITY_INVALID", 409)
            parent["remaining_qty"] -= typed.quantity
            if kind == "RECEIPT":
                if typed.quantity > parent.get("confirmed_remaining_qty", 0):
                    raise ServiceError("PROCUREMENT_EVENT_CONFIRMATION_REQUIRED", 409)
                parent["confirmed_remaining_qty"] -= typed.quantity
        if kind in {"PR", "PO", "SUPPLY_CONFIRMATION", "RECEIPT", "IQC"} and any(
                resolved.get(key) != parent["content"]["resolved"].get(key)
                for key in ("item_id", "item_entity_id", "supplier_id", "supplier_entity_id", "unit")):
            raise ServiceError("PROCUREMENT_EVENT_ITEM_MISMATCH", 409)
    row = {"id": identifier, "kind": kind, "version": value["document_version"], "payload_hash": value["payload_hash"],
           "ledger_version": version, "content": deepcopy(content), "status": "POSTED", "cancelled_by": None,
           "remaining_qty": getattr(typed, "quantity", 0), "supplier_confirmation": False, "transmission_status": "NOT_SENT"}
    state["documents"].append(row)
    if kind == "SUPPLY_CONFIRMATION":
        if typed.confirmed_qty > parent["content"]["quantity"] - parent.get("confirmed_qty", 0):
            raise ServiceError("PROCUREMENT_EVENT_CONFIRMATION_INVALID", 409)
        parent["confirmed_qty"] = parent.get("confirmed_qty", 0) + typed.confirmed_qty
        parent["confirmed_remaining_qty"] = parent.get("confirmed_remaining_qty", 0) + typed.confirmed_qty

    inventory = snapshot.setdefault("current_inventory", deepcopy(snapshot["opening_balance"]["inventory"]))
    receipt = None
    if kind == "RECEIPT":
        receipt = {"receipt_id": identifier, "po_id": parent["id"], "lot_code": typed.lot_code,
                   "item_id": resolved["item_id"], "unit": resolved["unit"], "quantity": typed.quantity,
                   "passed_qty": 0, "failed_qty": 0, "pending_qty": typed.quantity,
                   "provenance": "DEVELOPMENT_EVENT", "receipt_ref": typed.receipt_ref}
        if any(x["lot_code"] == typed.lot_code for x in state["receipts"]):
            raise ServiceError("PROCUREMENT_EVENT_LOT_DUPLICATE", 409)
        state["receipts"].append(receipt)
        item_rows = [x for x in inventory if x["item_id"] == resolved["item_id"]]
        aggregates = [x for x in item_rows if x["scope"] != "LOT"]
        if aggregates:
            if len(aggregates) != 1 or len(item_rows) != 1:
                raise ServiceError("PROCUREMENT_INVENTORY_COVERAGE_AMBIGUOUS", 409)
            target = aggregates[0]
            target["on_hand_qty"] += typed.quantity
            target["held_qty"] += typed.quantity
            target["pending_inspection_qty"] = target.get("pending_inspection_qty", 0) + typed.quantity
            target.setdefault("receipt_ids", []).append(identifier)
            target["inventory_origin"] = "OPENING_AGGREGATE_PLUS_DEVELOPMENT_RECEIPTS"
            receipt["inventory_key"] = {"scope": target["scope"], "item_id": target["item_id"]}
        else:
            if not item_rows or any(x["unit"] != resolved["unit"] for x in item_rows):
                raise ServiceError("PROCUREMENT_INVENTORY_UNIT_UNCONFIRMED", 409)
            target = {"scope": "LOT", "lot_id": typed.lot_code, "item_id": resolved["item_id"], "unit": resolved["unit"],
                      "location": "INTERNAL_RECEIVING", "on_hand_qty": typed.quantity,
                      "reserved_other_qty": 0, "reserved_self_qty": 0, "held_qty": typed.quantity,
                      "available_qty": 0, "unreserved_qty": 0, "pending_inspection_qty": typed.quantity,
                      "failed_inspection_qty": 0, "technical_mapping_status": "DEMO_ASSUMPTION",
                      "inventory_origin": "DEVELOPMENT_RECEIPT", "receipt_ids": [identifier]}
            inventory.append(target)
            receipt["inventory_key"] = {"scope": "LOT", "lot_id": typed.lot_code, "item_id": target["item_id"]}
    elif kind == "IQC":
        receipt = next((x for x in state["receipts"] if x["receipt_id"] == parent["id"]), None)
        inspected = typed.passed_qty + typed.failed_qty
        if not receipt or inspected > receipt["pending_qty"] or inspected > parent["remaining_qty"]:
            raise ServiceError("PROCUREMENT_EVENT_INSPECTION_INVALID", 409)
        parent["remaining_qty"] -= inspected
        receipt["passed_qty"] += typed.passed_qty
        receipt["failed_qty"] += typed.failed_qty
        receipt["pending_qty"] -= inspected
        matches = [x for x in inventory if all(x.get(k) == v for k, v in receipt["inventory_key"].items())]
        if len(matches) != 1:
            raise ServiceError("PROCUREMENT_EVENT_INVENTORY_INVALID", 409)
        target = matches[0]
        target["held_qty"] -= typed.passed_qty
        target["available_qty"] += typed.passed_qty
        target["unreserved_qty"] += typed.passed_qty
        target["pending_inspection_qty"] -= inspected
        target["failed_inspection_qty"] = target.get("failed_inspection_qty", 0) + typed.failed_qty
    elif kind == "CANCEL":
        if parent["kind"] not in {"PR", "PO"} or parent.get("cancelled_by") or parent["remaining_qty"] <= 0:
            raise ServiceError("PROCUREMENT_EVENT_CANCELLATION_INVALID", 409)
        if resolved["cancelled_remaining_qty"] != parent["remaining_qty"]:
            raise ServiceError("PROCUREMENT_EVENT_CANCELLATION_INVALID", 409)
        if parent["kind"] == "PO":
            request_id = parent["content"]["resolved"]["parent"]["id"]
            request = documents[request_id]
            if not request.get("cancelled_by"):
                request["remaining_qty"] += parent["remaining_qty"]
        parent["cancelled_qty"] = parent["remaining_qty"]
        parent["remaining_qty"] = 0
        parent["cancelled_by"] = identifier
        if parent["kind"] == "PO":
            parent["confirmed_remaining_qty"] = 0
    if receipt and receipt["quantity"] != receipt["passed_qty"] + receipt["failed_qty"] + receipt["pending_qty"]:
        raise ServiceError("PROCUREMENT_INSPECTION_BALANCE_INVALID", 409)

    evidence = {"record_type": "development_procurement", "event_id": str(event["event_id"]),
                "document_id": identifier, "document_version": value["document_version"],
                "payload_hash": value["payload_hash"], "ledger_version": version,
                "registered_at": recorded.isoformat(), "synthetic": True}
    def node_id(document_id, role="document"):
        return "urn:ontologics:procurement:" + content_hash([meta["company_id"], meta["run_id"], document_id, role])
    def node(role, type_, attributes, label):
        ident = node_id(identifier, role)
        if any(x["id"] == ident for x in snapshot["entities"]):
            raise ServiceError("PROCUREMENT_GRAPH_DUPLICATE", 409)
        snapshot["entities"].append({"id": ident, "type": type_, "source_type": type_, "display_id": label,
            "logicalId": label, "label": label, "origin": "development_workflow", "run_id": meta["run_id"],
            "attributes": deepcopy(attributes), "evidence": deepcopy(evidence)})
        return ident
    def edge(start, predicate, end):
        snapshot["relationships"].append({"id": node_id(identifier, [start, predicate, end]),
            "source": start, "predicate": predicate, "target": end, "evidence": deepcopy(evidence)})
    type_ = {"QUOTE": "SupplierOffer", "PR": "PurchaseRequest", "PO": "PurchaseOrder", "SUPPLY_CONFIRMATION": "SourceDocument",
             "RECEIPT": "PartsReceiptLine", "IQC": "InspectionResult", "CANCEL": "SourceDocument"}[kind]
    attributes = {"document_id": identifier, "kind": kind, "status": "POSTED", "quantity": getattr(typed, "quantity", 0),
                  "supplier_confirmation": False, "transmission_status": "NOT_SENT"}
    if kind == "IQC":
        attributes.update(received_qty=receipt["quantity"], passed_qty=receipt["passed_qty"], failed_qty=receipt["failed_qty"],
                          pending_qty=receipt["pending_qty"], batch_passed_qty=typed.passed_qty, batch_failed_qty=typed.failed_qty)
    elif kind == "SUPPLY_CONFIRMATION":
        attributes.update(quantity=typed.confirmed_qty, confirmed_qty=typed.confirmed_qty,
                          confirmation_mode="INTERNAL_SIMULATED", promised_arrival=None)
    elif kind == "CANCEL":
        attributes.update(cancelled_remaining_qty=resolved["cancelled_remaining_qty"])
    elif kind == "QUOTE":
        attributes.update({key: content[key] for key in ("unit_price", "currency", "moq", "order_multiple", "freight", "valid_until")})
    document_node = node("document", type_, attributes, kind + " " + identifier[:8])
    revision = node("version", "DocumentVersion", {"version": value["document_version"], "payload_hash": value["payload_hash"]}, kind + " version")
    grant = node("approval", "ApprovalGrant", approval, kind + " approval")
    edge(grant, "approvalForDocumentVersion", revision)
    if kind not in {"CANCEL", "SUPPLY_CONFIRMATION"}:
        edge(document_node, "recordedByDocumentVersion", revision)
    else:
        # The existing business record is recorded by this additional local
        # document version. No invented confirmation predicate/T-Box class.
        edge(node_id(parent["id"]), "recordedByDocumentVersion", revision)
        source = node("evidence", "Evidence", evidence, kind + " evidence")
        edge(node_id(parent["id"]), "supportedBy", source)
        edge(document_node, "supportedBy", source)
    if kind == "QUOTE":
        edge(document_node, "offersItem", resolved["item_entity_id"])
        edge(document_node, "offerFromSupplier", resolved["supplier_entity_id"])
    elif kind == "PR":
        edge(document_node, "requestSelectsOffer", node_id(parent["id"]))
    elif kind == "PO":
        line = node("line", "PurchaseOrderLine", {"quantity": typed.quantity, "unit": resolved["unit"]}, "PO line " + identifier[:8])
        edge(document_node, "hasPOLine", line)
        edge(line, "fulfillsRequest", node_id(parent["id"]))
        edge(line, "orderLineItem", resolved["item_entity_id"])
    elif kind == "RECEIPT":
        edge(document_node, "receiptAgainstPOLine", node_id(parent["id"], "line"))
        lot = node("lot", "PartLot", {"item_id": resolved["item_id"], "on_hand_qty": typed.quantity,
                    "available_qty": 0, "held_qty": typed.quantity, "unit": resolved["unit"]}, typed.lot_code)
        edge(lot, "lotOf", resolved["item_entity_id"])
    elif kind == "IQC":
        edge(document_node, "inspectionOfReceiptLine", node_id(parent["id"]))
        lot_id = node_id(parent["id"], "lot")
        lot = next(x for x in snapshot["entities"] if x["id"] == lot_id)
        lot["attributes"].update(available_qty=receipt["passed_qty"], held_qty=receipt["failed_qty"] + receipt["pending_qty"])
        lot["evidence"] = {**evidence, "receipt_document_id": parent["id"]}
        if typed.passed_qty:
            edge(lot_id, "lotAcceptedBy", document_node)
            edge(lot_id, "acceptedReceiptLine", node_id(parent["id"]))
    snapshot["metadata"].setdefault("opening_effective_at", meta["effective_at"])
    snapshot["metadata"].update(server_ledger_version=version, recorded_at=recorded.isoformat(),
        effective_at=max(datetime.fromisoformat(meta["effective_at"]), recorded).isoformat(),
        known_as_of=max(datetime.fromisoformat(meta["known_as_of"]), recorded).isoformat(),
        last_procurement_event=evidence, procurement_rule_version="internal-procurement/1")
    if kind in {"RECEIPT", "IQC"}:
        snapshot["metadata"]["current_inventory_effective_at"] = snapshot["metadata"]["effective_at"]
    if "internal-procurement/1" not in snapshot["metadata"]["rule_version"]:
        snapshot["metadata"]["rule_version"] += "+internal-procurement/1"
    return snapshot
