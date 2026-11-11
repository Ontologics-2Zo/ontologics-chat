"""Pure reduction of approved internal procurement events into new snapshots."""
from copy import deepcopy
from datetime import datetime

from service.contracts import content_hash
from service.errors import ServiceError
from service.procurement_contracts import CONTENT_TYPES


def legacy_pools(payload):
    """Opening-balance boards (no serials) still at each stage, after posted cohort moves."""
    stored = (payload.get("fulfillment") or {}).get("legacy")
    if stored:
        return dict(stored)
    opening = payload["opening_balance"]["delivery_round"]
    return {"good_not_received": opening["good_not_received_qty"], "warehouse": opening["warehouse_qty"],
            "in_transit": opening["in_transit_qty"]}


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
    if kind not in {"QUOTE", "SCENARIO", "COHORT", "SUBSTITUTION"} and not (kind == "PRODUCTION" and content.get("lot_code")):
        parent_ref = resolved.get("parent", {})
        parent = documents.get(parent_ref.get("id"))
        if (not parent or any(parent[key] != parent_ref[ref] for key, ref in
                (("kind", "kind"), ("version", "version"), ("payload_hash", "payload_hash"), ("ledger_version", "ledger_version")))):
            raise ServiceError("PROCUREMENT_EVENT_PARENT_INVALID", 409)
        expected_kind = {"PR": "QUOTE", "PO": "PR", "SUPPLY_CONFIRMATION": "PO", "RECEIPT": "PO", "IQC": "RECEIPT",
                         "PRODUCTION": "IQC", "FG_RECEIPT": "PRODUCTION", "SHIPMENT": "FG_RECEIPT", "ARRIVAL": "SHIPMENT"}.get(kind)
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

    if kind == "SUBSTITUTION":
        # Count part of an existing candidate for the selected demand; stock rows are not changed.
        candidates = snapshot.setdefault("current_substitutes", deepcopy(snapshot["opening_balance"].get("substitutes") or []))
        candidate = next((x for x in candidates if x["substitution_id"] == resolved["substitution_id"]), None)
        if (not candidate or candidate["original_item_id"] != typed.original_item_id
                or candidate["candidate_item_id"] != typed.candidate_item_id
                or int(candidate.get("counted_qty_for_selected_demand") or 0) != resolved["counted_before"]
                or typed.quantity > int(candidate["candidate_on_hand_qty"]) - resolved["counted_before"]):
            raise ServiceError("SUBSTITUTE_EVENT_INVALID", 409)
        candidate.update(status="APPROVED", approval_ref=identifier,
                         counted_qty_for_selected_demand=resolved["counted_before"] + typed.quantity,
                         approval_scope="DEMO_APPLICATION_NOT_TECHNICAL_REVIEW")
        item_entity = next((x for x in snapshot["entities"] if x["id"] == resolved["candidate_entity_id"]), None)
        if item_entity:
            item_entity.setdefault("attributes", {}).update(substitute_status="APPROVED",
                counted_qty_for_selected_demand=candidate["counted_qty_for_selected_demand"])

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
    if kind == "COHORT":
        # Baseline boards move by quantity between the stages the opening
        # balance recorded; never more than that stage still holds.
        cohort_state = snapshot.setdefault("fulfillment", {"boards": {}, "scope": "INTERNAL_DEVELOPMENT_ONLY"})
        pools = legacy_pools(snapshot)
        round_ = snapshot.setdefault("current_delivery_round", deepcopy(snapshot["opening_balance"]["delivery_round"]))
        n, stage = typed.quantity, typed.stage
        source, target = {"FG_RECEIPT": ("good_not_received", "warehouse"), "SHIPMENT": ("warehouse", "in_transit"),
                          "ARRIVAL": ("in_transit", None)}[stage]
        if resolved["round_id"] != round_["round_id"] or n > pools[source]:
            raise ServiceError("FULFILLMENT_EXCEEDS_LEGACY_COHORT", 409)
        pools[source] -= n
        if target:
            pools[target] += n
        cohort_state["legacy"] = pools
        if stage == "FG_RECEIPT":
            round_["fg_received_qty"] += n; round_["good_not_received_qty"] -= n; round_["warehouse_qty"] += n
        elif stage == "SHIPMENT":
            round_["shipped_qty"] += n; round_["warehouse_qty"] -= n; round_["in_transit_qty"] += n
        else:
            round_["arrived_qty"] += n; round_["in_transit_qty"] -= n; round_["remaining_promise_qty"] -= n
        if min(round_[key] for key in ("good_not_received_qty", "warehouse_qty", "in_transit_qty", "remaining_promise_qty")) < 0:
            raise ServiceError("FULFILLMENT_EVENT_BASIS_INVALID", 409)
        round_entity = next(x for x in snapshot["entities"] if x["id"] == resolved["round_entity_id"])
        for key in ("fg_received_qty", "shipped_qty", "arrived_qty", "good_not_received_qty", "warehouse_qty",
                    "in_transit_qty", "remaining_promise_qty"):
            round_entity.setdefault("attributes", {})[key] = round_[key]
    fulfillment = None
    if kind in {"PRODUCTION", "FG_RECEIPT", "SHIPMENT", "ARRIVAL"}:
        fulfillment = snapshot.setdefault("fulfillment", {"boards": {}, "scope": "INTERNAL_DEVELOPMENT_ONLY"})
        round_ = snapshot.setdefault("current_delivery_round", deepcopy(snapshot["opening_balance"]["delivery_round"]))
        serials, n = resolved["board_serials"], len(resolved["board_serials"])
        if serials != typed.board_serials or resolved["round_id"] != round_["round_id"]:
            raise ServiceError("FULFILLMENT_EVENT_CONTRACT_INVALID", 409)
        boards = fulfillment["boards"]
        if kind == "PRODUCTION":
            lots = [x for x in inventory if x.get("scope") == "LOT" and x.get("lot_id") == resolved["lot_code"] and x.get("item_id") == resolved["item_id"]]
            consumed = resolved["consumed_qty"]
            if (len(lots) != 1 or lots[0]["available_qty"] < consumed or any(serial in boards for serial in serials)
                    or n > round_["remaining_good_target_qty"]):
                raise ServiceError("FULFILLMENT_EVENT_BASIS_INVALID", 409)
            lot_row = lots[0]
            lot_row["on_hand_qty"] -= consumed
            lot_row["available_qty"] -= consumed
            if "unreserved_qty" in lot_row:
                lot_row["unreserved_qty"] -= consumed
            lot_row["consumed_qty"] = lot_row.get("consumed_qty", 0) + consumed
            lot_entity = next((x for x in snapshot["entities"] if x["id"] == resolved["lot_entity_id"]), None)
            if lot_entity is not None:
                lot_entity.setdefault("attributes", {}).update(on_hand_qty=lot_row["on_hand_qty"], available_qty=lot_row["available_qty"],
                                                               consumed_qty=lot_row["consumed_qty"])
            round_["good_qty"] += n
            round_["good_not_received_qty"] += n
            round_["remaining_good_target_qty"] -= n
            for serial in serials:
                boards[serial] = {"status": "PRODUCED", "production_id": identifier, "lot_code": resolved["lot_code"]}
        else:
            state, link, next_state, next_link = {"FG_RECEIPT": ("PRODUCED", "production_id", "RECEIVED", "fg_receipt_id"),
                                                  "SHIPMENT": ("RECEIVED", "fg_receipt_id", "SHIPPED", "shipment_id"),
                                                  "ARRIVAL": ("SHIPPED", "shipment_id", "ARRIVED", "arrival_id")}[kind]
            if any(boards.get(serial, {}).get("status") != state or boards[serial].get(link) != parent["id"] for serial in serials):
                raise ServiceError("FULFILLMENT_EVENT_BASIS_INVALID", 409)
            for serial in serials:
                boards[serial].update(status=next_state, **{next_link: identifier})
            if kind == "FG_RECEIPT":
                round_["fg_received_qty"] += n; round_["good_not_received_qty"] -= n; round_["warehouse_qty"] += n
            elif kind == "SHIPMENT":
                round_["shipped_qty"] += n; round_["warehouse_qty"] -= n; round_["in_transit_qty"] += n
            else:
                round_["arrived_qty"] += n; round_["in_transit_qty"] -= n; round_["remaining_promise_qty"] -= n
        if min(round_[key] for key in ("good_not_received_qty", "warehouse_qty", "in_transit_qty", "remaining_promise_qty", "remaining_good_target_qty")) < 0:
            raise ServiceError("FULFILLMENT_EVENT_BASIS_INVALID", 409)
        round_entity = next(x for x in snapshot["entities"] if x["id"] == resolved["round_entity_id"])
        for key in ("good_qty", "fg_received_qty", "shipped_qty", "arrived_qty", "good_not_received_qty", "warehouse_qty",
                    "in_transit_qty", "remaining_promise_qty", "remaining_good_target_qty"):
            round_entity.setdefault("attributes", {})[key] = round_[key]
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
             "RECEIPT": "PartsReceiptLine", "IQC": "InspectionResult", "CANCEL": "SourceDocument",
             "PRODUCTION": "ProductionReport", "FG_RECEIPT": "FinishedGoodsReceipt", "SHIPMENT": "Shipment", "ARRIVAL": "ArrivalRecord",
             "SCENARIO": "ScenarioRun", "SUBSTITUTION": "SourceDocument",
             "COHORT": {"FG_RECEIPT": "FinishedGoodsReceipt", "SHIPMENT": "Shipment", "ARRIVAL": "ArrivalRecord"}.get(content.get("stage"), "SourceDocument")}[kind]
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
    elif kind == "COHORT":
        attributes.update(stage=typed.stage, cohort_ref=typed.cohort_ref, round_id=resolved["round_id"],
                          board_identity="UNKNOWN_LEGACY_COHORT", quantity=typed.quantity)
    elif kind == "SUBSTITUTION":
        attributes.update(substitution_ref=typed.substitution_ref, round_id=resolved["round_id"],
                          original_item_id=resolved["original_item_id"], candidate_item_id=resolved["candidate_item_id"],
                          unit=resolved["unit"], provenance="DEMO_APPLICATION_APPROVAL",
                          approval_scope="DEMO_APPLICATION_NOT_TECHNICAL_REVIEW")
    elif kind == "SCENARIO":
        attributes.update(scenario_ref=typed.scenario_ref, round_id=resolved["round_id"], provenance="DEMO_ASSUMPTION",
                          assumption_status="APPROVED_ASSUMPTION_NOT_FACT")
    elif fulfillment is not None:
        attributes.update(board_serials=list(resolved["board_serials"]), round_id=resolved["round_id"])
        if kind == "PRODUCTION":
            attributes.update(lot_code=resolved["lot_code"], item_id=resolved["item_id"], consumed_qty=resolved["consumed_qty"],
                              quantity_per_board=resolved["quantity_per_board"], conditional_application=resolved["conditional_application"])
    document_node = node("document", type_, attributes,
                         typed.scenario_ref if kind == "SCENARIO"
                         else f"대체 {typed.candidate_item_id} {typed.quantity}개" if kind == "SUBSTITUTION"
                         else f"기초 보드 {dict(FG_RECEIPT='입고', SHIPMENT='출하', ARRIVAL='도착')[typed.stage]} {typed.quantity}대" if kind == "COHORT"
                         else kind + " " + identifier[:8])
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
    if kind == "COHORT":
        # One LegacyCohort per round: the opening-balance boards without serials.
        cohort_id = "urn:ontologics:procurement:" + content_hash([meta["company_id"], meta["run_id"], resolved["round_id"], "legacy-cohort"])
        if not any(x["id"] == cohort_id for x in snapshot["entities"]):
            snapshot["entities"].append({"id": cohort_id, "type": "LegacyCohort", "source_type": "LegacyCohort",
                "display_id": f"{resolved['round_id']} 기초 보드", "logicalId": f"{resolved['round_id']} 기초 보드",
                "label": f"{resolved['round_id']} 기초 보드", "origin": "synthetic_operation", "run_id": meta["run_id"],
                "attributes": {"board_identity": "UNKNOWN_LEGACY_COHORT", "round_id": resolved["round_id"]},
                "evidence": deepcopy(evidence)})
        if typed.stage == "SHIPMENT":
            edge(document_node, "shipmentForRound", resolved["round_entity_id"])
            edge(document_node, "shipsCohort", cohort_id)
        else:
            edge(document_node, "supportedBy", node("evidence", "Evidence", evidence, f"{typed.cohort_ref} evidence"))
    elif kind == "SUBSTITUTION":
        # No substitute class or relation exists in the T-Box; the approved document
        # is kept as a source document with its evidence (items stay attributes).
        edge(document_node, "supportedBy", node("evidence", "Evidence", evidence, f"{typed.substitution_ref} evidence"))
    elif kind == "SCENARIO":
        # ScenarioRun → targetsRound → round; → usesProfile → profile → usesAssumption → each value.
        # The job's ProductionPlan uses the capacity assumption. All values are assumptions.
        edge(document_node, "targetsRound", resolved["round_entity_id"])
        profile = node("profile", "ScenarioProfile", {"scenario_ref": typed.scenario_ref, "provenance": "DEMO_ASSUMPTION"},
                       typed.scenario_ref + " profile")
        edge(document_node, "usesProfile", profile)
        assumption_ids = {}
        for key, label, value_, unit in (("inspection_business_days", "수입검사 기간", typed.inspection_business_days, "영업일"),
                                         ("daily_capacity", "생산능력", typed.daily_capacity, "대/영업일"),
                                         ("ship_prep_business_days", "출하 준비 기간", typed.ship_prep_business_days, "영업일"),
                                         ("transit_days", "운송 기간", typed.transit_days, "일")):
            # "label" would collide with the entity's rdfs:label (one label only).
            assumption_ids[key] = node(key, "ScenarioAssumption", {"assumption_key": key, "assumption_label": label, "value": value_,
                                       "unit": unit, "provenance": "DEMO_ASSUMPTION"}, f"{label} {value_}{unit}")
            edge(profile, "usesAssumption", assumption_ids[key])
        if resolved.get("job_entity_id"):
            plan = node("plan", "ProductionPlan", {"scenario_ref": typed.scenario_ref, "daily_capacity": typed.daily_capacity,
                        "provenance": "DEMO_ASSUMPTION"}, typed.scenario_ref + " plan")
            edge(resolved["job_entity_id"], "hasPlan", plan)
            edge(plan, "usesAssumption", assumption_ids["daily_capacity"])
    elif kind == "QUOTE":
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
    elif fulfillment is not None:
        board_id = lambda serial: node_id(fulfillment["boards"][serial]["production_id"], ["board", serial])
        if kind == "PRODUCTION":
            consumption = node("consumption", "ConsumptionEvent", {"lot_code": resolved["lot_code"], "item_id": resolved["item_id"],
                               "quantity": resolved["consumed_qty"], "unit": resolved["unit"]}, "CONSUME " + resolved["lot_code"])
            edge(consumption, "consumesLot", resolved["lot_entity_id"])
            edge(consumption, "consumedForJob", resolved["job_entity_id"])
            edge(document_node, "productionAttributedToRound", resolved["round_entity_id"])
            edge(document_node, "reportForJob", resolved["job_entity_id"])
            for serial in resolved["board_serials"]:
                board = node(["board", serial], "BoardUnit", {"serial": serial, "status": "PRODUCED", "round_id": resolved["round_id"],
                             "lot_code": resolved["lot_code"]}, serial)
                install = node(["install", serial], "InstallationEvent", {"serial": serial, "item_id": resolved["item_id"],
                               "quantity": resolved["quantity_per_board"], "lot_code": resolved["lot_code"]}, "INSTALL " + serial)
                edge(board, "boardForRound", resolved["round_entity_id"])
                edge(board, "manufacturingConfiguration", resolved["configuration_entity_id"])
                edge(document_node, "reportedBoard", board)
                edge(install, "installedOn", board)
                edge(install, "installationForLine", resolved["bom_line_entity_id"])
                edge(install, "usesLot", resolved["lot_entity_id"])
                edge(install, "supportedByConsumption", consumption)
        elif kind == "FG_RECEIPT":
            allocation = node("allocation", "Allocation", {"quantity": len(resolved["board_serials"]), "round_id": resolved["round_id"],
                              "board_serials": list(resolved["board_serials"])}, "ALLOC " + identifier[:8])
            edge(allocation, "allocationFromReceipt", document_node)
            edge(allocation, "allocatedToRound", resolved["round_entity_id"])
            edge(document_node, "fgReceiptFromReport", node_id(parent["id"]))
        elif kind == "SHIPMENT":
            edge(document_node, "shipmentForRound", resolved["round_entity_id"])
            for serial in resolved["board_serials"]:
                edge(document_node, "shipsBoard", board_id(serial))
        else:
            edge(document_node, "arrivalOfShipment", node_id(parent["id"]))
            if resolved.get("customer_entity_id"):
                edge(document_node, "receivedByCustomer", resolved["customer_entity_id"])
            for serial in resolved["board_serials"]:
                edge(document_node, "arrivedBoard", board_id(serial))
        status = {"PRODUCTION": "PRODUCED", "FG_RECEIPT": "RECEIVED", "SHIPMENT": "SHIPPED", "ARRIVAL": "ARRIVED"}[kind]
        for serial in resolved["board_serials"]:
            entity_ = next(x for x in snapshot["entities"] if x["id"] == board_id(serial))
            entity_["attributes"]["status"] = status
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
