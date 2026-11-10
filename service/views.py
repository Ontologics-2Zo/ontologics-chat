"""MK-S2 read models computed exclusively from a committed ledger snapshot."""

from copy import deepcopy

from service.materials import requirements_for_snapshot


def display_graph(graph):
    types = {"ProductConfiguration": ("configuration", "design"), "ContractLine": ("contract_line", "doc"),
             "DeliveryRound": ("delivery_round", "doc"), "ProductionJob": ("production_job", "doc"),
             "Customer": ("customer", "party"), "Supplier": ("supplier", "party"),
             "InternalItem": ("item", "design"), "PartLot": ("lot", "phys"), "SupplierOffer": ("quotation", "doc"),
             "SourceDocument": ("source_document", "doc"), "SourceSnapshot": ("source_snapshot", "doc"),
             "SourceRow": ("source_row", "doc"), "BOMSnapshot": ("bom", "doc"),
             "BOMLine": ("bom_line", "design"), "BOMApplication": ("bom_application", "design"),
             "ApprovalGrant": ("approval", "doc"), "DocumentVersion": ("document_version", "doc")}
    types.update({name: (name, "doc") for name in ("PurchaseRequest", "PurchaseOrder", "PurchaseOrderLine",
        "PartsReceiptLine", "InspectionResult", "SupplierConfirmation")})
    nodes=[]
    for node in graph.get("nodes", []):
        kind, shape = types.get(node["type"], (node["type"], "design"))
        properties=deepcopy(node["attributes"])
        nodes.append({**node,"node_id":node["id"],"label":node["display_id"],"node_type":shape,
                      "entity_ref":{"entity_type":kind,"entity_id":node["id"]},
                      "attributes":{**node["attributes"],"display":node.get("label",node["display_id"]),
                                    "kind_label":node["type"],"properties":properties}})
    edges=[{**edge,"edge_id":edge["id"],"source_node_id":edge["source"],"target_node_id":edge["target"],
            "relation_type":edge.get("ontology_predicate") or edge["predicate"],"label":edge["predicate"],
            "state": {"bom_registration": "BOM_REGISTRATION", "development_procurement": "DEVELOPMENT_WORKFLOW"}.get(
                edge.get("evidence",{}).get("record_type"), "OPENING_BALANCE"),
            "evidence_refs":[edge["evidence"]]} for edge in graph.get("edges",[])]
    return {"nodes":nodes,"edges":edges,"complete":True}


def snapshot_view(payload):
    source = payload["metadata"]
    projection = payload.get("projection") or {}
    version = source["server_ledger_version"]
    ready = projection.get("state") == "READY" and projection.get("projection_version") == version
    balance = payload["opening_balance"]
    round_ = payload.get("current_delivery_round") or balance["delivery_round"]
    inventories = payload.get("current_inventory", balance["inventory"])
    lots = [row for row in inventories if row["scope"] == "LOT"]
    # Primary inventory is identified by its actual LOT records, never a fixed
    # product ID. Mixed item totals are not added together as one available item.
    item_ids = {row["item_id"] for row in lots}
    primary = lots if len(item_ids) == 1 else []
    config = balance["configuration"]
    requirements = requirements_for_snapshot(payload)
    bom = payload.get("bom")
    procurement = payload.get("procurement", {})
    receipts = procurement.get("receipts", [])
    inventory_origin = "mixed_sources" if receipts or payload.get("reservations") else "synthetic_opening_balance"
    summary = {
        "promise_qty": round_["promised_qty"], "promised_qty": round_["promised_qty"],
        "good_qty": round_["good_qty"], "fg_received_qty": round_["fg_received_qty"],
        "shipped_qty": round_["shipped_qty"], "arrived_qty": round_["arrived_qty"],
        "promise_remaining_qty": round_["remaining_promise_qty"],
        "production_remaining_qty": round_["remaining_good_target_qty"],
        "good_not_received_qty": round_["good_not_received_qty"],
        "warehouse_remaining_qty": round_["warehouse_qty"], "transit_qty": round_["in_transit_qty"],
        "already_produced_qty": round_["good_qty"] - round_["arrived_qty"],
        "defect_qty": sum(row["defect_qty"] for row in balance["production_reports"]),
        "pending_judgement_qty": sum(row["pending_judgement_qty"] for row in balance["production_reports"]),
        "on_hand_qty": sum(row["on_hand_qty"] for row in primary) if primary else None,
        "reserved_qty": sum(row["reserved_other_qty"] for row in primary) if primary else None,
        "available_qty": sum(row["available_qty"] for row in primary) if primary else None,
        "bom_status": "POSTED_SOURCE" if bom else config["registration_status"],
        "technical_review": bom.get("technical_review", "UNREVIEWED") if bom else config["technical_review_status"],
        "actual_manufacturing_feasibility": "UNKNOWN", "requirements_state": requirements["state"],
        "item_id": next(iter(item_ids)) if len(item_ids) == 1 else None,
        "delivery_round_id": round_["round_id"], "production_job_id": round_["job_id"],
        "configuration_id": round_["configuration_id"], "arrival_due_date": round_["arrival_due_date"],
        "effective_at": source.get("opening_effective_at", source["effective_at"]), "serial_coverage": "UNKNOWN",
    }
    if procurement:
        orders = [row for row in procurement.get("documents", []) if row["kind"] == "PO"]
        order_units = {row["content"]["resolved"]["unit"] for row in orders}
        receipt_units = {row["unit"] for row in receipts}
        summary.update(purchase_order_qty=sum(row["content"]["quantity"] for row in orders) if len(order_units) <= 1 else None,
            purchase_status="개발 원장 · 공급사 송신 없음", purchase_unit=next(iter(order_units), None),
            inspection_total_qty=sum(row["quantity"] for row in receipts) if len(receipt_units) <= 1 else None,
            inspection_pending_qty=sum(row["pending_qty"] for row in receipts) if len(receipt_units) <= 1 else None,
            inspection_passed_qty=sum(row["passed_qty"] for row in receipts) if len(receipt_units) <= 1 else None)
    meta = {
        "ledger_version": version, "projection_version": projection.get("projection_version"),
        "projection_state": "READY" if ready else projection.get("state", "PENDING"),
        "current_ledger_version": source["current_ledger_version"],
        "snapshot_id": source["snapshot_id"], "read_snapshot_id": source["snapshot_id"],
        "source_ledger_version": source["source_ledger_version"],
        "ontology_version": projection.get("ontology_version"), "rule_version": source["rule_version"],
        "company_id": source["company_id"], "run_id": source["run_id"],
        "effective_at": source["effective_at"], "known_as_of": source["known_as_of"],
        "recorded_at": source["recorded_at"], "synthetic": True, "record_type": "opening_balance",
        "source_sha256": source["source_sha256"],
        "full_bom_inventory_coverage": source["full_bom_inventory_coverage"],
        "bom_source_kind": source.get("bom_source_kind"), "bom_source_sha256": source.get("bom_source_sha256"),
        "bom_registered_at": source.get("bom_registered_at"), "opening_recorded_at": source.get("opening_recorded_at"),
        "material_rule_version": requirements["rule_version"],
        "data_origins": {"inventory": inventory_origin, "bom": "actual_bom" if bom else None,
                         "application": "conditional_demo" if requirements["has_demo_assumptions"] else "unreviewed"},
    }
    if source.get("opening_effective_at"):
        meta.update(opening_effective_at=source["opening_effective_at"],
                    inventory_effective_at=source.get("current_inventory_effective_at"))
    from service.reservations import reservation_impacts
    impacts = reservation_impacts(requirements, payload.get("reservations", []))
    suggestions = []
    if impacts:
        # Selecting a suggestion only fills the question draft (no model call).
        first = next(row for row in impacts if row["item_id"] == impacts[-1]["item_id"])
        suggestions = [f"왜 부족이 {impacts[-1]['planned_shortage_after']}개로 늘었어?",
                       f"기존 {first['planned_shortage_before']}개 안이면 충분해? 어떤 안을 다시 검토할까?"]
    return {
        "mode": "ledger-service", "serviceConnected": True, "meta": meta, "summary": summary,
        "entities": payload["entities"], "relationships": payload["relationships"],
        "graph": {"meta": meta, "data": display_graph(projection.get("graph", {})) if ready else {"nodes": [], "edges": []}},
        "projection": {key: value for key, value in projection.items() if key not in {"graph", "rdf_turtle"}},
        "rounds": [summary], "inventory": inventories,
        "events":payload.get("events",[]),"event_count":payload.get("event_count",0),"events_limit":100,
        "production": balance["production_reports"], "shipments": balance["shipments"],
        "quotations": balance["quotations"], "substitutes": balance["substitutes"],
        "mapping_candidates": balance["mapping_candidates"],
        "bom": deepcopy(bom), "material_requirements": requirements,
        "procurement": deepcopy(payload.get("procurement", {})),
        "reservations": deepcopy(payload.get("reservations", [])),
        "fulfillment": deepcopy(payload.get("fulfillment", {})),
        "capabilities": {"bom_upload": "RUNNER_UNAVAILABLE", "purchase_write": "NOT_IMPLEMENTED", "agent": "NOT_IMPLEMENTED", "model": "NOT_RUN"},
        "reservation_impacts": impacts,
        "suggested_questions": suggestions + ["남은 약속은 몇 대고, 얼마나 더 만들어야 해?", "수율을 적용하면 몇 대 투입하고 어떤 자재가 부족해?", f"남은 {summary['promise_remaining_qty']}대, 납기 안에 고객에게 도착할 수 있어?", "현재 가용 재고와 예약 근거를 보여줘", "전체 온톨로지의 클래스와 관계 범위를 보여줘"],
    }


def ontology_view(catalog):
    export = catalog.export()
    triples = export["tbox_triples"] + export["shape_triples"]
    terms = {}
    for triple in triples:
        for key in ("subject", "object"):
            term = triple[key]
            if term["kind"] != "literal":
                terms[term["value"]] = term
    kinds = {entry["id"]: kind for kind, entries in (("Class",export["classes"]),("Property",export["relations"]),("Shape",export["shapes"])) for entry in entries}
    properties = {key: {} for key in terms}
    labels = {}
    for triple in triples:
        s, p, o = triple["subject"]["value"], triple["predicate"]["value"], triple["object"]
        if o["kind"] == "literal":
            properties[s].setdefault(p, []).append(o["value"])
            if p.endswith("#label"):
                labels[s] = o["value"]
    nodes = [{"node_id":key,"label":labels.get(key, key.rsplit("#",1)[-1].rsplit("/",1)[-1]),
              "node_type":"design" if kinds.get(key)!="Shape" else "doc",
              "entity_ref":{"entity_type":kinds.get(key,"SchemaTerm"),"entity_id":key},
              "attributes":{"kind_label":kinds.get(key,"SchemaTerm"),"properties":{p:" · ".join(v) for p,v in properties[key].items()},"display":key}}
             for key in sorted(terms)]
    edges=[{"edge_id":f"schema-{index}","source_node_id":triple["subject"]["value"],"target_node_id":triple["object"]["value"],
            "relation_type":triple["predicate"]["value"],"label":triple["predicate"]["value"].rsplit("#",1)[-1],
            "state":"SCHEMA","source_version":catalog.version,"evidence_refs":[source["sha256"] for source in export["sources"]]}
           for index,triple in enumerate(triples) if triple["object"]["kind"]!="literal"]
    meta={"ontology_version":catalog.version,"projection_state":"SCHEMA_ONLY","scope":"full_schema","scope_label":"전체 T-Box · SHACL · 원장 사건과 구분", "node_count":len(nodes),"edge_count":len(edges)}
    return {**export,"meta":meta,"graph":{"meta":meta,"data":{"nodes":nodes,"edges":edges}}}



# Kept at this public import location for existing API and projection clients.
from service.query_answers import answer_query
