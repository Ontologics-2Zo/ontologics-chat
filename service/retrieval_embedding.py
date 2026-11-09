"""Pinned local multilingual sentence embeddings; no runtime network client.

The official Sentence Transformers ONNX export returns token embeddings. We
apply its documented attention-mask mean pooling, then cosine normalization.
Long documents are fully windowed with overlap, never silently truncated. The
weights/tokenizer are public artifacts installed separately outside Git.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from threading import Lock

from service.errors import ServiceError


MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_SHA256 = "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672"
TOKENIZER_SHA256 = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
MODEL_BYTES = 118412398
TOKENIZER_BYTES = 9081518


class LocalMultilingualEmbedding:
    """CPU-only inference over verified, data-only ONNX/tokenizer files.

    No pickle, provider SDK, remote code, automatic download or text logging.
    Model artifacts are verified once at construction and retained in memory.
    """

    provenance = {
        "implementation": "onnx-sentence-mean/1", "execution_mode": "local_pretrained",
        "model_id": MODEL_ID, "revision": REVISION, "model_sha256": MODEL_SHA256,
        "tokenizer_sha256": TOKENIZER_SHA256, "dimensions": 384,
        "pooling": "attention_mask_mean_then_window_mean_l2", "max_sequence_tokens": 128,
        "max_document_windows": 32, "external_transmission": False, "trust_remote_code": False,
        "quantization": "qint8_arm64", "provider": "CPUExecutionProvider",
    }

    @classmethod
    def from_settings(cls, settings_file):
        try:
            path = Path(settings_file)
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError
            document = json.loads(path.read_text())
            if not isinstance(document, dict):
                raise ValueError
            if "retrieval" not in document:
                return None
            settings = document["retrieval"]
            expected = {"model_id": MODEL_ID, "revision": REVISION,
                        "model_sha256": MODEL_SHA256, "tokenizer_sha256": TOKENIZER_SHA256}
            if any(settings.get(key) != value for key, value in expected.items()):
                raise ValueError
            return cls(settings["model_dir"])
        except ServiceError:
            raise
        except (KeyError, ValueError, TypeError, OSError):
            raise ServiceError("RETRIEVAL_MODEL_SETTINGS_INVALID", 503) from None

    def __init__(self, model_dir):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            ort.disable_telemetry_events()
            ort.set_default_logger_severity(4)
            root = Path(model_dir).resolve(strict=True)
            # Loading private corpora is never part of model discovery.
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
            self._tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=128, stride=32)
            self._tokenizer.enable_padding(pad_id=0, pad_token="<pad>")
            self._np, self._lock = np, Lock()
            if {item.name for item in self._session.get_inputs()} != {"input_ids", "attention_mask", "token_type_ids"}:
                raise ValueError
        except Exception:
            # Third-party errors may include private file paths or input text.
            raise ServiceError("RETRIEVAL_MODEL_UNAVAILABLE", 503) from None

    def encode(self, texts):
        if (not isinstance(texts, (list, tuple)) or not texts or len(texts) > 2500
                or any(not isinstance(text, str) or not text.strip() or len(text) > 32768 for text in texts)):
            raise ServiceError("RETRIEVAL_EMBEDDING_INPUT_INVALID", 422)
        with self._lock:
            try:
                return self._encode(texts)
            except ServiceError:
                raise
            except Exception:
                raise ServiceError("RETRIEVAL_EMBEDDING_FAILED", 503) from None

    def _encode(self, texts):
        np = self._np
        output = []
        for text in texts:
            encoding = self._tokenizer.encode(text)
            windows = [encoding, *encoding.overflowing]
            if len(windows) > self.provenance["max_document_windows"]:
                raise ServiceError("RETRIEVAL_EMBEDDING_WINDOW_LIMIT", 422)
            vectors = []
            for offset in range(0, len(windows), 8):
                batch = windows[offset:offset + 8]
                length = max(len(part.ids) for part in batch)
                def array(field):
                    return np.asarray([getattr(part, field) + [0] * (length - len(part.ids)) for part in batch], dtype=np.int64)
                mask = array("attention_mask")
                tokens = self._session.run(None, {"input_ids": array("ids"),
                    "attention_mask": mask, "token_type_ids": array("type_ids")})[0]
                weights = mask[..., None].astype(np.float32)
                vectors.extend((tokens * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1))
            vector = np.mean(vectors, axis=0)
            norm = np.linalg.norm(vector)
            if vector.shape != (384,) or not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
                raise ServiceError("RETRIEVAL_EMBEDDING_INVALID", 503)
            output.append((vector / norm).tolist())
        return output
