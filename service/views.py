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
            "state":"BOM_REGISTRATION" if edge.get("evidence",{}).get("record_type")=="bom_registration" else "OPENING_BALANCE",
            "evidence_refs":[edge["evidence"]]} for edge in graph.get("edges",[])]
    return {"nodes":nodes,"edges":edges,"complete":True}


def snapshot_view(payload):
    source = payload["metadata"]
    projection = payload.get("projection") or {}
    version = source["server_ledger_version"]
    ready = projection.get("state") == "READY" and projection.get("projection_version") == version
    balance = payload["opening_balance"]
    round_ = balance["delivery_round"]
    inventories = balance["inventory"]
    lots = [row for row in inventories if row["scope"] == "LOT"]
    # Primary inventory is identified by its actual LOT records, never a fixed
    # product ID. Mixed item totals are not added together as one available item.
    item_ids = {row["item_id"] for row in lots}
    primary = lots if len(item_ids) == 1 else []
    config = balance["configuration"]
    requirements = requirements_for_snapshot(payload)
    bom = payload.get("bom")
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
        "effective_at": source["effective_at"], "serial_coverage": "UNKNOWN",
    }
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
        "data_origins": {"inventory": "synthetic_opening_balance", "bom": "actual_bom" if bom else None,
                         "application": "conditional_demo" if requirements["has_demo_assumptions"] else "unreviewed"},
    }
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
        "capabilities": {"bom_upload": "RUNNER_UNAVAILABLE", "purchase_write": "NOT_IMPLEMENTED", "agent": "NOT_IMPLEMENTED", "model": "NOT_RUN"},
        "suggested_questions": ["현재 납품회차의 이행 현황을 보여줘", "현재 가용 재고와 예약 근거를 보여줘", "BOM 등록 상태와 소요 계산 가능 여부를 확인해줘", "전체 온톨로지의 클래스와 관계 범위를 보여줘"],
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


def answer_query(question, view, ontology):
    """A bounded deterministic domain query; no generated or stored gold answer."""
    q=question.strip().casefold()
    s=view["summary"]
    kinds=set()
    if "온톨로지" in q or "클래스" in q:
        counts=ontology["counts"]
        answer=f"서버에서 보존한 전체 스키마는 클래스 {counts['classes']}개, 관계 {counts['relations']}개, SHACL shape {counts['shapes']}개입니다. 전체 공식 요구 대조와 전체 CQ 인수는 별도 검증 중입니다."
        steps=["manifest와 대조한 전체 T-Box·SHACL을 조회했습니다.","시연에 쓰이지 않는 스키마도 포함했습니다."]
    elif "bom" in q or "소요" in q or "부족" in q or "생산 가능" in q:
        requirements=view["material_requirements"]
        if requirements["bom_id"]:
            basis=requirements["basis"]
            answer=(f"실제 BOM 원본 {requirements['row_count']}행이 원장에 등록되어 있습니다. "
                    f"원문 수량의 단순 환산 기준은 추가 양품 목표 {basis['production_remaining_qty']}대와 "
                    f"계획 수율 {float(basis['plan_yield'])*100:g}%를 가정한 투입 {basis['planned_input_qty']}대입니다. "
                    "단위·실장 검수 전 원문 환산은 확정 소요나 양품 보장이 아닙니다. ")
            calculated=[item for item in requirements["items"] if item["state"]=="CALCULATED"]
            if calculated:
                answer += "명시적으로 검토한 조건부 매핑과 합성 기초 재고로 계산한 품목: " + "; ".join(
                    f"{item['item_id']} 소요 {item['required_qty']}/{item['planned_required_qty']}{item['unit']}, "
                    f"가용 {item['available_qty']}{item['unit']}, 부족 {item['shortage_qty']}/{item['planned_shortage_qty']}{item['unit']}"
                    for item in calculated) + ". "
            answer += (f"부족량을 확정하지 못한 행은 {requirements['unknown_shortage_count']}개이며 "
                       "미확인 재고를 0으로 처리하지 않았습니다. 실제 제조 적용과 전체 확보 여부는 기술 검수 전 판단 보류입니다.")
            steps=["같은 원장 버전에 등록된 BOM의 전체 원문 행과 승인 hash를 조회했습니다.",
                   "실제 원문 수량·조건부 적용 매핑·합성 기초 재고를 구분했습니다.",
                   "같은 품목의 소요를 합산한 뒤 타수요 예약과 검사대기를 제외한 가용 수량과 비교했습니다."]
        else:
            answer=f"적용 BOM 상태는 {s['bom_status']}이며 기술 검수는 {s['technical_review']}입니다. 전체 자재 소요·부족량과 제조 가능성은 판단 보류입니다. 추가 양품 목표 {s['production_remaining_qty']}대와 잔여 고객 도착 약속 {s['promise_remaining_qty']}대는 서로 다른 값입니다."
            steps=["같은 원장 버전의 구성 등록 상태를 조회했습니다.","미검수 매핑을 적용 BOM이나 승인으로 사용하지 않았습니다."]
        kinds={"ProductConfiguration","ProductionJob","DeliveryRound","SourceDocument","SourceSnapshot","SourceRow", "BOMSnapshot", "BOMLine", "BOMApplication", "InternalItem", "PartLot", "ApprovalGrant", "DocumentVersion"}
    elif any(word in q for word in ("재고","예약","가용","lot")):
        entries=view["inventory"]
        answer="현재 기초 원장의 품목/LOT별 가용 수량: "+"; ".join(f"{row.get('lot_id',row['item_id'])} {row['available_qty']}{row.get('unit') or ''}" for row in entries)+". 검사대기와 타 수요 예약은 가용에 포함하지 않으며 전체 BOM 확보 완료를 뜻하지 않습니다."
        steps=["회사와 실행 범위가 같은 원장 재고를 조회했습니다.","품목 집계와 LOT 잔고를 구분하고, 자기예약을 중복 차감하지 않았습니다."]
        kinds={"PartLot","InternalItem"}
    elif any(word in q for word in ("납품","도착","이행","잔여","현황","약속")):
        answer=f"약속 {s['promised_qty']}대 중 양품 {s['good_qty']}대, 완성품 입고 {s['fg_received_qty']}대, 출하 {s['shipped_qty']}대, 고객 도착 {s['arrived_qty']}대입니다. 잔여 도착 약속은 {s['promise_remaining_qty']}대, 추가 양품 목표는 {s['production_remaining_qty']}대입니다. 과거 serial·장착 상세는 UNKNOWN입니다."
        steps=["PostgreSQL의 기초 이관 원장을 같은 snapshot으로 조회했습니다.","출하와 도착을 구분하여 잔여 약속을 계산했습니다."]
        kinds={"DeliveryRound","ContractLine","ProductionJob","Customer"}
    else:
        return {"status":"UNKNOWN","answer":"이 질문을 처리하는 업무 도구는 아직 연결되지 않았습니다. 현재 이행·재고·BOM 상태·전체 온톨로지 범위를 조회할 수 있습니다.","steps":[],"graph_path":[],"evidence_node_ids":[],"meta":view["meta"],"model_calls":0,"execution_kind":"deterministic_domain_query"}
    ids={row["id"] for row in view["entities"] if row["type"] in kinds}
    if view.get("bom") and any(word in q for word in ("bom","소요","부족","생산 가능")):
        active=view["bom"]
        # Older registered sources remain in the full graph, but a current
        # calculation must highlight only the active BOM version's evidence.
        ids={row["id"] for row in view["entities"] if row["id"] in ids and
             (row.get("evidence",{}).get("record_type")!="bom_registration" or
              (row["evidence"].get("bom_id")==active["bom_id"] and row["evidence"].get("bom_version")==active["bom_version"]))}
    edges=[row for row in view["relationships"] if row["source"] in ids and row["target"] in ids]
    ready=view["meta"]["projection_state"]=="READY"
    evidence_refs=[{"source_sha256":view["meta"]["source_sha256"],"record_type":"opening_balance","snapshot_id":view["meta"]["snapshot_id"]}]
    if view.get("bom") and any(word in q for word in ("bom","소요","부족","생산 가능")):
        bom=view["bom"]
        evidence_refs.append({"source_sha256":bom["source"]["sha256"],"record_type":"bom_registration",
                              "bom_id":bom["bom_id"],"bom_version":bom["bom_version"],"payload_hash":bom["payload_hash"],
                              "snapshot_id":view["meta"]["snapshot_id"]})
    return {"status":"ANSWERED","answer":answer,"steps":steps,"graph_path":[[row["source"],row["target"]] for row in edges] if ready else [],
            "evidence_node_ids":sorted(ids) if ready else [],"evidence_refs":evidence_refs,
            "meta":deepcopy(view["meta"]),"model_calls":0,"execution_kind":"deterministic_domain_query"}
