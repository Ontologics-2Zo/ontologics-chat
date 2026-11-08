"""Synthetic schema tests; no private source content is committed here."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from rdflib import Graph, OWL, RDF, URIRef

from service.ontology import OntologyCatalog, OntologyInputError, SchemaInput


TBOX = b'''@prefix ex: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Root a owl:Class .
ex:Child a owl:Class ; rdfs:subClassOf ex:Root ; rdfs:label "Child"@en .
ex:UnseenFutureClass a owl:Class .
ex:connects a owl:ObjectProperty ; rdfs:domain ex:Child ; rdfs:range ex:Root .
ex:quantity a owl:DatatypeProperty .
'''
SHAPES = b'''@prefix ex: <urn:test:> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
ex:Shape a sh:NodeShape ; sh:targetClass ex:Child ;
 sh:property [ sh:path ex:connects ; sh:minCount 1 ] .
'''
ADAPTERS = {"predicate_aliases": {"legacyConnects": "connects"}, "type_aliases": {"LegacyChild": "Child"}, "direction": "preserve"}


class OntologyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payloads = {"tbox.ttl": TBOX, "shapes.ttl": SHAPES, "predicate_adapters.json": json.dumps(ADAPTERS).encode()}
        self.write_sources()
        self.specs = [SchemaInput(name, role, "synthetic-v1") for name, role in (("tbox.ttl", "tbox"), ("shapes.ttl", "shapes"), ("predicate_adapters.json", "adapters"))]

    def write_sources(self):
        entries = []
        for name, data in self.payloads.items():
            (self.root / name).write_bytes(data)
            entries.append({"path": name, "slot": "synthetic-v1", "bytes": len(data), "sha256": sha256(data).hexdigest()})
        (self.root / "SOURCE_MANIFEST.json").write_text(json.dumps({"files": entries}))

    def load(self):
        return OntologyCatalog.load(self.root, self.specs)

    def test_exports_every_triple_class_relation_shape_and_adapter(self):
        catalog = self.load()
        result = catalog.export()
        self.assertEqual(result["counts"]["classes"], 3)
        self.assertEqual(result["counts"]["relations"], 2)
        self.assertEqual(len(result["tbox_triples"]), len(Graph().parse(data=TBOX, format="turtle")))
        self.assertEqual(len(result["shape_triples"]), len(Graph().parse(data=SHAPES, format="turtle")))
        self.assertEqual(result["adapters"][0]["mapping"], ADAPTERS)
        self.assertEqual(result["scope"], "schema_only")
        self.assertEqual(result["ledger_projection"], "NOT_RUN")
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_caller_cannot_prune_catalog_or_change_adapter(self):
        catalog = self.load()
        graph = catalog.tbox_graph()
        graph.remove((None, None, None))
        catalog.export()["adapters"][0]["mapping"]["predicate_aliases"].clear()
        self.assertEqual(catalog.export()["counts"]["classes"], 3)
        self.assertEqual(catalog.export()["adapters"][0]["mapping"], ADAPTERS)

    def test_byte_identity_stable_and_changes_with_shapes(self):
        version = self.load().version
        self.assertEqual(version, self.load().version)
        self.payloads["shapes.ttl"] += b'\n# reviewed constraint revision\n'
        self.write_sources()
        self.assertNotEqual(version, self.load().version)

    def test_tampered_source_is_rejected_without_leaking_path(self):
        (self.root / "tbox.ttl").write_bytes(TBOX + b'\n# tampered\n')
        with self.assertRaisesRegex(OntologyInputError, "^SOURCE_INTEGRITY_MISMATCH$"):
            self.load()

    def test_source_limit_is_enforced_before_parsing(self):
        with self.assertRaisesRegex(OntologyInputError, "^SOURCE_SIZE_LIMIT$"):
            OntologyCatalog.load(self.root, self.specs, max_source_bytes=16)

    def test_manifest_cannot_point_outside_input_root(self):
        manifest = json.loads((self.root / "SOURCE_MANIFEST.json").read_text())
        manifest["files"][0]["path"] = "../outside.ttl"
        (self.root / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
        specs = [SchemaInput("../outside.ttl", "tbox", "v1"), *self.specs[1:]]
        with self.assertRaisesRegex(OntologyInputError, "^SOURCE_MISSING_OR_OUTSIDE_ROOT$"):
            OntologyCatalog.load(self.root, specs)

    def test_unlisted_file_and_duplicate_manifest_fail_closed(self):
        with self.assertRaisesRegex(OntologyInputError, "^UNTRACKED_SCHEMA_INPUT$"):
            OntologyCatalog.load(self.root, [SchemaInput("new.ttl", "tbox", "v1"), *self.specs[1:]])
        manifest = json.loads((self.root / "SOURCE_MANIFEST.json").read_text())
        manifest["files"].append(manifest["files"][0])
        (self.root / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(OntologyInputError, "^INVALID_MANIFEST$"):
            self.load()

    def test_discovery_excludes_abox_future_runs_and_expected(self):
        self.payloads["abox.ttl"] = b'INVALID future ledger data'
        self.payloads["expected.json"] = b'INVALID expected answer'
        self.write_sources()
        catalog = OntologyCatalog.from_private_root(self.root, source_version="synthetic-v1")
        self.assertEqual(len(catalog.export()["sources"]), 3)
        self.assertEqual(catalog.export()["counts"]["classes"], 3)

    def test_remote_imports_remain_unresolved_and_are_never_loaded(self):
        self.payloads["tbox.ttl"] += b'\n<urn:test:ontology> owl:imports <https://invalid.example/schema.ttl> .'
        self.write_sources()
        result = self.load().export()
        self.assertFalse(result["imports_resolved"])
        self.assertEqual(result["unresolved_imports"], ["https://invalid.example/schema.ttl"])
        self.assertEqual(result["counts"]["classes"], 3)

    def test_parser_errors_are_sanitized(self):
        self.payloads["tbox.ttl"] = b'<private-content> deliberately invalid'
        self.write_sources()
        with self.assertRaisesRegex(OntologyInputError, "^INVALID_TURTLE_INPUT$"):
            self.load()

    def test_missing_shape_catalog_is_not_accepted(self):
        with self.assertRaisesRegex(OntologyInputError, "^INCOMPLETE_SCHEMA_INPUTS$"):
            OntologyCatalog.load(self.root, [self.specs[0], self.specs[2]])


if __name__ == "__main__":
    unittest.main()
