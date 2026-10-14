import io
import time
import pytest
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SimulatedSerial


def _create_synthetic_jpeg(width: int = 640, height: int = 480, color=(128, 128, 128)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class FakeEmbedder:
    def __init__(self):
        self.text_calls = []
        self.image_calls = 0

    def encode_text(self, texts):
        self.text_calls.append(list(texts))
        vecs = np.asarray([[float(i + 1), 1.0, 0.5] for i, _ in enumerate(texts)], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms

    def encode_images(self, images):
        self.image_calls += 1
        vecs = np.asarray([[0.5, float(i + 1), 1.0] for i, _ in enumerate(images)], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms


def test_full_configuration_lifecycle_e2e(tmp_path):
    # Setup test paths in tmp_path
    config_file = tmp_path / "app_config.json"
    gallery_file = tmp_path / "gallery.json"
    bg_path = tmp_path / "background.png"
    roi_path = tmp_path / "roi.json"

    # 1. Startup in preparation mode with no background or gallery
    settings = Settings(
        mode="preparation",
        pairing_key="pair123",
        admin_key="admin_super_secret",
        gallery_path=str(gallery_file),
        background_path=str(bg_path),
        roi_path=str(roi_path),
    )

    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    fake_embedder = FakeEmbedder()
    app = create_app(settings, pipeline, config_path=config_file, embedder=fake_embedder)
    client = TestClient(app)

    # Health check is public and reports preparation mode
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["mode"] == "preparation"

    # Unauthenticated / reader attempts to access endpoints fail appropriately
    unauth_readiness = client.get("/v1/system/readiness")
    assert unauth_readiness.status_code == 401

    unauth_resp = client.put("/v1/config", json={"settings": {}, "inventory": []})
    assert unauth_resp.status_code in {401, 403}

    # 2. Authentication: Pair as Admin
    admin_session_resp = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_super_secret"},
        json={"device_id": "e2e_tester", "role": "admin"},
    )
    assert admin_session_resp.status_code == 200
    session_data = admin_session_resp.json()
    assert session_data["role"] == "admin"
    admin_headers = {
        "X-Session-Id": session_data["session_id"],
        "X-Session-Ticket": session_data["ticket"],
    }

    # Authenticated readiness check reports preparation reasons
    readiness = client.get("/v1/system/readiness", headers=admin_headers).json()
    assert readiness["backend_ok"] is True
    assert readiness["active_mode"] == "preparation"
    assert readiness["can_activate_real"] is False
    assert readiness["calibration_ready"] is False
    assert len(readiness["preparation_reasons"]) > 0

    # Inferences in preparation mode are blocked with 409 Conflict
    photo_bytes = _create_synthetic_jpeg(640, 480)
    blocked = client.post(
        "/v1/analyze/photo",
        headers=admin_headers,
        files={"file": ("snap.jpg", photo_bytes, "image/jpeg")},
    )
    assert blocked.status_code == 409
    assert "preparation" in blocked.json()["detail"].lower()

    # 3. Background Upload & ROI Calibration
    bg_jpeg = _create_synthetic_jpeg(640, 480, color=(100, 150, 200))
    bg_resp = client.post(
        "/v1/calibration/background",
        headers=admin_headers,
        files={"file": ("bg.jpg", bg_jpeg, "image/jpeg")},
    )
    assert bg_resp.status_code == 200
    bg_json = bg_resp.json()
    assert bg_json["calibrated"] is True
    assert bg_json["image"]["width"] == 640
    assert bg_json["image"]["height"] == 480

    # Test out-of-bounds ROI rejection (bounds exceeded)
    invalid_roi = client.put(
        "/v1/calibration/roi",
        headers=admin_headers,
        json={"roi": [0, 0, 800, 600]},  # Exceeds 640x480
    )
    assert invalid_roi.status_code == 400

    # Save valid ROI
    valid_roi = client.put(
        "/v1/calibration/roi",
        headers=admin_headers,
        json={"roi": [20, 20, 600, 440]},
    )
    assert valid_roi.status_code == 200
    assert valid_roi.json()["roi"] == [20, 20, 600, 440]

    # 4. Reference uploads (partial hybrid: tool_1 gets a photo)
    ref1_bytes = _create_synthetic_jpeg(200, 200, color=(255, 0, 0))
    upload1 = client.post(
        "/v1/tools/tool_1/references",
        headers=admin_headers,
        files={"file": ("ref1.jpg", ref1_bytes, "image/jpeg")},
    )
    assert upload1.status_code == 200
    ref_info = upload1.json()
    assert "image_id" in ref_info
    assert "thumbnail_data_url" in ref_info

    # Attempt path traversal on tool references
    escape_attempt = client.post(
        "/v1/tools/..%2f..%2fetc/references",
        headers=admin_headers,
        files={"file": ("ref1.jpg", ref1_bytes, "image/jpeg")},
    )
    assert escape_attempt.status_code in {400, 404, 422}

    # List references for tool_1
    refs_list = client.get("/v1/tools/tool_1/references", headers=admin_headers)
    assert refs_list.status_code == 200
    refs_data = refs_list.json()
    assert refs_data["class_id"] == "tool_1"
    assert len(refs_data["references"]) >= 1
    assert "data:image/jpeg;base64," in refs_data["references"][0]["thumbnail_data_url"]

    # 5. Config update & Hot-apply
    cfg_resp = client.get("/v1/config", headers=admin_headers).json()
    saved_settings = cfg_resp["settings"]
    saved_inv = cfg_resp["inventory"]
    assert len(saved_inv["categories"]) == 9

    # Update tool_1 with custom display name
    saved_inv["categories"][0]["display_name"] = "Precision Screwdriver"
    saved_inv["categories"][0]["descriptions"] = ["small phillips screwdriver", "magnetic tip"]

    update_resp = client.put(
        "/v1/config",
        headers=admin_headers,
        json={
            "expected_version": cfg_resp["saved_version"],
            "settings": saved_settings,
            "inventory": saved_inv,
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["is_dirty"] is True

    # Hot-apply the configuration
    apply_resp = client.post(
        "/v1/config/apply",
        headers=admin_headers,
    )
    assert apply_resp.status_code == 200
    apply_json = apply_resp.json()
    assert apply_json["is_dirty"] is False
    assert apply_json["active_version"] == update_resp.json()["saved_version"]

    # Switch system mode to mock to allow full inference verification without downloading multi-GB weights
    mode_switch = client.post("/v1/system/mode", headers=admin_headers, json={"mode": "mock"})
    assert mode_switch.status_code == 200
    assert mode_switch.json()["mode"] == "mock"
    assert pipeline.mode == "mock"

    # 6. Runtime analysis & Display name verification
    analyze_resp = client.post(
        "/v1/analyze/photo",
        headers=admin_headers,
        files={"file": ("snap.jpg", photo_bytes, "image/jpeg")},
    )
    assert analyze_resp.status_code == 200
    analysis = analyze_resp.json()
    assert "frame_id" in analysis
    assert analysis["mode"] == "mock"
    assert "detections" in analysis
    assert "arduino" in analysis

    # 7. Arduino Maintenance Test
    # Check enumerated COM ports
    ports_resp = client.get("/v1/arduino/ports", headers=admin_headers)
    assert ports_resp.status_code == 200
    assert "ports" in ports_resp.json()

    # Enter maintenance mode
    maint_on = client.post("/v1/arduino/maintenance/enter", headers=admin_headers)
    assert maint_on.status_code == 200
    assert maint_on.json()["maintenance_mode"] is True

    # Trigger single-LED test (LED index 0 -> mask 1) with duration 0.2s
    led_test = client.post(
        "/v1/arduino/maintenance/test-led/0?duration=0.2",
        headers=admin_headers,
    )
    assert led_test.status_code == 200
    assert led_test.json()["mask"] == 1
    assert led_test.json()["arduino"]["confirmed_mask"] == 1

    # Inference during maintenance is blocked
    maint_blocked = client.post(
        "/v1/analyze/photo",
        headers=admin_headers,
        files={"file": ("snap.jpg", photo_bytes, "image/jpeg")},
    )
    assert maint_blocked.status_code == 409
    assert "maintenance" in maint_blocked.json()["detail"].lower()

    # Wait for auto-off timer
    time.sleep(0.3)
    # Check that mask returned to 0
    status_resp = client.get("/v1/status", headers=admin_headers).json()
    assert status_resp["arduino"]["confirmed_mask"] == 0

    # Exit maintenance mode
    maint_off = client.post("/v1/arduino/maintenance/leave", headers=admin_headers)
    assert maint_off.status_code == 200
    assert maint_off.json()["maintenance_mode"] is False

    # 8. Portable Export / Import
    export_resp = client.get("/v1/config/export", headers=admin_headers)
    assert export_resp.status_code == 200
    export_data = export_resp.json()
    assert "inventory" in export_data
    assert "settings" in export_data
    # Ensure secrets and local absolute paths were stripped
    assert "admin_key" not in export_data["settings"]
    assert "pairing_key" not in export_data["settings"]
    assert "background_path" not in export_data["settings"]

    # Test importing exported data
    import_resp = client.post(
        "/v1/config/import",
        headers=admin_headers,
        json=export_data,
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["is_dirty"] is True
