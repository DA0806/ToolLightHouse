from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Prediction:
    track_id: int
    class_id: str | None
    similarity_top1: float | None
    similarity_top2: float | None
    margin: float | None
    validation: str


def _cosine(a, b):
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if a.ndim != 1 or b.ndim != 1 or a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if not math.isfinite(den) or not den:
        return float("nan")
    value = float(np.dot(a, b) / den)
    return value if math.isfinite(value) else float("nan")


class SimilarityClassifier:
    def __init__(self, references: dict[str, np.ndarray], threshold: float = 0.72, margin: float = 0.08, *, gallery_mode: str = "text-only"):
        if gallery_mode not in {"text-only", "image-only", "hybrid"}:
            raise ValueError("gallery_mode must be text-only, image-only, or hybrid")
        if not math.isfinite(float(threshold)) or not -1 <= threshold <= 1:
            raise ValueError("classifier threshold must be finite and between -1 and 1")
        if not math.isfinite(float(margin)) or not 0 <= margin <= 2:
            raise ValueError("classifier margin must be finite and between 0 and 2")
        checked = {}
        for class_id, reference in references.items():
            value = np.asarray(reference, dtype=np.float32)
            norm = float(np.linalg.norm(value))
            if value.ndim != 1 or not np.isfinite(value).all() or not math.isfinite(norm) or not norm:
                raise ValueError(f"invalid gallery prototype for class {class_id}")
            checked[class_id] = value
        self.references = checked
        self.threshold, self.margin, self.gallery_mode = threshold, margin, gallery_mode

    def predict(self, embedding: np.ndarray, track_id: int) -> Prediction:
        value = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(value))
        if value.ndim != 1 or not np.isfinite(value).all() or not math.isfinite(norm) or not norm:
            return Prediction(track_id, None, None, None, None, "unknown")
        scores = sorted(((key, _cosine(embedding, value)) for key, value in self.references.items()), key=lambda item: item[1] if math.isfinite(item[1]) else -2, reverse=True)
        top = scores[0] if scores else (None, None)
        second = scores[1][1] if len(scores) > 1 else None
        margin = top[1] - second if top[1] is not None and second is not None else None
        valid = top[0] is not None and top[1] is not None and math.isfinite(top[1]) and top[1] >= self.threshold and (margin is None or margin >= self.margin)
        return Prediction(track_id, top[0] if valid else None, top[1], second, margin, "confirmed" if valid else ("unknown" if top[0] is None or top[1] is None or top[1] < self.threshold else "ambiguous"))


class MockClassifier:
    """Deterministic synthetic classifier used only when Settings.mode == mock."""

    def __init__(self, class_ids):
        self.class_ids = tuple(class_ids)

    def predict(self, crop: np.ndarray, track_id: int) -> Prediction:
        index = (track_id - 1) % len(self.class_ids)
        return Prediction(track_id, self.class_ids[index], 1.0, 0.0, 1.0, "simulated")
