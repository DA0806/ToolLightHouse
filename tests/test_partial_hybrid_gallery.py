from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from toollighthouse.config import Inventory, ToolCategory
from toollighthouse.gallery import build_gallery, load_validated_gallery


def _make_inventory():
    return Inventory([
        ToolCategory(f"tool_{i+1}", f"Tool {i+1}", "bench", i, i + 2, (f"desc {i+1}",))
        for i in range(9)
    ])


class MockEmbedder:
    def __init__(self):
        self.text_calls = []
        self.image_calls = []

    def encode_text(self, texts):
        self.text_calls.append(list(texts))
        # Return deterministic distinct normalized vectors for each text
        return np.asarray([
            [float(i + 1), 1.0, 0.5] / np.linalg.norm([float(i + 1), 1.0, 0.5])
            for i, _ in enumerate(texts)
        ], dtype=np.float32)

    def encode_images(self, images):
        self.image_calls.append(len(images))
        # Return deterministic distinct vectors
        return np.asarray([
            [0.5, float(i + 1), 1.0] / np.linalg.norm([0.5, float(i + 1), 1.0])
            for i, _ in enumerate(images)
        ], dtype=np.float32)


def test_hybrid_gallery_with_zero_references_preserves_text_prototypes():
    embedder = MockEmbedder()
    inventory = _make_inventory()

    gallery, manifest = build_gallery(embedder, inventory, mode="hybrid", image_paths=[], text_weight=0.5)

    assert len(gallery.references) == 9
    assert manifest["image_count"] == 0
    assert manifest["gallery_mode"] == "hybrid"
    # All vectors should be unit normalized
    for cid, vec in gallery.references.items():
        assert np.isclose(np.linalg.norm(vec), 1.0)


def test_hybrid_gallery_with_partial_references(tmp_path):
    embedder = MockEmbedder()
    inventory = _make_inventory()

    # Create reference images for ONLY 2 of the 9 categories: tool_1 and tool_3
    ref_paths = []
    for cid in ["tool_1", "tool_3"]:
        img_dir = tmp_path / cid
        img_dir.mkdir(parents=True)
        img_file = img_dir / "sample.jpg"
        Image.new("RGB", (32, 32), "red").save(img_file)
        ref_paths.append(img_file)

    gallery, manifest = build_gallery(embedder, inventory, mode="hybrid", image_paths=ref_paths, text_weight=0.7)

    assert len(gallery.references) == 9
    assert manifest["image_count"] == 2
    assert manifest["image_counts"]["tool_1"] == 1
    assert manifest["image_counts"]["tool_3"] == 1
    assert manifest["image_counts"]["tool_2"] == 0

    # Also build pure text-only gallery for comparison
    text_gallery, _ = build_gallery(MockEmbedder(), inventory, mode="text-only", image_paths=[])

    # tool_2 has no reference photos in hybrid, so it should match its pure text prototype!
    np.testing.assert_allclose(gallery.references["tool_2"], text_gallery.references["tool_2"], atol=1e-5)

    # tool_1 has reference photos, so its hybrid prototype should differ from pure text prototype
    diff = np.linalg.norm(gallery.references["tool_1"] - text_gallery.references["tool_1"])
    assert diff > 0.01


def test_hybrid_gallery_rejects_unknown_category_paths(tmp_path):
    embedder = MockEmbedder()
    inventory = _make_inventory()

    # Image for an unknown class 'tool_99'
    bad_dir = tmp_path / "tool_99"
    bad_dir.mkdir(parents=True)
    bad_file = bad_dir / "sample.jpg"
    Image.new("RGB", (32, 32), "blue").save(bad_file)

    with pytest.raises(ValueError, match="unknown"):
        build_gallery(embedder, inventory, mode="hybrid", image_paths=[bad_file])


def test_load_validated_gallery_supports_partial_hybrid(tmp_path):
    embedder = MockEmbedder()
    inventory = _make_inventory()

    # Partial references: 1 tool
    img_dir = tmp_path / "references" / "tool_1"
    img_dir.mkdir(parents=True)
    img_file = img_dir / "photo.png"
    Image.new("RGB", (32, 32), "green").save(img_file)

    gallery, manifest = build_gallery(embedder, inventory, mode="hybrid", image_paths=[img_file], text_weight=0.5)
    manifest["paths"] = [str(img_file)]
    manifest["references_root"] = str(tmp_path / "references")

    from toollighthouse.embeddings import references_fingerprint
    descriptions = {c.class_id: list(c.descriptions) for c in inventory.categories}
    names = {c.class_id: c.name for c in inventory.categories}
    expected_ids = {c.class_id for c in inventory.categories}
    fp = references_fingerprint(
        [img_file],
        "models/test",
        descriptions=descriptions,
        names=names,
        text_weight=0.5,
        model_identity=str(Path("models/test").resolve()),
        preprocessing="name-description-mean-normalized-v1",
        gallery_mode="hybrid",
        inventory_ids=expected_ids,
    )

    cache_file = tmp_path / "gallery.json"
    gallery.save(cache_file, fp, manifest)

    # Loading should succeed
    loaded = load_validated_gallery(
        cache_file,
        inventory=inventory,
        model_path="models/test",
        gallery_mode="hybrid",
        text_weight=0.5,
    )
    assert len(loaded.references) == 9
