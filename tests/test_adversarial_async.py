import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.camera import OpenCVCameraService
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SimulatedSerial


def _image_bytes(size=(100, 100)):
    image = Image.new("RGB", size, color="white")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def _admin_headers(client, admin_key="admin_secret"):
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": admin_key},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    client.post("/v1/sessions/lease/takeover", headers=headers)
    return headers


def _controller_headers(client, pairing_key="secret", device_id="phone-ctrl"):
    session = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": pairing_key},
        json={"device_id": device_id, "role": "controller"},
    ).json()
    return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


class RecordingSerial(SimulatedSerial):
    def __init__(self):
        super().__init__()
        self.history: list[tuple[int, float]] = []

    def send_mask(self, mask: int, retries: int = 2, expected_generation: int | None = None) -> bool:
        self.history.append((mask, time.monotonic()))
        return super().send_mask(mask, retries, expected_generation)


class NonZeroDelayedPipeline(Pipeline):
    def __init__(self, settings, serial=None):
        super().__init__(settings, serial=serial, safe_startup=True)
        self.inference_started = threading.Event()
        self.release_inference = threading.Event()

    def process(self, image, timestamp=None, photo=False, apply_leds=True):
        self.inference_started.set()
        # Block until released by the test
        self.release_inference.wait(timeout=5.0)
        res = super().process(image, timestamp, photo, apply_leds=False)
        # Produce a non-zero mask (LED 2 -> mask 4) that would actuate hardware if committed
        res["arduino"]["requested_mask"] = 4
        return res


def test_adversarial_phone_disconnect_zeroes_hardware(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", auth_required=True)
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        admin_h = _admin_headers(client)

        # 1. Generate camera invite for transmitter
        invite = client.post("/v1/camera/invite", headers=admin_h, json={"expires_in_seconds": 60}).json()
        token = invite["invite_token"]

        # 2. Connect transmitter over signaling websocket
        with client.websocket_connect("/v1/camera/signal") as tx_ws:
            tx_ws.send_json({"type": "auth_transmitter", "invite_token": token})
            auth_ack = tx_ws.receive_json()
            assert auth_ack["type"] == "auth_ok"
            assert auth_ack["role"] == "transmitter"

            # 3. Submit delayed photo inference in background
            data = _image_bytes()
            fut = executor.submit(
                lambda: client.post("/v1/analyze/photo", headers=admin_h, files={"file": ("snap.jpg", data, "image/jpeg")})
            )
            assert pipeline.inference_started.wait(timeout=2.0), "Inference did not start"

            # 4. Disconnect phone transmitter websocket while inference is pending
            tx_ws.close()

        # Brief pause to let websocket disconnect handler clear scene
        time.sleep(0.05)

        # 5. Release delayed inference: commit must be blocked by the transmitter disconnect fence
        pipeline.release_inference.set()

        infer_resp = fut.result(timeout=5.0)
        assert infer_resp.status_code == 409
        assert infer_resp.json()["detail"]["code"] == "inference_discarded"

        # Safety invariant: hardware mask remains 0, and no non-zero mask was ever sent
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0
        assert all(mask == 0 for mask, ts in serial.history), f"Non-zero mask was sent: {serial.history}"


def test_adversarial_source_switch_during_delayed_inference(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)
        data = _image_bytes()

        # Submit delayed photo inference
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=headers, files={"file": ("snap.jpg", data, "image/jpeg")})
        )
        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently switch camera source
        switch_resp = client.post("/v1/camera/sources/active", headers=headers, json={"source_id": "operator_device"})
        assert switch_resp.status_code == 200

        # Release delayed inference
        pipeline.release_inference.set()

        infer_resp = fut.result()
        # In-flight frame from prior source must be rejected (409)
        assert infer_resp.status_code == 409
        assert infer_resp.json()["detail"]["code"] == "inference_discarded"

        # Mask must never be actuated by the stale source
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0
        assert all(mask == 0 for mask, ts in serial.history)


def test_adversarial_admin_takeover_during_delayed_inference(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        # Operator has controller lease
        ctrl_h = _controller_headers(client)
        data = _image_bytes()

        # Controller submits delayed photo inference
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=ctrl_h, files={"file": ("snap.jpg", data, "image/jpeg")})
        )
        assert pipeline.inference_started.wait(timeout=2.0)

        # Admin concurrently takes over lease
        admin_sess = client.post(
            "/v1/sessions",
            headers={"X-Admin-Key": "admin_secret"},
            json={"device_id": "admin-lap", "role": "admin"},
        ).json()
        admin_h = {"X-Session-Id": admin_sess["session_id"], "X-Session-Ticket": admin_sess["ticket"]}
        takeover_resp = client.post("/v1/sessions/lease/takeover", headers=admin_h)
        assert takeover_resp.status_code == 200

        # Release delayed inference
        pipeline.release_inference.set()

        infer_resp = fut.result()
        # Evicted controller's delayed inference must fail commit with 409
        assert infer_resp.status_code == 409
        assert infer_resp.json()["detail"]["code"] == "lease_lost"

        # Safety invariant: hardware mask remains 0
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0
        assert all(mask == 0 for mask, ts in serial.history)


def test_adversarial_maintenance_during_delayed_inference(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        admin_h = _admin_headers(client)
        data = _image_bytes()

        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=admin_h, files={"file": ("snap.jpg", data, "image/jpeg")})
        )
        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently enter maintenance
        maint_resp = client.post("/v1/arduino/maintenance/enter", headers=admin_h)
        assert maint_resp.status_code == 200

        # Release delayed inference
        pipeline.release_inference.set()

        infer_resp = fut.result()
        assert infer_resp.status_code == 409
        assert infer_resp.json()["detail"]["code"] == "inference_discarded"

        # Hardware mask remains 0
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0
        assert all(mask == 0 for mask, ts in serial.history)


def test_adversarial_stale_remote_upload_buffer_never_actuates_leds(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", frame_max_age_seconds=0.1)
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        ctrl_h = _controller_headers(client)
        file_bytes = _image_bytes()

        # Stream frame that will age beyond frame_max_age_seconds (0.1s) during inference
        resp = client.post(
            "/v1/stream/frame",
            headers=ctrl_h,
            files={"file": ("snap.jpg", file_bytes, "image/jpeg")},
        )
        assert resp.status_code == 200
        assert pipeline.inference_started.wait(timeout=2.0)

        # Delay release past frame_max_age_seconds (0.1s)
        time.sleep(0.15)
        pipeline.release_inference.set()

        # Allow background stream task to finish processing
        time.sleep(0.1)

        # Never actuated LEDs because frame aged past frame_max_age_seconds
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0
        assert all(mask == 0 for mask, ts in serial.history)


def test_adversarial_stale_opencv_delayed_buffer_never_actuates_leds(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", frame_max_age_seconds=0.1)
    serial = RecordingSerial()
    pipeline = NonZeroDelayedPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller_headers(client)

        # Setup mock OpenCV service
        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = True
        frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # Case 1: Camera buffer itself is stale upon intake
        stale_mono = time.monotonic() - 2.0
        mock_service.get_latest_frame.return_value = (frame, time.time() - 2.0, stale_mono)

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            client.post("/v1/camera/sources/active", headers=headers, json={"source_id": "laptop_builtin"})
            status = client.get("/v1/status", headers=headers).json()
            source_ver = str(status["source_version"])

            stale_resp = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
            )
            assert stale_resp.status_code == 409
            assert "stale" in stale_resp.json()["detail"].lower()
        # Reset stream state via /v1/stream/stop
        stop_resp = client.post("/v1/stream/stop", headers=headers)
        assert stop_resp.status_code == 200

        # Case 2: Camera buffer is fresh upon intake, but delayed during inference past frame_max_age_seconds
        fresh_now = time.monotonic()
        mock_service.get_latest_frame.return_value = (frame, time.time(), fresh_now)

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            resp = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
            )
            assert resp.status_code == 200
            assert pipeline.inference_started.wait(timeout=2.0)

            # Hold until frame becomes stale
            time.sleep(0.15)
            pipeline.release_inference.set()

            time.sleep(0.1)

            # Safety check: despite pipeline producing mask 4, it was discarded post-inference
            assert serial.status()["confirmed_mask"] == 0
            assert serial.status()["requested_mask"] == 0
            assert all(mask == 0 for mask, ts in serial.history)
