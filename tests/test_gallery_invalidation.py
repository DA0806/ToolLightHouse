import json

import numpy as np
import pytest

from toollighthouse.config import Inventory, Settings, ToolCategory
from toollighthouse.embeddings import references_fingerprint
from toollighthouse.gallery import Gallery
from toollighthouse.pipeline import Pipeline


class NoopEmbedder:
    def encode_images(self, images):
        return np.ones((len(images), 4), dtype=np.float32)


def _inventory(description="hammer"):
    return Inventory([ToolCategory(f"tool_{index + 1}", f"Tool {index + 1}", "bench", index, index + 2, (description,) if index == 0 else ()) for index in range(9)])


def _real_settings(tmp_path, gallery_path, *, inventory=None, text_weight=0.0, model_path=None):
    return Settings(
        mode="real",
        pairing_key="test",
        inventory_confirmed=True,
        inventory=inventory or _inventory(),
        gallery_path=str(gallery_path),
        model_path=str(model_path or tmp_path / "model"),
        background_path=None,
        roi_path=None,
        text_weight=text_weight,
    )


def _write_gallery(tmp_path, *, descriptions=None, text_weight=0.0, model_path=None, class_id="tool_1"):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"reference")
    model_path = model_path or tmp_path / "model"
    model_path.mkdir(exist_ok=True)
    descriptions = descriptions or {f"tool_{index + 1}": ["hammer"] if index == 0 else [] for index in range(9)}
    manifest = {
        "paths": [str(reference)],
        "descriptions": descriptions,
        "text_weight": text_weight,
        "preprocessing": "rgb-pil-v1",
    }
    fingerprint = references_fingerprint([reference], str(model_path), descriptions=descriptions, text_weight=text_weight, model_identity=str(model_path.resolve()))
    path = tmp_path / "gallery.json"
    Gallery({class_id: np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)}).save(path, fingerprint, manifest=manifest)
    return path, model_path


def test_real_pipeline_accepts_gallery_with_matching_manifest(tmp_path):
    gallery, model_path = _write_gallery(tmp_path)
    pipeline = Pipeline(_real_settings(tmp_path, gallery, model_path=model_path), embedder=NoopEmbedder())
    assert pipeline.classifier.references["tool_1"].shape == (4,)


@pytest.mark.parametrize("change", ["description", "text_weight", "model_identity", "reference_bytes"])
def test_real_pipeline_rejects_gallery_when_live_inputs_no_longer_match_manifest(tmp_path, change):
    gallery, model_path = _write_gallery(tmp_path)
    reference = tmp_path / "reference.jpg"
    if change == "description":
        inventory = _inventory("different visual description")
        settings = _real_settings(tmp_path, gallery, inventory=inventory, model_path=model_path)
    elif change == "text_weight":
        settings = _real_settings(tmp_path, gallery, text_weight=0.5, model_path=model_path)
    elif change == "model_identity":
        changed_model = tmp_path / "changed-model"
        changed_model.mkdir()
        settings = _real_settings(tmp_path, gallery, model_path=changed_model)
    else:
        reference.write_bytes(b"changed reference")
        settings = _real_settings(tmp_path, gallery, model_path=model_path)
    with pytest.raises(RuntimeError, match="stale|rebuild"):
        Pipeline(settings, embedder=NoopEmbedder())


def test_real_pipeline_rejects_gallery_with_unknown_or_missing_inventory_class(tmp_path):
    gallery, model_path = _write_gallery(tmp_path, class_id="unknown_tool")
    with pytest.raises(RuntimeError, match="stale|reference|class|rebuild"):
        Pipeline(_real_settings(tmp_path, gallery, model_path=model_path), embedder=NoopEmbedder())
