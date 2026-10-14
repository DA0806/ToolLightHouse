import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
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
        json={"device_id": "test_runner", "role": "admin"},
    ).json()
    return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


class SlowInferencePipeline(Pipeline):
    def __init__(self, settings, serial=None, delay=0.15):
        super().__init__(settings, serial=serial, safe_startup=True)
        self.delay = delay
        self.inference_started = threading.Event()

    def process(self, image, timestamp=None, photo=False, video=False):
        self.inference_started.set()
        time.sleep(self.delay)
        return super().process(image, timestamp, photo, video)


def test_delayed_inference_race_with_hot_apply(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = SlowInferencePipeline(settings, serial=serial, delay=0.2)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)

        # Submit slow photo inference in background
        data = _image_bytes()
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=headers, files={"file": ("photo.jpg", data, "image/jpeg")})
        )

        # Wait until inference has actually started inside process()
        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently trigger hot-apply (which fences the scene and clears mask to 0)
        apply_resp = client.post("/v1/config/apply", headers=headers)
        assert apply_resp.status_code == 200

        # Wait for delayed inference to finish
        infer_resp = fut.result()
        # The delayed inference must have been fenced (409 Conflict)
        assert infer_resp.status_code == 409

        # Crucial safety check: Arduino confirmed mask must be 0, never the late detection mask!
        status = serial.status()
        assert status["confirmed_mask"] == 0
        assert status["requested_mask"] == 0


def test_delayed_inference_race_with_mode_switch(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = SlowInferencePipeline(settings, serial=serial, delay=0.2)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)

        data = _image_bytes()
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=headers, files={"file": ("photo.jpg", data, "image/jpeg")})
        )

        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently switch mode to preparation
        mode_resp = client.post("/v1/system/mode", headers=headers, json={"mode": "preparation"})
        assert mode_resp.status_code == 200
        assert mode_resp.json()["mode"] == "preparation"

        infer_resp = fut.result()
        assert infer_resp.status_code == 409

        # LEDs must remain 0
        status = serial.status()
        assert status["confirmed_mask"] == 0
        assert status["requested_mask"] == 0


def test_delayed_inference_race_with_maintenance_enter(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = SlowInferencePipeline(settings, serial=serial, delay=0.2)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)

        data = _image_bytes()
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=headers, files={"file": ("photo.jpg", data, "image/jpeg")})
        )

        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently enter maintenance mode
        maint_resp = client.post("/v1/arduino/maintenance/enter", headers=headers)
        assert maint_resp.status_code == 200
        assert maint_resp.json()["maintenance_mode"] is True

        infer_resp = fut.result()
        assert infer_resp.status_code == 409

        # Mask must remain 0
        status = serial.status()
        assert status["confirmed_mask"] == 0
        assert status["requested_mask"] == 0


def test_delayed_inference_race_with_disconnect(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = SlowInferencePipeline(settings, serial=serial, delay=0.2)
    app = create_app(settings, pipeline, config_path=config_file)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)

        data = _image_bytes()
        fut = executor.submit(
            lambda: client.post("/v1/analyze/photo", headers=headers, files={"file": ("photo.jpg", data, "image/jpeg")})
        )

        assert pipeline.inference_started.wait(timeout=2.0)

        # Concurrently disconnect Arduino
        dc_resp = client.post("/v1/arduino/disconnect", headers=headers)
        assert dc_resp.status_code == 200

        infer_resp = fut.result()
        assert infer_resp.status_code == 409

        # Transport must be disconnected and confirmed_mask must be None
        status = serial.status()
        assert status["connected"] is False
        assert status["confirmed_mask"] is None


def test_delayed_blocking_serial_keeps_event_loop_responsive(tmp_path):
    class SlowSerial(SimulatedSerial):
        def __init__(self):
            super().__init__()
            self.send_mask_started = threading.Event()
            self.send_mask_unblock = threading.Event()

        def send_mask(self, mask: int, retries: int = 2) -> bool:
            if mask != 0:
                self.send_mask_started.set()
                self.send_mask_unblock.wait(timeout=1.0)
            return super().send_mask(mask, retries)

    slow_serial = SlowSerial()
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    pipeline = Pipeline(settings, serial=slow_serial, safe_startup=True)
    app = create_app(settings, pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        headers = _admin_headers(client)

        # Enter maintenance
        client.post("/v1/arduino/maintenance/enter", headers=headers)

        # Launch LED test in worker thread (will block in send_mask)
        fut = executor.submit(
            lambda: client.post("/v1/arduino/maintenance/test-led/2?duration=1.0", headers=headers)
        )

        assert slow_serial.send_mask_started.wait(timeout=2.0)

        # Event loop must remain fully responsive to concurrent requests while serial is blocked
        start_time = time.perf_counter()
        health_resp = client.get("/health")
        elapsed = time.perf_counter() - start_time
        assert health_resp.status_code == 200
        assert elapsed < 0.1, f"Event loop stalled: /health took {elapsed:.3f}s"

        # Unblock serial
        slow_serial.send_mask_unblock.set()
        led_resp = fut.result()
        assert led_resp.status_code == 200
        assert led_resp.json()["arduino"]["confirmed_mask"] == 4
