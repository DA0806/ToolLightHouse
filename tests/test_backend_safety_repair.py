import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from toollighthouse.api import create_app
from toollighthouse.config import CalibrationProfile, Settings
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SimulatedSerial


def _jpeg(size=(320, 240)):
    image = Image.new("RGB", size, "black")
    stream = io.BytesIO()
    image.save(stream, format="JPEG")
    return stream.getvalue()


def _controller(client):
    response = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "tablet", "role": "controller"},
    )
    assert response.status_code == 200
    session = response.json()
    return session, {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


def _admin(client):
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "admin", "role": "admin"},
    ).json()
    return session, {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


class _RecordingSerial(SimulatedSerial):
    def __init__(self):
        super().__init__()
        self.masks = []
        self._masks_lock = threading.Lock()

    def send_mask(self, mask: int, retries: int = 2, expected_generation: int | None = None) -> bool:
        with self._masks_lock:
            self.masks.append(mask)
        return super().send_mask(mask, retries, expected_generation)


class _DelayedNonzeroPipeline(Pipeline):
    def __init__(self, settings, serial):
        super().__init__(settings, serial=serial, safe_startup=True)
        self.started = threading.Event()
        self.release = threading.Event()
        self.fence_seen = threading.Event()

    def reset_scene(self):
        # process_frame resets before it marks itself started; a later reset is
        # therefore evidence that the external fence was applied in-flight.
        if self.started.is_set():
            self.fence_seen.set()
        return super().reset_scene()

    def process(self, image, timestamp=None, photo=False, video=False):
        self.started.set()
        assert self.release.wait(timeout=3.0)
        result = super().process(image, timestamp, photo, video)
        # The normal photo path deliberately does not send LEDs inside the
        # worker. This makes the commit fence the only possible nonzero write.
        result["arduino"]["requested_mask"] = 1
        return result


class _ShapeRecordingPipeline(Pipeline):
    def __init__(self, settings):
        super().__init__(settings, safe_startup=True)
        self.shapes = []

    def process(self, image, timestamp=None, photo=False, video=False):
        self.shapes.append(tuple(image.shape[:2]))
        return super().process(image, timestamp, photo, video)


def _start_transmitter(client, headers):
    invite = client.post("/v1/camera/invite", headers=headers).json()
    ws = client.websocket_connect("/v1/camera/signal")
    ws.__enter__()
    ws.send_json({"type": "auth_transmitter", "invite_token": invite["invite_token"]})
    auth = ws.receive_json()
    assert auth["type"] == "auth_ok"
    return ws


def test_fallback_rejects_invalid_decode_and_enforces_latest_rate_limit():
    with TestClient(create_app(Settings(mode="mock", pairing_key="secret"))) as client:
        session, headers = _controller(client)
        invite = client.post("/v1/camera/invite", headers=headers).json()
        with client.websocket_connect("/v1/camera/signal") as transmitter:
            transmitter.send_json({"type": "auth_transmitter", "invite_token": invite["invite_token"]})
            auth = transmitter.receive_json()
            tx_headers = {
                "X-Transmitter-Id": auth["transmitter_id"],
                "X-Transmitter-Token": auth["transmitter_ticket"],
            }
            bad = client.post(
                "/v1/camera/frame-upload",
                headers=tx_headers,
                files={"file": ("frame.jpg", b"not-an-image", "image/jpeg")},
            )
            assert bad.status_code == 400
            first = client.post(
                "/v1/camera/frame-upload",
                headers={**tx_headers, "X-Capture-Sequence": "1"},
                files={"file": ("frame.jpg", _jpeg(), "image/jpeg")},
            )
            assert first.status_code == 200
            second = client.post(
                "/v1/camera/frame-upload",
                headers={**tx_headers, "X-Capture-Sequence": "2"},
                files={"file": ("frame.jpg", _jpeg(), "image/jpeg")},
            )
            assert second.status_code == 429
        stale = client.post(
            "/v1/camera/frame-upload",
            headers={**tx_headers, "X-Capture-Sequence": "3"},
            files={"file": ("frame.jpg", _jpeg(), "image/jpeg")},
        )
        assert stale.status_code == 403


def test_preview_requires_first_message_auth_and_does_not_accept_query_credentials():
    with TestClient(create_app(Settings(mode="mock", pairing_key="secret"))) as client:
        session, _ = _controller(client)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/camera/preview-ws?session_id={session['session_id']}&ticket={session['ticket']}") as ws:
                ws.send_json({"type": "noop"})
                ws.receive_text()


def test_transmitter_disconnect_blanks_current_scene():
    with TestClient(create_app(Settings(mode="mock", pairing_key="secret"))) as client:
        session, headers = _controller(client)
        analyzed = client.post(
            "/v1/analyze/photo",
            headers=headers,
            files={"file": ("scene.jpg", _jpeg(), "image/jpeg")},
        )
        assert analyzed.status_code == 200
        invite = client.post("/v1/camera/invite", headers=headers).json()
        with client.websocket_connect("/v1/camera/signal") as transmitter:
            transmitter.send_json({"type": "auth_transmitter", "invite_token": invite["invite_token"]})
            assert transmitter.receive_json()["type"] == "auth_ok"
        deadline = time.time() + 1.0
        while time.time() < deadline and client.get("/v1/status", headers=headers).json()["arduino"]["requested_mask"] != 0:
            time.sleep(0.02)
        assert client.get("/v1/status", headers=headers).json()["arduino"]["requested_mask"] == 0


def test_transmitter_disconnect_fences_inflight_nonzero_result():
    settings = Settings(mode="mock", pairing_key="secret")
    serial = _RecordingSerial()
    pipeline = _DelayedNonzeroPipeline(settings, serial)
    with TestClient(create_app(settings, pipeline)) as client, ThreadPoolExecutor(max_workers=2) as executor:
        _, headers = _controller(client)
        transmitter = _start_transmitter(client, headers)
        try:
            future = executor.submit(
                client.post,
                "/v1/analyze/photo",
                headers=headers,
                files={"file": ("scene.jpg", _jpeg(), "image/jpeg")},
            )
            assert pipeline.started.wait(timeout=2.0)
        finally:
            transmitter.__exit__(None, None, None)

        assert pipeline.fence_seen.wait(timeout=2.0)
        pipeline.release.set()
        response = future.result(timeout=3.0)
        assert response.status_code == 409
        assert serial.masks
        assert all(mask == 0 for mask in serial.masks)


def test_config_apply_fences_inflight_nonzero_result():
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = _RecordingSerial()
    pipeline = _DelayedNonzeroPipeline(settings, serial)
    with TestClient(create_app(settings, pipeline)) as client, ThreadPoolExecutor(max_workers=3) as executor:
        _, headers = _controller(client)
        _, admin_headers = _admin(client)
        future = executor.submit(
            client.post,
            "/v1/analyze/photo",
            headers=headers,
            files={"file": ("scene.jpg", _jpeg(), "image/jpeg")},
        )
        assert pipeline.started.wait(timeout=2.0)
        apply_future = executor.submit(client.post, "/v1/config/apply", headers=admin_headers)
        assert pipeline.fence_seen.wait(timeout=2.0)
        pipeline.release.set()
        assert apply_future.result(timeout=3.0).status_code == 200
        assert future.result(timeout=3.0).status_code == 409
        assert serial.masks
        assert all(mask == 0 for mask in serial.masks)


def test_profile_geometry_normalizes_background_and_analysis_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = CalibrationProfile(
        source_id="remote_phone",
        resolution={"width": 6, "height": 4},
        orientation="rotate_90",
        crop=(1, 1, 2, 2),
        roi=(0, 0, 2, 2),
        status="ready",
    )
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        calibration_profiles={"remote_phone": profile},
    )
    pipeline = _ShapeRecordingPipeline(settings)
    raw = _jpeg(size=(6, 4))
    with TestClient(create_app(settings, pipeline)) as client:
        _, headers = _controller(client)
        capture = client.post(
            "/v1/calibration/profiles/remote_phone/capture",
            headers=headers,
            files={"file": ("background.jpg", raw, "image/jpeg")},
        )
        assert capture.status_code == 200
        assert capture.json()["resolution"] == {"width": 6, "height": 4}
        assert pipeline.segmenter.background.shape[:2] == (2, 2)

        analyzed = client.post(
            "/v1/analyze/photo",
            headers=headers,
            files={"file": ("scene.jpg", raw, "image/jpeg")},
        )
        assert analyzed.status_code == 200
        assert pipeline.shapes == [(2, 2)]


def test_signaling_rejects_unallowlisted_origin_before_auth():
    with TestClient(create_app(Settings(mode="mock", pairing_key="secret"))) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/camera/signal", headers={"origin": "https://evil.example"}):
                pass
