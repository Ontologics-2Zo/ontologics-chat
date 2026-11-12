"""OWL RL reasoning check on synthetic schema and snapshots (no private data)."""

import unittest

from rdflib import Graph, OWL, RDF, URIRef

from service.reasoning import reasoning_report

TBOX = '''@prefix ex: <urn:test:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Entity a owl:Class .
ex:Record a owl:Class ; rdfs:subClassOf ex:Entity .
ex:Receipt a owl:Class ; rdfs:subClassOf ex:Record .
ex:Document a owl:Class ; rdfs:subClassOf ex:Entity .
ex:Lot a owl:Class ; rdfs:subClassOf ex:Entity ; owl:disjointWith ex:Document .
ex:Version a owl:Class ; rdfs:subClassOf ex:Entity .
ex:recordedBy a owl:ObjectProperty ; rdfs:domain ex:Record ; rdfs:range ex:Version .
ex:receivesLot a owl:ObjectProperty ; rdfs:domain ex:Receipt ; rdfs:range ex:Lot .
ex:receivedIn a owl:ObjectProperty ; owl:inverseOf ex:receivesLot .
ex:describes a owl:ObjectProperty ; rdfs:domain ex:Document ; rdfs:range ex:Entity .
'''
EX = "urn:test:"


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.tbox = Graph().parse(data=TBOX, format="turtle")
        self.classes = set(self.tbox.subjects(RDF.type, OWL.Class))
        self.predicates = set(self.tbox.subjects(RDF.type, OWL.ObjectProperty))

    def check(self, types, edges, extra=()):
        uris = {name: URIRef("urn:entity:" + name) for name in types}
        data = Graph()
        for name, cls in types.items():
            data.add((uris[name], RDF.type, URIRef(EX + cls)))
        for source, predicate, target in edges:
            data.add((uris[source], URIRef(EX + predicate), uris[target]))
        for triple in extra:
            data.add(triple)
        return reasoning_report(self.tbox, data, uris, self.classes, self.predicates)

    def test_consistent_snapshot_adds_inverse_relationships_as_inferred(self):
        report = self.check({"rc": "Receipt", "lot": "Lot", "v": "Version"},
                            [("rc", "receivesLot", "lot"), ("rc", "recordedBy", "v")])
        self.assertEqual(report["state"], "CONSISTENT")
        self.assertEqual(report["type_mismatch_count"], 0)
        self.assertEqual(report["inferred_relationships"], [{"source": "lot", "predicate": "receivedIn", "target": "rc"}])
        self.assertFalse(report["blocks_ready"])

    def test_relationship_outside_its_domain_is_a_type_mismatch(self):
        # A document recorded by a version: the domain says Record, the declared type does not imply it.
        report = self.check({"doc": "Document", "v": "Version"}, [("doc", "recordedBy", "v")])
        self.assertEqual(report["state"], "TYPE_MISMATCH")
        self.assertEqual(report["type_mismatches"], [{"entity_id": "doc", "declared": "Document", "entailed": ["Record"], "via": ["recordedBy (출발)"]}])

    def test_reversed_relationship_is_caught_on_both_ends(self):
        report = self.check({"rc": "Receipt", "lot": "Lot"}, [("lot", "receivesLot", "rc")])
        flagged = {row["entity_id"]: row for row in report["type_mismatches"]}
        self.assertEqual(set(flagged), {"rc", "lot"})
        self.assertEqual(flagged["rc"]["via"], ["receivesLot (도착)"])

    def test_disjoint_classes_are_an_inconsistency(self):
        # A lot described as a document becomes a Document by domain, and Lot is disjoint with Document.
        report = self.check({"lot": "Lot", "v": "Version"}, [("lot", "describes", "v")])
        self.assertEqual(report["state"], "INCONSISTENT")
        self.assertEqual(report["inconsistencies"], [{"entity_id": "lot", "kind": "DISJOINT_CLASSES", "classes": ["Document", "Lot"]}])

    def test_literals_and_non_entity_triples_are_ignored(self):
        from rdflib import Literal
        stray = (URIRef("urn:statement:1"), RDF.type, RDF.Statement)
        value = (URIRef("urn:entity:v"), URIRef(EX + "quantity"), Literal("12"))
        report = self.check({"v": "Version"}, [], extra=(stray, value))
        self.assertEqual(report["state"], "CONSISTENT")
        self.assertEqual(report["entity_count"], 1)
        self.assertNotIn("12", repr(report))


if __name__ == "__main__":
    unittest.main()
