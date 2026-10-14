import io
import time
import pytest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from PIL import Image, ImageDraw

from toollighthouse.api import create_app
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline


def jpeg_with_tool():
    image = Image.new("RGB", (160, 120), "black")
    ImageDraw.Draw(image).rectangle((20, 20, 80, 80), fill="white")
    stream = io.BytesIO()
    image.save(stream, format="JPEG")
    return stream.getvalue()


def test_photo_requires_pairing_and_returns_detection_contract():
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    assert client.post("/v1/sessions", json={"device_id": "phone", "role": "controller"}).status_code == 401
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "phone", "role": "controller"}).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    response = client.post("/v1/analyze/photo", headers=headers, files={"file": ("tool.jpg", jpeg_with_tool(), "image/jpeg")})
    assert response.status_code == 200
    payload = response.json()
    assert payload["image"]["width"] == 160
    assert payload["image"]["height"] == 120
    assert payload["image"]["orientation"] == "normalized"
    assert payload["image"]["preview_data_url"].startswith("data:image/jpeg;base64,")
    assert payload["arduino"]["ack"] is True
    assert payload["mode"] == "mock"
    assert payload["detections"][0]["bbox"] == [20, 20, 61, 61]


def test_second_controller_is_rejected_until_first_releases():
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    first = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "one", "role": "controller"})
    second = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "two", "role": "controller"})
    assert first.status_code == 200
    assert second.status_code == 409


def test_late_photo_cannot_apply_after_lease_expiry(monkeypatch, tmp_path):
    import importlib

    app_module = importlib.import_module("toollighthouse.api.app")
    clock = [1000.0]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])

    class SlowPipeline(Pipeline):
        def process(self, *args, **kwargs):
            self.process_started = True
            result = super().process(*args, **kwargs)
            # The request was authenticated at clock=1000.0.  Advance the
            # controlled wall clock only after inference has produced a result
            # so this exercises the during-inference fence deterministically.
            clock[0] = 1001.0
            return result

    settings = Settings(mode="mock", pairing_key="secret", lease_ttl_seconds=0.5)
    pipeline = SlowPipeline(settings)
    pipeline.process_started = False
    client = TestClient(create_app(settings, pipeline, config_path=tmp_path / "app_config.json"))
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "phone", "role": "controller"}).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    response = client.post("/v1/analyze/photo", headers=headers, files={"file": ("tool.jpg", jpeg_with_tool(), "image/jpeg")})
    assert response.status_code == 409
    assert pipeline.process_started is True
    assert pipeline.serial.status()["requested_mask"] == 0
    assert pipeline.serial.status()["confirmed_mask"] == 0


def test_admin_controller_lease_renews_on_use_and_expires_for_next_admin():
    settings = Settings(
        mode="mock",
        admin_key="admin_secret",
        pairing_key="secret",
        lease_ttl_seconds=0.5,
    )
    client = TestClient(create_app(settings))

    def admin(device):
        session = client.post(
            "/v1/sessions",
            headers={"X-Admin-Key": "admin_secret"},
            json={"device_id": device, "role": "admin"},
        ).json()
        return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    first = admin("admin-one")
    second = admin("admin-two")
    invalid_upload = {"file": ("invalid.jpg", b"not-an-image", "image/jpeg")}

    # A controller-capable admin claims the exclusive lease even when the
    # request later fails image validation.
    assert client.post("/v1/analyze/photo", headers=first, files=invalid_upload).status_code == 400
    assert client.post("/v1/analyze/photo", headers=second, files=invalid_upload).status_code == 409

    time.sleep(0.1)
    # A valid controller request renews the admin lease before validation.
    assert client.post("/v1/analyze/photo", headers=first, files=invalid_upload).status_code == 400
    time.sleep(0.1)
    assert client.post("/v1/analyze/photo", headers=second, files=invalid_upload).status_code == 409

    time.sleep(0.7)
    # After TTL expiry, the second admin can claim the controller lease.
    assert client.post("/v1/analyze/photo", headers=second, files=invalid_upload).status_code == 400


def test_real_mode_config_apply_and_mode_endpoint_cannot_bypass_readiness(tmp_path):
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        model_path=str(tmp_path / "missing-model"),
        gallery_path=str(tmp_path / "missing-gallery.json"),
        background_path=str(tmp_path / "missing-background.png"),
    )
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "readiness", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    cfg = client.get("/v1/config", headers=headers).json()
    real_settings = dict(cfg["settings"])
    real_settings["mode"] = "real"
    update = client.put(
        "/v1/config",
        headers=headers,
        json={"settings": real_settings, "inventory": cfg["inventory"]},
    )
    assert update.status_code == 200
    assert update.json()["is_dirty"] is True
    apply = client.post("/v1/config/apply", headers=headers)
    assert apply.status_code == 400
    assert "failed to rebuild gallery" in apply.json()["detail"].lower()
    mode = client.post("/v1/system/mode", headers=headers, json={"mode": "real"})
    assert mode.status_code == 409
    assert "cannot activate real mode" in mode.json()["detail"].lower()
    assert client.get("/health").json()["mode"] == "mock"


def test_stream_endpoint_accepts_latest_only_slot():
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "phone", "role": "controller"}).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    response = client.post("/v1/stream/frame", headers=headers, files={"file": ("tool.jpg", jpeg_with_tool(), "image/jpeg")})
    assert response.status_code == 200
    assert response.json()["latest_only"] is True


def test_websocket_uses_one_time_ticket_and_does_not_accept_session_ticket():
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "phone", "role": "reader"}).json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/v1/ws?session_id={session['session_id']}&ticket={session['ticket']}"):
            pass
    ws_ticket = client.post(f"/v1/sessions/{session['session_id']}/ws-ticket", headers={"X-Session-Ticket": session["ticket"]}).json()["ticket"]
    with client.websocket_connect(f"/v1/ws?session_id={session['session_id']}&ticket={ws_ticket}") as websocket:
        assert websocket.receive_json()["event"] == "connected"
