import sys
import types

import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline


@pytest.fixture
def fake_torch_runtime(monkeypatch):
    """Make readiness deterministic for tests that inject a fake embedder."""
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)


def test_safe_startup_in_real_mode_without_assets_enters_preparation():
    # If mode is 'real' but background and gallery are missing, do NOT crash!
    settings = Settings(
        mode="real",
        background_path="nonexistent_background.png",
        gallery_path="nonexistent_gallery.json",
        auth_required=False,
    )

    # Creating pipeline and app must succeed safely
    pipeline = Pipeline(settings, safe_startup=True)
    assert pipeline.mode == "preparation"
    assert not pipeline.is_ready()
    assert len(pipeline.preparation_reasons) > 0

    client = TestClient(create_app(settings, pipeline))

    # Health check is available
    health = client.get("/health").json()
    assert health["ok"] is True
    assert health["mode"] == "preparation"

    # Readiness check reports honest status and preparation reasons
    readiness = client.get("/v1/system/readiness").json()
    assert readiness["backend_ok"] is True
    assert readiness["active_mode"] == "preparation"
    assert readiness["can_activate_real"] is False
    assert readiness["calibration_ready"] is False
    assert readiness["gallery_ready"] is False

    # Inference attempts in preparation mode return 409 Conflict with clear detail
    resp = client.post(
        "/v1/analyze/photo",
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg")},
    )
    assert resp.status_code == 409
    assert "preparation" in resp.json()["detail"].lower()


def test_system_mode_switch_validates_readiness():
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings))

    # Authenticate as admin
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    # Trying to switch to 'real' mode when requirements are missing returns 409
    resp = client.post("/v1/system/mode", headers=headers, json={"mode": "real"})
    assert resp.status_code == 409
    assert "cannot activate real mode" in resp.json()["detail"].lower()

    # Switching to 'preparation' or 'mock' succeeds
    resp_prep = client.post("/v1/system/mode", headers=headers, json={"mode": "preparation"})
    assert resp_prep.status_code == 200
    assert resp_prep.json()["mode"] == "preparation"

    resp_mock = client.post("/v1/system/mode", headers=headers, json={"mode": "mock"})
    assert resp_mock.status_code == 200
    assert resp_mock.json()["mode"] == "mock"


class FakeEmbedder:
    def __init__(self):
        self.text_calls = []
        self.image_calls = 0

    def encode_text(self, texts):
        import numpy as np
        self.text_calls.append(list(texts))
        vecs = np.asarray([[float(i + 1), 1.0, 0.5] for i, _ in enumerate(texts)], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms

    def encode_images(self, images):
        import numpy as np
        self.image_calls += 1
        vecs = np.asarray([[0.5, float(i + 1), 1.0] for i, _ in enumerate(images)], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms


def test_first_run_preparation_builds_text_gallery_with_fake_embedder_and_enables_real_readiness(tmp_path, fake_torch_runtime):
    import io
    from PIL import Image

    config_file = tmp_path / "app_config.json"
    gallery_file = tmp_path / "gallery.json"
    bg_file = tmp_path / "bg.png"
    roi_file = tmp_path / "roi.json"

    # Start in preparation mode with confirmed inventory
    from toollighthouse.config import Inventory, ToolCategory
    confirmed_cats = [
        ToolCategory(f"tool_{i+1}", f"Tool {i+1}", f"drawer_{i+1}", i, i + 2, (f"desc {i+1}",))
        for i in range(9)
    ]
    confirmed_inv = Inventory(confirmed_cats, example=False)
    settings = Settings(
        mode="preparation",
        pairing_key="secret",
        admin_key="admin_secret",
        inventory_confirmed=True,
        inventory=confirmed_inv,
        gallery_path=str(gallery_file),
        background_path=str(bg_file),
        roi_path=str(roi_file),
    )
    fake_embedder = FakeEmbedder()
    app = create_app(settings, config_path=config_file, embedder=fake_embedder)
    client = TestClient(app)

    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    # 1. Initial readiness: missing background and gallery
    readiness1 = client.get("/v1/system/readiness", headers=headers).json()
    assert readiness1["can_activate_real"] is False
    assert readiness1["gallery_ready"] is False
    assert readiness1["calibration_ready"] is False

    # 2. Upload background and set ROI
    bg_img = Image.new("RGB", (640, 480), "gray")
    bg_buf = io.BytesIO()
    bg_img.save(bg_buf, "JPEG")
    client.post("/v1/calibration/background", headers=headers, files={"file": ("bg.jpg", bg_buf.getvalue(), "image/jpeg")})
    client.put("/v1/calibration/roi", headers=headers, json={"roi": [10, 10, 600, 400]})

    readiness2 = client.get("/v1/system/readiness", headers=headers).json()
    assert readiness2["calibration_ready"] is True
    assert readiness2["gallery_ready"] is False
    assert readiness2["can_activate_real"] is False

    # 3. Apply config: builds the first text-only candidate gallery using FakeEmbedder
    apply_resp = client.post("/v1/config/apply", headers=headers)
    assert apply_resp.status_code == 200
    assert gallery_file.exists()

    # 4. Readiness now has gallery ready and can activate real mode!
    readiness3 = client.get("/v1/system/readiness", headers=headers).json()
    assert readiness3["gallery_ready"] is True
    assert readiness3["can_activate_real"] is True

    # 5. Activate real mode
    mode_resp = client.post("/v1/system/mode", headers=headers, json={"mode": "real"})
    assert mode_resp.status_code == 200
    assert mode_resp.json()["mode"] == "real"


def test_rollback_with_incompatible_gallery_falls_back_to_preparation_mode(tmp_path, fake_torch_runtime):
    import io
    from PIL import Image
    from toollighthouse.config import Inventory, ToolCategory

    config_file = tmp_path / "app_config.json"
    gallery_file = tmp_path / "gallery.json"
    bg_file = tmp_path / "bg.png"
    roi_file = tmp_path / "roi.json"

    cats_v1 = [
        ToolCategory(f"tool_{i+1}", f"Tool {i+1}", f"drawer_{i+1}", i, i + 2, (f"desc {i+1}",))
        for i in range(9)
    ]
    inv_v1 = Inventory(cats_v1, example=False)
    settings = Settings(
        mode="preparation",
        pairing_key="secret",
        admin_key="admin_secret",
        inventory_confirmed=True,
        inventory=inv_v1,
        gallery_path=str(gallery_file),
        background_path=str(bg_file),
        roi_path=str(roi_file),
    )
    fake_embedder = FakeEmbedder()
    app = create_app(settings, config_path=config_file, embedder=fake_embedder)
    client = TestClient(app)

    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    # Calibrate background and ROI
    bg_img = Image.new("RGB", (640, 480), "gray")
    bg_buf = io.BytesIO()
    bg_img.save(bg_buf, "JPEG")
    client.post("/v1/calibration/background", headers=headers, files={"file": ("bg.jpg", bg_buf.getvalue(), "image/jpeg")})
    client.put("/v1/calibration/roi", headers=headers, json={"roi": [10, 10, 600, 400]})

    # Build gallery and activate real mode
    client.post("/v1/config/apply", headers=headers)
    client.post("/v1/system/mode", headers=headers, json={"mode": "real"})
    assert client.get("/health").json()["mode"] == "real"

    # Save v2 with different description
    cfg = client.get("/v1/config", headers=headers).json()
    new_inv = dict(cfg["inventory"])
    new_inv["categories"][0]["descriptions"] = ["completely new description v2"]
    client.put("/v1/config", headers=headers, json={"settings": cfg["settings"], "inventory": new_inv})

    # Now delete gallery file to simulate incompatible / missing gallery on rollback
    gallery_file.unlink()

    rb_resp = client.post("/v1/config/rollback", headers=headers)
    assert rb_resp.status_code == 200

    # System must safely drop back to preparation mode
    health = client.get("/health").json()
    assert health["mode"] == "preparation"
    status = client.get("/v1/system/readiness", headers=headers).json()
    assert status["active_mode"] == "preparation"
    assert status["gallery_ready"] is False
    assert status["can_activate_real"] is False


