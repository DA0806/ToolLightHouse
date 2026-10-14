import os
import threading
import copy
import time
import io
from pathlib import Path
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.config import Settings


def _admin_headers(client, admin_key="admin_secret"):
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": admin_key},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


def test_hot_apply_and_rollback_lifecycle(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # 1. Read initial config
    cfg_initial = client.get("/v1/config", headers=headers).json()
    assert "saved_version" in cfg_initial
    assert "active_version" in cfg_initial
    assert cfg_initial["is_dirty"] is False

    # 2. Update configuration (rename display_name, update location)
    current_inventory = copy.deepcopy(cfg_initial["inventory"])
    current_inventory["categories"][0]["display_name"] = "Renamed Display Tool 1"
    current_inventory["categories"][0]["location"] = "drawer_3"

    put_resp = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg_initial["settings"], "inventory": current_inventory},
    )
    assert put_resp.status_code == 200
    cfg_updated = put_resp.json()
    assert cfg_updated["is_dirty"] is True
    assert cfg_updated["saved_version"] > cfg_initial["saved_version"]

    # 3. Hot apply
    apply_resp = client.post("/v1/config/apply", headers=headers)
    assert apply_resp.status_code == 200
    cfg_applied = apply_resp.json()
    assert cfg_applied["is_dirty"] is False
    assert cfg_applied["active_version"] == cfg_applied["saved_version"]
    assert cfg_applied["inventory"]["categories"][0]["display_name"] == "Renamed Display Tool 1"

    # 4. Rollback
    rb_resp = client.post("/v1/config/rollback", headers=headers)
    assert rb_resp.status_code == 200
    cfg_rb = rb_resp.json()
    assert cfg_rb["active_version"] == cfg_rb["saved_version"]
    assert cfg_rb["inventory"]["categories"][0]["location"] == cfg_initial["inventory"]["categories"][0]["location"]


def test_first_mock_workflow_applies_hybrid_metadata_without_model_and_survives_restart(tmp_path):
    """Mock edits must hot-apply even when a hybrid gallery cannot be built yet."""
    config_path = tmp_path / "app_config.json"
    missing_model = tmp_path / "missing-model"
    gallery_path = tmp_path / "missing-gallery.json"
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        gallery_mode="text-only",
        model_path=str(missing_model),
        gallery_path=str(gallery_path),
    )
    client = TestClient(create_app(settings, config_path=config_path))
    headers = _admin_headers(client)

    instructions = client.post("/v1/system/download-model", headers=headers)
    assert instructions.status_code == 200
    assert "python -m toollighthouse download-model" in instructions.json()["detail"]

    initial = client.get("/v1/config", headers=headers).json()
    inventory = copy.deepcopy(initial["inventory"])
    for category in inventory["categories"]:
        category["display_name"] = f"Edited {category['class_id']}"
    inventory["categories"][0]["descriptions"] = ["precision driver", "slim handle"]
    first, second = inventory["categories"][:2]
    first["led_index"], second["led_index"] = second["led_index"], first["led_index"]
    first["pin"], second["pin"] = second["pin"], first["pin"]

    settings_payload = copy.deepcopy(initial["settings"])
    settings_payload.update({
        "gallery_mode": "hybrid",
        "text_weight": 0.65,
        "classifier_threshold": 0.65,
        "inventory_confirmed": True,
    })
    update = client.put(
        "/v1/config",
        headers=headers,
        json={
            "expected_version": initial["saved_version"],
            "settings": settings_payload,
            "inventory": inventory,
        },
    )
    assert update.status_code == 200

    image = Image.new("RGB", (32, 32), color=(220, 220, 220))
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="JPEG")
    uploaded = client.post(
        "/v1/tools/tool_1/references",
        headers=headers,
        files={"file": ("tool.jpg", image_bytes.getvalue(), "image/jpeg")},
    )
    assert uploaded.status_code == 200

    applied = client.post("/v1/config/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    applied_config = applied.json()
    assert applied_config["is_dirty"] is False
    assert applied_config["settings"]["gallery_mode"] == "hybrid"
    assert applied_config["settings"]["classifier_threshold"] == pytest.approx(0.65)
    assert applied_config["inventory"]["categories"][0]["display_name"] == "Edited tool_1"
    assert applied_config["inventory"]["categories"][0]["reference_count"] == 1
    assert not gallery_path.exists()

    # Recreate the app from persisted state with the model still absent.
    restarted = TestClient(create_app(settings, config_path=config_path))
    restarted_headers = _admin_headers(restarted)
    persisted = restarted.get("/v1/config", headers=restarted_headers).json()
    assert persisted["is_dirty"] is False
    assert persisted["settings"]["gallery_mode"] == "hybrid"
    assert persisted["settings"]["inventory_confirmed"] is True
    assert persisted["inventory"]["categories"][0]["descriptions"] == ["precision driver", "slim handle"]
    assert persisted["inventory"]["categories"][0]["reference_count"] == 1
    assert persisted["inventory"]["categories"][0]["display_name"] == "Edited tool_1"


def test_concurrency_conflict_detected_on_version_mismatch(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    cfg = client.get("/v1/config", headers=headers).json()
    base_version = cfg["saved_version"]

    # First update succeeds
    put1 = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg["settings"], "inventory": cfg["inventory"], "expected_version": base_version},
    )
    assert put1.status_code == 200

    # Second update using stale base_version is rejected with 409 Conflict
    put2 = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg["settings"], "inventory": cfg["inventory"], "expected_version": base_version},
    )
    assert put2.status_code == 409
    assert "version mismatch" in put2.json()["detail"].lower()


def test_get_config_status_reports_version_and_task_state(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    status_resp = client.get("/v1/config/status", headers=headers)
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert "saved_version" in data
    assert "active_version" in data
    assert "is_dirty" in data
    assert "task" in data
    assert data["task"]["state"] in ("idle", "running", "completed", "failed")


def test_failed_candidate_build_preserves_active_runtime_and_allows_retry(tmp_path):
    import numpy as np

    class FlakyEmbedder:
        def __init__(self):
            self.should_fail = True

        def encode_text(self, texts):
            if self.should_fail:
                raise RuntimeError("Simulated embedding engine failure")
            vecs = np.asarray([[float(i + 1), 1.0, 0.5] for i, _ in enumerate(texts)], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            return vecs / norms

        def encode_images(self, images):
            vecs = np.asarray([[0.5, float(i + 1), 1.0] for i, _ in enumerate(images)], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            return vecs / norms

    gallery_file = tmp_path / "gallery.json"
    settings = Settings(
        mode="preparation",
        pairing_key="secret",
        admin_key="admin_secret",
        gallery_path=str(gallery_file),
    )
    embedder = FlakyEmbedder()
    app = create_app(settings, config_path=tmp_path / "app_config.json", embedder=embedder)
    client = TestClient(app)
    headers = _admin_headers(client)

    # Trigger candidate build which will fail
    fail_apply = client.post("/v1/config/apply", headers=headers)
    assert fail_apply.status_code == 400
    assert "failed to rebuild gallery" in fail_apply.json()["detail"].lower()

    # Task status reports failed
    task_status = client.get("/v1/config/status", headers=headers).json()
    assert task_status["task"]["state"] == "failed"
    assert "Simulated embedding engine failure" in task_status["task"]["error"]

    # Active system is still in known-good state
    assert client.get("/health").json()["ok"] is True

    # Now fix embedder and retry
    embedder.should_fail = False
    retry_apply = client.post("/v1/config/apply", headers=headers)
    assert retry_apply.status_code == 200

    # Task status now completed
    task_status2 = client.get("/v1/config/status", headers=headers).json()
    assert task_status2["task"]["state"] == "completed"
    assert task_status2["task"]["error"] is None
    assert gallery_file.exists()


def test_import_is_rejected_while_candidate_apply_holds_lifecycle_barrier(tmp_path):
    import numpy as np

    class BlockingEmbedder:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def encode_text(self, texts):
            self.started.set()
            assert self.release.wait(2)
            vectors = np.asarray([[float(index + 1), 1.0, 0.5] for index, _ in enumerate(texts)], dtype=np.float32)
            return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

        def encode_images(self, images):
            vectors = np.asarray([[0.5, float(index + 1), 1.0] for index, _ in enumerate(images)], dtype=np.float32)
            return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    embedder = BlockingEmbedder()
    settings = Settings(
        mode="preparation",
        pairing_key="secret",
        admin_key="admin_secret",
        gallery_path=str(tmp_path / "gallery.json"),
    )
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json", embedder=embedder))
    headers = _admin_headers(client)
    cfg = client.get("/v1/config", headers=headers).json()
    pending_inventory = dict(cfg["inventory"])
    pending_inventory["categories"] = [dict(category) for category in pending_inventory["categories"]]
    pending_inventory["categories"][0]["descriptions"] = ["barrier description"]
    assert client.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg["settings"], "inventory": pending_inventory},
    ).status_code == 200

    result = {}

    def apply_request():
        result["response"] = client.post("/v1/config/apply", headers=headers)

    worker = threading.Thread(target=apply_request)
    worker.start()
    assert embedder.started.wait(1)
    export = client.get("/v1/config/export", headers=headers).json()
    blocked = client.post("/v1/config/import", headers=headers, json=export)
    assert blocked.status_code == 409
    assert "currently running" in blocked.json()["detail"].lower()
    embedder.release.set()
    worker.join(timeout=3)
    assert result["response"].status_code == 200


def test_candidate_promotion_failure_keeps_previous_gallery_cache(tmp_path, monkeypatch):
    import numpy as np

    class StableEmbedder:
        def encode_text(self, texts):
            vectors = np.asarray([[float(index + 1), 1.0, 0.5] for index, _ in enumerate(texts)], dtype=np.float32)
            return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

        def encode_images(self, images):
            vectors = np.asarray([[0.5, float(index + 1), 1.0] for index, _ in enumerate(images)], dtype=np.float32)
            return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    gallery_file = tmp_path / "gallery.json"
    client = TestClient(create_app(
        Settings(mode="preparation", pairing_key="secret", admin_key="admin_secret", gallery_path=str(gallery_file)),
        config_path=tmp_path / "app_config.json",
        embedder=StableEmbedder(),
    ))
    headers = _admin_headers(client)
    assert client.post("/v1/config/apply", headers=headers).status_code == 200
    previous_cache = gallery_file.read_bytes()
    cfg = client.get("/v1/config", headers=headers).json()
    changed_inventory = dict(cfg["inventory"])
    changed_inventory["categories"] = [dict(category) for category in changed_inventory["categories"]]
    changed_inventory["categories"][0]["descriptions"] = ["promotion failure description"]
    assert client.put(
        "/v1/config",
        headers=headers,
        json={"settings": cfg["settings"], "inventory": changed_inventory},
    ).status_code == 200

    original_replace = os.replace

    def fail_gallery_promotion(source, destination):
        if Path(destination) == gallery_file and ".candidate." in str(source):
            raise OSError("synthetic gallery promotion failure")
        return original_replace(source, destination)

    monkeypatch.setattr("toollighthouse.api.app.os.replace", fail_gallery_promotion)
    failed = client.post("/v1/config/apply", headers=headers)
    assert failed.status_code == 400
    assert "promotion failure" in failed.json()["detail"].lower()
    assert gallery_file.read_bytes() == previous_cache
    status = client.get("/v1/config/status", headers=headers).json()
    assert status["is_dirty"] is True
    assert status["active_version"] < status["saved_version"]

