from __future__ import annotations

import json
import math
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from .embeddings import references_fingerprint

GALLERY_VERSION = 2
GALLERY_MODES = {"text-only", "image-only", "hybrid"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _unit(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    if value.ndim != 1:
        raise ValueError("gallery embedding must be a finite one-dimensional vector")
    if not np.isfinite(value).all():
        raise ValueError("gallery cache contains non-finite references")
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or not norm:
        raise ValueError("gallery embedding must have a non-zero norm")
    return value / norm


def _aggregate(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ValueError("gallery category has no embeddings")
    return _unit(np.mean(np.stack([_unit(vector) for vector in vectors]), axis=0))


class Gallery:
    def __init__(self, references: dict[str, np.ndarray] | None = None, manifest: dict | None = None):
        self.references = references or {}
        self.manifest = manifest or {}

    def save(self, path: str | Path, fingerprint: str, manifest: dict | None = None):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        effective_manifest = {"version": GALLERY_VERSION, **(manifest or self.manifest)}
        payload = {"fingerprint": fingerprint, "manifest": effective_manifest, "references": {key: np.asarray(value, dtype=np.float32).tolist() for key, value in self.references.items()}}
        target.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, fingerprint: str, *, expected_class_ids=None):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("fingerprint") != fingerprint:
            raise ValueError("gallery cache is stale; rebuild it")
        manifest = payload.get("manifest") or {}
        if manifest.get("version") != GALLERY_VERSION:
            raise ValueError("gallery cache format is outdated; rebuild it")
        raw_refs = payload.get("references")
        if not isinstance(raw_refs, dict) or not raw_refs:
            raise ValueError("gallery cache contains no references")
        if expected_class_ids is not None and set(raw_refs) != set(expected_class_ids):
            raise ValueError("gallery cache must contain exactly the configured inventory classes")
        refs = {key: _unit(np.asarray(value, dtype=np.float32)) for key, value in raw_refs.items()}
        dimensions = {value.shape for value in refs.values()}
        if len(dimensions) != 1:
            raise ValueError("gallery cache references have inconsistent dimensions")
        return cls(refs, manifest)


def build_gallery(embedder, inventory, *, mode: str = "text-only", image_paths=(), text_weight: float = 0.5) -> tuple[Gallery, dict]:
    """Build class prototypes from text, images, or their weighted combination."""
    if mode not in GALLERY_MODES:
        raise ValueError("gallery mode must be text-only, image-only, or hybrid")
    if not 0 <= float(text_weight) <= 1:
        raise ValueError("text_weight must be between 0 and 1")
    if mode == "hybrid" and not 0 < float(text_weight) < 1:
        raise ValueError("hybrid text_weight must be strictly between 0 and 1")
    categories = tuple(inventory.categories)
    expected_ids = {category.class_id for category in categories}
    image_paths = list(image_paths)
    image_refs: dict[str, list[np.ndarray]] = {}
    if mode == "image-only":
        if not image_paths:
            raise ValueError("image-only gallery requires reference images")
        image_class_ids = {Path(path).parent.name for path in image_paths}
        if image_class_ids != expected_ids:
            missing = sorted(expected_ids - image_class_ids)
            extra = sorted(image_class_ids - expected_ids)
            raise ValueError(f"gallery must contain exactly the configured nine classes; missing={missing} extra={extra}")
    elif mode == "hybrid" and image_paths:
        image_class_ids = {Path(path).parent.name for path in image_paths}
        extra = sorted(image_class_ids - expected_ids)
        if extra:
            raise ValueError(f"reference images contain unknown classes not in inventory: {extra}")

    if (mode == "image-only" and image_paths) or (mode == "hybrid" and image_paths):
        from PIL import Image

        with ExitStack() as stack:
            images = []
            for path in image_paths:
                source = stack.enter_context(Image.open(path))
                images.append(source.convert("RGB"))
            vectors = np.asarray(embedder.encode_images(images), dtype=np.float32)
        if len(vectors) != len(image_paths):
            raise ValueError("image encoder returned an unexpected number of embeddings")
        for path, vector in zip(image_paths, vectors):
            image_refs.setdefault(Path(path).parent.name, []).append(vector)
        if mode == "image-only" and set(image_refs) != expected_ids:
            missing = sorted(expected_ids - set(image_refs))
            extra = sorted(set(image_refs) - expected_ids)
            raise ValueError(f"gallery must contain exactly the configured nine classes; missing={missing} extra={extra}")

    text_refs: dict[str, np.ndarray] = {}
    text_count = 0
    if mode in {"text-only", "hybrid"}:
        prompts: list[str] = []
        prompt_ids: list[str] = []
        for category in categories:
            descriptions = [str(value).strip() for value in category.descriptions if str(value).strip()]
            category_prompts = [f"{category.name}. {description}" for description in descriptions] if descriptions else [category.name]
            prompts.extend(category_prompts)
            prompt_ids.extend([category.class_id] * len(category_prompts))
        encoded = np.asarray(embedder.encode_text(prompts), dtype=np.float32)
        if len(encoded) != len(prompts):
            raise ValueError("text encoder returned an unexpected number of embeddings")
        grouped: dict[str, list[np.ndarray]] = {category.class_id: [] for category in categories}
        for class_id, vector in zip(prompt_ids, encoded):
            grouped[class_id].append(vector)
        text_refs = {class_id: _aggregate(vectors) for class_id, vectors in grouped.items()}
        text_count = len(prompts)

    references: dict[str, np.ndarray] = {}
    for class_id in expected_ids:
        if mode == "text-only":
            references[class_id] = text_refs[class_id]
        elif mode == "image-only":
            references[class_id] = _aggregate(image_refs[class_id])
        else:
            if class_id in image_refs and image_refs[class_id]:
                references[class_id] = _unit(text_weight * text_refs[class_id] + (1.0 - text_weight) * _aggregate(image_refs[class_id]))
            else:
                references[class_id] = text_refs[class_id]

    manifest = {
        "version": GALLERY_VERSION,
        "gallery_mode": mode,
        "text_weight": float(text_weight),
        "descriptions": {category.class_id: list(category.descriptions) for category in categories},
        "names": {category.class_id: category.name for category in categories},
        "preprocessing": "name-description-mean-normalized-v1",
        "image_count": len(image_paths) if mode in {"image-only", "hybrid"} else 0,
        "image_counts": {category.class_id: len(image_refs.get(category.class_id, [])) for category in categories},
        "text_count": text_count,
    }
    return Gallery(references, manifest), manifest


def _reference_files(root: str | Path) -> list[Path]:
    directory = Path(root)
    return sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def load_validated_gallery(path: str | Path, *, inventory, model_path: str, gallery_mode: str, text_weight: float) -> Gallery:
    """Re-derive the current cache recipe before accepting persisted prototypes."""
    cache_path = Path(path)
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    manifest = payload.get("manifest") or {}
    if manifest.get("version") != GALLERY_VERSION:
        raise ValueError("gallery cache format is outdated; rebuild it")
    if manifest.get("gallery_mode") != gallery_mode:
        raise ValueError("gallery mode differs from cache; rebuild it")
    descriptions = {category.class_id: list(category.descriptions) for category in inventory.categories}
    names = {category.class_id: category.name for category in inventory.categories}
    if manifest.get("descriptions") != descriptions or manifest.get("names") != names:
        raise ValueError("gallery recipe differs from inventory names or descriptions; rebuild it")
    if manifest.get("preprocessing") != "name-description-mean-normalized-v1":
        raise ValueError("gallery preprocessing recipe is outdated; rebuild it")
    if float(manifest.get("text_weight", 0.5)) != float(text_weight):
        raise ValueError("gallery recipe differs from text_weight; rebuild it")
    expected_ids = {category.class_id for category in inventory.categories}
    paths = [Path(item) for item in manifest.get("paths", [])] if gallery_mode in {"image-only", "hybrid"} else []
    if gallery_mode == "image-only":
        root = manifest.get("references_root")
        if root:
            current = _reference_files(root)
            if {str(item.resolve()) for item in current} != {str(item.resolve()) for item in paths}:
                raise ValueError("reference image set changed; rebuild it")
        if not paths or any(not item.exists() for item in paths):
            raise ValueError("gallery reference images are missing; rebuild it")
    elif gallery_mode == "hybrid":
        root = manifest.get("references_root")
        if root and Path(root).exists():
            current = _reference_files(root)
            if {str(item.resolve()) for item in current} != {str(item.resolve()) for item in paths}:
                raise ValueError("reference image set changed; rebuild it")
        if any(not item.exists() for item in paths):
            raise ValueError("gallery reference images are missing; rebuild it")
    fingerprint = references_fingerprint(
        paths,
        str(model_path),
        descriptions=descriptions,
        names=names,
        text_weight=text_weight,
        model_identity=str(Path(model_path).resolve()),
        preprocessing=manifest.get("preprocessing", "name-description-mean-normalized-v1"),
        gallery_mode=gallery_mode,
        inventory_ids=expected_ids,
    )
    return Gallery.load(cache_path, fingerprint, expected_class_ids=expected_ids)
