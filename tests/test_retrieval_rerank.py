"""Pinned local reranker: tampered or unconfigured files are refused; the real model runs offline."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from service.errors import ServiceError
from service.retrieval_rerank import (LocalCrossEncoderReranker, MODEL_ID, MODEL_SHA256, REVISION,
                                      TOKENIZER_SHA256)


class RerankLoaderTests(unittest.TestCase):
    def test_settings_without_rerank_section_disable_r4(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model-settings.json"
            path.write_text(json.dumps({"retrieval": {}}))
            self.assertIsNone(LocalCrossEncoderReranker.from_settings(path))

    def test_mismatched_pin_or_tampered_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.onnx").write_bytes(b"not a model")
            (root / "tokenizer.json").write_text("{}")
            settings = root / "model-settings.json"
            pinned = {"model_id": MODEL_ID, "revision": REVISION, "model_sha256": MODEL_SHA256,
                      "tokenizer_sha256": TOKENIZER_SHA256, "model_dir": str(root)}
            for document, code in (({"rerank": {**pinned, "revision": "other"}}, "RERANK_MODEL_SETTINGS_INVALID"),
                                   ({"rerank": pinned}, "RERANK_MODEL_UNAVAILABLE")):
                settings.write_text(json.dumps(document))
                with self.subTest(code=code), self.assertRaises(ServiceError) as caught:
                    LocalCrossEncoderReranker.from_settings(settings)
                self.assertEqual(caught.exception.code, code)


@unittest.skipUnless(os.environ.get("ONTO_TEST_RERANK_MODEL_DIR"), "ONTO_TEST_RERANK_MODEL_DIR not configured")
class RealRerankerTests(unittest.TestCase):
    def test_korean_query_prefers_the_matching_item(self):
        reranker = LocalCrossEncoderReranker(os.environ["ONTO_TEST_RERANK_MODEL_DIR"])
        scores = reranker.score("충전 IC 부품", ["사내 품목 충전 IC 리튬 배터리 충전 컨트롤러", "노란색 LED 표시등", "47k 저항"])
        self.assertEqual(max(range(3), key=scores.__getitem__), 0)
        with self.assertRaises(ServiceError):
            reranker.score("", ["x"])


if __name__ == "__main__":
    unittest.main()
