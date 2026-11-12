"""OWL RL reasoning check over a projected ledger snapshot.

The check runs the OWL RL rules (owlrl) on the T-Box plus the snapshot's
entity types and entity-to-entity relationships, then reports three things:

- inconsistencies: an entity in two disjoint classes (owl:disjointWith) or in
  owl:Nothing. These are logical contradictions.
- type mismatches: reasoning gives an entity a class that its declared type
  does not imply. In OWL, rdfs:domain/range add types instead of rejecting a
  relationship, so a reversed or misplaced relationship silently changes what
  an entity is. SHACL with RDFS inference sees the added type and passes.
- inferred relationships: relationships the rules add (for example the other
  direction of an owl:inverseOf pair). They are marked as inferred and never
  become ledger facts or quantities.

Nothing here writes to the ledger or changes the projection READY rule; the
result is reported next to the SHACL report. Messages carry entity ids and
schema terms only, never attribute values or evidence text.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

import owlrl
from rdflib import Graph, OWL, RDF, RDFS, URIRef

ENGINE = "owlrl-OWL_RL"
LIST_LIMIT = 50


def _local(term: Any) -> str:
    return str(term).rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def reasoning_report(tbox: Graph, data: Graph, entity_uris: dict[str, URIRef], classes: set, predicates: set) -> dict[str, Any]:
    started = perf_counter()
    try:
        entities = set(entity_uris.values())
        reverse = {uri: entity_id for entity_id, uri in entity_uris.items()}
        declared: dict[URIRef, URIRef] = {}
        core = Graph()
        for triple in tbox:
            core.add(triple)
        asserted_edges = set()
        for subject, predicate, value in data:
            if subject not in entities:
                continue
            if predicate == RDF.type and value in classes:
                core.add((subject, predicate, value))
                declared[subject] = value
            elif predicate in predicates and value in entities:
                core.add((subject, predicate, value))
                asserted_edges.add((subject, predicate, value))
        owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False).expand(core)

        inconsistencies = []
        for first, second in tbox.subject_objects(OWL.disjointWith):
            for entity in core.subjects(RDF.type, first):
                if entity in entities and (entity, RDF.type, second) in core:
                    inconsistencies.append({"entity_id": reverse[entity], "kind": "DISJOINT_CLASSES",
                                            "classes": sorted([_local(first), _local(second)])})
        for entity in core.subjects(RDF.type, OWL.Nothing):
            if entity in entities:
                inconsistencies.append({"entity_id": reverse[entity], "kind": "NOTHING", "classes": []})

        supers: dict[URIRef, set] = {}
        def implied(cls: URIRef) -> set:
            if cls not in supers:
                supers[cls] = {cls} | set(tbox.transitive_objects(cls, RDFS.subClassOf))
            return supers[cls]

        mismatches = []
        for entity, cls in declared.items():
            extra = {c for c in core.objects(entity, RDF.type) if c in classes} - implied(cls)
            if not extra:
                continue
            via = set()
            for predicate, value in core.predicate_objects(entity):
                if (entity, predicate, value) in asserted_edges and set(tbox.objects(predicate, RDFS.domain)) & extra:
                    via.add(f"{_local(predicate)} (출발)")
            for subject, predicate in core.subject_predicates(entity):
                if (subject, predicate, entity) in asserted_edges and set(tbox.objects(predicate, RDFS.range)) & extra:
                    via.add(f"{_local(predicate)} (도착)")
            mismatches.append({"entity_id": reverse[entity], "declared": _local(cls),
                               "entailed": sorted(_local(c) for c in extra), "via": sorted(via)})

        inferred = sorted(({"source": reverse[s], "predicate": _local(p), "target": reverse[o]}
                           for s, p, o in core if s in entities and o in entities and p in predicates
                           and (s, p, o) not in asserted_edges), key=lambda e: (e["predicate"], e["source"], e["target"]))
        inconsistencies.sort(key=lambda e: (e["kind"], e["entity_id"]))
        mismatches.sort(key=lambda e: (e["declared"], e["entity_id"]))
        state = "INCONSISTENT" if inconsistencies else "TYPE_MISMATCH" if mismatches else "CONSISTENT"
        return {"state": state, "engine": ENGINE, "profile": "OWL 2 RL",
                "inconsistency_count": len(inconsistencies), "inconsistencies": inconsistencies[:LIST_LIMIT],
                "type_mismatch_count": len(mismatches), "type_mismatches": mismatches[:LIST_LIMIT],
                "inferred_relationship_count": len(inferred), "inferred_relationships": inferred[:LIST_LIMIT],
                "entity_count": len(entities), "relationship_count": len(asserted_edges),
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "blocks_ready": False}
    except Exception:
        # No graph content in the error; the SHACL result still stands on its own.
        return {"state": "ERROR", "engine": ENGINE, "code": "REASONING_FAILED", "blocks_ready": False}
