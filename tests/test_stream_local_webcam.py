import time
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.camera import OpenCVCameraService
from toollighthouse.config import CalibrationProfile, Settings, apply_canonical_transform
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SimulatedSerial


def _controller(client: TestClient, pairing_key: str = "secret") -> dict[str, str]:
    res = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": pairing_key},
        json={"device_id": "laptop-ctrl", "role": "controller"},
    )
    assert res.status_code == 200
    data = res.json()
    return {
        "X-Session-Id": data["session_id"],
        "X-Session-Ticket": data["ticket"],
    }


def _admin_headers(client: TestClient, admin_key: str = "admin_secret") -> dict[str, str]:
    res = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": admin_key},
        json={"device_id": "admin-lap", "role": "admin"},
    )
    assert res.status_code == 200
    data = res.json()
    h = {"X-Session-Id": data["session_id"], "X-Session-Ticket": data["ticket"]}
    client.post("/v1/sessions/lease/takeover", headers=h)
    return h


def _jpeg_bytes(width: int = 640, height: int = 480, color: str = "red") -> bytes:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    if color == "red":
        img[:, :] = (0, 0, 255)
    elif color == "blue":
        img[:, :] = (255, 0, 0)
    else:
        img[:, :] = (0, 255, 0)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class MockCapturingPipeline(Pipeline):
    def __init__(self, settings: Settings, serial=None):
        super().__init__(settings, serial=serial, safe_startup=True)
        self.received_images = []

    def process(self, image, timestamp=None, photo=False, video=False):
        self.received_images.append(image.copy())
        return super().process(image, timestamp, photo, video)


def test_stream_local_webcam_pristine_buffer_actual_pixels(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = MockCapturingPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller(client)

        # Create native asymmetric pixel pattern
        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = True
        native_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        native_frame[0:100, 0:100] = (255, 0, 0)
        native_frame[0:100, 100:200] = (0, 255, 0)
        native_frame[100:200, 0:100] = (0, 0, 255)
        native_frame[500:720, 1000:1280] = (123, 45, 67)

        now_wall = time.time()
        now_mono = time.monotonic()
        mock_service.get_latest_frame.return_value = (native_frame, now_wall, now_mono)

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            # Switch source to laptop_builtin
            resp_switch = client.post(
                "/v1/camera/sources/active",
                headers=headers,
                json={"source_id": "laptop_builtin"},
            )
            assert resp_switch.status_code == 200
            status = client.get("/v1/status", headers=headers).json()
            source_ver = str(status["source_version"])

            # Supply a different degraded preview payload (640x360 red)
            degraded_preview = _jpeg_bytes(640, 360, "red")

            # Stream frame with preview upload supplied: endpoint MUST ignore it and use pristine native buffer
            resp = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
                files={"file": ("preview.jpg", degraded_preview, "image/jpeg")},
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["accepted"] is True
            assert payload["latest_only"] is True

            # Wait for background stream task to process the frame
            deadline = time.monotonic() + 2.0
            while not pipeline.received_images and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(pipeline.received_images) >= 1

            received = pipeline.received_images[0]
            # Verify actual pixel array matches apply_canonical_transform on native frame
            expected_pixels = apply_canonical_transform(native_frame)
            assert np.array_equal(received, expected_pixels), "Pixels do not match canonical transform of pristine buffer"
            # Verify it did not use the degraded preview upload
            assert received.shape == (720, 1280, 3)
            assert not np.array_equal(received[:360, :640], cv2.imdecode(np.frombuffer(degraded_preview, np.uint8), cv2.IMREAD_COLOR))


def test_stream_local_webcam_calibration_profile_transform_and_incompatible_resolution(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = MockCapturingPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        admin_h = _admin_headers(client)

        # 1. Configure profile with rotation and crop
        profile = CalibrationProfile(
            source_id="laptop_builtin",
            orientation="rotate_90",
            crop=(20, 30, 200, 150),
            resolution={"width": 1280, "height": 720},
            status="ready",
        )
        pipeline.settings.calibration_profiles["laptop_builtin"] = profile

        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = True
        native_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        native_frame[0:50, 0:50] = (10, 20, 30)
        native_frame[50:100, 50:100] = (40, 50, 60)

        mock_service.get_latest_frame.return_value = (native_frame, time.time(), time.monotonic())

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            client.post("/v1/camera/sources/active", headers=admin_h, json={"source_id": "laptop_builtin"})
            status = client.get("/v1/status", headers=admin_h).json()
            source_ver = str(status["source_version"])

            resp = client.post(
                "/v1/stream/frame",
                headers={
                    **admin_h,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
            )
            assert resp.status_code == 200

            deadline = time.monotonic() + 2.0
            while not pipeline.received_images and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(pipeline.received_images) >= 1

            expected = apply_canonical_transform(native_frame, orientation="rotate_90", crop=(20, 30, 200, 150))
            assert np.array_equal(pipeline.received_images[0], expected)

            # 2. Incompatible resolution test
            client.post("/v1/stream/stop", headers=admin_h)
            pipeline.settings.calibration_profiles["laptop_builtin"] = CalibrationProfile(
                source_id="laptop_builtin",
                orientation="normal",
                crop=None,
                resolution={"width": 1920, "height": 1080},
                status="ready",
            )

            resp_incompat = client.post(
                "/v1/stream/frame",
                headers={
                    **admin_h,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
            )
            assert resp_incompat.status_code == 409
            assert "incompatible" in resp_incompat.json()["detail"].lower()


def test_stream_local_webcam_ignores_uploaded_file_no_loopback(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = MockCapturingPipeline(settings, serial=serial)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller(client)
        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = False
        mock_service.get_latest_frame.return_value = None

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            client.post(
                "/v1/camera/sources/active",
                headers=headers,
                json={"source_id": "laptop_builtin"},
            )
            status = client.get("/v1/status", headers=headers).json()
            source_ver = str(status["source_version"])

            # Supply an uploaded degraded preview file (640x360)
            degraded_file = _jpeg_bytes(640, 360, "red")

            # Even though file is uploaded, endpoint must ignore it and pull pristine buffer from camera
            resp = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
                files={"file": ("preview.jpg", degraded_file, "image/jpeg")},
            )
            # Backend camera is not running in pure mock environment -> confirms it attempted to read from opencv_service!
            assert resp.status_code == 400
            assert "camera" in resp.json()["detail"].lower() or "frame" in resp.json()["detail"].lower()


def test_stream_local_webcam_stale_buffer_rejection(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", frame_max_age_seconds=0.5)
    app = create_app(settings, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller(client)

        # Set mock opencv service with stale frame (mono timestamp 2.0s old)
        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = True
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        stale_mono = time.monotonic() - 2.0
        mock_service.get_latest_frame.return_value = (frame, time.time() - 2.0, stale_mono)

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            resp_switch = client.post(
                "/v1/camera/sources/active",
                headers=headers,
                json={"source_id": "laptop_builtin"},
            )
            assert resp_switch.status_code == 200
            status = client.get("/v1/status", headers=headers).json()
            source_ver = str(status["source_version"])

            resp = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": source_ver,
                },
            )
            # Stale buffer must return 409
            assert resp.status_code == 409
            assert "stale" in resp.json()["detail"].lower()


def test_stream_source_fencing_and_start_stop(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    app = create_app(settings, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller(client)
        mock_service = MagicMock(spec=OpenCVCameraService)
        mock_service.is_running = False
        mock_service.get_latest_frame.return_value = None

        with patch("toollighthouse.api.app.OpenCVCameraService", return_value=mock_service):
            # 1. Start/stop: when laptop_builtin is active but camera not started/failed -> 400
            client.post("/v1/camera/sources/active", headers=headers, json={"source_id": "laptop_builtin"})
            status = client.get("/v1/status", headers=headers).json()
            source_ver = status["source_version"]

            resp_stopped = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": str(source_ver),
                },
            )
            assert resp_stopped.status_code == 400
            assert "camera" in resp_stopped.json()["detail"].lower() or "frame" in resp_stopped.json()["detail"].lower()

            # 2. Source fencing: mismatch in source ID or stale version -> 409
            resp_wrong_source = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "wrong_source",
                    "X-Source-Version": str(source_ver),
                },
            )
            assert resp_wrong_source.status_code == 409
            assert "not active" in resp_wrong_source.json()["detail"].lower()

            resp_stale_ver = client.post(
                "/v1/stream/frame",
                headers={
                    **headers,
                    "X-Source-Id": "laptop_builtin",
                    "X-Source-Version": str(source_ver - 1),
                },
            )
            assert resp_stale_ver.status_code == 409
            assert "stale" in resp_stale_ver.json()["detail"].lower()


def test_stream_source_switch_save_restart_persisted_no_overwrite(tmp_path):
    config_file = tmp_path / "app_config.json"
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    app1 = create_app(settings, pipeline=pipeline, config_path=config_file)

    with TestClient(app1) as client1:
        headers = _admin_headers(client1)

        # Configure custom settings for source A (remote_phone): fps = 12.0
        set_res = client1.post("/v1/camera/sources/remote_phone/settings", headers=headers, json={"fps": 12.0})
        assert set_res.status_code == 200

        # Switch A -> B (operator_device)
        sw_b = client1.post("/v1/camera/sources/active", headers=headers, json={"source_id": "operator_device"})
        assert sw_b.status_code == 200

        # Switch B -> A (remote_phone)
        sw_a = client1.post("/v1/camera/sources/active", headers=headers, json={"source_id": "remote_phone"})
        assert sw_a.status_code == 200

    # Simulate restart by creating new app from same config file
    app2 = create_app(settings, pipeline=pipeline, config_path=config_file)
    with TestClient(app2) as client2:
        h2 = _admin_headers(client2)
        sources = client2.get("/v1/camera/sources", headers=h2).json()["sources"]
        rp = next(s for s in sources if s["source_id"] == "remote_phone")
        assert rp["current_fps"] == 12.0
        cfg = client2.get("/v1/config", headers=h2).json()
        assert cfg["settings"]["camera_sources"]["remote_phone"]["fps"] == 12.0


def test_stream_stop_cancels_stream_and_resets_fps(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _admin_headers(client)

        # 1. Stop stream clears scene and resets last_stream_mono
        stop_resp = client.post("/v1/stream/stop", headers=headers)
        assert stop_resp.status_code == 200
        data = stop_resp.json()
        assert data["stopped"] is True
        assert data["stream_generation"] >= 1

        # 2. Hardware is safely zeroed
        assert serial.status()["confirmed_mask"] == 0
        assert serial.status()["requested_mask"] == 0


def test_stream_stop_rejects_stale_source_context(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    app = create_app(settings, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        headers = _controller(client)
        before = client.get("/v1/status", headers=headers).json()
        switch = client.post(
            "/v1/camera/sources/active",
            headers=headers,
            json={"source_id": "operator_device"},
        )
        assert switch.status_code == 200

        stale_stop = client.post(
            "/v1/stream/stop",
            headers={
                **headers,
                "X-Source-Id": before["active_source_id"],
                "X-Source-Version": str(before["source_version"]),
            },
        )
        assert stale_stop.status_code == 409
        assert stale_stop.json()["detail"]["code"] == "source_stale"


def test_stream_run_fence_rejects_late_frame_and_old_stop(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    app = create_app(settings, config_path=tmp_path / "app_config.json")
    upload = {"file": ("frame.jpg", _jpeg_bytes(100, 100), "image/jpeg")}

    with TestClient(app) as client:
        headers = _admin_headers(client)
        run_one = {**headers, "X-Stream-Run": "1"}
        first = client.post("/v1/stream/frame", headers=run_one, files=upload)
        assert first.status_code == 200

        stopped = client.post("/v1/stream/stop", headers=run_one)
        assert stopped.status_code == 200
        late = client.post("/v1/stream/frame", headers=run_one, files=upload)
        assert late.status_code == 409
        assert late.json()["detail"]["code"] == "stream_stale"

        run_two = {**headers, "X-Stream-Run": "2"}
        second = client.post("/v1/stream/frame", headers=run_two, files=upload)
        assert second.status_code == 200
        old_stop = client.post("/v1/stream/stop", headers=run_one)
        assert old_stop.status_code == 200
        assert old_stop.json()["stopped"] is False


def test_stream_rate_limit_fps(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    app = create_app(settings, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client:
        admin_h = _admin_headers(client)

        # Configure remote_phone FPS to 2.0 so min_interval is 0.5s
        set_fps_res = client.post("/v1/camera/sources/remote_phone/settings", headers=admin_h, json={"fps": 2.0})
        assert set_fps_res.status_code == 200

        file_bytes = _jpeg_bytes(100, 100)
        upload = {"file": ("frame.jpg", file_bytes, "image/jpeg")}

        # First stream call succeeds
        first = client.post("/v1/stream/frame", headers=admin_h, files=upload)
        assert first.status_code == 200

        # Immediate second call without delay triggers FPS limit (429)
        upload2 = {"file": ("frame.jpg", file_bytes, "image/jpeg")}
        second = client.post("/v1/stream/frame", headers=admin_h, files=upload2)
        assert second.status_code == 429
        assert "rate limit" in second.json()["detail"].lower()
