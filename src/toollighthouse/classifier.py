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
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if not den:
        return float("nan")
    value = float(np.dot(a, b) / den)
    return value if math.isfinite(value) else float("nan")


class SimilarityClassifier:
    def __init__(self, references: dict[str, np.ndarray], threshold: float = 0.72, margin: float = 0.08):
        self.references = references
        self.threshold, self.margin = threshold, margin

    def predict(self, embedding: np.ndarray, track_id: int) -> Prediction:
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
