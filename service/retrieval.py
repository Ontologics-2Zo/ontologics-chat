"""Authorized snapshot-local evidence retrieval, separate from canonical facts.

R0 uses exact identifiers and BM25; R1 adds local pretrained multilingual dense
vectors and reciprocal-rank fusion; R3 also follows bounded, recorded edges.
R2/R4/R5 are explicitly unimplemented. No global corpus, DB write, model API,
generated rewrite, automatic mapping or approval exists in this module.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import math
import re
from threading import Lock
from typing import Literal
import unicodedata

from pydantic import Field, field_validator

from service.contracts import Contract, canonical_bytes, content_hash
from service.errors import ServiceError
from service.imports import ENTITY_FIELDS
from service.procurement_contracts import CONTENT_TYPES


INDEX_VERSION = "snapshot-hybrid/2"
PROFILES = {"R0": "IMPLEMENTED", "R1": "IMPLEMENTED", "R2": "NOT_IMPLEMENTED",
            "R3": "IMPLEMENTED", "R4": "NOT_IMPLEMENTED", "R5": "NOT_IMPLEMENTED"}
MAX_DOCUMENTS = 2500
MAX_CORPUS_BYTES = 4 * 1024 * 1024
MIN_COSINE = 0.45
_HASH = re.compile(r"^[0-9a-f]{64}$")
_BOM_NODES = {"SourceDocument", "SourceSnapshot", "SourceRow", "BOMSnapshot", "BOMLine",
              "BOMApplication", "DocumentVersion", "ApprovalGrant"}
_PROCUREMENT_NODES = {"SupplierOffer", "PurchaseRequest", "PurchaseOrder", "PurchaseOrderLine",
                      "PartsReceiptLine", "InspectionResult", "PartLot", "SourceDocument", "DocumentVersion", "ApprovalGrant", "Evidence"}
_ROW_FIELDS = {"row_id", "ordinal", "source_ordinal", "source_row", "sheet", "locator", "cells",
               "designators", "source_quantity", "source_quantity_text", "source_description"}
_DECISION_FIELDS = {"application_status", "quantity_per_unit", "unit", "item_id", "review_basis", "evidence_note"}


class RetrievalSearch(Contract):
    query: str = Field(min_length=1, max_length=1000)
    profile: Literal["R0", "R1", "R2", "R3", "R4", "R5"] = "R1"
    limit: int = Field(default=8, ge=1, le=20)
    context_entity_id: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("query")
    @classmethod
    def query_text(cls, value):
        if not value.strip() or any(ord(char) < 32 and char not in "\t\n" for char in value):
            raise ValueError("RETRIEVAL_QUERY_INVALID")
        return value.strip()


class RetrievalEvidence(Contract):
    evidence_id: str = Field(min_length=1, max_length=200)
    corpus_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def _normal(text):
    return unicodedata.normalize("NFKC", text).casefold()


def lexical_tokens(text):
    """Unicode words, intact part numbers/units, and Korean character bigrams.

    This is a declared tokenizer, not a morphological Korean analyzer. Numeric
    tokens remain retrieval cues and are never arithmetic/equivalence evidence.
    """
    words = re.findall(r"\w+(?:[-./:+]\w+)*|[%Ωµμ±]", _normal(text))
    result = list(words)
    for word in words:
        for run in re.findall(r"[가-힣]{2,}", word):
            result.extend("ko:" + run[i:i + 2] for i in range(len(run) - 1))
    return result


def _time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed


def _text(value):
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return "" if value is None else str(value)


def _visible(evidence, context):
    # Original source acquisition may be later than its business effective time.
    # BOM registration introduces knowledge without moving the opening balance.
    effective = evidence.get("effective_at")
    known = evidence.get("known_as_of", evidence.get("registered_at"))
    if effective is not None and _time(effective) > context.as_of:
        return False
    return known is not None and _time(known) <= context.known_as_of


@dataclass(frozen=True)
class _Corpus:
    version: str
    documents: tuple
    edges: tuple
    node_ids: frozenset
    meta: dict


def _build_corpus(payload, context):
    """Only the known INITIAL conversion and current posted BOM are eligible.

    Expected answers, private traces, arbitrary attachments and obsolete BOM
    versions never enter either lexical or embedding input. Every selected row
    keeps its original locator/hash; inferred review text is a separate document.
    """
    meta = payload["metadata"]
    entities = payload["entities"]
    if not isinstance(entities, list) or len(entities) > 20000:
        raise ValueError
    bom = payload.get("bom")
    active = None
    if bom:
        if (bom.get("status") != "POSTED_SOURCE" or bom.get("bom_id") != meta.get("active_bom_id")
                or bom.get("ledger_version", context.ledger_version + 1) > context.ledger_version):
            raise ValueError
        active = (bom["bom_id"], bom["bom_version"], bom["payload_hash"])
    procurement = {}
    for document in payload.get("procurement", {}).get("documents", []):
        if document["ledger_version"] > context.ledger_version:
            continue
        if (document["status"] != "POSTED" or document["kind"] not in CONTENT_TYPES
                or document["content"]["kind"] != document["kind"]
                or content_hash(document["content"]) != document["payload_hash"]
                or document["content"]["basis_ledger_version"] != document["ledger_version"] - 1
                or document["id"] in procurement):
            raise ValueError
        CONTENT_TYPES[document["kind"]].model_validate({key: value for key, value in document["content"].items()
                                                        if key not in {"resolved", "basis_ledger_version"}})
        procurement[document["id"]] = document

    def procurement_evidence(evidence):
        document = procurement.get(evidence.get("document_id"))
        return bool(document and evidence.get("record_type") == "development_procurement"
            and evidence.get("synthetic") is True
            and evidence.get("document_version") == document["version"]
            and evidence.get("payload_hash") == document["payload_hash"]
            and evidence.get("ledger_version") == document["ledger_version"]
            and _time(evidence["registered_at"]) <= context.as_of
            and _visible(evidence, context))

    selected, baseline = {}, []
    for entity in entities:
        evidence = entity.get("evidence", {})
        if entity.get("run_id") != meta["run_id"] or not _visible(evidence, context):
            continue
        if evidence.get("record_type") == "opening_balance":
            if (entity.get("type") not in ENTITY_FIELDS or entity.get("origin") != "synthetic_operation"
                    or evidence.get("source_sha256") != meta["source_sha256"]):
                continue
            baseline.append(entity)
        elif (active and entity.get("type") in _BOM_NODES
              and evidence.get("record_type") == "bom_registration"
              and (evidence.get("bom_id"), evidence.get("bom_version"), evidence.get("payload_hash")) == active
              and evidence.get("source_sha256") == bom["source"]["sha256"]):
            pass
        elif (entity.get("type") in _PROCUREMENT_NODES and entity.get("origin") == "development_workflow"
              and procurement_evidence(evidence)):
            pass
        else:
            continue
        if entity["id"] in selected:
            raise ValueError
        selected[entity["id"]] = entity

    documents = []
    def add(kind, original, source, node_ids, identifiers, origin, review_status, text):
        if (not isinstance(source.get("sha256"), str) or not _HASH.fullmatch(source["sha256"])
                or not isinstance(source.get("locator"), str) or not source["locator"]
                or not node_ids or any(node not in selected for node in node_ids)
                or len(text) > 32768):
            raise ValueError
        digest = content_hash(original)
        evidence_id = "retrieval:" + content_hash([context.company_id, meta["run_id"], context.read_snapshot_id,
                                                   kind, source, digest])
        documents.append({"evidence_id": evidence_id, "node_ids": sorted(node_ids), "kind": kind,
            "origin": origin, "review_status": review_status, "text": text,
            "source": source, "content_hash": digest, "original": deepcopy(original),
            "identifiers": sorted({_normal(value) for value in identifiers if isinstance(value, str) and value.strip()})})

    for entity in baseline:
        attrs = {key: value for key, value in entity.get("attributes", {}).items() if key in ENTITY_FIELDS[entity["type"]]}
        original = {"id": entity["id"], "type": entity["type"], "display_id": entity["display_id"],
                    "label": entity.get("label", entity["display_id"]), "attributes": attrs}
        add("ledger_entity", original, {"sha256": entity["evidence"]["source_sha256"],
            "locator": entity["evidence"]["source_locator"], "source_version": meta.get("source_snapshot_id")},
            [entity["id"]], [entity["id"], entity["display_id"]], "synthetic_operation", "CANONICAL_SYNTHETIC",
            _text(original))

    if bom:
        if _time(bom["registered_at"]) > context.known_as_of:
            # A malformed Host snapshot must not quietly yield only baseline.
            raise ValueError
        source_rows = {}
        applications = {}
        for identifier, entity in selected.items():
            if entity["type"] == "SourceRow":
                row_id = entity["attributes"]["row_id"]
                if row_id in source_rows:
                    raise ValueError
                source_rows[row_id] = identifier
            elif entity["type"] == "BOMApplication":
                applications.setdefault(entity["evidence"]["source_locator"], []).append(identifier)
        if len(source_rows) != len(bom["rows"]):
            raise ValueError
        for row in bom["rows"]:
            identifier = source_rows[row["row_id"]]
            original = {key: deepcopy(value) for key, value in row.items() if key in _ROW_FIELDS}
            stored = {key: value for key, value in selected[identifier]["attributes"].items() if key in _ROW_FIELDS}
            if original != stored or row["locator"] != selected[identifier]["evidence"]["source_locator"]:
                raise ValueError
            source = {"sha256": bom["source"]["sha256"], "locator": row["locator"], "row_id": row["row_id"],
                      "source_id": bom["source"].get("source_id"), "bom_id": bom["bom_id"], "bom_version": bom["bom_version"]}
            identifiers = [row["row_id"], *row.get("designators", []), row.get("source_description", "")]
            text = _text([row.get("source_description"), row.get("designators"), row.get("cells")])
            add("source_row", original, source, [identifier], identifiers, "actual_source", "SOURCE_ONLY", text or row["row_id"])
            decision = {key: value for key, value in row.get("decision", {}).items() if key in _DECISION_FIELDS}
            application_ids = applications.get(row["locator"], [])
            if application_ids:
                if len(application_ids) != 1 or selected[application_ids[0]]["attributes"] != decision:
                    raise ValueError
                origin = "synthetic_assumption" if decision.get("review_basis") == "DEMO_ASSUMPTION" else "reviewed_application"
                if selected[application_ids[0]]["origin"] != origin:
                    raise ValueError
                add("application_review", decision, source, application_ids, [row["row_id"], decision.get("item_id")],
                    origin, decision.get("review_basis", "UNREVIEWED"), _text(decision))

    for identifier, document in procurement.items():
        nodes = [entity for entity in selected.values()
                 if entity.get("attributes", {}).get("document_id") == identifier
                 and entity["attributes"].get("kind") == document["kind"]
                 and entity["attributes"].get("status") == "POSTED"
                 and procurement_evidence(entity.get("evidence", {}))]
        if len(nodes) != 1:
            raise ValueError
        original = document["content"]
        # Canonical signed content is returned only by an explicit evidence-open.
        # Search text uses declared typed fields, never arbitrary nested metadata
        # or a copied parent's entire resolved payload/evaluation attachments.
        fields = {key: value for key, value in original.items() if key not in {"resolved", "basis_ledger_version"}}
        resolved = original["resolved"]
        summary = {key: resolved[key] for key in ("item_id", "supplier_id", "unit", "confirmation_mode",
            "supplier_confirmation", "transmission_status") if key in resolved}
        source = {"sha256": document["payload_hash"], "source_kind": "internal_development_document",
                  "locator": "procurement.documents/" + identifier + "/content", "document_id": identifier,
                  "document_version": document["version"], "source_version": str(document["version"]),
                  "ledger_version": document["ledger_version"], "registered_at": nodes[0]["evidence"]["registered_at"]}
        identifiers = [identifier, *[value for key, value in fields.items() if key.endswith("_ref")],
                       *[summary.get(key) for key in ("item_id", "supplier_id")]]
        add("development_document", original, source, [nodes[0]["id"]], identifiers, "development_workflow",
            "CANCELLED_DEVELOPMENT_DOCUMENT" if document.get("cancelled_by") else "POSTED_DEVELOPMENT_DOCUMENT",
            _text([fields, summary]))

    edges = []
    for edge in payload["relationships"]:
        if edge.get("source") not in selected or edge.get("target") not in selected:
            continue
        evidence = edge.get("evidence", {})
        if not _visible(evidence, context):
            continue
        if evidence.get("record_type") == "opening_balance" and evidence.get("source_sha256") == meta["source_sha256"]:
            pass
        elif procurement_evidence(evidence):
            pass
        elif not (active and evidence.get("record_type") == "bom_registration"
                  and (evidence.get("bom_id"), evidence.get("bom_version"), evidence.get("payload_hash")) == active
                  and evidence.get("source_sha256") == bom["source"]["sha256"]):
            continue
        edges.append({key: deepcopy(edge[key]) for key in ("id", "source", "target", "predicate", "evidence")})
    documents.sort(key=lambda doc: doc["evidence_id"])
    edges.sort(key=lambda edge: edge["id"])
    if (len(documents) > MAX_DOCUMENTS or len(canonical_bytes(documents)) > MAX_CORPUS_BYTES
            or len({doc["evidence_id"] for doc in documents}) != len(documents)):
        raise ValueError
    scope = {"company_id": context.company_id, "run_id": meta["run_id"], "snapshot_id": context.read_snapshot_id,
             "ledger_version": context.ledger_version, "projection_version": context.projection_version,
             "ontology_version": context.ontology_version, "rule_version": context.rule_version,
             "as_of": context.as_of.isoformat(), "known_as_of": context.known_as_of.isoformat()}
    version = content_hash([INDEX_VERSION, scope, documents, edges])
    return _Corpus(version, tuple(documents), tuple(edges), frozenset(selected), scope)


def _neighbors(seeds, edges, *, hops=2, maximum=40):
    visited, selected = set(seeds), []
    for _ in range(hops):
        frontier = set(visited)
        for edge in edges:
            if edge["source"] not in frontier and edge["target"] not in frontier:
                continue
            additions = {edge["source"], edge["target"]} - visited
            if len(visited) + len(additions) > maximum:
                continue
            visited.update(additions)
            if edge not in selected and len(selected) < 60:
                selected.append(edge)
        if visited == frontier:
            break
    return visited, selected


class _Index:
    def __init__(self, corpus, embedding):
        self.corpus = corpus
        self.tokens = [Counter(lexical_tokens(doc["text"])) for doc in corpus.documents]
        self.frequency = Counter(token for tokens in self.tokens for token in tokens)
        self.average = sum(sum(tokens.values()) for tokens in self.tokens) / max(len(self.tokens), 1)
        self.vectors = None
        self.embedding = embedding
        self.lock = Lock()

    def dense(self):
        with self.lock:
            if self.vectors is None:
                vectors = self.embedding.encode([doc["text"] for doc in self.corpus.documents])
                self.vectors = self._validated(vectors, len(self.corpus.documents))
            return self.vectors

    def _validated(self, vectors, count):
        dimension = self.embedding.provenance["dimensions"]
        if len(vectors) != count:
            raise ValueError
        result = []
        for vector in vectors:
            if len(vector) != dimension or not all(math.isfinite(float(v)) for v in vector):
                raise ValueError
            norm = math.sqrt(sum(float(v) ** 2 for v in vector))
            if norm <= 0:
                raise ValueError
            result.append(tuple(float(v) / norm for v in vector))
        return result

    def rank(self, query, indices, use_dense):
        normalized = _normal(query)
        query_tokens = set(lexical_tokens(query))
        exact, lexical, dense = [], [], []
        for i in indices:
            doc = self.corpus.documents[i]
            if any(identifier == normalized or (len(identifier) >= 2 and re.search(
                    r"(?<!\w)" + re.escape(identifier) + r"(?!\w)", normalized)) for identifier in doc["identifiers"]):
                exact.append((i, 1.0))
            tokens = self.tokens[i]
            length, score = sum(tokens.values()), 0.0
            for term in query_tokens:
                tf = tokens.get(term, 0)
                if tf:
                    df = self.frequency[term]
                    idf = math.log(1 + (len(self.tokens) - df + 0.5) / (df + 0.5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(self.average, 1)))
            if score > 0:
                lexical.append((i, score))
        if use_dense:
            vectors = self.dense()
            query_vector = self._validated(self.embedding.encode([query]), 1)[0]
            for i in indices:
                score = sum(a * b for a, b in zip(query_vector, vectors[i]))
                if score >= MIN_COSINE:
                    dense.append((i, score))
        rankings = {"exact": sorted(exact, key=lambda pair: (-pair[1], pair[0])),
                    "lexical": sorted(lexical, key=lambda pair: (-pair[1], pair[0])),
                    "vector": sorted(dense, key=lambda pair: (-pair[1], pair[0]))}
        fused, modes = {}, {}
        for mode, ranking in rankings.items():
            for rank, (i, _) in enumerate(ranking[:100], 1):
                fused[i] = fused.get(i, 0) + (3 if mode == "exact" else 1) / (60 + rank)
                modes.setdefault(i, []).append(mode)
        return sorted(fused, key=lambda i: (-fused[i], i)), fused, modes


class RetrievalService:
    def __init__(self, authorize, embedding=None, *, cache_size=4):
        if not callable(authorize) or type(cache_size) is not int or not 1 <= cache_size <= 8:
            raise ValueError("RETRIEVAL_CONFIGURATION_INVALID")
        self.authorize, self.embedding, self.cache_size = authorize, embedding, cache_size
        self._cache, self._lock = OrderedDict(), Lock()

    def _authorize(self, principal, payload, context):
        # Rechecked even for cached hits and original evidence opens. Nothing is
        # embedded or searched before this authoritative permission boundary.
        for permission in ("ledger.read", "document.read", "graph.read"):
            self.authorize(principal, context.company_id, permission)
        if principal.company_id != context.company_id or principal.identity_revision != context.context_revision:
            raise ServiceError("RETRIEVAL_IDENTITY_CHANGED", 409)
        try:
            meta, projection = payload["metadata"], payload["projection"]
            if (context.as_of is None or context.known_as_of is None
                    or meta["company_id"] != context.company_id or meta["snapshot_id"] != context.read_snapshot_id
                    or meta["server_ledger_version"] != context.ledger_version
                    or projection["state"] != "READY" or projection["company_id"] != context.company_id
                    or projection["run_id"] != meta["run_id"] or projection["ledger_version"] != context.ledger_version
                    or projection["projection_version"] != context.projection_version
                    or projection["ontology_version"] != context.ontology_version
                    or meta["rule_version"] != context.rule_version
                    or _time(meta["effective_at"]) != context.as_of or _time(meta["known_as_of"]) != context.known_as_of):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ServiceError("RETRIEVAL_SNAPSHOT_MISMATCH", 409) from None

    def _index(self, payload, context):
        corpus = _build_corpus(payload, context)
        with self._lock:
            if corpus.version not in self._cache:
                self._cache[corpus.version] = _Index(corpus, self.embedding)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            self._cache.move_to_end(corpus.version)
            return self._cache[corpus.version]

    def _result(self, context, profile, *, index=None, status, code=None):
        return {"status": status, "code": code, "profile": profile, "profile_status": deepcopy(PROFILES),
                "hits": [], "graph": {"node_ids": [], "edges": [], "max_hops": 2},
                "meta": {**(deepcopy(index.corpus.meta) if index else {
                    "company_id": context.company_id, "snapshot_id": context.read_snapshot_id, "ledger_version": context.ledger_version,
                    "projection_version": context.projection_version, "ontology_version": context.ontology_version,
                    "as_of": context.as_of.isoformat(), "known_as_of": context.known_as_of.isoformat()}),
                    "corpus_version": index.corpus.version if index else None, "index_version": INDEX_VERSION,
                    "document_count": len(index.corpus.documents) if index else None,
                    "embedding": deepcopy(self.embedding.provenance) if self.embedding else None,
                    "lexical": "BM25 k1=1.2 b=0.75; unicode-parts-korean-bigrams/1",
                    "fusion": "weighted_reciprocal_rank k=60; exact=3 lexical=1 vector=1",
                    "vector_min_cosine": MIN_COSINE, "external_model_calls": 0},
                "limitations": ["RETRIEVAL_IS_NOT_CANONICAL_QUANTITY_OR_IDENTITY_APPROVAL",
                    "INITIAL_CURRENT_POSTED_BOM_AND_INTERNAL_DEVELOPMENT_DOCUMENTS_ONLY", "NO_GENERATED_QUERY_OR_CHUNK_CONTEXT",
                    "DEVELOPMENT_DOCUMENTS_ARE_NOT_EXTERNAL_SUPPLIER_COMMITMENTS",
                    "UNREVIEWED_AND_SYNTHETIC_EVIDENCE_REMAIN_SEPARATE"]}

    @staticmethod
    def _hit(doc, modes, score):
        return {**deepcopy({key: value for key, value in doc.items() if key not in {"original", "identifiers"}}),
                "match_modes": list(modes), "score": round(score, 8)}

    def search(self, principal, payload, context, request: RetrievalSearch):
        self._authorize(principal, payload, context)
        if PROFILES[request.profile] != "IMPLEMENTED":
            return self._result(context, request.profile, status="PROFILE_NOT_IMPLEMENTED", code="RETRIEVAL_PROFILE_NOT_IMPLEMENTED")
        try:
            index = self._index(payload, context)
        except (KeyError, TypeError, ValueError, AttributeError):
            return self._result(context, request.profile, status="INDEX_INCOMPLETE", code="RETRIEVAL_CORPUS_INCOMPLETE")
        corpus = index.corpus
        if request.context_entity_id and request.context_entity_id not in corpus.node_ids:
            raise ServiceError("RETRIEVAL_CONTEXT_UNAVAILABLE", 404)
        if request.profile != "R0" and self.embedding is None:
            return self._result(context, request.profile, index=index, status="INDEX_INCOMPLETE", code="RETRIEVAL_EMBEDDING_UNAVAILABLE")
        allowed = corpus.node_ids
        if request.context_entity_id:
            allowed, _ = _neighbors([request.context_entity_id], corpus.edges, hops=3, maximum=200)
        indices = [i for i, doc in enumerate(corpus.documents) if set(doc["node_ids"]) & allowed]
        try:
            ranked, scores, modes = index.rank(request.query, indices, request.profile != "R0") if indices else ([], {}, {})
        except ServiceError as error:
            incomplete = error.code in {"RETRIEVAL_EMBEDDING_INPUT_INVALID", "RETRIEVAL_EMBEDDING_WINDOW_LIMIT"}
            return self._result(context, request.profile, index=index,
                status="INDEX_INCOMPLETE" if incomplete else "INDEX_ERROR", code="RETRIEVAL_EMBEDDING_FAILED")
        except Exception:
            return self._result(context, request.profile, index=index, status="INDEX_ERROR", code="RETRIEVAL_INDEX_FAILED")
        result = self._result(context, request.profile, index=index, status="HIT" if ranked else "NO_HIT")
        selected = ranked[:request.limit]
        if request.profile == "R3" and ranked:
            seeds = {node for i in ranked[:3] for node in corpus.documents[i]["node_ids"]}
            reached, edges = _neighbors(seeds, corpus.edges)
            result["graph"] = {"node_ids": sorted(reached), "edges": deepcopy(edges), "max_hops": 2}
            for i in indices:
                if i not in selected and set(corpus.documents[i]["node_ids"]) & reached and len(selected) < request.limit:
                    selected.append(i)
                    modes[i] = ["graph"]
                    scores[i] = 0.0
        result["hits"] = [self._hit(corpus.documents[i], modes[i], scores[i]) for i in selected]
        result["context_entity_id"] = request.context_entity_id
        # A revocation during expensive local inference cannot expose cached text.
        self._authorize(principal, payload, context)
        return result

    def evidence(self, principal, payload, context, request: RetrievalEvidence):
        self._authorize(principal, payload, context)
        try:
            index = self._index(payload, context)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ServiceError("RETRIEVAL_CORPUS_INCOMPLETE", 409) from None
        if request.corpus_version != index.corpus.version:
            raise ServiceError("RETRIEVAL_CORPUS_CHANGED", 409)
        doc = next((doc for doc in index.corpus.documents if doc["evidence_id"] == request.evidence_id), None)
        if doc is None:
            raise ServiceError("RETRIEVAL_EVIDENCE_UNAVAILABLE", 404)
        return {"status": "HIT", "evidence": self._hit(doc, [], 0), "original": deepcopy(doc["original"]),
                "meta": self._result(context, "R0", index=index, status="HIT")["meta"]}
