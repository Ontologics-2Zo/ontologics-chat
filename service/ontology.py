"""Read-only, lossless schema catalog; never an A-Box or ledger initializer.

Only manifest-pinned Turtle schema and JSON adapter inputs are accepted. Turtle
is parsed from already verified bytes, so neither parsing nor owl:imports can
fetch external resources. The catalog exposes every schema triple and adapter;
view filtering belongs to the caller and cannot change the source catalog.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from rdflib import BNode, Graph, Literal, OWL, RDF, RDFS, SH, URIRef


class OntologyInputError(ValueError):
    """A safe error code, without private paths or parser diagnostics."""


@dataclass(frozen=True)
class SchemaInput:
    path: str
    role: str  # tbox | shapes | adapters
    version: str


def _contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise OntologyInputError("INVALID_SOURCE_PATH")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise OntologyInputError("SOURCE_MISSING_OR_OUTSIDE_ROOT")
    return path


def _read_bounded(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        raise OntologyInputError("SOURCE_UNREADABLE") from None
    if len(data) > limit:
        raise OntologyInputError("SOURCE_SIZE_LIMIT")
    return data


def _term(value: Any) -> dict[str, str]:
    if isinstance(value, Literal):
        result = {"kind": "literal", "value": str(value)}
        if value.language:
            result["language"] = value.language
        if value.datatype:
            result["datatype"] = str(value.datatype)
        return result
    return {"kind": "blank" if isinstance(value, BNode) else "iri", "value": str(value)}


def _triples(graph: Graph) -> list[dict[str, dict[str, str]]]:
    return [
        {"subject": _term(s), "predicate": _term(p), "object": _term(o)}
        for s, p, o in sorted(graph, key=lambda triple: tuple(term.n3() for term in triple))
    ]


def _describe(graph: Graph, subject: Any) -> dict[str, Any]:
    properties: dict[str, list[dict[str, str]]] = {}
    for predicate, value in sorted(graph.predicate_objects(subject), key=lambda pair: (str(pair[0]), pair[1].n3())):
        properties.setdefault(str(predicate), []).append(_term(value))
    return {"id": str(subject), "term": _term(subject), "properties": properties}


class OntologyCatalog:
    """Immutable-by-interface catalog of all selected schema input bytes.

    This proves integrity/preservation of selected inputs, not equivalence to
    the complete official requirements, SHACL conformance, or projection health.
    """

    def __init__(self, tbox: Graph, shapes: Graph, adapters: list[dict[str, Any]], sources: list[dict[str, Any]]):
        self._tbox = tbox
        self._shapes = shapes
        self._adapters = adapters
        self._sources = sources
        # A byte-version identity is stable even when blank node labels differ
        # between parser invocations. Paths never enter the exported identity.
        identity = [{k: source[k] for k in ("role", "version", "sha256")} for source in sources]
        self.version = sha256(json.dumps(sorted(identity, key=lambda x: (x["role"], x["sha256"])), sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, root: Path | str, inputs: Iterable[SchemaInput], *, max_source_bytes: int = 4_000_000) -> "OntologyCatalog":
        root = Path(root).resolve()
        try:
            manifest = json.loads(_read_bounded(_contained(root, "SOURCE_MANIFEST.json"), 4_000_000))
            entries = manifest["files"]
            if not isinstance(entries, list):
                raise ValueError
            indexed = {entry["path"]: entry for entry in entries}
            if len(indexed) != len(entries):
                raise ValueError
        except OntologyInputError:
            raise
        except (ValueError, KeyError, TypeError):
            raise OntologyInputError("INVALID_MANIFEST") from None

        specs = list(inputs)
        if not specs or {spec.role for spec in specs} != {"tbox", "shapes", "adapters"}:
            raise OntologyInputError("INCOMPLETE_SCHEMA_INPUTS")
        if len({spec.path for spec in specs}) != len(specs):
            raise OntologyInputError("DUPLICATE_SCHEMA_INPUT")
        tbox, shapes = Graph(), Graph()
        adapters, sources = [], []
        for spec in specs:
            if not isinstance(spec.version, str) or not spec.version:
                raise OntologyInputError("MISSING_SOURCE_VERSION")
            entry = indexed.get(spec.path)
            if entry is None:
                raise OntologyInputError("UNTRACKED_SCHEMA_INPUT")
            data = _read_bounded(_contained(root, spec.path), max_source_bytes)
            digest = sha256(data).hexdigest()
            if entry.get("sha256") != digest or entry.get("bytes") != len(data):
                raise OntologyInputError("SOURCE_INTEGRITY_MISMATCH")
            if spec.role == "adapters":
                if Path(spec.path).suffix.lower() != ".json":
                    raise OntologyInputError("INVALID_ADAPTER_FORMAT")
                try:
                    adapter = json.loads(data)
                    if not isinstance(adapter, dict):
                        raise ValueError
                    for field in ("predicate_aliases", "type_aliases"):
                        mapping = adapter.get(field)
                        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                            raise ValueError
                except (ValueError, TypeError):
                    raise OntologyInputError("INVALID_ADAPTER_INPUT") from None
                adapters.append({"source_sha256": digest, "version": spec.version, "mapping": adapter})
                triple_count = None
            else:
                if Path(spec.path).suffix.lower() != ".ttl":
                    raise OntologyInputError("INVALID_SCHEMA_FORMAT")
                try:
                    # Do not pass the file name: parser errors and relative IRIs
                    # must not expose the local private input directory.
                    graph = Graph().parse(data=data, format="turtle", publicID="urn:ontologics:schema-input:" + digest)
                except Exception:
                    raise OntologyInputError("INVALID_TURTLE_INPUT") from None
                if spec.role == "tbox":
                    tbox += graph
                else:
                    shapes += graph
                triple_count = len(graph)
            sources.append({"role": spec.role, "version": spec.version, "sha256": digest, "bytes": len(data), "triples": triple_count})
        if not len(tbox) or not len(shapes):
            raise OntologyInputError("EMPTY_SCHEMA_INPUT")
        return cls(tbox, shapes, adapters, sources)

    @classmethod
    def from_private_root(cls, root: Path | str, *, source_version: str = "full-delivery-v1.3") -> "OntologyCatalog":
        """Discover only manifest-listed schema artifacts in the named slot.

        No graph.json, A-Box, TriG, expected results, or scenario run is loaded.
        Additional versions can use load() with explicit SchemaInput entries.
        """
        root = Path(root).resolve()
        try:
            entries = json.loads(_read_bounded(_contained(root, "SOURCE_MANIFEST.json"), 4_000_000))["files"]
            specs = []
            for entry in entries:
                if entry.get("slot") != source_version:
                    continue
                name = Path(entry["path"]).name.lower()
                role = None
                if re.fullmatch(r"tbox[^/]*\.ttl", name):
                    role = "tbox"
                elif re.fullmatch(r"(?:shacl[^/]*|[^/]*shapes[^/]*)\.ttl", name):
                    role = "shapes"
                elif name == "predicate_adapters.json":
                    role = "adapters"
                if role:
                    specs.append(SchemaInput(entry["path"], role, source_version))
        except OntologyInputError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OntologyInputError("INVALID_MANIFEST") from None
        return cls.load(root, specs)

    def tbox_graph(self) -> Graph:
        """Independent full graph for runtime querying; mutation cannot prune us."""
        return Graph() + self._tbox

    def shapes_graph(self) -> Graph:
        return Graph() + self._shapes

    def export(self) -> dict[str, Any]:
        """Full schema response for the authenticated API, with no private paths."""
        classes = set(self._tbox.subjects(RDF.type, OWL.Class)) | set(self._tbox.subjects(RDF.type, RDFS.Class))
        relations = set()
        for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, RDF.Property):
            relations.update(self._tbox.subjects(RDF.type, kind))
        shape_ids = set(self._shapes.subjects(RDF.type, SH.NodeShape)) | set(self._shapes.subjects(RDF.type, SH.PropertyShape))
        imports = sorted({str(value) for graph in (self._tbox, self._shapes) for value in graph.objects(None, OWL.imports)})
        return {
            "ontology_version": self.version,
            "scope": "schema_only",
            "integrity": "VERIFIED",
            "official_requirement_coverage": "NOT_RUN",
            "shacl_conformance": "NOT_RUN",
            "ledger_projection": "NOT_RUN",
            "imports_resolved": not imports,
            "unresolved_imports": imports,
            "sources": deepcopy(self._sources),
            "counts": {"classes": len(classes), "relations": len(relations), "shapes": len(shape_ids), "tbox_triples": len(self._tbox), "shape_triples": len(self._shapes)},
            "classes": [_describe(self._tbox, value) for value in sorted(classes, key=str)],
            "relations": [_describe(self._tbox, value) for value in sorted(relations, key=str)],
            "shapes": [_describe(self._shapes, value) for value in sorted(shape_ids, key=str)],
            "tbox_triples": _triples(self._tbox),
            "shape_triples": _triples(self._shapes),
            "adapters": deepcopy(self._adapters),
        }
