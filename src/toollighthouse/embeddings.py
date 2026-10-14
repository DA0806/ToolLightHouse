from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np


class EmbeddingEngine:
    """Optional local EmbeddingGemma adapter; imports Torch only on construction."""

    def __init__(self, model_path: str = "models/embeddinggemma-2", device: str | None = None):
        self.model_path, self.device = model_path, device
        self._lock = threading.Lock()
        self._model = None

    def load(self):
        if not Path(self.model_path).exists():
            raise FileNotFoundError(f"local model not found: {self.model_path}")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("install the optional 'ai' dependencies for real inference") from exc
        import torch
        target = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        with self._lock:
            if self._model is None:
                self._model = SentenceTransformer(self.model_path, device=target, model_kwargs={"torch_dtype": torch.bfloat16 if target.startswith("cuda") and torch.cuda.is_bf16_supported() else torch.float32}, config_kwargs={"audio_config": None})
        return self

    def encode_images(self, images):
        self.load()
        from PIL import Image
        with self._lock:
            prepared = []
            for image in images:
                if isinstance(image, np.ndarray):
                    image = Image.fromarray(image[..., ::-1])
                prepared.append({"image": image})
            try:
                result = self._model.encode(prepared, normalize_embeddings=True)
            except (TypeError, ValueError):
                result = self._model.encode([item["image"] for item in prepared], normalize_embeddings=True)
        values = np.asarray(result, dtype=np.float32)
        if not np.isfinite(values).all():
            raise ValueError("model returned non-finite embeddings")
        return values

    def encode_text(self, texts):
        self.load()
        with self._lock:
            result = self._model.encode(list(texts), normalize_embeddings=True)
        values = np.asarray(result, dtype=np.float32)
        if not np.isfinite(values).all():
            raise ValueError("model returned non-finite embeddings")
        return values


def references_fingerprint(paths: list[str | Path], config: str = "", *, descriptions=None, text_weight: float = 0.0, model_identity: str = "", preprocessing: str = "rgb-pil-v1") -> str:
    digest = hashlib.sha256(config.encode())
    recipe = {"descriptions": descriptions or {}, "text_weight": text_weight, "model_identity": model_identity, "preprocessing": preprocessing}
    digest.update(json.dumps(recipe, sort_keys=True, ensure_ascii=True).encode())
    for path in sorted(map(str, paths)):
        p = Path(path)
        digest.update(path.encode())
        if p.exists():
            stat = p.stat()
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
            digest.update(p.read_bytes())
    return digest.hexdigest()
