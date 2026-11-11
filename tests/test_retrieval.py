"""Synthetic, independent ACL/time/provenance and hybrid retrieval cases."""

from copy import deepcopy
from datetime import datetime
import unittest

from service.contracts import content_hash
from service.errors import ServiceError
from service.gateway import ToolContext
from service.identity import Principal
from service.ledger import apply_bom_event
from service.procurement_state import apply_procurement_event
from service.retrieval import RetrievalEvidence, RetrievalSearch, RetrievalService, lexical_tokens
from tests.test_bom_projection import initial_payload, registration_event


def retrieval_fixture(*, with_bom=True, previous=None):
    payload = previous or initial_payload(company_id="retrieval-company")
    if with_bom:
        event = registration_event(payload)
        for i, row in enumerate(event["payload"]["content"]["rows"]):
            row["designators"] = [f"DEMO-R{i+1}"]
            row["source_description"] = ["precision resistor 10kΩ", "ceramic capacitor", "power connector", "test switch"][i]
            row["cells"] = [row["source_quantity"], row["source_description"], row["designators"][0]]
        digest = content_hash(event["payload"]["content"])
        event["payload"]["payload_hash"] = digest
        event["payload"]["approval"]["approved_hash"] = digest
        payload = apply_bom_event(payload, event)
    version = payload["metadata"]["server_ledger_version"]
    payload["metadata"]["snapshot_id"] = content_hash([version, payload.get("bom", {}).get("payload_hash")])
    payload["projection"] = {"state": "READY", "company_id": "retrieval-company", "run_id": "INITIAL",
                             "ledger_version": version, "projection_version": version, "ontology_version": "test-ontology"}
    principal = Principal("reader", "retrieval-company", ("local",), ("ledger.read", "document.read", "graph.read"),
                          1, "test-identity", "test-session")
    meta = payload["metadata"]
    context = ToolContext(execution_id="test-execution", request_id="test-request", context_revision=principal.identity_revision,
        company_id=principal.company_id, read_snapshot_id=meta["snapshot_id"], ledger_version=version,
        projection_version=version, ontology_version="test-ontology", rule_version=meta["rule_version"],
        as_of=datetime.fromisoformat(meta["effective_at"]), known_as_of=datetime.fromisoformat(meta["known_as_of"]))
    return payload, principal, context


class Authorization:
    def __init__(self):
        self.revoked, self.calls = False, []

    def __call__(self, principal, company, permission):
        self.calls.append((principal.actor_id, company, permission))
        if self.revoked or principal.company_id != company or permission not in principal.permissions:
            raise ServiceError("FORBIDDEN", 403)


class SyntheticEmbedding:
    """Explicit unit double, never described as a pretrained semantic model."""
    provenance = {"implementation": "test-only-independent-vectors", "execution_mode": "synthetic_test", "dimensions": 3}

    def __init__(self):
        self.calls = []
        self.fail = False
        self.hook = None

    def encode(self, texts):
        self.calls.append(list(texts))
        if self.hook:
            self.hook()
        if self.fail:
            raise RuntimeError("private diagnostic must never escape")
        return [[1, 0, 0] if "resistor" in text or "저항" in text else [0, 1, 0] if "capacitor" in text else [0, 0, 1]
                for text in texts]


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.payload, self.principal, self.context = retrieval_fixture()
        self.authorize = Authorization()
        self.embedding = SyntheticEmbedding()
        self.service = RetrievalService(self.authorize, self.embedding)

    def search(self, query, profile="R0", **kwargs):
        return self.service.search(self.principal, self.payload, self.context,
                                   RetrievalSearch(query=query, profile=profile, **kwargs))

    def test_exact_identifier_and_declared_bm25_tokenization(self):
        result = self.search("DEMO-R1")
        self.assertEqual(result["status"], "HIT")
        self.assertEqual(result["hits"][0]["kind"], "source_row")
        self.assertIn("exact", result["hits"][0]["match_modes"])
        tokens = lexical_tokens("저항기의 ABC-12.5 10kΩ 0.1µF ±1%")
        for value in ("ko:저항", "abc-12.5", "10kω", "0.1μf", "±", "%"):
            self.assertIn(value, tokens)
        self.assertEqual(self.embedding.calls, [])

    def test_slash_joined_specs_are_also_indexed_by_part(self):
        tokens = lexical_tokens("100nF/16V/10%/X7R/C0402 C8")
        for value in ("100nf/16v/10", "100nf", "16v", "x7r", "c0402", "c8"):
            self.assertIn(value, tokens)
        self.assertEqual(lexical_tokens("ABC-12.5"), ["abc-12.5"])

    def test_entity_text_carries_korean_class_and_status_names(self):
        from service.retrieval import _korean_hints
        hints = _korean_hints({"type": "InternalItem", "attributes": {"substitute_status": "NOT_REVIEWED", "location": ""}})
        self.assertIn("사내 품목", hints); self.assertIn("대체 후보", hints); self.assertIn("검토되지 않음", hints)
        self.assertNotIn("보관 위치", hints)
        self.assertEqual(_korean_hints({"type": "Unknown", "attributes": {}}), "")

    def test_dense_can_recover_without_lexical_match_and_reports_provenance(self):
        self.assertEqual(self.search("저항")["status"], "NO_HIT")
        result = self.search("저항", "R1")
        self.assertEqual(result["hits"][0]["text"].split()[0], "precision")
        self.assertEqual(result["hits"][0]["match_modes"], ["vector"])
        self.assertEqual(result["meta"]["embedding"]["execution_mode"], "synthetic_test")
        self.assertEqual(result["meta"]["external_model_calls"], 0)

    def test_company_snapshot_version_projection_and_time_guard_before_embedding(self):
        for updates in ({"company_id": "other-company"}, {"read_snapshot_id": "other-snapshot"}, {"ledger_version": 99},
                        {"projection_version": 99}, {"ontology_version": "other"}, {"rule_version": "other"},
                        {"known_as_of": datetime.fromisoformat("2025-02-04T00:00:00+00:00")}, {"context_revision": "old-identity"}):
            with self.subTest(updates=updates), self.assertRaises(ServiceError):
                self.service.search(self.principal, self.payload, self.context.model_copy(update=updates),
                                    RetrievalSearch(query="resistor"))
        self.assertEqual(self.embedding.calls, [])

    def test_original_open_is_fresh_authorized_and_exact_corpus_bound(self):
        found = self.search("DEMO-R1")
        hit = found["hits"][0]
        request = RetrievalEvidence(evidence_id=hit["evidence_id"], corpus_version=found["meta"]["corpus_version"])
        result = self.service.evidence(self.principal, self.payload, self.context, request)
        self.assertEqual(result["original"]["cells"], self.payload["bom"]["rows"][0]["cells"])
        self.assertEqual(content_hash(result["original"]), hit["content_hash"])
        self.assertEqual(hit["source"]["sha256"], self.payload["bom"]["source"]["sha256"])
        self.authorize.revoked = True
        with self.assertRaises(ServiceError):
            self.service.evidence(self.principal, self.payload, self.context, request)
        self.authorize.revoked = False
        with self.assertRaisesRegex(ServiceError, "RETRIEVAL_CORPUS_CHANGED"):
            self.service.evidence(self.principal, self.payload, self.context,
                request.model_copy(update={"corpus_version": "0" * 64}))
        with self.assertRaisesRegex(ServiceError, "RETRIEVAL_EVIDENCE_UNAVAILABLE"):
            self.service.evidence(self.principal, self.payload, self.context,
                request.model_copy(update={"evidence_id": "retrieval:other"}))

    def test_revocation_during_dense_inference_returns_no_evidence(self):
        self.embedding.hook = lambda: setattr(self.authorize, "revoked", True)
        with self.assertRaisesRegex(ServiceError, "FORBIDDEN"):
            self.search("resistor", "R1")

    def test_gold_trace_unknown_fields_and_future_entities_are_never_embedded(self):
        self.payload["expected_answers"] = {"answer": "SECRET_GOLD_SENTINEL"}
        self.payload["traces"] = ["PRIVATE_TRACE_SENTINEL"]
        entity = deepcopy(next(e for e in self.payload["entities"] if e["type"] == "InternalItem"))
        entity["id"] = "future-entity"
        entity["label"] = "FUTURE_SENTINEL"
        entity["evidence"]["known_as_of"] = "2099-01-01T00:00:00+00:00"
        self.payload["entities"].append(entity)
        self.payload["entities"][0]["attributes"]["expected_answer"] = "ATTR_GOLD_SENTINEL"
        self.search("resistor", "R1")
        submitted = str(self.embedding.calls)
        for forbidden in ("SECRET_GOLD_SENTINEL", "PRIVATE_TRACE_SENTINEL", "FUTURE_SENTINEL", "ATTR_GOLD_SENTINEL"):
            self.assertNotIn(forbidden, submitted)

    def test_source_and_application_notes_have_distinct_origins_no_auto_approval(self):
        source = self.search("DEMO-R1")["hits"][0]
        self.assertEqual(source["origin"], "actual_source")
        self.assertEqual(source["review_status"], "SOURCE_ONLY")
        review = self.search("Independently authored conditional test decision")
        applications = [hit for hit in review["hits"] if hit["kind"] == "application_review"]
        self.assertTrue(applications)
        self.assertTrue(all(hit["origin"] == "synthetic_assumption" for hit in applications))
        self.assertNotIn("decision", self.service.evidence(self.principal, self.payload, self.context,
            RetrievalEvidence(evidence_id=source["evidence_id"], corpus_version=review["meta"]["corpus_version"]))["original"])

    def test_graph_uses_actual_edges_and_previous_bom_is_excluded(self):
        result = self.search("DEMO-R1", "R3", limit=20)
        self.assertTrue(result["graph"]["edges"])
        actual_ids = {edge["id"] for edge in self.payload["relationships"]}
        self.assertTrue(all(edge["id"] in actual_ids for edge in result["graph"]["edges"]))
        old_bom = self.payload["bom"]["bom_id"]
        self.payload, self.principal, self.context = retrieval_fixture(previous=self.payload)
        result = self.search("DEMO-R1", "R3", limit=20)
        self.assertNotEqual(old_bom, self.payload["bom"]["bom_id"])
        self.assertTrue(all(hit["source"].get("bom_id") != old_bom for hit in result["hits"]))
        self.assertTrue(all(edge["evidence"].get("bom_id") != old_bom for edge in result["graph"]["edges"]))

    def test_graph_reached_documents_only_fill_free_slots(self):
        result = self.search("DEMO-R1", "R3", limit=20)
        modes = [hit["match_modes"] for hit in result["hits"]]
        first_graph = next((n for n, mode in enumerate(modes) if mode == ["graph"]), len(modes))
        # Direct matches keep their order; graph-only documents come after them.
        self.assertTrue(all(mode != ["graph"] for mode in modes[:first_graph]))
        self.assertTrue(all(mode == ["graph"] for mode in modes[first_graph:]))

    def test_scope_context_rejects_unknown_and_only_visits_recorded_neighborhood(self):
        with self.assertRaisesRegex(ServiceError, "RETRIEVAL_CONTEXT_UNAVAILABLE"):
            self.search("resistor", "R1", context_entity_id="not-in-snapshot")
        self.assertEqual(self.embedding.calls, [])
        customer = next(e["id"] for e in self.payload["entities"] if e["type"] == "Customer")
        result = self.search("DEMO-R1", context_entity_id=customer)
        self.assertEqual(result["status"], "NO_HIT")

    def test_no_hit_incomplete_and_execution_error_are_distinct(self):
        self.assertEqual(self.search("unrelated-qxyzz123")["status"], "NO_HIT")
        missing = RetrievalService(self.authorize).search(self.principal, self.payload, self.context,
                                                        RetrievalSearch(query="resistor"))
        self.assertEqual(missing["status"], "INDEX_INCOMPLETE")
        self.embedding.fail = True
        error = self.search("resistor", "R1")
        self.assertEqual(error["status"], "INDEX_ERROR")
        self.assertNotIn("private diagnostic", str(error))
        self.payload["entities"] = [entity for entity in self.payload["entities"] if entity["type"] != "SourceRow"]
        self.assertEqual(self.search("resistor")["status"], "INDEX_INCOMPLETE")

    def test_optional_profiles_explicit_and_do_not_invoke_embeddings(self):
        for profile in ("R2", "R5"):
            result = self.search("resistor", profile)
            self.assertEqual(result["status"], "PROFILE_NOT_IMPLEMENTED")
            self.assertEqual(result["profile_status"][profile], "NOT_IMPLEMENTED")
        self.assertEqual(self.embedding.calls, [])
        # R4 is implemented but needs a configured local reranker.
        result = self.search("resistor", "R4")
        self.assertEqual((result["status"], result["code"]), ("INDEX_INCOMPLETE", "RETRIEVAL_RERANKER_UNAVAILABLE"))

    def test_r4_reorders_candidates_including_graph_reached_ones(self):
        class Reranker:
            provenance = {"model_id": "synthetic-rerank"}
            def __init__(self): self.calls = []
            def score(self, query, texts):
                self.calls.append(len(texts))
                # Prefer later candidates so the order visibly changes.
                return [float(n) for n in range(len(texts))]
        reranker = Reranker()
        service = RetrievalService(self.authorize, self.embedding, reranker=reranker)
        result = service.search(self.principal, self.payload, self.context, RetrievalSearch(query="DEMO-R1", profile="R4", limit=20))
        self.assertEqual(result["status"], "HIT")
        self.assertTrue(reranker.calls and reranker.calls[0] <= 30)
        self.assertTrue(all("rerank" in hit["match_modes"] for hit in result["hits"]))
        self.assertEqual([hit["score"] for hit in result["hits"]], sorted((hit["score"] for hit in result["hits"]), reverse=True))
        self.assertEqual(result["meta"]["reranker"], {"model_id": "synthetic-rerank"})
        self.assertEqual(result["meta"]["external_model_calls"], 0)

    def test_cache_is_content_scoped_bounded_and_does_not_mutate_inputs(self):
        original = deepcopy(self.payload)
        first = self.search("resistor", "R1")
        second = self.search("resistor", "R1")
        self.assertEqual(first, second)
        self.assertEqual(self.payload, original)
        # One document encoding and two query encodings; no hidden paid calls.
        self.assertEqual(len(self.embedding.calls), 3)
        self.payload["entities"][0]["label"] = "Changed authoritative test label"
        changed = self.search("resistor", "R1")
        self.assertNotEqual(first["meta"]["corpus_version"], changed["meta"]["corpus_version"])
        second["hits"][0]["text"] = "client mutation"
        self.assertNotIn("client mutation", str(self.search("resistor", "R1")))

    def test_historical_snapshot_keeps_original_evidence_scope(self):
        old, principal, context = retrieval_fixture(with_bom=False)
        result = self.service.search(principal, old, context, RetrievalSearch(query="item-a", profile="R0"))
        self.assertEqual(result["meta"]["ledger_version"], 1)
        self.assertTrue(result["hits"])
        self.assertTrue(all(hit["kind"] == "ledger_entity" for hit in result["hits"]))
        self.assertTrue(all(hit["origin"] == "synthetic_operation" for hit in result["hits"]))

    def add_internal_quote(self):
        item = next(entity for entity in self.payload["entities"] if entity["type"] == "InternalItem")
        supplier = next(entity for entity in self.payload["entities"] if entity["type"] == "Supplier")
        version = self.context.ledger_version + 1
        content = {"kind": "QUOTE", "quote_ref": "synthetic-new-quote", "supplier_id": supplier["display_id"],
            "item_id": item["display_id"], "unit": "piece", "unit_price": "1.25", "currency": "USD",
            "moq": 2, "order_multiple": 1, "freight": None, "valid_until": "2099-01-01",
            "lead_business_days": None, "provenance": "DEMO_ASSUMPTION", "note": "Independent internal development evidence",
            "basis_ledger_version": version - 1, "resolved": {"item_id": item["display_id"], "item_entity_id": item["id"],
                "supplier_id": supplier["display_id"], "supplier_entity_id": supplier["id"], "unit": "piece",
                "supplier_confirmation": False, "transmission_status": "NOT_SENT"}}
        digest = content_hash(content)
        event = {"event_id": "synthetic-event-id", "event_type": "ProcurementPosted", "ledger_version": version,
            "procurement_document_id": "synthetic-document-id", "recorded_at": "2025-02-04T12:00:00+00:00",
            "payload": {"event_type": "ProcurementPosted", "company_id": self.principal.company_id,
                "ledger_version": version, "document_id": "synthetic-document-id", "document_version": 1,
                "payload_hash": digest, "content": content, "approval": {"created_by": "author", "reviewed_by": "reviewer",
                    "approved_by": "reviewer", "approved_version": 1, "approved_hash": digest, "approval_policy_version": 1}}}
        self.payload = apply_procurement_event(self.payload, event)
        meta = self.payload["metadata"]
        meta["snapshot_id"] = content_hash([version, digest])
        self.payload["projection"].update(ledger_version=version, projection_version=version)
        self.context = self.context.model_copy(update={"read_snapshot_id": meta["snapshot_id"], "ledger_version": version,
            "projection_version": version, "rule_version": meta["rule_version"],
            "as_of": datetime.fromisoformat(meta["effective_at"]), "known_as_of": datetime.fromisoformat(meta["known_as_of"])})

    def test_posted_internal_document_is_hash_bound_and_not_supplier_truth(self):
        self.add_internal_quote()
        result = self.search("synthetic-new-quote", "R3")
        self.assertEqual(result["status"], "HIT")
        hit = result["hits"][0]
        self.assertEqual(hit["kind"], "development_document")
        self.assertEqual(hit["origin"], "development_workflow")
        self.assertEqual(hit["review_status"], "POSTED_DEVELOPMENT_DOCUMENT")
        self.assertEqual(hit["source"]["source_kind"], "internal_development_document")
        self.assertEqual(hit["source"]["ledger_version"], 3)
        opened = self.service.evidence(self.principal, self.payload, self.context,
            RetrievalEvidence(evidence_id=hit["evidence_id"], corpus_version=result["meta"]["corpus_version"]))
        self.assertEqual(content_hash(opened["original"]), hit["source"]["sha256"])
        self.assertFalse(opened["original"]["resolved"]["supplier_confirmation"])
        self.assertEqual(opened["original"]["resolved"]["transmission_status"], "NOT_SENT")
        self.assertTrue(any(edge["predicate"] == "offersItem" for edge in result["graph"]["edges"]))
        self.assertEqual(self.search("DEMO-R1")["hits"][0]["origin"], "actual_source")

    def test_internal_document_hash_state_and_temporal_mismatch_never_embed(self):
        self.add_internal_quote()
        good = deepcopy(self.payload)
        for mutation in (lambda payload: payload["procurement"]["documents"][0].update(status="DRAFT"),
                         lambda payload: payload["procurement"]["documents"][0]["content"].update(note="tampered approval content"),
                         lambda payload: [entity["evidence"].update(registered_at="2099-01-01T00:00:00+00:00")
                             for entity in payload["entities"] if entity["evidence"].get("record_type") == "development_procurement"]):
            self.payload = deepcopy(good)
            mutation(self.payload)
            with self.subTest(mutation=mutation):
                result = self.search("synthetic-new-quote", "R1")
                self.assertEqual(result["status"], "INDEX_INCOMPLETE")
                self.assertEqual(result["hits"], [])
        self.assertEqual(self.embedding.calls, [])


if __name__ == "__main__":
    unittest.main()
