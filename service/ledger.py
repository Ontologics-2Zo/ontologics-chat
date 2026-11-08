"""Durable opening balances and immutable versioned read snapshots.

Only the explicit local import command calls install_initial. It accepts the
validated INITIAL converter output, not arbitrary A-Box/fixture data. HTTP
clients have no import/reset endpoint. Original source versions remain separate
from the PostgreSQL ledger sequence.
"""

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from psycopg.types.json import Jsonb

from service.contracts import canonical_bytes
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
        """Carry read facts forward after generic evidence-note commits only.

        An EvidenceDocumentPosted event records evidence; it has no inventory,
        BOM, production or delivery effect. Unknown event kinds stop this worker
        until an explicit domain reducer is implemented. Every emitted snapshot
        still requires fresh RDF/SHACL/Neo4j validation before becoming READY.
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
                    "SELECT event_id,ledger_version,event_type,payload,document_id FROM ledger_events WHERE company_id=%s AND ledger_version>%s ORDER BY ledger_version",
                    (company, previous["ledger_version"]),
                ).fetchall()
                expected = previous["ledger_version"] + 1
                pending_count = 0
                for event in events:
                    if event["ledger_version"] != expected:
                        raise ServiceError("LEDGER_EVENT_GAP")
                    if event["event_type"] != "EvidenceDocumentPosted" or event["document_id"] is None:
                        raise ServiceError("UNSUPPORTED_LEDGER_EVENT")
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
