"""Allowlisted opening-balance conversion, separate from offline scenarios.

The only INITIAL input is the pinned baseline. A scenario bundle's entities,
relationships, expected answers and future documents are never an initializer.
This module performs no database write, approval, file parsing, or event replay.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5


CONVERTER_VERSION = "opening-balance/1"
BASELINE_PATH = "cq-v1.1/data/baseline.json"
SOURCE_NAMESPACE = "cq-v1.1"
MAX_SOURCE_BYTES = 4_000_000
FUTURE_COLLECTIONS = (
    "purchase_requests", "purchase_orders", "new_receipts", "board_instances",
    "installation_events", "coverage_assertions",
)
ROUND_FIELDS = ("round_id", "contract_line_id", "job_id", "promised_qty", "arrival_due_date",
                "configuration_id", "good_qty", "fg_received_qty", "shipped_qty", "arrived_qty", "unit",
                "remaining_promise_qty", "remaining_good_target_qty", "good_not_received_qty", "warehouse_qty", "in_transit_qty")
BALANCE_FIELDS = ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty", "available_qty", "unreserved_qty")
ENTITY_FIELDS = {
    "ProductConfiguration": {"registration_status", "technical_review_status", "source_document_ref", "bom_snapshot_id"},
    "ContractLine": {"customer_id", "promised_qty", "unit"},
    "ProductionJob": {"configuration_id"}, "DeliveryRound": set(ROUND_FIELDS), "Customer": set(),
    "InternalItem": {"display_name", "technical_mapping_status", "population_status", "quantity_unit_review_status", "unit_assumption",
                     *BALANCE_FIELDS, "candidate_on_hand_qty", "substitute_status", "counted_qty_for_selected_demand"},
    "PartLot": {"item_id", "unit", "location", *BALANCE_FIELDS},
    "SupplierOffer": {"quote_id", "supplier_id", "item_id", "unit_price", "moq", "order_multiple", "freight", "lead_business_days",
                      "assumed_arrival_date", "currency", "unit", "supplier_confirmation", "valid_until", "source_kind", "order_multiple_semantics", "status", "known_total"},
    "Supplier": set(),
}
INITIAL_PREDICATES = {
    "roundOfContractLine": ("DeliveryRound", "ContractLine"),
    "jobForRound": ("ProductionJob", "DeliveryRound"),
    "jobConfiguration": ("ProductionJob", "ProductConfiguration"),
    "deliverToCustomer": ("DeliveryRound", "Customer"),
    "lotOf": ("PartLot", "InternalItem"),
    "offersItem": ("SupplierOffer", "InternalItem"),
    "offerFromSupplier": ("SupplierOffer", "Supplier"),
}


class InitialInputError(ValueError):
    """A fixed safe code; private source contents never enter exception text."""


def _fail(code: str = "INITIAL_SCHEMA_INVALID") -> None:
    raise InitialInputError(code)


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        _fail()
    return value


def _qty(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail("INITIAL_QUANTITY_INVALID")
    return value


def _time(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if result.utcoffset() is None:
            _fail("INITIAL_TIMEZONE_REQUIRED")
        return result
    except (ValueError, TypeError):
        _fail("INITIAL_TIME_INVALID")


def _pick(source: dict, fields: tuple[str, ...]) -> dict:
    if not isinstance(source, dict):
        _fail()
    return {field: deepcopy(source[field]) for field in fields if field in source}


def _list(source: dict, key: str) -> list:
    value = source.get(key)
    if not isinstance(value, list) or len(value) > 10000:
        _fail()
    return value


def canonical_id(company_id: str, profile_id: str, run_id: str, kind: str, display_id: str,
                 source_namespace: str = SOURCE_NAMESPACE) -> str:
    """No display ID, source namespace, company or run can silently merge."""
    parts = [company_id, source_namespace, profile_id, run_id, kind, display_id]
    for part in parts:
        _text(part)
    return "urn:ontologics:" + str(uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)))


def _read_source(root: Path, relative: str) -> bytes:
    try:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            _fail("INITIAL_SOURCE_LOCATION_REJECTED")
        with path.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            _fail("INITIAL_SOURCE_SIZE_LIMIT")
        return data
    except InitialInputError:
        raise
    except (OSError, RuntimeError):
        _fail("INITIAL_SOURCE_UNAVAILABLE")


def load_initial(private_root: Path | str, *, company_id: str, run_id: str,
                 profile_id: str, imported_at: str) -> dict:
    """Read only a manifest-pinned baseline; never read bundle.json or A-Box."""
    try:
        root = Path(private_root).resolve(strict=True)
        repo = Path(__file__).resolve().parents[1]
        if not root.is_dir() or root.is_relative_to(repo):
            _fail("INITIAL_SOURCE_LOCATION_REJECTED")
        manifest_bytes = _read_source(root, "SOURCE_MANIFEST.json")
        manifest = json.loads(manifest_bytes)
        entries = manifest["files"]
        matches = [entry for entry in entries if entry.get("path") == BASELINE_PATH]
        if len(matches) != 1:
            _fail("INITIAL_SOURCE_UNTRACKED")
        raw = _read_source(root, BASELINE_PATH)
        digest = sha256(raw).hexdigest()
        if matches[0].get("sha256") != digest or matches[0].get("bytes") != len(raw):
            _fail("INITIAL_SOURCE_INTEGRITY_MISMATCH")
        result = convert_baseline(json.loads(raw), company_id=company_id, run_id=run_id,
                                  profile_id=profile_id, imported_at=imported_at,
                                  source_sha256=digest)
        result["metadata"]["source_manifest_sha256"] = sha256(manifest_bytes).hexdigest()
        validate_initial(result)
        return result
    except InitialInputError:
        raise
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        _fail("INITIAL_SOURCE_INVALID")


def convert_baseline(baseline: dict, *, company_id: str, run_id: str, profile_id: str,
                     imported_at: str, source_sha256: str) -> dict:
    """Convert only supported fields, failing closed on future-state inputs.

    The source ledger version is evidence metadata. Server versions are assigned
    later by a Domain transaction; the source's READY projection is not reused.
    """
    try:
        return _convert_baseline(baseline, company_id, run_id, profile_id, imported_at, source_sha256)
    except InitialInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        _fail()


def _convert_baseline(source: dict, company_id: str, run_id: str, profile_id: str,
                      imported_at: str, source_sha256: str) -> dict:
    for value in (company_id, run_id, profile_id):
        _text(value)
    if not isinstance(source_sha256, str) or len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
        _fail("INITIAL_DIGEST_INVALID")
    if source.get("schema_version") != "cq123-design-data/1.1":
        _fail("INITIAL_SCHEMA_UNSUPPORTED")
    if source.get("service_implementation_claim") is not False or source.get("immutable") is not True:
        _fail("INITIAL_BASELINE_NOT_IMMUTABLE")
    if source.get("origin") != "synthetic_operation":
        _fail("INITIAL_SOURCE_MODE_UNSUPPORTED")
    for field in FUTURE_COLLECTIONS:
        if _list(source, field):
            _fail("INITIAL_FUTURE_STATE_REJECTED")
    if _time(source["as_of"]) > _time(source["known_as_of"]) or _time(source["known_as_of"]) > _time(imported_at):
        _fail("INITIAL_TIME_ORDER_INVALID")
    scope = source["scope"]
    configuration = source["configuration"]
    if configuration.get("registration_status") != "NOT_REGISTERED" or configuration.get("technical_review_status") != "UNREVIEWED":
        _fail("INITIAL_BOM_STATE_REJECTED")
    round_source = source["delivery_round"]
    contract = source["contract_line"]
    if (round_source["contract_line_id"] != contract["contract_line_id"]
            or round_source["configuration_id"] != configuration["configuration_id"]
            or any(scope[k] != round_source[k] for k in ("round_id", "job_id", "configuration_id", "contract_line_id"))):
        _fail("INITIAL_SCOPE_MISMATCH")
    round_balance = _pick(round_source, ("round_id", "contract_line_id", "job_id", "promised_qty", "arrival_due_date",
                                          "configuration_id", "good_qty", "fg_received_qty", "shipped_qty", "arrived_qty", "unit"))
    quantities = [_qty(round_balance[key]) for key in ("promised_qty", "good_qty", "fg_received_qty", "shipped_qty", "arrived_qty")]
    if quantities != sorted(quantities, reverse=True) or _qty(contract["promised_qty"]) < quantities[0]:
        _fail("INITIAL_BALANCE_INCONSISTENT")
    promise, good, received, shipped, arrived = quantities
    round_balance.update(remaining_promise_qty=promise-arrived, remaining_good_target_qty=promise-good,
                         good_not_received_qty=good-received, warehouse_qty=received-shipped, in_transit_qty=shipped-arrived)
    metadata = {
        "mode": "INITIAL", "converter_version": CONVERTER_VERSION,
        "company_id": company_id, "profile_id": profile_id, "run_id": run_id,
        "source_namespace": SOURCE_NAMESPACE, "source_profile_id": _text(source["profile_id"]),
        "source_run_id": _text(source["reference_run_id"]), "source_snapshot_id": _text(source["snapshot_id"]),
        "source_ledger_version": _qty(source["ledger_version"]),
        "source_projection_version": _qty(source["projection_version"]),
        "source_sha256": source_sha256, "source_schema_version": source["schema_version"],
        "effective_at": source["as_of"], "known_as_of": source["known_as_of"], "recorded_at": imported_at,
        "server_ledger_version": None, "projection_version": None, "projection_state": "NOT_PROJECTED",
        "ontology_version": None, "rule_version": CONVERTER_VERSION,
        "synthetic": True, "technical_review": "UNREVIEWED", "serial_coverage": "UNKNOWN",
        "initial_scope": "BASELINE_ONLY", "full_bom_inventory_coverage": "NOT_IMPORTED",
    }
    entities: dict[tuple[str, str], dict] = {}
    relationships: list[dict] = []

    def evidence(locator: str, refs: list | None = None) -> dict:
        return {"source_sha256": source_sha256, "source_locator": locator,
                "source_refs": deepcopy(refs or []), "effective_at": source["as_of"],
                "known_as_of": source["known_as_of"], "recorded_at": imported_at,
                "record_type": "opening_balance", "synthetic": True}

    def entity(kind: str, display_id: str, attrs: dict, locator: str, refs: list | None = None) -> str:
        key = (kind, _text(display_id))
        identity = canonical_id(company_id, profile_id, run_id, kind, display_id)
        if key in entities:
            # A reference may add no fields, but cannot overwrite existing facts.
            old = entities[key]["attributes"]
            if any(k in old and old[k] != value for k, value in attrs.items()):
                _fail("INITIAL_CONFLICTING_ENTITY")
            old.update(deepcopy(attrs))
            return identity
        entities[key] = {"id": identity, "type": kind, "source_type": kind, "display_id": display_id,
                         "logicalId": display_id, "label": attrs.get("display_name", display_id),
                         "origin": source["origin"], "run_id": run_id,
                         "attributes": deepcopy(attrs), "evidence": evidence(locator, refs)}
        return identity

    def edge(start: str, predicate: str, target: str, locator: str) -> None:
        identity = "urn:ontologics:" + str(uuid5(NAMESPACE_URL, json.dumps([start, predicate, target])))
        if not any(row["id"] == identity for row in relationships):
            relationships.append({"id": identity, "source": start, "target": target,
                                  "predicate": predicate, "evidence": evidence(locator)})

    # These are known master identities; no hypothetical board or event is made.
    config_id = entity("ProductConfiguration", configuration["configuration_id"],
                       _pick(configuration, ("registration_status", "technical_review_status", "source_document_ref", "bom_snapshot_id")), "/configuration")
    contract_id = entity("ContractLine", contract["contract_line_id"],
                         _pick(contract, ("customer_id", "promised_qty", "unit")), "/contract_line")
    job_id = entity("ProductionJob", round_source["job_id"], {"configuration_id": configuration["configuration_id"]}, "/delivery_round/job_id")
    round_id = entity("DeliveryRound", round_source["round_id"], round_balance, "/delivery_round")
    edge(round_id, "roundOfContractLine", contract_id, "/delivery_round/contract_line_id")
    edge(job_id, "jobForRound", round_id, "/delivery_round/job_id")
    edge(job_id, "jobConfiguration", config_id, "/delivery_round/configuration_id")
    customer_id = entity("Customer", contract["customer_id"], {}, "/contract_line/customer_id")
    edge(round_id, "deliverToCustomer", customer_id, "/contract_line/customer_id")
    # Remaining relationships are added only when a declared canonical predicate
    # expresses the baseline fact; raw source IDs remain attributes otherwise.

    inventory = source["inventory"]
    inventory_rows = []
    for index, lot in enumerate(_list(inventory, "lots")):
        locator = f"/inventory/lots/{index}"
        attrs = _pick(lot, ("item_id", "on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty", "unit", "location"))
        held, other, own, on_hand = (_qty(attrs[key]) for key in ("held_qty", "reserved_other_qty", "reserved_self_qty", "on_hand_qty"))
        if held + other + own > on_hand:
            _fail("INITIAL_INVENTORY_INCONSISTENT")
        attrs["available_qty"] = on_hand-other-held
        attrs["unreserved_qty"] = on_hand-other-own-held
        item_id = entity("InternalItem", lot["item_id"], {}, locator+"/item_id")
        lot_id = entity("PartLot", lot["lot_id"], attrs, locator, lot.get("evidence_refs"))
        edge(lot_id, "lotOf", item_id, locator)
        inventory_rows.append({"scope": "LOT", "lot_id": lot["lot_id"], **attrs})
    for index, item in enumerate(_list(inventory, "other_item_snapshots")):
        locator = f"/inventory/other_item_snapshots/{index}"
        attrs = _pick(item, ("display_name", "technical_mapping_status", "population_status", "quantity_unit_review_status", "unit_assumption"))
        balances = {field: _qty(item["baseline_"+field]) for field in ("on_hand_qty", "reserved_other_qty", "reserved_self_qty", "held_qty")}
        if balances["reserved_other_qty"]+balances["reserved_self_qty"]+balances["held_qty"] > balances["on_hand_qty"]:
            _fail("INITIAL_INVENTORY_INCONSISTENT")
        balances["available_qty"] = balances["on_hand_qty"]-balances["reserved_other_qty"]-balances["held_qty"]
        balances["unreserved_qty"] = balances["available_qty"]-balances["reserved_self_qty"]
        attrs.update(balances)
        entity("InternalItem", item["item_id"], attrs, locator, item.get("evidence_refs"))
        inventory_rows.append({"scope": "ITEM_AGGREGATE_LOTS_UNKNOWN", "item_id": item["item_id"], **balances,
                               "unit": item.get("unit_assumption"), "technical_mapping_status": item.get("technical_mapping_status")})

    reservations = []
    lot_by_id = {row["lot_id"]: row for row in inventory_rows if row["scope"] == "LOT"}
    for index, row in enumerate(_list(inventory, "reservations")):
        if row["lot_id"] not in lot_by_id or row["item_id"] != lot_by_id[row["lot_id"]]["item_id"]:
            _fail("INITIAL_RESERVATION_SCOPE_INVALID")
        _qty(row["qty"])
        if row.get("status") != "ACTIVE" or row.get("reservation_relation_to_selected_demand") not in {"OTHER", "SELF"}:
            _fail("INITIAL_RESERVATION_STATE_UNSUPPORTED")
        reservations.append(_pick(row, ("reservation_id", "lot_id", "item_id", "qty", "unit", "demand_id", "status", "reservation_relation_to_selected_demand")))
    for lot in lot_by_id.values():
        rows = [row for row in reservations if row["lot_id"] == lot["lot_id"]]
        if sum(row["qty"] for row in rows) != lot["reserved_other_qty"]+lot["reserved_self_qty"]:
            _fail("INITIAL_RESERVATION_BALANCE_MISMATCH")
        if sum(row["qty"] for row in rows if row["reservation_relation_to_selected_demand"] == "OTHER") != lot["reserved_other_qty"]:
            _fail("INITIAL_RESERVATION_BALANCE_MISMATCH")

    mapping_candidates = []
    for index, row in enumerate(_list(source, "material_mapping_assumptions")):
        if any(row.get(key) != "UNREVIEWED" for key in ("technical_mapping_status", "population_status", "quantity_unit_review_status")):
            _fail("INITIAL_MAPPING_APPROVAL_REJECTED")
        candidate = _pick(row, ("item_id", "display_name", "source_row_id", "source_row_number", "source_ordinal", "refdes",
                               "qty_per_board_assumption", "unit_assumption", "technical_mapping_status", "population_status",
                               "quantity_unit_review_status", "assumption_refs"))
        _qty(candidate["qty_per_board_assumption"])
        mapping_candidates.append({**candidate, "applied": False, "evidence": evidence(f"/material_mapping_assumptions/{index}", row.get("evidence_refs"))})
        entity("InternalItem", row["item_id"], _pick(row, ("display_name", "technical_mapping_status", "population_status", "quantity_unit_review_status")),
               f"/material_mapping_assumptions/{index}", row.get("evidence_refs"))

    quotations = []
    for index, quote in enumerate(_list(source, "quotations")):
        if quote.get("supplier_confirmation") is not False:
            _fail("INITIAL_QUOTE_CONFIRMATION_REJECTED")
        attrs = _pick(quote, ("quote_id", "supplier_id", "item_id", "unit_price", "moq", "order_multiple", "freight",
                              "lead_business_days", "assumed_arrival_date", "currency", "unit", "supplier_confirmation",
                              "valid_until", "source_kind", "order_multiple_semantics"))
        for field in ("unit_price", "moq", "order_multiple", "lead_business_days"):
            _qty(attrs[field])
        attrs.update(status="CANDIDATE", known_total=None)
        quotations.append(attrs)
        quote_id = entity("SupplierOffer", quote["quote_id"], attrs, f"/quotations/{index}", quote.get("evidence_refs"))
        supplier_id = entity("Supplier", quote["supplier_id"], {}, f"/quotations/{index}/supplier_id")
        item_id = entity("InternalItem", quote["item_id"], {}, f"/quotations/{index}/item_id")
        edge(quote_id, "offersItem", item_id, f"/quotations/{index}/item_id")
        edge(quote_id, "offerFromSupplier", supplier_id, f"/quotations/{index}/supplier_id")

    substitutes = []
    for index, row in enumerate(_list(source, "substitutes")):
        if row.get("status") != "NOT_REVIEWED" or row.get("approval_ref") is not None or row.get("counted_qty_for_selected_demand") != 0:
            _fail("INITIAL_SUBSTITUTE_APPROVAL_REJECTED")
        _qty(row["candidate_on_hand_qty"])
        attrs = _pick(row, ("substitution_id", "original_item_id", "candidate_item_id", "candidate_on_hand_qty", "unit", "status"))
        attrs.update(approval_ref=None, counted_qty_for_selected_demand=0)
        substitutes.append(attrs)
        entity("InternalItem", row["candidate_item_id"], {"candidate_on_hand_qty": row["candidate_on_hand_qty"],
               "substitute_status": "NOT_REVIEWED", "counted_qty_for_selected_demand": 0}, f"/substitutes/{index}")

    # Historical aggregate rows are evidence, not newly invented timestamped
    # production/receipt/shipment/arrival events or board membership.
    reports = []
    for row in _list(source, "production_reports"):
        if row.get("record_type") != "immutable_baseline_aggregate" or row.get("board_ids") is not None or row.get("effective_at") is not None or row.get("recorded_at") is not None:
            _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
        report = _pick(row, ("report_id", "job_id", "good_qty", "defect_qty", "pending_judgement_qty", "total_reported_qty",
                             "record_type", "effective_at", "recorded_at", "event_time_coverage", "board_ids", "board_membership_coverage"))
        if report["job_id"] != round_source["job_id"] or sum(_qty(report[k]) for k in ("good_qty", "defect_qty", "pending_judgement_qty")) != _qty(report["total_reported_qty"]):
            _fail("INITIAL_PRODUCTION_BALANCE_MISMATCH")
        reports.append(report)
    receipts = []
    for row in _list(source, "fg_receipts"):
        if row.get("board_ids") is not None or row["job_id"] != round_source["job_id"]:
            _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
        _qty(row["qty"])
        receipts.append(_pick(row, ("fg_receipt_id", "source_report_id", "job_id", "qty", "unit", "board_ids", "membership_coverage")))
    shipments = []
    for row in _list(source, "shipments"):
        if row.get("board_ids") is not None or row["round_id"] != round_source["round_id"]:
            _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
        qty = _qty(row["qty"])
        shipment = _pick(row, ("shipment_id", "round_id", "qty", "unit", "board_ids", "membership_coverage"))
        shipment["arrivals"] = []
        for arrival in _list(row, "arrivals"):
            if arrival.get("board_ids") is not None:
                _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
            _qty(arrival["qty"])
            shipment["arrivals"].append(_pick(arrival, ("arrival_id", "qty", "board_ids")))
        if sum(a["qty"] for a in shipment["arrivals"]) > qty:
            _fail("INITIAL_ARRIVAL_BALANCE_MISMATCH")
        shipments.append(shipment)
    if (sum(r["good_qty"] for r in reports) != good or sum(r["qty"] for r in receipts) != received
            or sum(r["qty"] for r in shipments) != shipped
            or sum(a["qty"] for row in shipments for a in row["arrivals"]) != arrived):
        _fail("INITIAL_AGGREGATE_RECONCILIATION_FAILED")
    return {"metadata": metadata, "entities": list(entities.values()), "relationships": relationships,
            "opening_balance": {"delivery_round": round_balance, "inventory": inventory_rows,
                "reservations": reservations, "production_reports": reports, "fg_receipts": receipts,
                "shipments": shipments, "quotations": quotations, "substitutes": substitutes,
                "mapping_candidates": mapping_candidates,
                "configuration": _pick(configuration, ("configuration_id", "registration_status", "technical_review_status", "bom_snapshot_id")),
                "known_installation_count": 0, "actual_exposure_count": None, "serial_coverage": "UNKNOWN"}}


def validate_initial(payload: dict) -> None:
    """Validate a trusted local converter output before its first transaction.

    Hash strings alone are not signatures. This is an invariant guard for the
    manifest-verifying local loader, not authorization for an HTTP import API.
    Server ledger/projection metadata must be assigned only after this check.
    """
    try:
        _validate_initial(payload)
    except InitialInputError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        _fail("INITIAL_OUTPUT_INVALID")


def _validate_initial(payload: dict) -> None:
    if not isinstance(payload, dict) or set(payload) != {"metadata", "entities", "relationships", "opening_balance"}:
        _fail("INITIAL_OUTPUT_INVALID")
    meta = payload["metadata"]
    if set(meta) != {"mode", "converter_version", "company_id", "profile_id", "run_id", "source_namespace", "source_profile_id", "source_run_id",
                     "source_snapshot_id", "source_ledger_version", "source_projection_version", "source_sha256", "source_schema_version", "effective_at",
                     "known_as_of", "recorded_at", "server_ledger_version", "projection_version", "projection_state", "ontology_version", "rule_version",
                     "synthetic", "technical_review", "serial_coverage", "initial_scope", "full_bom_inventory_coverage", "source_manifest_sha256"}:
        _fail("INITIAL_METADATA_INVALID")
    expected = {"mode": "INITIAL", "converter_version": CONVERTER_VERSION, "source_namespace": SOURCE_NAMESPACE,
                "source_schema_version": "cq123-design-data/1.1", "server_ledger_version": None, "projection_version": None,
                "projection_state": "NOT_PROJECTED", "ontology_version": None, "rule_version": CONVERTER_VERSION,
                "technical_review": "UNREVIEWED", "serial_coverage": "UNKNOWN", "initial_scope": "BASELINE_ONLY",
                "full_bom_inventory_coverage": "NOT_IMPORTED"}
    if any(meta.get(key) != value for key, value in expected.items()) or meta.get("synthetic") is not True:
        _fail("INITIAL_METADATA_INVALID")
    for key in ("company_id", "profile_id", "run_id", "source_profile_id", "source_run_id", "source_snapshot_id"):
        _text(meta[key])
    for key in ("source_sha256", "source_manifest_sha256"):
        if not isinstance(meta[key], str) or len(meta[key]) != 64 or any(c not in "0123456789abcdef" for c in meta[key]):
            _fail("INITIAL_DIGEST_INVALID")
    _qty(meta["source_ledger_version"])
    _qty(meta["source_projection_version"])
    if not (_time(meta["effective_at"]) <= _time(meta["known_as_of"]) <= _time(meta["recorded_at"])):
        _fail("INITIAL_TIME_ORDER_INVALID")

    def check_evidence(value):
        if set(value) != {"source_sha256", "source_locator", "source_refs", "effective_at", "known_as_of", "recorded_at", "record_type", "synthetic"}:
            _fail("INITIAL_EVIDENCE_INVALID")
        if any(value[key] != meta[key] for key in ("source_sha256", "effective_at", "known_as_of", "recorded_at")):
            _fail("INITIAL_EVIDENCE_INVALID")
        if value["record_type"] != "opening_balance" or value["synthetic"] is not True or not _text(value["source_locator"]).startswith("/"):
            _fail("INITIAL_EVIDENCE_INVALID")
        if not isinstance(value["source_refs"], list) or any(not isinstance(ref, str) for ref in value["source_refs"]):
            _fail("INITIAL_EVIDENCE_INVALID")

    entities = _list(payload, "entities")
    indexed = {}
    for row in entities:
        if set(row) != {"id", "type", "source_type", "display_id", "logicalId", "label", "origin", "run_id", "attributes", "evidence"}:
            _fail("INITIAL_ENTITY_INVALID")
        kind = row["type"]
        if kind not in ENTITY_FIELDS or row["source_type"] != kind or row["logicalId"] != row["display_id"] or row["run_id"] != meta["run_id"] or row["origin"] != "synthetic_operation":
            _fail("INITIAL_ENTITY_INVALID")
        _text(row["label"])
        if row["id"] != canonical_id(meta["company_id"], meta["profile_id"], meta["run_id"], kind, row["display_id"]):
            _fail("INITIAL_ENTITY_ID_INVALID")
        if row["id"] in indexed or not isinstance(row["attributes"], dict) or not set(row["attributes"]).issubset(ENTITY_FIELDS[kind]):
            _fail("INITIAL_ENTITY_INVALID")
        check_evidence(row["evidence"])
        indexed[row["id"]] = row
    edge_ids = set()
    for row in _list(payload, "relationships"):
        if set(row) != {"id", "source", "target", "predicate", "evidence"} or row["predicate"] not in INITIAL_PREDICATES:
            _fail("INITIAL_RELATIONSHIP_INVALID")
        if row["source"] not in indexed or row["target"] not in indexed:
            _fail("INITIAL_RELATIONSHIP_ENDPOINT_INVALID")
        if (indexed[row["source"]]["type"], indexed[row["target"]]["type"]) != INITIAL_PREDICATES[row["predicate"]]:
            _fail("INITIAL_RELATIONSHIP_TYPE_INVALID")
        expected_id = "urn:ontologics:" + str(uuid5(NAMESPACE_URL, json.dumps([row["source"], row["predicate"], row["target"]])))
        if row["id"] != expected_id or row["id"] in edge_ids:
            _fail("INITIAL_RELATIONSHIP_ID_INVALID")
        edge_ids.add(row["id"])
        check_evidence(row["evidence"])
    opening = payload["opening_balance"]
    if set(opening) != {"delivery_round", "inventory", "reservations", "production_reports", "fg_receipts", "shipments", "quotations",
                        "substitutes", "mapping_candidates", "configuration", "known_installation_count", "actual_exposure_count", "serial_coverage"}:
        _fail("INITIAL_OPENING_BALANCE_INVALID")
    if opening["known_installation_count"] != 0 or opening["actual_exposure_count"] is not None or opening["serial_coverage"] != "UNKNOWN":
        _fail("INITIAL_OPENING_BALANCE_INVALID")
    balance = opening["delivery_round"]
    if not set(balance).issubset(set(ROUND_FIELDS)):
        _fail("INITIAL_BALANCE_INCONSISTENT")
    promise, good, received, shipped, arrived = values = [_qty(balance[k]) for k in ("promised_qty", "good_qty", "fg_received_qty", "shipped_qty", "arrived_qty")]
    if values != sorted(values, reverse=True):
        _fail("INITIAL_BALANCE_INCONSISTENT")
    calculations = {"remaining_promise_qty": promise-arrived, "remaining_good_target_qty": promise-good, "good_not_received_qty": good-received,
                    "warehouse_qty": received-shipped, "in_transit_qty": shipped-arrived}
    if any(balance[key] != value for key, value in calculations.items()):
        _fail("INITIAL_BALANCE_INCONSISTENT")
    rounds = [row for row in entities if row["type"] == "DeliveryRound"]
    if len(rounds) != 1 or rounds[0]["attributes"] != balance:
        _fail("INITIAL_GRAPH_BALANCE_MISMATCH")
    configuration = opening["configuration"]
    if not set(configuration).issubset({"configuration_id", "registration_status", "technical_review_status", "bom_snapshot_id"}):
        _fail("INITIAL_OPENING_BALANCE_INVALID")
    if configuration.get("registration_status") != "NOT_REGISTERED" or configuration.get("technical_review_status") != "UNREVIEWED":
        _fail("INITIAL_BOM_STATE_REJECTED")
    inventory_keys = set()
    allowed_records = {
        "inventory": {"scope", "lot_id", "item_id", "unit", "location", "technical_mapping_status", *BALANCE_FIELDS},
        "reservations": {"reservation_id", "lot_id", "item_id", "qty", "unit", "demand_id", "status", "reservation_relation_to_selected_demand"},
        "production_reports": {"report_id", "job_id", "good_qty", "defect_qty", "pending_judgement_qty", "total_reported_qty", "record_type", "effective_at", "recorded_at", "event_time_coverage", "board_ids", "board_membership_coverage"},
        "fg_receipts": {"fg_receipt_id", "source_report_id", "job_id", "qty", "unit", "board_ids", "membership_coverage"},
        "shipments": {"shipment_id", "round_id", "qty", "unit", "board_ids", "membership_coverage", "arrivals"},
        "quotations": ENTITY_FIELDS["SupplierOffer"],
        "substitutes": {"substitution_id", "original_item_id", "candidate_item_id", "candidate_on_hand_qty", "unit", "status", "approval_ref", "counted_qty_for_selected_demand"},
        "mapping_candidates": {"item_id", "display_name", "source_row_id", "source_row_number", "source_ordinal", "refdes", "qty_per_board_assumption", "unit_assumption", "technical_mapping_status", "population_status", "quantity_unit_review_status", "assumption_refs", "applied", "evidence"},
    }
    record_id_fields = {"reservations": "reservation_id", "production_reports": "report_id", "fg_receipts": "fg_receipt_id",
                        "shipments": "shipment_id", "quotations": "quote_id", "substitutes": "substitution_id", "mapping_candidates": "source_row_id"}
    for collection, fields in allowed_records.items():
        record_ids = set()
        for row in _list(opening, collection):
            if not isinstance(row, dict) or not set(row).issubset(fields):
                _fail("INITIAL_OPENING_BALANCE_INVALID")
            if collection in record_id_fields:
                identity = _text(row[record_id_fields[collection]])
                if identity in record_ids:
                    _fail("INITIAL_RECORD_DUPLICATE")
                record_ids.add(identity)
    for row in _list(opening, "inventory"):
        scope = row["scope"]
        if scope not in {"LOT", "ITEM_AGGREGATE_LOTS_UNKNOWN"}:
            _fail("INITIAL_INVENTORY_SCOPE_INVALID")
        key = (scope, row["lot_id"] if scope == "LOT" else row["item_id"])
        if key in inventory_keys:
            _fail("INITIAL_INVENTORY_DUPLICATE")
        inventory_keys.add(key)
        on_hand, other, own, held, available, unreserved = [_qty(row[k]) for k in BALANCE_FIELDS]
        if other+own+held > on_hand or available != on_hand-other-held or unreserved != available-own:
            _fail("INITIAL_INVENTORY_INCONSISTENT")
        kind = "PartLot" if scope == "LOT" else "InternalItem"
        identity = canonical_id(meta["company_id"], meta["profile_id"], meta["run_id"], kind, key[1])
        if identity not in indexed or any(indexed[identity]["attributes"].get(k) != row[k] for k in BALANCE_FIELDS):
            _fail("INITIAL_GRAPH_BALANCE_MISMATCH")
    reservations = _list(opening, "reservations")
    lots = {row["lot_id"]: row for row in opening["inventory"] if row["scope"] == "LOT"}
    for row in reservations:
        if row["lot_id"] not in lots or row["item_id"] != lots[row["lot_id"]]["item_id"] or row["status"] != "ACTIVE" or row["reservation_relation_to_selected_demand"] not in {"OTHER", "SELF"}:
            _fail("INITIAL_RESERVATION_SCOPE_INVALID")
        _qty(row["qty"])
    for lot in lots.values():
        rows = [row for row in reservations if row["lot_id"] == lot["lot_id"]]
        if sum(row["qty"] for row in rows) != lot["reserved_other_qty"]+lot["reserved_self_qty"] or sum(row["qty"] for row in rows if row["reservation_relation_to_selected_demand"] == "OTHER") != lot["reserved_other_qty"]:
            _fail("INITIAL_RESERVATION_BALANCE_MISMATCH")
    reports, receipts, shipments = (_list(opening, key) for key in ("production_reports", "fg_receipts", "shipments"))
    for row in reports:
        if row.get("record_type") != "immutable_baseline_aggregate" or row.get("board_ids") is not None or row.get("effective_at") is not None or row.get("recorded_at") is not None:
            _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
        if sum(_qty(row[k]) for k in ("good_qty", "defect_qty", "pending_judgement_qty")) != _qty(row["total_reported_qty"]):
            _fail("INITIAL_PRODUCTION_BALANCE_MISMATCH")
    for row in receipts+shipments:
        if row.get("board_ids") is not None:
            _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
        _qty(row["qty"])
    for row in shipments:
        for arrival in _list(row, "arrivals"):
            if not set(arrival).issubset({"arrival_id", "qty", "board_ids"}) or arrival.get("board_ids") is not None:
                _fail("INITIAL_EVENT_DETAIL_UNSUPPORTED")
            _qty(arrival["qty"])
        if sum(a["qty"] for a in row["arrivals"]) > row["qty"]:
            _fail("INITIAL_ARRIVAL_BALANCE_MISMATCH")
    if sum(row["good_qty"] for row in reports) != good or sum(row["qty"] for row in receipts) != received or sum(row["qty"] for row in shipments) != shipped or sum(a["qty"] for row in shipments for a in row["arrivals"]) != arrived:
        _fail("INITIAL_AGGREGATE_RECONCILIATION_FAILED")
    for row in _list(opening, "quotations"):
        if row.get("supplier_confirmation") is not False or row.get("status") != "CANDIDATE" or row.get("known_total") is not None:
            _fail("INITIAL_QUOTE_CONFIRMATION_REJECTED")
    for row in _list(opening, "substitutes"):
        if row.get("status") != "NOT_REVIEWED" or row.get("approval_ref") is not None or row.get("counted_qty_for_selected_demand") != 0:
            _fail("INITIAL_SUBSTITUTE_APPROVAL_REJECTED")
    for row in _list(opening, "mapping_candidates"):
        if row.get("applied") is not False or any(row.get(key) != "UNREVIEWED" for key in ("technical_mapping_status", "population_status", "quantity_unit_review_status")):
            _fail("INITIAL_MAPPING_APPROVAL_REJECTED")
        check_evidence(row["evidence"])

    # A caller cannot silently remove known masters/edges or add unrelated ones.
    def identity(kind, display):
        return canonical_id(meta["company_id"], meta["profile_id"], meta["run_id"], kind, display)
    config_id = identity("ProductConfiguration", configuration["configuration_id"])
    contract_id = identity("ContractLine", balance["contract_line_id"])
    job_id = identity("ProductionJob", balance["job_id"])
    round_id = identity("DeliveryRound", balance["round_id"])
    if any(key not in indexed for key in (config_id, contract_id, job_id, round_id)):
        _fail("INITIAL_GRAPH_INCOMPLETE")
    contract_attrs = indexed[contract_id]["attributes"]
    customer_id = identity("Customer", contract_attrs["customer_id"])
    if _qty(contract_attrs["promised_qty"]) < promise or indexed[job_id]["attributes"].get("configuration_id") != configuration["configuration_id"]:
        _fail("INITIAL_GRAPH_BALANCE_MISMATCH")
    if any(indexed[config_id]["attributes"].get(key) != configuration[key] for key in ("registration_status", "technical_review_status")):
        _fail("INITIAL_BOM_STATE_REJECTED")
    expected_ids = {config_id, contract_id, job_id, round_id, customer_id}
    expected_edges = {(round_id, "roundOfContractLine", contract_id), (job_id, "jobForRound", round_id),
                      (job_id, "jobConfiguration", config_id), (round_id, "deliverToCustomer", customer_id)}
    for row in opening["inventory"]:
        item_id = identity("InternalItem", row["item_id"])
        expected_ids.add(item_id)
        if row["scope"] == "LOT":
            lot_id = identity("PartLot", row["lot_id"])
            expected_ids.add(lot_id)
            expected_edges.add((lot_id, "lotOf", item_id))
    for row in opening["quotations"]:
        quote_id, supplier_id, item_id = identity("SupplierOffer", row["quote_id"]), identity("Supplier", row["supplier_id"]), identity("InternalItem", row["item_id"])
        expected_ids.update((quote_id, supplier_id, item_id))
        expected_edges.update(((quote_id, "offersItem", item_id), (quote_id, "offerFromSupplier", supplier_id)))
        if quote_id not in indexed or indexed[quote_id]["attributes"] != row:
            _fail("INITIAL_GRAPH_BALANCE_MISMATCH")
    for row in opening["substitutes"]:
        expected_ids.add(identity("InternalItem", row["candidate_item_id"]))
    for row in opening["mapping_candidates"]:
        expected_ids.add(identity("InternalItem", row["item_id"]))
    if expected_ids != set(indexed) or expected_edges != {(r["source"], r["predicate"], r["target"]) for r in payload["relationships"]}:
        _fail("INITIAL_GRAPH_INCOMPLETE")


def partition_reference_bundle(bundle: dict) -> dict:
    """Offline-only scenario separation. Neither output can initialize a ledger.

    Scenario events retain source identity/sequence only as review candidates;
    source approval IDs, lifecycle status and document hashes are not authority.
    EXPECTED is a separate object and must stay outside runtime retrieval.
    """
    try:
        if bundle["meta"].get("real_service_execution") is not False:
            _fail("REFERENCE_EXECUTION_MODE_REJECTED")
        candidates = []
        for index, event in enumerate(_list(bundle, "events")):
            if event.get("real_service_execution") is not False:
                _fail("REFERENCE_EXECUTION_MODE_REJECTED")
            candidates.append({"source_event_id": _text(event["id"]), "source_sequence": index,
                               "source_run_id": _text(event["run_id"]), "candidate_type": _text(event["type"]),
                               "proposed_effective_at": event.get("effective_at"),
                               "proposed_quantity": event.get("qty"), "unit": event.get("unit"),
                               "review_input": deepcopy(event.get("payload", {})), "executable": False,
                               "requires_server_validation": True, "requires_new_server_approval": True})
        return {"commands": {"mode": "COMMANDS", "executable": False, "candidates": candidates},
                "expected": {"mode": "EXPECTED", "runtime_retrieval_allowed": False,
                             "snapshots": deepcopy(_list(bundle, "snapshots")),
                             "query_results": deepcopy(_list(bundle, "cq_results")),
                             "findings": deepcopy(_list(bundle, "findings"))}}
    except InitialInputError:
        raise
    except (KeyError, TypeError, ValueError):
        _fail("REFERENCE_SCHEMA_INVALID")
