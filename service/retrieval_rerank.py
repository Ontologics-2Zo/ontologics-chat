"""Pinned local multilingual cross-encoder reranker (R4); no runtime network client.

Same rules as the embedding model: public ONNX weights and tokenizer installed
outside Git, verified by size and SHA256 at load, CPU only, no remote code,
no automatic download and no text logging. Scores are relevance cues for
ordering evidence, never facts or approvals.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from threading import Lock

from service.errors import ServiceError


MODEL_ID = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
MODEL_FILE = "onnx/model_qint8_arm64.onnx"
MODEL_SHA256 = "1825907d6c1a9001ff78124780bbde20a614a8c3df3b63409cf3c72c6fe5c8b4"
TOKENIZER_SHA256 = "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"
MODEL_BYTES = 118620017
TOKENIZER_BYTES = 17082660
MAX_PAIRS = 40
MAX_TOKENS = 256


class LocalCrossEncoderReranker:
    provenance = {
        "implementation": "onnx-cross-encoder/1", "execution_mode": "local_pretrained",
        "model_id": MODEL_ID, "revision": REVISION, "model_file": MODEL_FILE,
        "model_sha256": MODEL_SHA256, "tokenizer_sha256": TOKENIZER_SHA256,
        "max_sequence_tokens": MAX_TOKENS, "max_pairs": MAX_PAIRS, "external_transmission": False,
        "trust_remote_code": False, "quantization": "qint8_arm64", "provider": "CPUExecutionProvider",
    }

    @classmethod
    def from_settings(cls, settings_file):
        """None when the settings file has no rerank section (R4 then stays unavailable)."""
        try:
            path = Path(settings_file)
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError
            document = json.loads(path.read_text())
            if not isinstance(document, dict):
                raise ValueError
            if "rerank" not in document:
                return None
            settings = document["rerank"]
            expected = {"model_id": MODEL_ID, "revision": REVISION,
                        "model_sha256": MODEL_SHA256, "tokenizer_sha256": TOKENIZER_SHA256}
            if any(settings.get(key) != value for key, value in expected.items()):
                raise ValueError
            return cls(settings["model_dir"])
        except ServiceError:
            raise
        except (KeyError, ValueError, TypeError, OSError):
            raise ServiceError("RERANK_MODEL_SETTINGS_INVALID", 503) from None

    def __init__(self, model_dir):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            ort.disable_telemetry_events()
            ort.set_default_logger_severity(4)
            root = Path(model_dir).resolve(strict=True)
            for filename, size, digest in (("model.onnx", MODEL_BYTES, MODEL_SHA256),
                                           ("tokenizer.json", TOKENIZER_BYTES, TOKENIZER_SHA256)):
                path = root / filename
                if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                    raise ValueError
                with path.open("rb") as stream:
                    hasher = sha256()
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                if hasher.hexdigest() != digest:
                    raise ValueError
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.log_severity_level = 4
            self._session = ort.InferenceSession(str(root / "model.onnx"), options,
                                                providers=["CPUExecutionProvider"])
            if {item.name for item in self._session.get_inputs()} != {"input_ids", "attention_mask"}:
                raise ValueError
            self._tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=MAX_TOKENS)
            self._tokenizer.enable_padding()
            self._np, self._lock = np, Lock()
        except Exception:
            # Third-party errors may include private file paths or input text.
            raise ServiceError("RERANK_MODEL_UNAVAILABLE", 503) from None

    def score(self, query, texts):
        if (not isinstance(query, str) or not query.strip() or len(query) > 1000
                or not isinstance(texts, (list, tuple)) or not 1 <= len(texts) <= MAX_PAIRS
                or any(not isinstance(text, str) or not text.strip() or len(text) > 32768 for text in texts)):
            raise ServiceError("RERANK_INPUT_INVALID", 422)
        with self._lock:
            try:
                np = self._np
                encodings = self._tokenizer.encode_batch([(query, text) for text in texts])
                logits = self._session.run(None, {
                    "input_ids": np.asarray([e.ids for e in encodings], dtype=np.int64),
                    "attention_mask": np.asarray([e.attention_mask for e in encodings], dtype=np.int64)})[0]
                values = [float(value) for value in logits.reshape(-1)]
                if len(values) != len(texts) or not all(np.isfinite(values)):
                    raise ValueError
                return values
            except Exception:
                raise ServiceError("RERANK_FAILED", 503) from None
