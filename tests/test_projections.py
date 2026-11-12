"""RDF/SHACL behavior against synthetic schema and ledger snapshots."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import BNode, Graph, OWL, RDF, SH, URIRef, XSD

from service.ontology import OntologyCatalog, SchemaInput
from service.projections import OntologyProjector, ProjectionError


TBOX = b'''@prefix ex: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Item a owl:Class . ex:Lot a owl:Class . ex:Future a owl:Class .
ex:lotOf a owl:ObjectProperty ; rdfs:domain ex:Lot ; rdfs:range ex:Item .
'''
SHAPES = b'''@prefix ex: <urn:test:> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:LotShape a sh:NodeShape ; sh:targetClass ex:Lot ;
 sh:property [ sh:path ex:lotOf ; sh:class ex:Item ; sh:minCount 1 ] ;
 sh:property [ sh:path ex:origin ; sh:minCount 1 ] .
'''


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        files = {"tbox.ttl": TBOX, "shapes.ttl": SHAPES, "adapters.json": json.dumps({"predicate_aliases": {"oldLotOf": "lotOf"}, "type_aliases": {"OldLot": "Lot"}}).encode()}
        manifest = []
        for name, data in files.items():
            (root / name).write_bytes(data)
            manifest.append({"path": name, "bytes": len(data), "sha256": sha256(data).hexdigest()})
        (root / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": manifest}))
        self.catalog = OntologyCatalog.load(root, [SchemaInput(name, role, "test-v1") for name, role in (("tbox.ttl", "tbox"), ("shapes.ttl", "shapes"), ("adapters.json", "adapters"))])
        self.projector = OntologyProjector(self.catalog)
        self.payload = {"metadata": {"company_id": "test-company", "run_id": "test-run"}, "entities": [
            {"id": "item", "type": "Item", "display_id": "item", "attributes": {"quantity": 12}, "evidence": {"source": "synthetic"}},
            {"id": "lot", "type": "OldLot", "display_id": "lot", "origin": "synthetic", "attributes": {}, "evidence": {"source": "synthetic"}}],
            "relationships": [{"id": "edge", "source": "lot", "target": "item", "predicate": "oldLotOf", "evidence": {"source": "synthetic"}}]}

    def project(self, payload=None, version=1, run="test-run"):
        return self.projector.project(payload or self.payload, version, "test-company", run)

    def test_explicit_aliases_preserve_full_snapshot_and_validate(self):
        result = self.project()
        self.assertEqual(result["validation"]["state"], "CONFORMS")
        self.assertEqual(result["state"], "NOT_READY")  # Neo4j was not run.
        self.assertEqual(result["neo4j"]["state"], "NOT_RUN")
        self.assertEqual(result["graph"]["node_count"], 2)
        self.assertEqual(result["graph"]["edge_count"], 1)
        self.assertEqual(result["graph"]["nodes"][1]["ontology_type"], "urn:test:Lot")
        self.assertEqual(result["graph"]["edges"][0]["ontology_predicate"], "urn:test:lotOf")
        rdf = Graph().parse(data=result["rdf_turtle"], format="turtle")
        self.assertEqual(len(list(rdf.triples((None, URIRef("urn:test:lotOf"), None)))), 1)
        self.assertEqual(self.catalog.export()["counts"]["classes"], 3)  # Future vocabulary is retained.
        # OWL RL check is reported next to SHACL and never decides READY.
        self.assertEqual(result["reasoning"]["state"], "CONSISTENT")
        self.assertFalse(result["reasoning"]["blocks_ready"])
        self.assertEqual(self.projector.reason(self.payload, 1, "test-company", "test-run")["state"], "CONSISTENT")

    def test_missing_required_fact_is_not_invented(self):
        payload = deepcopy(self.payload)
        del payload["entities"][1]["origin"]
        result = self.project(payload)
        self.assertEqual(result["validation"]["state"], "VIOLATIONS")
        self.assertEqual(result["state"], "NOT_READY")
        self.assertTrue(any(v["path"] == "urn:test:origin" for v in result["validation"]["violations"]))

    def test_unknown_class_and_predicate_remain_visible_but_not_ready(self):
        payload = deepcopy(self.payload)
        payload["entities"][1]["type"] = "UnreviewedClass"
        payload["relationships"][0]["predicate"] = "unreviewedRelation"
        result = self.project(payload)
        self.assertEqual(len(result["validation"]["unmapped"]), 2)
        self.assertEqual(result["graph"]["node_count"], 2)
        self.assertEqual(result["graph"]["edge_count"], 1)
        self.assertEqual(result["state"], "NOT_READY")

    def test_scope_and_version_are_exact(self):
        with self.assertRaisesRegex(ProjectionError, "^PAYLOAD_SCOPE_MISMATCH$"):
            self.project(run="another-run")
        payload = deepcopy(self.payload)
        payload["metadata"]["server_ledger_version"] = 9
        with self.assertRaisesRegex(ProjectionError, "^PAYLOAD_VERSION_MISMATCH$"):
            self.project(payload)
        self.assertNotEqual(self.project(version=1)["snapshot_key"], self.project(version=2)["snapshot_key"])

    def test_duplicate_or_dangling_identifiers_fail_closed(self):
        payload = deepcopy(self.payload)
        payload["entities"].append(payload["entities"][0])
        with self.assertRaisesRegex(ProjectionError, "^INVALID_OR_DUPLICATE_ENTITY_ID$"):
            self.project(payload)
        payload = deepcopy(self.payload)
        payload["relationships"][0]["target"] = "missing"
        with self.assertRaisesRegex(ProjectionError, "^DANGLING_RELATIONSHIP$"):
            self.project(payload)

    def test_source_attributes_and_evidence_are_not_mutated(self):
        original = deepcopy(self.payload)
        result = self.project()
        result["graph"]["nodes"][0]["attributes"].clear()
        self.assertEqual(self.payload, original)
        self.assertIn('"quantity":12', self.project()["rdf_turtle"].replace('\\"', '"'))

    def test_separate_relationship_evidence_is_not_collapsed(self):
        payload = deepcopy(self.payload)
        payload["relationships"].append({**deepcopy(payload["relationships"][0]), "id": "edge-2", "evidence": {"source": "second-synthetic-document"}})
        result = self.project(payload)
        rdf = Graph().parse(data=result["rdf_turtle"], format="turtle")
        self.assertEqual(result["graph"]["edge_count"], 2)
        self.assertEqual(len(list(rdf.subjects(RDF.type, RDF.Statement))), 2)

    def test_invalid_numeric_value_fails_without_logging_source_value(self):
        shape = URIRef("urn:test:LotShape")
        constraint = BNode()
        quantity = URIRef("urn:test:quantity")
        self.projector.shapes.add((shape, SH.property, constraint))
        self.projector.shapes.add((constraint, SH.path, quantity))
        self.projector.shapes.add((constraint, SH.datatype, XSD.integer))
        self.projector.scalar_paths.add(quantity)
        self.projector.datatypes[quantity] = XSD.integer
        payload = deepcopy(self.payload)
        payload["entities"][1]["attributes"]["quantity"] = "invalid-private-like-value"
        with self.assertNoLogs("rdflib", level="WARNING"):
            result = self.project(payload)
        self.assertEqual(result["validation"]["state"], "VIOLATIONS")
        self.assertTrue(any(v["path"] == str(quantity) for v in result["validation"]["violations"]))

    def install_fake_projection(self):
        metadata = self.project()
        metadata["state"] = "READY"
        driver = FakeReadDriver(metadata)
        self.projector.driver = driver
        return metadata, driver

    def test_live_read_returns_all_records_in_original_order(self):
        metadata, driver = self.install_fake_projection()
        driver.node_rows.reverse()
        graph = self.projector.read_projection(metadata)
        self.assertEqual(graph, metadata["graph"])
        self.assertIsNot(graph["nodes"][0], metadata["graph"]["nodes"][0])
        self.assertEqual(len(driver.queries), 3)
        self.assertTrue(all("$key" in query for query, _ in driver.queries))
        self.assertTrue(all(params["key"] == metadata["snapshot_key"] for _, params in driver.queries))

    def test_live_read_fails_if_neo4j_is_disconnected_or_snapshot_missing(self):
        metadata, driver = self.install_fake_projection()
        driver.fail = True
        with self.assertRaisesRegex(ProjectionError, "^NEO4J_READ_FAILED$"):
            self.projector.read_projection(metadata)
        driver.fail = False
        driver.missing = True
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_SNAPSHOT_UNAVAILABLE$"):
            self.projector.read_projection(metadata)

    def test_live_read_rejects_another_scope_or_schema(self):
        metadata, driver = self.install_fake_projection()
        changed = deepcopy(metadata)
        changed["company_id"] = "different-company"
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_SCOPE_MISMATCH$"):
            self.projector.read_projection(changed)
        changed = deepcopy(metadata)
        changed["ontology_version"] = "different-schema"
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_SCHEMA_MISMATCH$"):
            self.projector.read_projection(changed)
        changed = deepcopy(metadata)
        changed["payload_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_SNAPSHOT_UNAVAILABLE$"):
            self.projector.read_projection(changed)

    def test_live_read_rejects_missing_records_and_changed_content(self):
        metadata, driver = self.install_fake_projection()
        row = driver.node_rows.pop()
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_COUNT_MISMATCH$"):
            self.projector.read_projection(metadata)
        driver.node_rows.append(row)
        changed = json.loads(driver.node_rows[0]["payload"])
        changed["attributes"]["unexpected"] = "tampered"
        driver.node_rows[0]["payload"] = json.dumps(changed)
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_CONTENT_MISMATCH$"):
            self.projector.read_projection(metadata)

    def test_live_read_checks_physical_relationship_endpoints(self):
        metadata, driver = self.install_fake_projection()
        driver.edge_rows[0]["target"] = driver.edge_rows[0]["source"]
        with self.assertRaisesRegex(ProjectionError, "^PROJECTION_CONTENT_MISMATCH$"):
            self.projector.read_projection(metadata)


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeReadDriver:
    """A transaction double exposing only scoped read queries."""

    def __init__(self, metadata):
        self.metadata = metadata
        self.fail = False
        self.missing = False
        self.queries = []
        self.node_rows = [{"id": node["id"], "uri": node["uri"], "ontology_type": node["ontology_type"], "payload": json.dumps(node)} for node in metadata["graph"]["nodes"]]
        self.edge_rows = [{"id": edge["id"], "source": edge["source"], "target": edge["target"], "ontology_predicate": edge["ontology_predicate"], "payload": json.dumps(edge)} for edge in metadata["graph"]["edges"]]

    def session(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute_read(self, callback, *args):
        if self.fail:
            raise ConnectionError("synthetic connectivity failure")
        return callback(self, *args)

    def run(self, query, **params):
        self.queries.append((query, params))
        if "OntologySnapshot" in query:
            expected = {"key": self.metadata["snapshot_key"], "company": self.metadata["company_id"], "run": self.metadata["run_id"], "version": self.metadata["ledger_version"], "ontology": self.metadata["ontology_version"], "digest": self.metadata["payload_sha256"]}
            return FakeResult([] if self.missing or params != expected else [{"validation": "CONFORMS"}])
        if "ONTOLOGY_RELATION" in query:
            return FakeResult(self.edge_rows)
        return FakeResult(self.node_rows)


if __name__ == "__main__":
    unittest.main()
