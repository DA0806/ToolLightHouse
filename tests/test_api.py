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


def test_late_photo_cannot_apply_after_lease_expiry():
    class SlowPipeline(Pipeline):
        def process(self, *args, **kwargs):
            time.sleep(0.03)
            return super().process(*args, **kwargs)

    settings = Settings(mode="mock", pairing_key="secret", lease_ttl_seconds=0.01)
    client = TestClient(create_app(settings, SlowPipeline(settings)))
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"device_id": "phone", "role": "controller"}).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    response = client.post("/v1/analyze/photo", headers=headers, files={"file": ("tool.jpg", jpeg_with_tool(), "image/jpeg")})
    assert response.status_code == 409


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
