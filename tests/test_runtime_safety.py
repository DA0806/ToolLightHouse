"""Runtime regression checks use simulated IO, never claim physical evidence."""
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from toollighthouse.api import create_app
from toollighthouse.config import Settings
from toollighthouse.metrics import Metrics
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SerialTransport, SimulatedSerial


def image_bytes(size=(100, 100)):
    image = Image.new("RGB", size)
    ImageDraw.Draw(image).rectangle((20, 20, 70, 70), fill="white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def settings(**kwargs):
    return Settings(auth_required=False, background_path=None, roi_path=None, **kwargs)


def upload(client):
    return client.post("/v1/analyze/photo", files={"file": ("scene.png", image_bytes(), "image/png")})


def test_metrics_only_actual_capture_render_and_encoder_events():
    metrics = Metrics()
    for i in range(20):
        metrics.observe(0.01, float(i))
    report = metrics.as_dict()
    assert report["capture_fps"] is None
    assert report["inference_hz"] is None
    assert report["display_fps"] is None
    for i in range(20):
        metrics.observe_capture(i / 30)
        metrics.observe_display(i / 60)
        metrics.observe_inference(i / 10)
    report = metrics.as_dict()
    assert report["capture_fps"] == 30
    assert report["display_fps"] == 60
    assert report["inference_hz"] == 10


def test_image_pixel_limit_checked_before_conversion(monkeypatch):
    original = Image.Image.convert
    converted = []
    def spy(self, *args, **kwargs):
        converted.append(True)
        return original(self, *args, **kwargs)
    data = image_bytes()
    monkeypatch.setattr(Image.Image, "convert", spy)
    with TestClient(create_app(settings(max_pixels=100))) as client:
        response = client.post("/v1/analyze/photo", files={"file": ("x.png", data, "image/png")})
    assert response.status_code == 400
    assert converted == []


def test_health_responds_while_serial_ack_is_blocked():
    class BlockedSerial(SimulatedSerial):
        def __init__(self):
            super().__init__()
            self.entered, self.finish = threading.Event(), threading.Event()
        def send_mask(self, mask, retries=2):
            if mask:
                self.entered.set()
                self.finish.wait(1.5)
            return super().send_mask(mask, retries)
    serial = BlockedSerial()
    config = settings()
    with TestClient(create_app(config, Pipeline(config, serial=serial))) as client, ThreadPoolExecutor() as executor:
        request = executor.submit(upload, client)
        assert serial.entered.wait(1)
        started = time.perf_counter()
        try:
            assert client.get("/health").status_code == 200
            assert time.perf_counter() - started < 0.3
        finally:
            serial.finish.set()
        assert request.result().status_code == 200


def test_photo_retention_expires_independently_and_emits_ack():
    config = settings(photo_retention_seconds=0.15)
    pipeline = Pipeline(config)
    with TestClient(create_app(config, pipeline)) as client, client.websocket_connect("/v1/ws") as ws:
        assert ws.receive_json()["event"] == "connected"
        assert upload(client).json()["arduino"]["requested_mask"] != 0
        events = [ws.receive_json() for _ in range(4)]
        assert any(event.get("event") == "scene_expired" for event in events)
        assert events[-1]["type"] == "arduino_ack"
        assert events[-1]["arduino"]["requested_mask"] == 0
        assert pipeline.serial.status()["confirmed_mask"] == 0


def test_photo_background_dimension_errors_are_client_errors():
    config = settings()
    pipeline = Pipeline(config)
    import numpy as np
    pipeline.segmenter.set_background(np.zeros((20, 20, 3), dtype=np.uint8))
    with TestClient(create_app(config, pipeline)) as client:
        assert upload(client).status_code == 400


def test_failed_zero_ack_preserves_requested_zero(monkeypatch):
    class Port:
        def write(self, value): pass
        def readline(self): return b"bad\n"
        def close(self): pass
    transport = SerialTransport("test", retries=0)
    transport._serial = Port()
    transport.requested_mask = 7
    assert not transport.send_mask(0)
    assert transport.status()["requested_mask"] == 0
    assert transport.status()["confirmed_mask"] is None
    transport.close()


def controller(client):
    session = client.post("/v1/sessions", headers={"X-Pairing-Key": "secret"}, json={"role": "controller"}).json()
    return session, {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


def test_stream_scene_ttl_survives_controller_heartbeat_and_emits_zero():
    class MaskPipeline(Pipeline):
        def process(self, *args, **kwargs):
            result = super().process(*args, **kwargs)
            result["arduino"]["requested_mask"] = 7
            return result
    config = Settings(pairing_key="secret", background_path=None, roi_path=None, scene_ttl_seconds=.2, lease_ttl_seconds=.4)
    pipeline = MaskPipeline(config)
    with TestClient(create_app(config, pipeline)) as client:
        session, headers = controller(client)
        ticket = client.post(f"/v1/sessions/{session['session_id']}/ws-ticket", headers=headers).json()["ticket"]
        with client.websocket_connect(f"/v1/ws?session_id={session['session_id']}&ticket={ticket}") as ws:
            ws.receive_json()
            assert client.post("/v1/stream/frame", headers=headers, files={"file": ("x.png", image_bytes(), "image/png")}).status_code == 200
            while ws.receive_json()["type"] != "detection":
                pass
            assert pipeline.serial.status()["requested_mask"] == 7
            until = time.time() + .35
            while time.time() < until:
                assert client.get("/v1/status", headers=headers).status_code == 200
                time.sleep(.025)
            assert pipeline.serial.status()["requested_mask"] == 0
            events = []
            while not any(event.get("event") == "scene_expired" for event in events):
                events.append(ws.receive_json())
            assert ws.receive_json()["arduino"]["confirmed_mask"] == 0
            assert not pipeline.state._states


def test_late_old_inference_cannot_zero_or_reassert_new_controller():
    class SlowFirst(Pipeline):
        def __init__(self, config):
            super().__init__(config)
            self.entered, self.finish = threading.Event(), threading.Event()
            self.first = True
        def process(self, *args, **kwargs):
            if self.first:
                self.first = False
                self.entered.set()
                self.finish.wait(2)
            return super().process(*args, **kwargs)
    config = Settings(pairing_key="secret", background_path=None, roi_path=None)
    pipeline = SlowFirst(config)
    with TestClient(create_app(config, pipeline)) as client, ThreadPoolExecutor() as executor:
        old, old_headers = controller(client)
        old_result = executor.submit(client.post, "/v1/analyze/photo", headers=old_headers, files={"file": ("x.png", image_bytes(), "image/png")})
        assert pipeline.entered.wait(1)
        assert client.delete(f"/v1/sessions/{old['session_id']}", headers=old_headers).status_code == 200
        _, new_headers = controller(client)
        new_result = executor.submit(client.post, "/v1/analyze/photo", headers=new_headers, files={"file": ("x.png", image_bytes(), "image/png")})
        pipeline.finish.set()
        assert old_result.result().status_code == 409
        result = new_result.result()
        assert result.status_code == 200
        assert result.json()["arduino"]["confirmed_mask"] != 0
        time.sleep(.1)
        assert pipeline.serial.status()["confirmed_mask"] == result.json()["arduino"]["confirmed_mask"]


@pytest.mark.parametrize("simulated", [True, False])
def test_capture_to_ack_uses_real_matching_ack_timestamp_only(simulated):
    serial = SimulatedSerial()
    serial.simulated = simulated
    config = settings()
    with TestClient(create_app(config, Pipeline(config, serial=serial))) as client:
        result = upload(client).json()
        latency = result["metrics"]["capture_to_ack_ms"]
        if simulated:
            assert latency is None
        else:
            assert latency == round((serial.last_ack_at - result["timestamp"]) * 1000, 2)
            assert latency >= 0
        assert result["metrics"]["capture_fps"] is None
        assert result["metrics"]["inference_hz"] is None


@pytest.mark.parametrize("fresh", [True, False])
def test_reconnect_acknowledges_zero_before_restoring_only_fresh_scene(monkeypatch, fresh):
    class Port:
        def __init__(self): self.writes, self.closed = [], False
        def write(self, value): self.writes.append(value)
        def readline(self): return self.writes[-1].replace(b"MASK:", b"ACK:")
        def close(self): self.closed = True
    port = Port()
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: port)
    monkeypatch.setattr("toollighthouse.serial_transport.time.sleep", lambda _: None)
    transport = SerialTransport("test", retries=0)
    transport.requested_mask = 7
    transport.set_scene_deadline(time.time() + (10 if fresh else -1))
    transport._refresh()
    assert port.writes == [b"MASK:0\n", b"MASK:7\n" if fresh else b"MASK:0\n"]
    assert transport.status()["confirmed_mask"] == (7 if fresh else 0)
    transport.close()
    assert port.closed


def test_failed_reconnect_bootstrap_closes_port_and_never_restores(monkeypatch):
    class Port:
        def __init__(self): self.writes, self.closed = [], False
        def write(self, value): self.writes.append(value)
        def readline(self): return b"ACK:7\n"
        def close(self): self.closed = True
    port = Port()
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: port)
    monkeypatch.setattr("toollighthouse.serial_transport.time.sleep", lambda _: None)
    transport = SerialTransport("test", retries=0)
    transport.requested_mask = 7
    transport.set_scene_deadline(time.time() + 10)
    transport._refresh()
    assert port.writes == [b"MASK:0\n", b"MASK:0\n"]
    assert port.closed
    assert not transport.status()["connected"]
    assert transport.status()["confirmed_mask"] is None
    transport.close()


def test_metrics_stage_percentiles_require_twenty_and_bound_storage():
    metrics = Metrics()
    for i in range(19):
        metrics.observe_stage("embed", .01)
    assert metrics.as_dict()["inference_ms_p50"] is None
    metrics.observe_stage("embed", .01)
    assert metrics.as_dict()["inference_ms_p50"] == 10
    for i in range(300):
        metrics.observe_stage("serial", .02)
    assert metrics.as_dict()["stages"]["serial"] == {"sample_count": 256, "p50_ms": 20, "p95_ms": 20}


def test_missing_pairing_key_cannot_create_authenticated_session():
    with TestClient(create_app(Settings(background_path=None, roi_path=None))) as client:
        assert client.post("/v1/sessions", json={"role": "controller"}).status_code == 401


def test_no_background_photo_returns_400():
    with TestClient(create_app(settings(require_background=True))) as client:
        assert upload(client).status_code == 400


def test_calibration_fences_running_inference_and_rejects_concurrent_frames():
    class SlowPipeline(Pipeline):
        def __init__(self, config):
            super().__init__(config)
            self.entered, self.finish = threading.Event(), threading.Event()
        def process(self, *args, **kwargs):
            self.entered.set()
            self.finish.wait(2)
            return super().process(*args, **kwargs)
    config = settings()
    pipeline = SlowPipeline(config)
    with TestClient(create_app(config, pipeline)) as client, client.websocket_connect("/v1/ws") as ws, ThreadPoolExecutor() as executor:
        ws.receive_json()
        old = executor.submit(upload, client)
        assert pipeline.entered.wait(1)
        calibration = executor.submit(client.post, "/v1/calibration/background", files={"file": ("new.png", image_bytes((80, 80)), "image/png")})
        while ws.receive_json().get("event") != "calibration_started":
            pass
        assert pipeline.segmenter.background is None
        assert upload(client).status_code == 409
        pipeline.finish.set()
        assert old.result().status_code == 409
        assert calibration.result().status_code == 200
        assert pipeline.segmenter.background.shape == (80, 80, 3)
        assert pipeline.serial.status()["confirmed_mask"] == 0


def test_ack_loss_and_recovery_emit_current_state_events():
    serial = SimulatedSerial()
    serial.simulated = False
    config = settings()
    with TestClient(create_app(config, Pipeline(config, serial=serial))) as client, client.websocket_connect("/v1/ws") as ws:
        ws.receive_json()
        time.sleep(.075)
        serial.connected = False
        serial.send_mask(0)
        assert ws.receive_json()["event"] == "arduino_state_changed"
        assert ws.receive_json()["arduino"]["confirmed_mask"] is None
        serial.connected = True
        serial.send_mask(0)
        assert ws.receive_json()["event"] == "arduino_state_changed"
        assert ws.receive_json()["arduino"]["confirmed_mask"] == 0


def test_photograph_remains_valid_across_renewed_lease_until_retention():
    config = Settings(pairing_key="secret", background_path=None, roi_path=None, lease_ttl_seconds=.2, photo_retention_seconds=.5)
    pipeline = Pipeline(config)
    with TestClient(create_app(config, pipeline)) as client:
        _, headers = controller(client)
        result = client.post("/v1/analyze/photo", headers=headers, files={"file": ("x.png", image_bytes(), "image/png")})
        assert result.status_code == 200
        until = time.time() + .3
        while time.time() < until:
            assert client.get("/v1/status", headers=headers).status_code == 200
            time.sleep(.025)
        assert pipeline.serial.status()["confirmed_mask"] != 0
        until = time.time() + .3
        while time.time() < until:
            client.get("/v1/status", headers=headers)
            time.sleep(.025)
        assert pipeline.serial.status()["confirmed_mask"] == 0
