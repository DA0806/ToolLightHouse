import json
import sys
import types

import pytest

import numpy as np
from PIL import Image

from toollighthouse.classifier import SimilarityClassifier
from toollighthouse.config import Inventory, Settings, ToolCategory
from toollighthouse.embeddings import references_fingerprint
from toollighthouse.gallery import Gallery, build_gallery, load_validated_gallery


def _inventory():
    return Inventory([
        ToolCategory(f"tool_{index + 1}", f"Tool {index + 1}", "bench", index, index + 2, (f"description {index + 1}", f"alternate {index + 1}"))
        for index in range(9)
    ])


class FakeEmbedder:
    def __init__(self):
        self.text_calls = []
        self.image_calls = 0

    def encode_text(self, texts):
        self.text_calls.append(list(texts))
        return np.asarray([[float(index + 1), 1.0, 0.0] for index, _ in enumerate(texts)], dtype=np.float32)

    def encode_images(self, images):
        self.image_calls += 1
        return np.tile(np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32), (len(images), 1))


def test_text_only_builds_all_nine_classes_without_reference_directory():
    embedder = FakeEmbedder()
    gallery, manifest = build_gallery(embedder, _inventory(), mode="text-only", image_paths=[])
    assert set(gallery.references) == {f"tool_{index + 1}" for index in range(9)}
    assert embedder.image_calls == 0
    assert len(embedder.text_calls[0]) == 18
    assert manifest["image_count"] == 0


def test_image_only_never_encodes_text_and_hybrid_combines_normalized_prototypes(tmp_path):
    paths = []
    for index in range(9):
        target = tmp_path / f"tool_{index + 1}" / "sample.png"
        target.parent.mkdir()
        Image.new("RGB", (4, 4), "white").save(target)
        paths.append(target)
    image_embedder = FakeEmbedder()
    image_gallery, _ = build_gallery(image_embedder, _inventory(), mode="image-only", image_paths=paths)
    assert image_embedder.text_calls == []
    assert all(np.isclose(np.linalg.norm(value), 1.0) for value in image_gallery.references.values())
    hybrid_embedder = FakeEmbedder()
    hybrid_gallery, _ = build_gallery(hybrid_embedder, _inventory(), mode="hybrid", image_paths=paths, text_weight=0.25)
    assert hybrid_embedder.image_calls == 1
    assert hybrid_embedder.text_calls
    assert all(np.isclose(np.linalg.norm(value), 1.0) for value in hybrid_gallery.references.values())


def test_settings_profiles_and_classifier_reject_unknown_and_ambiguous():
    settings = Settings(gallery_mode="hybrid", classifier_profiles={"hybrid": {"threshold": 0.4, "margin": 0.2}})
    assert settings.classifier_parameters() == (0.4, 0.2)
    classifier = SimilarityClassifier({"a": np.array([1.0, 0.0]), "b": np.array([0.99, 0.1])}, 0.8, 0.5, gallery_mode="text-only")
    assert classifier.predict(np.array([0.0, 1.0]), 1).validation == "unknown"
    assert classifier.predict(np.array([1.0, 0.1]), 2).validation == "ambiguous"


def test_inventory_rejects_empty_names_and_scalar_descriptions(tmp_path):
    with pytest.raises(ValueError, match="name"):
        ToolCategory("tool", "", "bench", 0, 2)
    with pytest.raises(ValueError, match="descriptions"):
        ToolCategory("tool", "Tool", "bench", 0, 2, "one description")
    with pytest.raises(ValueError, match="descriptions"):
        ToolCategory("tool", "Tool", "bench", 0, 2, ("",))
    payload = {"categories": [{"class_id": f"tool_{index + 1}", "name": f"Tool {index + 1}", "location": "bench", "led_index": index, "pin": index + 2, "descriptions": "scalar"} for index in range(9)]}
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    from toollighthouse.config import Inventory
    with pytest.raises(ValueError, match="descriptions"):
        Inventory.from_file(path)


@pytest.mark.parametrize("mode", ["text-only", "image-only", "hybrid"])
def test_classifier_reports_confirmed_unknown_and_ambiguous_for_each_gallery_mode(mode):
    classifier = SimilarityClassifier({"a": np.array([1.0, 0.0]), "b": np.array([0.8, 0.6])}, 0.8, 0.05, gallery_mode=mode)
    assert classifier.predict(np.array([1.0, 0.0]), 1).validation == "confirmed"
    assert classifier.predict(np.array([0.0, 1.0]), 2).validation == "unknown"
    ambiguous = SimilarityClassifier({"a": np.array([1.0, 0.0]), "b": np.array([0.99, 0.1])}, 0.8, 0.5, gallery_mode=mode)
    assert ambiguous.predict(np.array([1.0, 0.1]), 3).validation == "ambiguous"


def test_hybrid_uses_text_weight_before_final_normalization(tmp_path):
    class BlendEmbedder:
        def encode_text(self, texts):
            return np.tile(np.array([[0.0, 1.0]], dtype=np.float32), (len(texts), 1))

        def encode_images(self, images):
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(images), 1))

    paths = []
    for index in range(9):
        target = tmp_path / f"tool_{index + 1}" / "sample.png"
        target.parent.mkdir()
        Image.new("RGB", (2, 2), "white").save(target)
        paths.append(target)
    gallery, _ = build_gallery(BlendEmbedder(), _inventory(), mode="hybrid", image_paths=paths, text_weight=0.25)
    assert np.allclose(gallery.references["tool_1"], np.array([0.9486833, 0.3162278]), atol=1e-6)


def test_cli_build_gallery_defaults_to_text_only_without_images(monkeypatch, tmp_path):
    import toollighthouse.embeddings as embeddings
    from toollighthouse.__main__ import main

    class FakeEngine(FakeEmbedder):
        def __init__(self, *args, **kwargs):
            super().__init__()

    engine = FakeEngine()
    monkeypatch.setattr(embeddings, "EmbeddingEngine", lambda *args, **kwargs: engine)
    output = tmp_path / "nested" / "gallery.json"
    main(["build-gallery", "--model-path", str(tmp_path / "model"), "--output", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["manifest"]["gallery_mode"] == "text-only"
    assert payload["manifest"]["image_count"] == 0
    assert engine.image_calls == 0
    assert len(payload["references"]) == 9


def test_cli_download_model_uses_current_huggingface_signature(monkeypatch, tmp_path):
    from toollighthouse.__main__ import main
    calls = []
    def snapshot_download(*, repo_id, local_dir):
        calls.append({"repo_id": repo_id, "local_dir": local_dir})

    fake_hub = types.SimpleNamespace(snapshot_download=snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    main(["download-model", "--model-path", str(tmp_path / "model")])
    assert calls == [{"repo_id": "google/embeddinggemma-2", "local_dir": str(tmp_path / "model")}]


def test_mock_skips_configured_gallery_and_model(monkeypatch):
    import toollighthouse.pipeline as pipeline_module

    def forbidden(*args, **kwargs):
        raise AssertionError("mock must not read a gallery or its model")

    monkeypatch.setattr(pipeline_module, "load_validated_gallery", forbidden)
    pipeline = pipeline_module.Pipeline(Settings(
        mode="mock", gallery_path="unused-gallery.json", model_path="unused-model",
        background_path=None, roi_path=None,
    ))
    assert pipeline.embedder is None
    assert pipeline.classifier.predict(np.zeros((4, 4, 3)), 1).validation == "simulated"


def _save_text_cache(tmp_path, *, model_path=None):
    inventory = _inventory()
    model = model_path or tmp_path / "model"
    model.mkdir(exist_ok=True)
    gallery, manifest = build_gallery(FakeEmbedder(), inventory, mode="text-only")
    manifest.update({"paths": [], "references_root": str(tmp_path / "references")})
    fingerprint = references_fingerprint([], str(model), descriptions=manifest["descriptions"], names=manifest["names"], text_weight=0.5, model_identity=str(model.resolve()), preprocessing=manifest["preprocessing"], gallery_mode="text-only", inventory_ids={category.class_id for category in inventory.categories})
    cache = tmp_path / "gallery.json"
    gallery.save(cache, fingerprint, manifest=manifest)
    return cache, model, inventory


def test_text_gallery_cache_rejects_mode_switch_and_model_content_change(tmp_path):
    cache, model, inventory = _save_text_cache(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        load_validated_gallery(cache, inventory=inventory, model_path=str(model), gallery_mode="image-only", text_weight=0.5)
    (model / "config.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="stale|rebuild"):
        load_validated_gallery(cache, inventory=inventory, model_path=str(model), gallery_mode="text-only", text_weight=0.5)


@pytest.mark.parametrize("mutation", ["add", "delete"])
def test_image_gallery_cache_rejects_reference_set_changes(tmp_path, mutation):
    inventory = _inventory()
    root = tmp_path / "references"
    paths = []
    for index in range(9):
        target = root / f"tool_{index + 1}" / "sample.png"
        target.parent.mkdir(parents=True)
        Image.new("RGB", (2, 2), "white").save(target)
        paths.append(target)
    gallery, manifest = build_gallery(FakeEmbedder(), inventory, mode="image-only", image_paths=paths)
    model = tmp_path / "model"
    model.mkdir()
    manifest.update({"paths": [str(path) for path in paths], "references_root": str(root)})
    fingerprint = references_fingerprint(paths, str(model), descriptions=manifest["descriptions"], names=manifest["names"], text_weight=0.5, model_identity=str(model.resolve()), preprocessing=manifest["preprocessing"], gallery_mode="image-only", inventory_ids={category.class_id for category in inventory.categories})
    cache = tmp_path / "gallery.json"
    gallery.save(cache, fingerprint, manifest=manifest)
    extra = root / "tool_1" / "extra.png"
    if mutation == "add":
        Image.new("RGB", (2, 2), "black").save(extra)
    else:
        paths[0].unlink()
    with pytest.raises(ValueError, match="reference image set|missing"):
        load_validated_gallery(cache, inventory=inventory, model_path=str(model), gallery_mode="image-only", text_weight=0.5)


def test_cli_real_text_only_does_not_require_photographs(monkeypatch, tmp_path):
    import toollighthouse.embeddings as embeddings
    from toollighthouse.__main__ import main

    monkeypatch.setattr(embeddings, "EmbeddingEngine", lambda *args, **kwargs: FakeEmbedder())
    output = tmp_path / "gallery.json"
    main(["build-gallery", "--mode", "real", "--gallery-mode", "text-only", "--references", str(tmp_path / "does-not-exist"), "--model-path", str(tmp_path / "model"), "--output", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["manifest"]["gallery_mode"] == "text-only"
    assert payload["manifest"]["image_count"] == 0


def test_cli_active_profile_wins_until_explicit_flags_override(monkeypatch, tmp_path, capsys):
    import toollighthouse.embeddings as embeddings
    from toollighthouse.__main__ import main

    monkeypatch.setattr(embeddings, "EmbeddingEngine", lambda *args, **kwargs: FakeEmbedder())
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"gallery_mode": "text-only", "classifier_threshold": 0.72, "classifier_margin": 0.08, "classifier_profiles": {"text-only": {"threshold": 0.61, "margin": 0.17}}}), encoding="utf-8")
    output = tmp_path / "profile.json"
    main(["build-gallery", "--config", str(config), "--model-path", str(tmp_path / "model"), "--output", str(output)])
    profile_report = json.loads(capsys.readouterr().out)
    assert (profile_report["threshold"], profile_report["margin"]) == (0.61, 0.17)
    main(["build-gallery", "--config", str(config), "--classifier-threshold", "0.31", "--classifier-margin", "0.12", "--model-path", str(tmp_path / "model"), "--output", str(output)])
    flag_report = json.loads(capsys.readouterr().out)
    assert (flag_report["threshold"], flag_report["margin"]) == (0.31, 0.12)


@pytest.mark.parametrize("profile", [{"threshold": float("nan")}, {"threshold": 1.1}, {"margin": 2.1}])
def test_settings_rejects_invalid_classifier_profiles(tmp_path, profile):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"classifier_profiles": {"text-only": profile}}), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        Settings.from_file(path)


@pytest.mark.parametrize("field", ["classifier_threshold", "classifier_margin", "text_weight"])
def test_settings_rejects_nonfinite_classifier_configuration(tmp_path, field):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({field: "Infinity"}), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        Settings.from_file(path)


def test_gallery_rejects_old_recipe_missing_mode_and_invalid_shape(tmp_path):
    inventory = _inventory()
    model = tmp_path / "model"
    model.mkdir()
    gallery, manifest = build_gallery(FakeEmbedder(), inventory, mode="text-only")
    manifest.pop("gallery_mode")
    manifest.update({"paths": [], "references_root": str(tmp_path / "missing")})
    cache = tmp_path / "gallery.json"
    fingerprint = references_fingerprint([], str(model), descriptions=manifest["descriptions"], names=manifest["names"], text_weight=0.5, model_identity=str(model.resolve()), preprocessing=manifest["preprocessing"], gallery_mode="text-only", inventory_ids={category.class_id for category in inventory.categories})
    gallery.save(cache, fingerprint, manifest=manifest)
    with pytest.raises(ValueError, match="mode|rebuild"):
        load_validated_gallery(cache, inventory=inventory, model_path=str(model), gallery_mode="text-only", text_weight=0.5)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["manifest"]["gallery_mode"] = "text-only"
    payload["references"]["tool_1"] = [1.0, 0.0]
    payload["references"]["tool_2"] = [1.0, 0.0, 0.0]
    cache.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="dimension|stale"):
        Gallery.load(cache, fingerprint, expected_class_ids={category.class_id for category in inventory.categories})


def test_text_gallery_cache_invalidates_inventory_recipe(tmp_path):
    inventory = _inventory()
    embedder = FakeEmbedder()
    gallery, manifest = build_gallery(embedder, inventory, mode="text-only")
    model = tmp_path / "model"
    model.mkdir()
    paths = []
    manifest.update({"paths": paths, "references_root": str(tmp_path / "missing-references")})
    fingerprint = references_fingerprint(paths, str(model), descriptions=manifest["descriptions"], names=manifest["names"], text_weight=0.5, model_identity=str(model.resolve()), preprocessing=manifest["preprocessing"], gallery_mode="text-only", inventory_ids={category.class_id for category in inventory.categories})
    cache = tmp_path / "gallery.json"
    gallery.save(cache, fingerprint, manifest=manifest)
    load_validated_gallery(cache, inventory=inventory, model_path=str(model), gallery_mode="text-only", text_weight=0.5)
    # A replacement inventory with changed text must fail before loading vectors.
    changed = Inventory([ToolCategory(category.class_id, category.name + " changed" if category.class_id == "tool_1" else category.name, category.location, category.led_index, category.pin, category.descriptions) for category in inventory.categories])
    try:
        load_validated_gallery(cache, inventory=changed, model_path=str(model), gallery_mode="text-only", text_weight=0.5)
    except ValueError as exc:
        assert "recipe" in str(exc)
    else:
        raise AssertionError("changed inventory recipe was accepted")
