"""Durable opening balances and immutable versioned read snapshots.

Only the explicit local import command calls install_initial. It accepts the
validated INITIAL converter output, not arbitrary A-Box/fixture data. HTTP
clients have no import/reset endpoint. Original source versions remain separate
from the PostgreSQL ledger sequence.
"""

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from psycopg.types.json import Jsonb

from service.contracts import canonical_bytes, content_hash
from service.errors import ServiceError
from service.imports import InitialInputError, validate_initial


def _import_hash(payload):
    """Retry timestamps do not change meaning; every factual field does."""
    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()
                    if key not in {"recorded_at", "server_ledger_version"}}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value
    return sha256(canonical_bytes(normalize(payload))).hexdigest()


def _projection_hash(projection):
    # Turtle statement order is not semantic and can vary between serializers.
    return sha256(canonical_bytes({key: value for key, value in projection.items() if key != "rdf_turtle"})).hexdigest()


def apply_bom_event(previous, event):
    """Reduce one approved registration without changing opening inventory.

    Every raw BOM row remains a SourceRow and BOMLine. Only an explicit reviewed
    application becomes a BOMApplication; an unresolved mapping creates no item
    link. No production, consumption or installation is inferred from a BOM.
    """
    try:
        value = event["payload"]
        content = value["content"]
        meta = previous["metadata"]
        approval = value["approval"]
        recorded_at = event["recorded_at"]
        recorded_time = recorded_at if isinstance(recorded_at, datetime) else datetime.fromisoformat(recorded_at)
        if recorded_time.utcoffset() is None:
            raise ValueError
        registered_at = recorded_time.isoformat()
        if (event["event_type"] != "BomRegistered" or value["event_type"] != "BomRegistered"
                or value["company_id"] != meta["company_id"]
                or value["ledger_version"] != meta["server_ledger_version"] + 1
                or event["ledger_version"] != value["ledger_version"]
                or content["kind"] != "bom_registration"
                or content["expected_ledger_version"] != meta["server_ledger_version"]
                or content_hash(content) != value["payload_hash"]
                or value["source"] != content["source"] or value["rows"] != content["rows"]
                or value["configuration_id"] != content["configuration_id"]
                or not approval["approved_by"] or not approval["reviewed_by"]
                or approval["created_by"] in {approval["approved_by"], approval["reviewed_by"]}
                or approval["approved_hash"] != value["payload_hash"]
                or approval["approved_version"] != value["bom_version"]):
            raise ValueError
        configurations = [entity for entity in previous["entities"]
                          if entity["type"] == "ProductConfiguration"
                          and content["configuration_id"] in {entity["id"], entity["display_id"]}]
        rows = content["rows"]
        if len(configurations) != 1 or not isinstance(rows, list) or not rows or len(rows) > 10000:
            raise ValueError
        if len({row["row_id"] for row in rows}) != len(rows):
            raise ValueError
        if (event.get("bom_document_id") is not None
                and str(event["bom_document_id"]) != value["bom_id"]):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ServiceError("BOM_EVENT_CONTRACT_INVALID") from None
    snapshot = deepcopy(previous)
    company, run = meta["company_id"], meta["run_id"]
    source = content["source"]
    version_key = [value["bom_id"], value["bom_version"], value["payload_hash"]]
    evidence = {"record_type": "bom_registration", "source_sha256": source["sha256"],
                "parsed_sha256": source.get("parsed_sha256"), "bom_id": value["bom_id"],
                "bom_version": value["bom_version"], "payload_hash": value["payload_hash"],
                "event_id": str(event["event_id"]), "ledger_version": value["ledger_version"],
                "registered_at": registered_at,
                "source_kind": source["source_kind"], "configuration_binding": content.get("configuration_binding"),
                "synthetic_inventory": True}
    entity_ids = {entity["id"] for entity in snapshot["entities"]}
    edge_ids = {edge["id"] for edge in snapshot["relationships"]}

    def identity(kind, key):
        return "urn:ontologics:bom:" + content_hash([company, run, version_key, kind, key])

    def entity(kind, key, label, attributes, *, origin="actual_source", locator=None):
        identifier = identity(kind, key)
        if identifier in entity_ids:
            raise ServiceError("BOM_GRAPH_DUPLICATE")
        entity_ids.add(identifier)
        snapshot["entities"].append({"id": identifier, "type": kind, "source_type": kind,
            "display_id": label, "logicalId": label, "label": label, "origin": origin,
            "run_id": run, "attributes": deepcopy(attributes),
            "evidence": {**evidence, "source_locator": locator}})
        return identifier

    def edge(start, predicate, target, locator=None):
        identifier = identity("relationship", [start, predicate, target])
        if identifier in edge_ids:
            raise ServiceError("BOM_GRAPH_DUPLICATE")
        edge_ids.add(identifier)
        snapshot["relationships"].append({"id": identifier, "source": start, "target": target,
            "predicate": predicate, "evidence": {**evidence, "source_locator": locator}})

    source_document = entity("SourceDocument", "source", source["filename"],
                             {"source_sha256": source["sha256"], "source_kind": source["source_kind"]})
    source_snapshot = entity("SourceSnapshot", "source-snapshot", "Source " + source["sha256"][:12], source)
    bom_snapshot = entity("BOMSnapshot", "bom-snapshot", "BOM " + str(value["bom_version"]),
                          {"bom_id": value["bom_id"], "bom_version": value["bom_version"],
                           "row_count": len(rows), "payload_hash": value["payload_hash"],
                           "registration_scope": content.get("registration_scope"),
                           "configuration_binding": content.get("configuration_binding"),
                           "technical_review": content.get("technical_review"), "status": "POSTED_SOURCE"})
    edge(source_snapshot, "snapshotOfDocument", source_document)
    edge(bom_snapshot, "parsedFromSnapshot", source_snapshot)
    configuration_id = configurations[0]["id"]
    edge(configuration_id, "hasBOMSnapshot", bom_snapshot)
    # The approval is a real development-workflow record, not a claim that an
    # operating-company engineer validated the source or mapping.
    document_version = entity("DocumentVersion", "document-version", "BOM revision " + str(value["bom_version"]),
                              {"bom_id": value["bom_id"], "version": value["bom_version"],
                               "payload_hash": value["payload_hash"]}, origin="development_workflow")
    approval_grant = entity("ApprovalGrant", "approval", "BOM source approval", approval, origin="development_workflow")
    edge(approval_grant, "approvalForDocumentVersion", document_version)
    items = {}
    for existing in previous["entities"]:
        if existing["type"] == "InternalItem":
            for key in {existing["id"], existing["display_id"]}:
                if key in items and items[key] != existing["id"]:
                    raise ServiceError("BOM_MAPPING_AMBIGUOUS")
                items[key] = existing["id"]
    for row in rows:
        row_id, locator = row["row_id"], row["locator"]
        row_label = "BOM row " + str(row.get("ordinal", row.get("source_ordinal", row.get("source_row"))))
        source_row = entity("SourceRow", row_id, row_label + " source",
                            {key: deepcopy(value_) for key, value_ in row.items() if key != "decision"}, locator=locator)
        bom_line = entity("BOMLine", row_id, row_label,
                          {"source_quantity": row.get("source_quantity"), "designators": row.get("designators"),
                           "source_description": row.get("source_description"), "decision": row.get("decision"),
                           "source_row_id": row_id}, locator=locator)
        edge(source_row, "rowInSnapshot", source_snapshot, locator)
        edge(bom_snapshot, "hasBOMLine", bom_line, locator)
        edge(bom_line, "lineSourceRow", source_row, locator)
        decision = row.get("decision", {})
        if (decision.get("application_status") != "UNRESOLVED"
                and decision.get("review_basis") in {"SOURCE_VERIFIED", "DEMO_ASSUMPTION"}):
            application = entity("BOMApplication", row_id, row_label + " application", decision,
                                 origin="synthetic_assumption" if decision["review_basis"] == "DEMO_ASSUMPTION" else "reviewed_application",
                                 locator=locator)
            edge(bom_line, "lineHasApplication", application, locator)
            edge(application, "appliesToConfiguration", configuration_id, locator)
            if decision.get("application_status") == "APPLICABLE" and decision.get("item_id"):
                if decision["item_id"] not in items:
                    raise ServiceError("BOM_MAPPING_UNAVAILABLE")
                edge(application, "applicationMapsItem", items[decision["item_id"]], locator)
    snapshot["bom"] = {**deepcopy(content), "bom_id": value["bom_id"], "bom_version": value["bom_version"],
                       "payload_hash": value["payload_hash"], "ledger_version": value["ledger_version"],
                       "registered_at": registered_at,
                       "approval": deepcopy(approval), "status": "POSTED_SOURCE"}
    snapshot["metadata"]["server_ledger_version"] = value["ledger_version"]
    snapshot["metadata"]["active_bom_id"] = value["bom_id"]
    snapshot["metadata"]["bom_source_sha256"] = source["sha256"]
    snapshot["metadata"]["bom_source_kind"] = source["source_kind"]
    snapshot["metadata"]["initial_rule_version"] = meta.get("initial_rule_version", meta.get("rule_version"))
    snapshot["metadata"]["rule_version"] = "bom-registration/1+bom-material-requirements/1"
    snapshot["metadata"]["bom_registered_at"] = registered_at
    snapshot["metadata"]["opening_recorded_at"] = meta.get("opening_recorded_at", meta.get("recorded_at"))
    snapshot["metadata"]["recorded_at"] = registered_at
    # The inventory's historical effective_at remains the opening baseline;
    # knowledge of the newly registered source starts at this real event.
    previous_knowledge = datetime.fromisoformat(meta["known_as_of"])
    snapshot["metadata"]["known_as_of"] = max(previous_knowledge, recorded_time).isoformat()
    snapshot["metadata"]["full_bom_inventory_coverage"] = "REGISTERED_SOURCE_APPLICATION_REVIEW_SEPARATE"
    snapshot["metadata"]["last_bom_event"] = evidence
    return snapshot


class LedgerReader:
    def __init__(self, db, identity):
        self.db, self.identity = db, identity

    def install_initial(self, payload, *, local_development=False):
        if not isinstance(payload, dict):
            raise ServiceError("INITIAL_CONTRACT_INVALID", 422)
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict):
            raise ServiceError("INITIAL_CONTRACT_INVALID", 422)
        company, run = meta.get("company_id"), meta.get("run_id")
        if local_development is not True or not isinstance(company, str) or not company.startswith("DEMO-"):
            raise ServiceError("INITIAL_IMPORT_FORBIDDEN", 403)
        try:
            validate_initial(payload)
        except (InitialInputError, TypeError, ValueError):
            raise ServiceError("INITIAL_CONTRACT_INVALID", 422) from None
        source_hash = meta["source_sha256"]
        content_hash = _import_hash(payload)
        with self.db.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (company,))
            existing = conn.execute("SELECT * FROM opening_imports WHERE company_id=%s AND run_id=%s", (company, run)).fetchone()
            if existing:
                if existing["source_hash"] != source_hash or existing["content_hash"] != content_hash:
                    raise ServiceError("OPENING_IMPORT_CONFLICT")
                return existing["ledger_version"]
            # No implicit merge into a company whose ledger already contains work.
            conn.execute("INSERT INTO ledger_state(company_id,version) VALUES (%s,0) ON CONFLICT DO NOTHING", (company,))
            prior = conn.execute("SELECT version FROM ledger_state WHERE company_id=%s FOR UPDATE", (company,)).fetchone()
            if prior["version"]:
                raise ServiceError("OPENING_IMPORT_REQUIRES_EMPTY_LEDGER")
            conn.execute("UPDATE ledger_state SET version=1 WHERE company_id=%s", (company,))
            copy = deepcopy(payload)
            copy["metadata"]["server_ledger_version"] = 1
            event_id = uuid4()
            evidence = {"run_id": run, "source_sha256": source_hash, "content_sha256": content_hash,
                        "source_ledger_version": meta.get("source_ledger_version"), "converter_version": meta.get("converter_version"),
                        "record_type": "opening_balance", "opening_balance": True}
            conn.execute("INSERT INTO ledger_events(event_id,company_id,ledger_version,document_id,event_type,payload,actor_id) VALUES (%s,%s,1,NULL,'OPENING_BALANCE_IMPORTED',%s,'system:local-import')", (event_id, company, Jsonb(evidence)))
            conn.execute("INSERT INTO outbox(event_id,company_id,ledger_version,payload) VALUES (%s,%s,1,%s)", (event_id, company, Jsonb(evidence)))
            conn.execute("INSERT INTO service_snapshots(company_id,run_id,ledger_version,source_hash,content_hash,payload) VALUES (%s,%s,1,%s,%s,%s)", (company, run, source_hash, content_hash, Jsonb(copy)))
            conn.execute("INSERT INTO opening_imports(company_id,run_id,source_hash,content_hash,ledger_version) VALUES (%s,%s,%s,%s,1)", (company, run, source_hash, content_hash))
        return 1

    def read(self, principal, *, run_id="INITIAL", version=None):
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
            raise ServiceError("INVALID_SNAPSHOT_VERSION", 422)
        with self.db.connect() as conn:
            self.identity.require(principal, "ledger.read", conn, principal.company_id)
            row = conn.execute("SELECT * FROM service_snapshots WHERE company_id=%s AND run_id=%s AND (%s::bigint IS NULL OR ledger_version=%s) ORDER BY ledger_version DESC LIMIT 1", (principal.company_id, run_id, version, version)).fetchone()
            if not row:
                raise ServiceError("LEDGER_NOT_INITIALIZED", 404)
            current = conn.execute("SELECT version FROM ledger_state WHERE company_id=%s", (principal.company_id,)).fetchone()["version"]
            result = deepcopy(row["payload"])
            result["projection"] = row["projection"]
            events=conn.execute("SELECT event_id,ledger_version,event_type,recorded_at FROM ledger_events WHERE company_id=%s AND ledger_version<=%s ORDER BY ledger_version DESC LIMIT 100", (principal.company_id,row["ledger_version"])).fetchall()
            result["events"]=[{**event,"event_id":str(event["event_id"]),"recorded_at":event["recorded_at"].isoformat()} for event in events]
            result["event_count"]=conn.execute("SELECT count(*) AS n FROM ledger_events WHERE company_id=%s AND ledger_version<=%s", (principal.company_id,row["ledger_version"])).fetchone()["n"]
            result["metadata"]["server_ledger_version"] = row["ledger_version"]
            result["metadata"]["current_ledger_version"] = current
            result["metadata"]["snapshot_id"] = sha256(canonical_bytes([principal.company_id,run_id,row["ledger_version"],row["content_hash"]])).hexdigest()
            return result

    def pending(self):
        with self.db.connect() as conn:
            return conn.execute("SELECT company_id,run_id,ledger_version,payload FROM service_snapshots WHERE projection IS NULL ORDER BY company_id,ledger_version").fetchall()

    def advance_documents(self):
        """Apply supported committed documents to immutable read snapshots.

        An EvidenceDocumentPosted event records evidence; it has no inventory,
        BOM, production or delivery effect. A BomRegistered event adds the source
        and reviewed application decisions, never stock or future manufacturing
        events. Every emitted snapshot requires fresh RDF/SHACL/Neo4j validation.
        """
        with self.db.connect() as conn:
            scopes = conn.execute("SELECT DISTINCT company_id,run_id FROM service_snapshots ORDER BY company_id,run_id").fetchall()
        advanced = 0
        for scope in scopes:
            company, run = scope["company_id"], scope["run_id"]
            with self.db.connect() as conn:
                state = conn.execute("SELECT version FROM ledger_state WHERE company_id=%s FOR UPDATE", (company,)).fetchone()
                previous = conn.execute(
                    "SELECT * FROM service_snapshots WHERE company_id=%s AND run_id=%s ORDER BY ledger_version DESC LIMIT 1 FOR UPDATE",
                    (company, run),
                ).fetchone()
                events = conn.execute(
                    "SELECT event_id,ledger_version,event_type,payload,document_id,bom_document_id,recorded_at FROM ledger_events WHERE company_id=%s AND ledger_version>%s ORDER BY ledger_version",
                    (company, previous["ledger_version"]),
                ).fetchall()
                expected = previous["ledger_version"] + 1
                pending_count = 0
                for event in events:
                    if event["ledger_version"] != expected:
                        raise ServiceError("LEDGER_EVENT_GAP")
                    if event["event_type"] == "BomRegistered" and event["document_id"] is None and event["bom_document_id"] is not None:
                        snapshot = apply_bom_event(previous["payload"], event)
                    elif event["event_type"] == "EvidenceDocumentPosted" and event["document_id"] is not None:
                        event_payload = event["payload"]
                        if (event_payload.get("event_type") != "EvidenceDocumentPosted" or event_payload.get("company_id") != company
                                or event_payload.get("ledger_version") != expected
                                or event_payload.get("content", {}).get("kind") != "evidence_note"):
                            raise ServiceError("LEDGER_EVENT_CONTRACT_INVALID")
                        snapshot = deepcopy(previous["payload"])
                        snapshot["metadata"]["server_ledger_version"] = expected
                        snapshot["metadata"]["last_document_event"] = {
                            "event_id": str(event["event_id"]), "document_id": str(event["document_id"]),
                            "document_version": event_payload["document_version"], "payload_hash": event_payload["payload_hash"],
                            "effect": "EVIDENCE_NOTE_ONLY", "business_facts_changed": False,
                        }
                    else:
                        raise ServiceError("UNSUPPORTED_LEDGER_EVENT")
                    conn.execute(
                        "INSERT INTO service_snapshots(company_id,run_id,ledger_version,source_hash,content_hash,payload) VALUES (%s,%s,%s,%s,%s,%s)",
                        (company, run, expected, previous["source_hash"], _import_hash(snapshot), Jsonb(snapshot)),
                    )
                    previous = {**previous, "payload": snapshot, "ledger_version": expected}
                    expected += 1
                    pending_count += 1
                if expected - 1 != state["version"]:
                    raise ServiceError("LEDGER_EVENT_GAP")
                advanced += pending_count
        return advanced

    def save_projection(self, company, run, version, projection, rdf_turtle):
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ServiceError("PROJECTION_VERSION_MISMATCH")
        with self.db.connect() as conn:
            row = conn.execute("SELECT payload,projection FROM service_snapshots WHERE company_id=%s AND run_id=%s AND ledger_version=%s FOR UPDATE", (company,run,version)).fetchone()
            if not row:
                raise ServiceError("SNAPSHOT_NOT_FOUND", 404)
            if not isinstance(projection, dict) or projection.get("ledger_version") != version or projection.get("projection_version") != version:
                raise ServiceError("PROJECTION_VERSION_MISMATCH")
            if projection.get("company_id") != company or projection.get("run_id") != run:
                raise ServiceError("PROJECTION_SCOPE_MISMATCH")
            if projection.get("payload_sha256") != sha256(canonical_bytes(row["payload"])).hexdigest():
                raise ServiceError("PROJECTION_PAYLOAD_MISMATCH")
            if not isinstance(projection.get("ontology_version"), str) or not projection["ontology_version"]:
                raise ServiceError("PROJECTION_ONTOLOGY_REQUIRED")
            graph = projection.get("graph", {})
            if not isinstance(graph, dict):
                raise ServiceError("PROJECTION_COVERAGE_MISMATCH")
            nodes, edges = graph.get("nodes", []), graph.get("edges", [])
            if (not isinstance(nodes, list) or not isinstance(edges, list) or graph.get("complete") is not True
                    or any(not isinstance(node, dict) for node in nodes) or any(not isinstance(edge, dict) for edge in edges)
                    or graph.get("node_count") != len(row["payload"]["entities"])
                    or graph.get("edge_count") != len(row["payload"]["relationships"])
                    or len(nodes) != graph.get("node_count") or len(edges) != graph.get("edge_count")
                    or {node.get("id") for node in nodes} != {node["id"] for node in row["payload"]["entities"]}
                    or {edge.get("id") for edge in edges} != {edge["id"] for edge in row["payload"]["relationships"]}):
                raise ServiceError("PROJECTION_COVERAGE_MISMATCH")
            if projection.get("state") not in {"READY", "NOT_READY"}:
                raise ServiceError("PROJECTION_STATE_INVALID")
            if projection["state"] == "READY" and (not isinstance(projection.get("validation"), dict)
                    or not isinstance(projection.get("neo4j"), dict)
                    or projection["validation"].get("state") != "CONFORMS"
                    or projection["neo4j"].get("state") != "READY"):
                raise ServiceError("PROJECTION_VALIDATION_REQUIRED")
            if not isinstance(rdf_turtle, str) or not rdf_turtle:
                raise ServiceError("PROJECTION_RDF_REQUIRED")
            if row["projection"] and row["projection"].get("state") == "READY":
                if _projection_hash(row["projection"]) != _projection_hash(projection):
                    raise ServiceError("READY_PROJECTION_IMMUTABLE")
                return
            conn.execute("UPDATE service_snapshots SET projection=%s,rdf_turtle=%s WHERE company_id=%s AND run_id=%s AND ledger_version=%s", (Jsonb(projection),rdf_turtle,company,run,version))
            if projection.get("state") == "READY":
                conn.execute("UPDATE outbox SET status='PROJECTED' WHERE company_id=%s AND ledger_version=%s", (company,version))

    def events(self, principal, after=0):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ServiceError("INVALID_EVENT_CURSOR", 422)
        with self.db.connect() as conn:
            self.identity.require(principal, "ledger.read", conn, principal.company_id)
            rows=conn.execute("SELECT event_id,ledger_version,event_type,recorded_at FROM ledger_events WHERE company_id=%s AND ledger_version>%s ORDER BY ledger_version LIMIT 100", (principal.company_id,after)).fetchall()
            return [{**row,"event_id":str(row["event_id"]),"recorded_at":row["recorded_at"].isoformat()} for row in rows]
