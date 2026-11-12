"""Version-isolated RDF/SHACL and Neo4j projections of committed snapshots.

This module does not import the private A-Box, generate business events, repair
missing provenance, or turn schema validity into manufacturing approval.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any

from pyshacl import validate
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, SH, URIRef, XSD

from service.ontology import OntologyCatalog
from service.reasoning import reasoning_report


PROJECTION = Namespace("urn:ontologics:projection:")
MAPPING_VERSION = "snapshot-rdf-v1"


class ProjectionError(ValueError):
    """Safe machine-readable projection failure; no raw source diagnostics."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return sha256(_json(value).encode()).hexdigest()


def _local(term: Any) -> str:
    return str(term).rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]


class OntologyProjector:
    def __init__(self, catalog: OntologyCatalog, neo4j_driver: Any = None, *, neo4j_database: str = "neo4j"):
        self.catalog = catalog
        self.driver = neo4j_driver
        self.database = neo4j_database
        self.tbox = catalog.tbox_graph()
        self.shapes = catalog.shapes_graph()
        self.schema = catalog.export()
        self.classes = set(self.tbox.subjects(RDF.type, OWL.Class)) | set(self.tbox.subjects(RDF.type, RDFS.Class))
        self.predicates = set()
        for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property):
            self.predicates.update(self.tbox.subjects(RDF.type, kind))
        self.scalar_paths = {p for p in self.shapes.objects(None, SH.path) if isinstance(p, URIRef)}
        self.scalar_paths.update(self.predicates)
        self.datatypes: dict[URIRef, URIRef] = {}
        for constraint, path in self.shapes.subject_objects(SH.path):
            datatypes = list(self.shapes.objects(constraint, SH.datatype))
            if isinstance(path, URIRef) and len(datatypes) == 1:
                self.datatypes[path] = datatypes[0]
        self.aliases = {"type_aliases": {}, "predicate_aliases": {}, "reserved_attribute_mapping": {}}
        for source in self.schema["adapters"]:
            for field in self.aliases:
                for name, value in source["mapping"].get(field, {}).items():
                    previous = self.aliases[field].get(name)
                    if previous is not None and previous != value:
                        raise ProjectionError("CONFLICTING_SCHEMA_ADAPTERS")
                    self.aliases[field][name] = value

    @staticmethod
    def _resolve(value: str, terms: set, aliases: dict) -> URIRef | None:
        visited = set()
        while value in aliases:
            if value in visited:
                raise ProjectionError("CYCLIC_SCHEMA_ADAPTER")
            visited.add(value)
            value = aliases[value]
        direct = URIRef(value)
        if direct in terms:
            return direct
        matches = [term for term in terms if _local(term) == value]
        return matches[0] if len(matches) == 1 else None

    def _attribute_path(self, name: str) -> URIRef | None:
        reserved = self.aliases["reserved_attribute_mapping"].get("attributes." + name)
        if reserved:
            # Adapter text may annotate a compact IRI; only that explicit IRI is
            # read, never its description as code or as an execution instruction.
            match = re.match(r"^([A-Za-z][\w-]*):([A-Za-z_][\w-]*)\b", reserved)
            if not match:
                return None
            namespaces = dict(self.tbox.namespaces())
            namespace = namespaces.get(match.group(1))
            return URIRef(str(namespace) + match.group(2)) if namespace else None
        return self._resolve(name, self.scalar_paths, {})

    def _literal(self, value: Any, path: URIRef) -> Literal:
        # A datatype annotates an existing value; it never supplies a value that
        # the source omitted. SHACL will reject lexical/value mismatches.
        datatype = self.datatypes.get(path)
        valid = False
        if datatype == XSD.string:
            valid = isinstance(value, str)
        elif datatype in (XSD.integer, XSD.nonNegativeInteger, XSD.positiveInteger):
            valid = not isinstance(value, bool) and re.fullmatch(r"[+-]?\d+", str(value)) is not None
            if valid and datatype != XSD.integer:
                valid = int(value) >= (1 if datatype == XSD.positiveInteger else 0)
        elif datatype in (XSD.decimal, XSD.double, XSD.float):
            try:
                valid = not isinstance(value, bool) and Decimal(str(value)).is_finite()
            except InvalidOperation:
                valid = False
        elif datatype == XSD.boolean:
            valid = isinstance(value, bool) or (isinstance(value, str) and value in ("true", "false", "1", "0"))
        elif datatype == XSD.dateTime and isinstance(value, str):
            try:
                valid = "T" in value and datetime.fromisoformat(value.replace("Z", "+00:00")) is not None
            except ValueError:
                valid = False
        # Avoid rdflib's malformed typed-literal diagnostics containing private
        # values. Keeping the original inferred type lets SHACL report failure.
        return Literal(value, datatype=datatype, normalize=False) if valid else Literal(value)

    def project(self, payload: dict, version: int, company_id: str, run_id: str) -> dict[str, Any]:
        graph, entity_uris, nodes, edges, unsupported, scope, snapshot_key, payload_hash = self._build(payload, version, company_id, run_id)
        report = self._validate(graph, entity_uris, unsupported)
        # OWL RL check next to SHACL; reported only, it does not change READY.
        reasoning = reasoning_report(self.tbox, graph, entity_uris, self.classes, self.predicates)
        neo = self._project_neo4j(snapshot_key, scope, payload_hash, nodes, edges, report)
        ready = report["state"] == "CONFORMS" and neo["state"] == "READY"
        return {"state": "READY" if ready else "NOT_READY", **scope, "projection_version": version,
                "snapshot_key": snapshot_key, "payload_sha256": payload_hash,
                # N-Triples is a Turtle subset. Sorted explicit IRIs make the
                # serialized snapshot stable across process restarts, unlike
                # auto-assigned Turtle prefixes and hash iteration order.
                "rdf_turtle": "\n".join(sorted(graph.serialize(format="nt").splitlines())) + "\n",
                "graph": {"nodes": nodes, "edges": edges, "complete": True, "node_count": len(nodes), "edge_count": len(edges)},
                "validation": report, "reasoning": reasoning, "neo4j": neo}

    def reason(self, payload: dict, version: int, company_id: str, run_id: str) -> dict[str, Any]:
        """OWL RL check for a stored snapshot without re-projecting or writing it."""
        graph, entity_uris, *_ = self._build(payload, version, company_id, run_id)
        return reasoning_report(self.tbox, graph, entity_uris, self.classes, self.predicates)

    def _build(self, payload: dict, version: int, company_id: str, run_id: str):
        if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not company_id or not run_id:
            raise ProjectionError("INVALID_PROJECTION_SCOPE")
        metadata = payload.get("metadata", {})
        for name, value in (("company_id", company_id), ("run_id", run_id)):
            if name in metadata and metadata[name] != value:
                raise ProjectionError("PAYLOAD_SCOPE_MISMATCH")
        if metadata.get("server_ledger_version") not in (None, version):
            raise ProjectionError("PAYLOAD_VERSION_MISMATCH")
        try:
            payload_hash = _digest(payload)
        except (ValueError, TypeError):
            raise ProjectionError("INVALID_SNAPSHOT_PAYLOAD") from None
        scope = {"company_id": company_id, "run_id": run_id, "ledger_version": version, "ontology_version": self.catalog.version, "mapping_version": MAPPING_VERSION}
        snapshot_key = _digest(scope)
        graph = Graph(identifier=URIRef("urn:ontologics:snapshot:" + snapshot_key))
        entity_uris, nodes, edges, unsupported = {}, [], [], []
        for entity in payload.get("entities", []):
            entity_id = entity.get("id")
            if not isinstance(entity_id, str) or not entity_id or entity_id in entity_uris:
                raise ProjectionError("INVALID_OR_DUPLICATE_ENTITY_ID")
            uri = URIRef("urn:ontologics:entity:" + _digest([company_id, run_id, entity_id]))
            entity_uris[entity_id] = uri
            class_uri = self._resolve(entity.get("type", ""), self.classes, self.aliases["type_aliases"])
            if class_uri is None:
                unsupported.append({"code": "UNMAPPED_CLASS", "entity_id": entity_id, "term": entity.get("type")})
            else:
                graph.add((uri, RDF.type, class_uri))
            graph.add((uri, PROJECTION.sourceEntityId, Literal(entity_id)))
            graph.add((uri, PROJECTION.attributes, Literal(_json(entity.get("attributes", {})), datatype=RDF.JSON)))
            graph.add((uri, PROJECTION.evidence, Literal(_json(entity.get("evidence", {})), datatype=RDF.JSON)))
            for name in ("origin", "logicalId", "label", "runId"):
                if name not in entity or entity[name] is None:
                    continue
                path = self._resolve(name, self.scalar_paths, {})
                if path:
                    graph.add((uri, path, self._literal(entity[name], path)))
            for name, value in entity.get("attributes", {}).items():
                path = self._attribute_path(name)
                if path and value is not None and isinstance(value, (str, int, float, bool)):
                    graph.add((uri, path, self._literal(value, path)))
            nodes.append({**deepcopy(entity), "uri": str(uri), "ontology_type": str(class_uri) if class_uri else None})
        if not nodes:
            raise ProjectionError("EMPTY_LEDGER_SNAPSHOT")
        edge_ids = set()
        for relationship in payload.get("relationships", []):
            edge_id = relationship.get("id")
            if not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids:
                raise ProjectionError("INVALID_OR_DUPLICATE_RELATIONSHIP_ID")
            edge_ids.add(edge_id)
            source, target = relationship.get("source"), relationship.get("target")
            if source not in entity_uris or target not in entity_uris:
                raise ProjectionError("DANGLING_RELATIONSHIP")
            predicate = self._resolve(relationship.get("predicate", ""), self.predicates, self.aliases["predicate_aliases"])
            if predicate is None:
                unsupported.append({"code": "UNMAPPED_PREDICATE", "relationship_id": edge_id, "term": relationship.get("predicate")})
            else:
                graph.add((entity_uris[source], predicate, entity_uris[target]))
            # Reification preserves separate evidence even for two relationships
            # with the same source/predicate/target in the ledger.
            statement = URIRef("urn:ontologics:relationship:" + _digest([company_id, run_id, edge_id]))
            graph.add((statement, RDF.type, RDF.Statement))
            graph.add((statement, RDF.subject, entity_uris[source]))
            graph.add((statement, RDF.object, entity_uris[target]))
            if predicate:
                graph.add((statement, RDF.predicate, predicate))
            graph.add((statement, PROJECTION.evidence, Literal(_json(relationship.get("evidence", {})), datatype=RDF.JSON)))
            edges.append({**deepcopy(relationship), "ontology_predicate": str(predicate) if predicate else None})
        return graph, entity_uris, nodes, edges, unsupported, scope, snapshot_key, payload_hash

    def _validate(self, graph: Graph, entity_uris: dict, unsupported: list) -> dict:
        if not self.schema["imports_resolved"]:
            return {"state": "UNSUPPORTED", "code": "UNRESOLVED_SCHEMA_IMPORTS", "violations": [], "unmapped": unsupported}
        queries = list(self.shapes.objects(None, SH.select)) + list(self.shapes.objects(None, SH.ask))
        if any(re.search(r"\b(?:SERVICE|LOAD)\b", str(query), flags=re.I) for query in queries):
            return {"state": "UNSUPPORTED", "code": "EXTERNAL_SHACL_QUERY", "violations": [], "unmapped": unsupported}
        try:
            conforms, validation_graph, _ = validate(data_graph=graph, shacl_graph=self.shapes, ont_graph=self.tbox,
                inference="rdfs", advanced=False, js=False, do_owl_imports=False, inplace=False)
            if not isinstance(validation_graph, Graph):
                raise ValueError
            reverse = {str(uri): source for source, uri in entity_uris.items()}
            violations = []
            for result in validation_graph.subjects(RDF.type, SH.ValidationResult):
                focus = validation_graph.value(result, SH.focusNode)
                violations.append({"entity_id": reverse.get(str(focus)), "focus": str(focus),
                    "path": str(validation_graph.value(result, SH.resultPath) or ""),
                    "constraint": str(validation_graph.value(result, SH.sourceConstraintComponent) or ""),
                    "severity": str(validation_graph.value(result, SH.resultSeverity) or "")})
            return {"state": "CONFORMS" if conforms and not unsupported else "VIOLATIONS", "conforms": bool(conforms),
                    "violation_count": len(violations), "violations": violations, "unmapped": unsupported,
                    "engine": "pyshacl", "inference": "rdfs", "ontology_version": self.catalog.version}
        except Exception:
            return {"state": "ERROR", "code": "SHACL_EXECUTION_FAILED", "violations": [], "unmapped": unsupported}

    def _project_neo4j(self, key: str, scope: dict, payload_hash: str, nodes: list, edges: list, report: dict) -> dict:
        if self.driver is None:
            return {"state": "NOT_RUN", "code": "NEO4J_NOT_CONFIGURED"}
        try:
            with self.driver.session(database=self.database) as session:
                for query in (
                    "CREATE CONSTRAINT ontology_snapshot_key IF NOT EXISTS FOR (n:OntologySnapshot) REQUIRE n.key IS UNIQUE",
                    "CREATE CONSTRAINT ontology_entity_scope IF NOT EXISTS FOR (n:OntologyEntity) REQUIRE (n.snapshot_key, n.id) IS UNIQUE",
                    "CREATE CONSTRAINT ontology_schema_version IF NOT EXISTS FOR (n:OntologySchema) REQUIRE n.version IS UNIQUE",
                ):
                    session.run(query).consume()
                counts = session.execute_write(self._write_neo4j, key, scope, payload_hash, nodes, edges, report)
            return {"state": "READY", **counts, "ledger_version": scope["ledger_version"], "ontology_version": self.catalog.version,
                    "snapshot_key": key}
        except ProjectionError:
            raise
        except Exception:
            return {"state": "ERROR", "code": "NEO4J_PROJECTION_FAILED"}

    def _write_neo4j(self, tx: Any, key: str, scope: dict, payload_hash: str, nodes: list, edges: list, report: dict) -> dict:
        existing = tx.run("MATCH (s:OntologySnapshot {key: $key}) RETURN s.payload_sha256 AS digest", key=key).single()
        if existing:
            if existing["digest"] != payload_hash:
                raise ProjectionError("IMMUTABLE_PROJECTION_CONFLICT")
        else:
            tx.run("CREATE (s:OntologySnapshot {key: $key, company_id: $company, run_id: $run, ledger_version: $version, ontology_version: $ontology, payload_sha256: $digest, validation_state: $validation})",
                key=key, company=scope["company_id"], run=scope["run_id"], version=scope["ledger_version"], ontology=self.catalog.version,
                digest=payload_hash, validation=report["state"]).consume()
            tx.run("MERGE (s:OntologySchema {version: $version}) ON CREATE SET s.definition_json = $schema, s.tbox_triples = $tbox_count, s.shape_triples = $shape_count",
                version=self.catalog.version, schema=_json(self.schema), tbox_count=self.schema["counts"]["tbox_triples"], shape_count=self.schema["counts"]["shape_triples"]).consume()
            rows = [{"id": node["id"], "uri": node["uri"], "type": node["ontology_type"], "payload": _json(node)} for node in nodes]
            tx.run("UNWIND $rows AS row CREATE (n:OntologyEntity {snapshot_key: $key, id: row.id, uri: row.uri, ontology_type: row.type, payload_json: row.payload})", rows=rows, key=key).consume()
            rows = [{"id": edge["id"], "source": edge["source"], "target": edge["target"], "predicate": edge["ontology_predicate"], "payload": _json(edge)} for edge in edges]
            tx.run("UNWIND $rows AS row MATCH (a:OntologyEntity {snapshot_key: $key, id: row.source}), (b:OntologyEntity {snapshot_key: $key, id: row.target}) CREATE (a)-[:ONTOLOGY_RELATION {snapshot_key: $key, id: row.id, predicate: row.predicate, payload_json: row.payload}]->(b)", rows=rows, key=key).consume()
        node_count = tx.run("MATCH (n:OntologyEntity {snapshot_key: $key}) RETURN count(n) AS count", key=key).single()["count"]
        edge_count = tx.run("MATCH ()-[r:ONTOLOGY_RELATION {snapshot_key: $key}]->() RETURN count(r) AS count", key=key).single()["count"]
        if node_count != len(nodes) or edge_count != len(edges):
            raise ProjectionError("PROJECTION_COUNT_MISMATCH")
        return {"node_count": node_count, "edge_count": edge_count}

    def read_projection(self, projection_metadata: dict) -> dict:
        """Read a complete live graph, checking the immutable ledger evidence.

        The saved projection supplies the exact scope and integrity baseline;
        returned node/edge values always come from Neo4j. A missing or altered
        projection cannot fall back to the saved JSON or a different version.
        """
        if self.driver is None:
            raise ProjectionError("NEO4J_NOT_CONFIGURED")
        if projection_metadata.get("state") != "READY":
            raise ProjectionError("PROJECTION_NOT_READY")
        try:
            scope = {name: projection_metadata[name] for name in ("company_id", "run_id", "ledger_version", "ontology_version", "mapping_version")}
            version = scope["ledger_version"]
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ValueError
            if not isinstance(scope["company_id"], str) or not scope["company_id"] or not isinstance(scope["run_id"], str) or not scope["run_id"]:
                raise ValueError
            if scope["ontology_version"] != self.catalog.version or scope["mapping_version"] != MAPPING_VERSION:
                raise ProjectionError("PROJECTION_SCHEMA_MISMATCH")
            key = _digest(scope)
            if projection_metadata["snapshot_key"] != key:
                raise ProjectionError("PROJECTION_SCOPE_MISMATCH")
            payload_hash = projection_metadata["payload_sha256"]
            if not isinstance(payload_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_hash):
                raise ValueError
            baseline = projection_metadata["graph"]
            expected_nodes = baseline["nodes"]
            expected_edges = baseline["edges"]
            if baseline.get("complete") is not True or baseline["node_count"] != len(expected_nodes) or baseline["edge_count"] != len(expected_edges):
                raise ValueError
            expected = self._graph_content(expected_nodes, expected_edges)
        except ProjectionError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProjectionError("INVALID_PROJECTION_METADATA") from None
        try:
            with self.driver.session(database=self.database) as session:
                nodes, edges = session.execute_read(self._read_neo4j, key, scope, payload_hash)
        except ProjectionError:
            raise
        except Exception:
            raise ProjectionError("NEO4J_READ_FAILED") from None
        if len(nodes) != len(expected_nodes) or len(edges) != len(expected_edges):
            raise ProjectionError("PROJECTION_COUNT_MISMATCH")
        if self._graph_content(nodes, edges) != expected:
            raise ProjectionError("PROJECTION_CONTENT_MISMATCH")
        # Preserve the layout order while returning only freshly read values.
        node_by_id = {node["id"]: node for node in nodes}
        edge_by_id = {edge["id"]: edge for edge in edges}
        return {"nodes": [node_by_id[node["id"]] for node in expected_nodes],
                "edges": [edge_by_id[edge["id"]] for edge in expected_edges],
                "complete": True, "node_count": len(nodes), "edge_count": len(edges)}

    @staticmethod
    def _graph_content(nodes: list, edges: list) -> str:
        try:
            if len({node["id"] for node in nodes}) != len(nodes) or len({edge["id"] for edge in edges}) != len(edges):
                raise ValueError
            return _digest({"nodes": sorted(nodes, key=lambda node: node["id"]), "edges": sorted(edges, key=lambda edge: edge["id"])})
        except (KeyError, TypeError, ValueError):
            raise ProjectionError("INVALID_PROJECTION_GRAPH") from None

    @staticmethod
    def _read_neo4j(tx: Any, key: str, scope: dict, payload_hash: str) -> tuple[list, list]:
        snapshot = tx.run(
            "MATCH (s:OntologySnapshot {key: $key, company_id: $company, run_id: $run, ledger_version: $version, ontology_version: $ontology, payload_sha256: $digest}) RETURN s.validation_state AS validation",
            key=key, company=scope["company_id"], run=scope["run_id"], version=scope["ledger_version"], ontology=scope["ontology_version"], digest=payload_hash,
        ).single()
        if not snapshot or snapshot["validation"] != "CONFORMS":
            raise ProjectionError("PROJECTION_SNAPSHOT_UNAVAILABLE")
        node_rows = tx.run("MATCH (n:OntologyEntity {snapshot_key: $key}) RETURN n.id AS id, n.uri AS uri, n.ontology_type AS ontology_type, n.payload_json AS payload", key=key)
        nodes = []
        try:
            for row in node_rows:
                node = json.loads(row["payload"])
                if any(node[field] != row[field] for field in ("id", "uri", "ontology_type")):
                    raise ValueError
                nodes.append(node)
            edge_rows = tx.run(
                "MATCH (a:OntologyEntity {snapshot_key: $key})-[r:ONTOLOGY_RELATION {snapshot_key: $key}]->(b:OntologyEntity {snapshot_key: $key}) RETURN r.id AS id, a.id AS source, b.id AS target, r.predicate AS ontology_predicate, r.payload_json AS payload",
                key=key,
            )
            edges = []
            for row in edge_rows:
                edge = json.loads(row["payload"])
                if any(edge[field] != row[field] for field in ("id", "source", "target", "ontology_predicate")):
                    raise ValueError
                edges.append(edge)
        except (KeyError, TypeError, ValueError):
            raise ProjectionError("PROJECTION_CONTENT_MISMATCH") from None
        return nodes, edges
