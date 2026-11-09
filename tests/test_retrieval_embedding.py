"""Optional real local embedding gate; fixtures contain no private source text.

Set ONTO_RETRIEVAL_MODEL_SETTINGS to the private installed-model settings file.
No weights are downloaded by the application or by these tests.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.errors import ServiceError
from service.retrieval import RetrievalSearch, RetrievalService
from service.retrieval_embedding import LocalMultilingualEmbedding, MODEL_ID, REVISION
from tests.test_retrieval import Authorization, retrieval_fixture


class LocalEmbeddingConfigurationTests(unittest.TestCase):
    def test_missing_retrieval_section_is_disabled_and_invalid_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(json.dumps({"unrelated": {}}))
            self.assertIsNone(LocalMultilingualEmbedding.from_settings(settings))
            settings.write_text(json.dumps({"retrieval": {"model_id": "untrusted", "model_dir": "private-location"}}))
            with self.assertRaisesRegex(ServiceError, "RETRIEVAL_MODEL_SETTINGS_INVALID") as caught:
                LocalMultilingualEmbedding.from_settings(settings)
            self.assertNotIn("private-location", str(caught.exception))


@unittest.skipUnless(os.environ.get("ONTO_RETRIEVAL_MODEL_SETTINGS"), "Pinned local multilingual model is not installed")
class LocalMultilingualEmbeddingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Python clients cannot silently download a model or send source text.
        # The execution gate also runs under the network-restricted sandbox.
        with patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            cls.model = LocalMultilingualEmbedding.from_settings(os.environ["ONTO_RETRIEVAL_MODEL_SETTINGS"])

    def test_real_pretrained_cross_language_similarity_and_fixed_provenance(self):
        with patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            query, relevant, unrelated = self.model.encode([
                "정밀 저항 소자", "A precision resistor used to limit current in an electrical circuit.",
                "The warehouse received a shipment of mechanical connectors."])
        dot = lambda a, b: sum(x * y for x, y in zip(a, b))
        self.assertGreater(dot(query, relevant), 0.45)
        self.assertGreater(dot(query, relevant), dot(query, unrelated) + 0.25)
        self.assertEqual(len(query), 384)
        self.assertAlmostEqual(dot(query, query), 1.0, places=5)
        self.assertEqual(self.model.provenance["model_id"], MODEL_ID)
        self.assertEqual(self.model.provenance["revision"], REVISION)
        self.assertFalse(self.model.provenance["external_transmission"])
        self.assertFalse(self.model.provenance["trust_remote_code"])

    def test_actual_hybrid_retrieval_recovers_korean_query_without_lexical_hit(self):
        payload, principal, context = retrieval_fixture()
        before = deepcopy(payload)
        service = RetrievalService(Authorization(), self.model)
        with patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            baseline = service.search(principal, payload, context, RetrievalSearch(query="정밀 저항 소자", profile="R0"))
            result = service.search(principal, payload, context, RetrievalSearch(query="정밀 저항 소자", profile="R1"))
            absent = service.search(principal, payload, context, RetrievalSearch(query="foobarzzqqxx", profile="R1"))
        self.assertEqual(baseline["status"], "NO_HIT")
        self.assertEqual(result["status"], "HIT")
        self.assertEqual(result["hits"][0]["kind"], "source_row")
        self.assertEqual(result["hits"][0]["match_modes"], ["vector"])
        self.assertIn("precision resistor", result["hits"][0]["text"])
        self.assertEqual(result["hits"][0]["review_status"], "SOURCE_ONLY")
        self.assertEqual(absent["status"], "NO_HIT")
        self.assertEqual(payload, before)

    def test_long_source_is_windowed_and_over_budget_input_is_explicit(self):
        with patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            vector = self.model.encode(["A precision resistor limits current. " * 70])[0]
            self.assertEqual(len(vector), 384)
            with self.assertRaisesRegex(ServiceError, "RETRIEVAL_EMBEDDING_WINDOW_LIMIT"):
                self.model.encode(["x " * 5000])


if __name__ == "__main__":
    unittest.main()
