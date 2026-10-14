import json
import io
from pathlib import Path

import pytest
from PIL import Image

from toollighthouse.config import (
    ConfigStore,
    Inventory,
    Settings,
    ToolCategory,
)


def _make_categories():
    return [
        ToolCategory(
            f"tool_{i+1}",
            f"Tool {i+1}",
            "bench_a",
            i,
            i + 2,
            (f"desc {i+1}",),
            display_name=f"Custom Tool {i+1}",
        )
        for i in range(9)
    ]


def test_tool_category_validates_safe_class_id_and_display_name():
    # Valid
    cat = ToolCategory("safe_id-123", "Hammer", "rack_1", 0, 2, ("Steel head",), display_name="Heavy Hammer")
    assert cat.class_id == "safe_id-123"
    assert cat.effective_display_name == "Heavy Hammer"

    # Default display_name falls back to name
    cat_no_display = ToolCategory("safe_id-123", "Hammer", "rack_1", 0, 2, ("Steel head",))
    assert cat_no_display.effective_display_name == "Hammer"

    # Unsafe class_id rejected
    for bad_id in ["../escape", "tool/1", "", ".", "..", "tool\\1", "tool with spaces"]:
        with pytest.raises(ValueError):
            ToolCategory(bad_id, "Name", "bench", 0, 2)


def test_inventory_rejects_duplicate_led_or_pin_or_missing_classes():
    cats = _make_categories()

    # Valid 9 categories
    inv = Inventory(cats)
    assert len(inv.categories) == 9

    # Duplicate LED index
    cats_dup_led = list(cats)
    cats_dup_led[1] = ToolCategory("tool_2", "Tool 2", "bench", 0, 2)
    with pytest.raises(ValueError):
        Inventory(cats_dup_led)

    # Pin must match led_index + 2
    cats_bad_pin = list(cats)
    cats_bad_pin[0] = ToolCategory("tool_1", "Tool 1", "bench", 0, 3)
    with pytest.raises(ValueError):
        Inventory(cats_bad_pin)

    # Not 9 categories
    with pytest.raises(ValueError):
        Inventory(cats[:8])


def test_config_store_atomic_save_load_and_backup(tmp_path):
    target = tmp_path / "app_config.json"
    store = ConfigStore(target)

    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        gallery_mode="hybrid",
        text_weight=0.6,
        roi=(10, 10, 100, 80),
    )
    inventory = Inventory(_make_categories())

    v1 = store.save_atomic(settings, inventory)
    assert v1 >= 1
    assert target.exists()

    loaded_settings, loaded_inventory, loaded_v = store.load()
    assert loaded_v == v1
    assert loaded_settings.gallery_mode == "hybrid"
    assert loaded_settings.text_weight == 0.6
    assert loaded_settings.roi == (10, 10, 100, 80)
    assert loaded_inventory.categories[0].effective_display_name == "Custom Tool 1"

    # Save a second time to create backup
    settings.text_weight = 0.4
    v2 = store.save_atomic(settings, inventory)
    assert v2 > v1
    assert store.backup_path.exists()

    # Load returns v2
    loaded_settings2, _, loaded_v2 = store.load()
    assert loaded_v2 == v2
    assert loaded_settings2.text_weight == 0.4


def test_config_store_recovers_from_corrupted_file(tmp_path):
    target = tmp_path / "app_config.json"
    store = ConfigStore(target)

    settings = Settings(mode="mock", text_weight=0.55)
    inventory = Inventory(_make_categories())

    # Save valid version 1 and 2
    store.save_atomic(settings, inventory)
    settings.text_weight = 0.75
    store.save_atomic(settings, inventory)

    # Corrupt target file with invalid bytes/JSON
    target.write_text("NOT_VALID_JSON{[[", encoding="utf-8")

    # Load should recover from backup
    recovered_settings, recovered_inventory, _ = store.load()
    assert recovered_settings.text_weight in (0.55, 0.75)
    assert len(recovered_inventory.categories) == 9
    # Target file should be re-written with valid data
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1


def test_config_store_rollback(tmp_path):
    target = tmp_path / "app_config.json"
    store = ConfigStore(target)

    settings = Settings(mode="mock", text_weight=0.3)
    inventory = Inventory(_make_categories())

    store.save_atomic(settings, inventory)

    # Change and save v2
    settings.text_weight = 0.8
    store.save_atomic(settings, inventory)

    loaded, _, _ = store.load()
    assert loaded.text_weight == 0.8

    # Rollback to v1 from backup
    rb_settings, rb_inv, rb_v = store.rollback()
    assert rb_settings.text_weight == 0.3
    assert len(rb_inv.categories) == 9


def test_save_atomic_failure_keeps_version_primary_and_backup(tmp_path):
    import toollighthouse.config as config_module

    target = tmp_path / "app_config.json"
    store = ConfigStore(target)
    inventory = Inventory(_make_categories())
    baseline = Settings(mode="mock", text_weight=0.3)
    baseline_copy, baseline_inventory = store._copy_settings(baseline, inventory)
    store.active_snapshot = (baseline_copy, baseline_inventory, 1)
    assert store.save_atomic(baseline, inventory) == 2

    before_primary = target.read_bytes()
    before_version = store.version
    edited = Settings(mode="mock", text_weight=0.8)
    original_replace = config_module.os.replace

    def fail_primary(source, destination):
        if Path(destination) == target:
            raise OSError("injected primary replacement failure")
        return original_replace(source, destination)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config_module.os, "replace", fail_primary)
        with pytest.raises(OSError, match="primary replacement"):
            store.save_atomic(edited, inventory)

    assert store.version == before_version
    assert target.read_bytes() == before_primary
    assert json.loads(store.backup_path.read_text(encoding="utf-8"))["settings"]["text_weight"] == 0.3
    assert not list(tmp_path.glob("*.tmp.*"))

    # A later successful save must still carry the last known-good active
    # snapshot, proving the failed write did not advance in-memory state.
    assert store.save_atomic(edited, inventory) == before_version + 1
    restarted = ConfigStore(target)
    loaded, _, _ = restarted.load()
    assert loaded.text_weight == 0.8
    assert restarted.active_snapshot is not None
    assert restarted.active_snapshot[0].text_weight == 0.3


def test_save_active_failure_does_not_promote_snapshot_or_poison_next_save(tmp_path):
    import toollighthouse.config as config_module

    target = tmp_path / "app_config.json"
    store = ConfigStore(target)
    inventory = Inventory(_make_categories())
    baseline = Settings(mode="mock", classifier_threshold=0.3)
    baseline_copy, baseline_inventory = store._copy_settings(baseline, inventory)
    store.active_snapshot = (baseline_copy, baseline_inventory, 1)
    store.save_atomic(baseline, inventory)
    before_primary = target.read_bytes()
    before_snapshot = store.active_snapshot

    promoted = Settings(mode="mock", classifier_threshold=0.8)
    original_replace = config_module.os.replace

    def fail_primary(source, destination):
        if Path(destination) == target:
            raise OSError("injected active snapshot failure")
        return original_replace(source, destination)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config_module.os, "replace", fail_primary)
        with pytest.raises(OSError, match="active snapshot"):
            store.save_active_atomic(promoted, inventory, 2)

    assert target.read_bytes() == before_primary
    assert before_snapshot is not None
    assert store.active_snapshot is not None
    assert store.active_snapshot[0].classifier_threshold == before_snapshot[0].classifier_threshold == 0.3
    assert not list(tmp_path.glob("*.tmp.*"))

    # A subsequent draft save and restart must continue to expose the old
    # active runtime rather than the failed promotion.
    store.save_atomic(promoted, inventory)
    restarted = ConfigStore(target)
    restarted.load()
    assert restarted.active_snapshot is not None
    assert restarted.active_snapshot[0].classifier_threshold == 0.3


def test_first_edit_rollback_restores_initial_runtime_baseline(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "config" / "app_config.json"
    base = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", classifier_threshold=0.25)
    client = TestClient(create_app(base, config_path=config_file))
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "rollback-baseline", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    initial = client.get("/v1/config", headers=headers).json()
    edited_settings = dict(initial["settings"])
    edited_settings["classifier_threshold"] = 0.85

    saved = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": edited_settings, "inventory": initial["inventory"], "expected_version": initial["saved_version"]},
    )
    assert saved.status_code == 200
    assert saved.json()["is_dirty"] is True

    rolled_back = client.post("/v1/config/rollback", headers=headers)
    assert rolled_back.status_code == 200
    result = rolled_back.json()
    assert result["settings"]["classifier_threshold"] == 0.25
    assert result["is_dirty"] is False
    assert result["active_version"] == result["saved_version"]


def test_config_export_and_import_portable(tmp_path):
    target = tmp_path / "app_config.json"
    store = ConfigStore(target)

    local_settings = Settings(
        mode="mock",
        pairing_key="super_secret_key",
        admin_key="super_admin_key",
        background_path="D:/local/path/bg.png",
        model_path="D:/local/path/model",
        gallery_mode="hybrid",
        text_weight=0.45,
        roi=(0, 0, 200, 150),
    )
    inventory = Inventory(_make_categories())

    portable = store.export_portable(local_settings, inventory)

    # Secrets and machine-local paths must NOT be in portable export
    assert "super_secret_key" not in json.dumps(portable)
    assert "super_admin_key" not in json.dumps(portable)
    assert "D:/local" not in json.dumps(portable)
    assert portable["type"] == "portable_configuration"
    assert "notice" in portable

    # Import into another machine settings
    other_machine_settings = Settings(
        mode="mock",
        pairing_key="other_key",
        admin_key="other_admin",
        background_path="C:/other/bg.png",
        model_path="C:/other/model",
    )

    imported_settings, imported_inventory = store.import_portable(portable, other_machine_settings)

    # Imported config has the portable values
    assert imported_settings.gallery_mode == "hybrid"
    assert imported_settings.text_weight == 0.45
    assert imported_settings.roi == (0, 0, 200, 150)
    assert len(imported_inventory.categories) == 9

    # Local credentials and paths on the second machine are preserved
    assert imported_settings.pairing_key == "other_key"
    assert imported_settings.admin_key == "other_admin"
    assert imported_settings.background_path == "C:/other/bg.png"
    assert imported_settings.model_path == "C:/other/model"

    # Reject import with unknown fields
    bad_portable = dict(portable)
    bad_portable["settings"] = dict(portable["settings"], unknown_field="evil")
    with pytest.raises(ValueError, match="unknown"):
        store.import_portable(bad_portable, other_machine_settings)


def test_put_config_modifies_draft_without_altering_active_pipeline_before_apply(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app
    from toollighthouse.pipeline import Pipeline

    class FakeEmbedder:
        def __init__(self):
            self.text_calls = []
        def encode_text(self, texts):
            import numpy as np
            self.text_calls.append(list(texts))
            vecs = np.asarray([[float(i + 1), 1.0, 0.5] for i, _ in enumerate(texts)], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            return vecs / norms
        def encode_images(self, images):
            import numpy as np
            vecs = np.asarray([[0.5, float(i + 1), 1.0] for i, _ in enumerate(images)], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            return vecs / norms

    config_file = tmp_path / "app_config.json"
    gallery_file = tmp_path / "gallery.json"
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        classifier_threshold=0.3,
        gallery_path=str(gallery_file),
    )
    fake_embedder = FakeEmbedder()
    pipeline = Pipeline(settings, embedder=fake_embedder, safe_startup=True)
    app = create_app(settings, pipeline, config_path=config_file, embedder=fake_embedder)
    client = TestClient(app)

    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "test_device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    # 1. Read initial config
    cfg_initial = client.get("/v1/config", headers=headers).json()
    assert cfg_initial["is_dirty"] is False
    assert pipeline.settings.classifier_threshold == 0.3

    # 2. PUT config modifies draft (change descriptions, LED location, display_name, and threshold)
    new_settings = dict(cfg_initial["settings"])
    new_settings["classifier_threshold"] = 0.75
    new_inv = dict(cfg_initial["inventory"])
    new_inv["categories"][0]["display_name"] = "Pending Display Name"
    new_inv["categories"][0]["descriptions"] = ["brand new description"]
    new_inv["categories"][0]["location"] = "new_bin_42"

    put_resp = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": new_settings, "inventory": new_inv},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["is_dirty"] is True
    assert put_data["saved_version"] > put_data["active_version"]

    # BEFORE APPLY: Active pipeline and public inventory MUST NOT be mutated!
    assert pipeline.settings.classifier_threshold == 0.3
    assert pipeline.settings.inventory.categories[0].descriptions != ("brand new description",)
    assert pipeline.settings.inventory.categories[0].location != "new_bin_42"
    assert pipeline.serial.status()["confirmed_mask"] == 0
    active_inv_resp = client.get("/v1/inventory").json()
    assert active_inv_resp["categories"][0].get("display_name") != "Pending Display Name"
    assert active_inv_resp["categories"][0].get("location") != "new_bin_42"

    # 3. Apply config: active pipeline updates
    apply_resp = client.post("/v1/config/apply", headers=headers)
    assert apply_resp.status_code == 200
    apply_data = apply_resp.json()
    assert apply_data["is_dirty"] is False
    assert apply_data["active_version"] == apply_data["saved_version"]

    # AFTER APPLY: Active pipeline and public inventory ARE mutated!
    assert pipeline.settings.classifier_threshold == 0.75
    assert pipeline.settings.inventory.categories[0].descriptions == ("brand new description",)
    assert pipeline.settings.inventory.categories[0].location == "new_bin_42"
    active_inv_resp2 = client.get("/v1/inventory").json()
    assert active_inv_resp2["categories"][0].get("display_name") == "Pending Display Name"
    assert active_inv_resp2["categories"][0].get("location") == "new_bin_42"


def test_server_restart_reloads_persisted_config_and_saved_version(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", classifier_margin=0.1)

    # First app run
    app1 = create_app(settings, config_path=config_file)
    client1 = TestClient(app1)
    session = client1.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "test_device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    cfg1 = client1.get("/v1/config", headers=headers).json()
    new_settings = dict(cfg1["settings"])
    new_settings["classifier_margin"] = 0.25
    client1.put(
        "/v1/config",
        headers=headers,
        json={"settings": new_settings, "inventory": cfg1["inventory"]},
    )
    client1.post("/v1/config/apply", headers=headers)

    # Server restart: create_app reloaded with same config_file
    app2 = create_app(Settings(mode="mock", pairing_key="secret", admin_key="admin_secret"), config_path=config_file)
    client2 = TestClient(app2)
    session2 = client2.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "test_device", "role": "admin"},
    ).json()
    headers2 = {"X-Session-Id": session2["session_id"], "X-Session-Ticket": session2["ticket"]}

    cfg2 = client2.get("/v1/config", headers=headers2).json()
    assert cfg2["settings"]["classifier_margin"] == 0.25
    assert cfg2["is_dirty"] is False
    assert cfg2["saved_version"] == cfg2["active_version"]
    assert cfg2["saved_version"] >= 2


def test_corrupt_config_recovers_valid_previous_on_create_app(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", classifier_threshold=0.45)

    app1 = create_app(settings, config_path=config_file)
    client1 = TestClient(app1)
    session = client1.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "test_device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    cfg1 = client1.get("/v1/config", headers=headers).json()
    new_settings = dict(cfg1["settings"])
    new_settings["classifier_threshold"] = 0.65
    client1.put(
        "/v1/config",
        headers=headers,
        json={"settings": new_settings, "inventory": cfg1["inventory"]},
    )
    client1.post("/v1/config/apply", headers=headers)

    # Corrupt target file
    config_file.write_text("CORRUPTED_JSON_NOT_VALID", encoding="utf-8")

    # Restart should recover from backup automatically without crashing
    app2 = create_app(Settings(mode="mock", pairing_key="secret", admin_key="admin_secret"), config_path=config_file)
    client2 = TestClient(app2)
    session2 = client2.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "test_device", "role": "admin"},
    ).json()
    headers2 = {"X-Session-Id": session2["session_id"], "X-Session-Ticket": session2["ticket"]}

    cfg2 = client2.get("/v1/config", headers=headers2).json()
    assert cfg2["settings"]["classifier_threshold"] in (0.45, 0.65)


def test_saved_only_draft_survives_restart_without_promoting_active_snapshot(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "config" / "app_config.json"
    base = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client1 = TestClient(create_app(base, config_path=config_file))
    session = client1.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "draft-device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    cfg = client1.get("/v1/config", headers=headers).json()
    pending = dict(cfg["inventory"])
    pending["categories"] = [dict(category) for category in pending["categories"]]
    pending["categories"][0]["descriptions"] = ["pending restart description"]
    response = client1.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg["settings"], "inventory": pending, "expected_version": cfg["saved_version"]},
    )
    assert response.status_code == 200
    assert response.json()["is_dirty"] is True

    client2 = TestClient(create_app(Settings(mode="mock", pairing_key="secret", admin_key="admin_secret"), config_path=config_file))
    session2 = client2.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "draft-device-2", "role": "admin"},
    ).json()
    headers2 = {"X-Session-Id": session2["session_id"], "X-Session-Ticket": session2["ticket"]}
    cfg2 = client2.get("/v1/config", headers=headers2).json()

    assert cfg2["is_dirty"] is True
    assert cfg2["saved_version"] > cfg2["active_version"]
    assert cfg2["inventory"]["categories"][0]["descriptions"] == ["pending restart description"]
    # Public runtime inventory is the active snapshot until an explicit apply.
    assert client2.get("/v1/inventory").json()["categories"][0]["descriptions"] != ["pending restart description"]


def test_reference_path_counts_and_versions_survive_restart(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "config" / "app_config.json"
    client1 = TestClient(create_app(Settings(mode="mock", pairing_key="secret", admin_key="admin_secret"), config_path=config_file))
    session = client1.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "reference-device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    image = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(image, format="JPEG")
    upload = client1.post(
        "/v1/tools/tool_1/references",
        headers=headers,
        files={"file": ("reference.jpg", image.getvalue(), "image/jpeg")},
    )
    assert upload.status_code == 200
    cfg1 = client1.get("/v1/config", headers=headers).json()
    assert cfg1["inventory"]["categories"][0]["reference_count"] == 1
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert Path(persisted["settings"]["gallery_path"]) == tmp_path / "config" / "data" / "cache" / "gallery.json"
    assert Path(persisted["settings"]["gallery_path"]).parent == tmp_path / "config" / "data" / "cache"

    client2 = TestClient(create_app(Settings(mode="mock", pairing_key="secret", admin_key="admin_secret"), config_path=config_file))
    session2 = client2.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "reference-device-2", "role": "admin"},
    ).json()
    headers2 = {"X-Session-Id": session2["session_id"], "X-Session-Ticket": session2["ticket"]}
    refs = client2.get("/v1/tools/tool_1/references", headers=headers2)
    assert refs.status_code == 200
    assert len(refs.json()["references"]) == 1
    cfg2 = client2.get("/v1/config", headers=headers2).json()
    assert cfg2["inventory"]["categories"][0]["reference_count"] == 1
    assert cfg2["is_dirty"] is True
    assert cfg2["saved_version"] > cfg2["active_version"]


def test_calibration_and_mode_changes_do_not_promote_unrelated_pending_draft(tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.api import create_app

    config_file = tmp_path / "config" / "app_config.json"
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        background_path=str(tmp_path / "background.png"),
    )
    client = TestClient(create_app(settings, config_path=config_file))
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "calibration-device", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    cfg = client.get("/v1/config", headers=headers).json()
    pending = dict(cfg["inventory"])
    pending["categories"] = [dict(category) for category in pending["categories"]]
    pending["categories"][0]["descriptions"] = ["pending while calibrating"]
    assert client.put("/v1/config", headers=headers, json={"settings": cfg["settings"], "inventory": pending}).status_code == 200
    initial_status = client.get("/v1/config/status", headers=headers).json()
    assert initial_status["is_dirty"] is True
    active_version = initial_status["active_version"]

    assert client.put("/v1/calibration/roi", headers=headers, json={"roi": [0, 0, 20, 20]}).status_code == 200
    after_roi = client.get("/v1/config/status", headers=headers).json()
    assert after_roi["is_dirty"] is True
    assert after_roi["active_version"] == active_version

    assert client.post("/v1/system/mode", headers=headers, json={"mode": "mock"}).status_code == 200
    after_mode = client.get("/v1/config/status", headers=headers).json()
    assert after_mode["is_dirty"] is True
    assert after_mode["active_version"] == active_version

    image = io.BytesIO()
    Image.new("RGB", (32, 32), "gray").save(image, format="JPEG")
    assert client.post(
        "/v1/calibration/background",
        headers=headers,
        files={"file": ("background.jpg", image.getvalue(), "image/jpeg")},
    ).status_code == 200
    after_background = client.get("/v1/config/status", headers=headers).json()
    assert after_background["is_dirty"] is True
    assert after_background["active_version"] == active_version

