from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class Gallery:
    def __init__(self, references: dict[str, np.ndarray] | None = None):
        self.references = references or {}

    def save(self, path: str | Path, fingerprint: str, manifest: dict | None = None):
        payload = {"fingerprint": fingerprint, "manifest": manifest or {}, "references": {key: value.tolist() for key, value in self.references.items()}}
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, fingerprint: str):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("fingerprint") != fingerprint:
            raise ValueError("gallery cache is stale; rebuild it")
        refs = {key: np.asarray(value, dtype=np.float32) for key, value in payload["references"].items()}
        if not refs:
            raise ValueError("gallery cache contains no references")
        if not all(np.isfinite(value).all() for value in refs.values()):
            raise ValueError("gallery cache contains non-finite references")
        return cls(refs)
