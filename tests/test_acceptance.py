"""Acceptance boundaries for the local pipeline and its trust boundaries."""

import io
import json
import threading
import time
from concurrent.futures import CancelledError

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from toollighthouse.api import create_app
from toollighthouse.camera import LatestInferenceWorker
from toollighthouse.classifier import SimilarityClassifier
from toollighthouse.config import Inventory, Settings, ToolCategory
from toollighthouse.gallery import Gallery
from toollighthouse.embeddings import references_fingerprint
from toollighthouse.led_controller import mask_for_detections
from toollighthouse.pipeline import Pipeline
from toollighthouse.segmentation import Segmenter
from toollighthouse.serial_transport import SerialTransport, SimulatedSerial
from toollighthouse.state_manager import StateManager
from toollighthouse.tracking import CentroidTracker


def _scene(rectangles, size=(420, 420)):
    image = np.zeros((*size, 3), dtype=np.uint8)
    for x, y, width, height in rectangles:
        image[y:y + height, x:x + width] = (220, 220, 220)
    return image


def _rectangles(count):
    return [(20 + (index % 3) * 130, 20 + (index // 3) * 110, 60, 60) for index in range(count)]


@pytest.mark.parametrize("count", [0, 1, 2, 3, 5, 9])
def test_classical_segmenter_handles_requested_object_counts_and_background(count):
    background = _scene([])
    image = _scene(_rectangles(count))
    segmenter = Segmenter(min_area=100, max_area=10_000)
    segmenter.set_background(background)
    found = segmenter.detect(image)
    assert len(found) == count
    assert all(segment.crop is not None and segment.crop.shape[2] == 3 for segment in found)


def test_segmenter_roi_rejects_outside_objects_and_invalid_geometry():
    background = _scene([])
    image = _scene([(20, 20, 50, 50), (250, 250, 50, 50)])
    segmenter = Segmenter(min_area=100, roi=(0, 0, 180, 180))
    segmenter.set_background(background)
    assert len(segmenter.detect(image)) == 1
    with pytest.raises(ValueError, match="ROI"):
        Segmenter(roi=(0, 0, 999, 999)).detect(image)


def test_led_mask_or_and_withdraw_keeps_duplicate_category_until_last_instance():
    inventory = Inventory.example()
    first = [{"class_id": "tool_1", "validation": "confirmed"}, {"class_id": "tool_1", "validation": "confirmed"}, {"class_id": "tool_2", "validation": "confirmed"}]
    assert mask_for_detections(first, inventory) == 0b11
    assert mask_for_detections(first[1:], inventory) == 0b11
    assert mask_for_detections(first[1:2], inventory) == 0b01
    assert mask_for_detections([{"class_id": "unknown", "validation": "unknown"}], inventory) == 0


def test_tracker_expires_missing_track_and_allocates_new_id_after_reentry():
    tracker = CentroidTracker(max_distance=50, max_missed=1)
    original = tracker.update([(10, 10, 30, 30)])[0].track_id
    assert tracker.update([]) == []
    assert tracker.update([]) == []
    replacement = tracker.update([(10, 10, 30, 30)])[0].track_id
    assert replacement != original


def test_classifier_rejects_nan_unknown_and_low_margin():
    classifier = SimilarityClassifier({"a": np.array([1, 0]), "b": np.array([0.99, 0.1])}, threshold=0.8, margin=0.5)
    assert classifier.predict(np.array([np.nan, 1]), 1).class_id is None
    assert classifier.predict(np.array([0, 1]), 2).validation == "unknown"
    assert classifier.predict(np.array([1, 0.1]), 3).validation == "ambiguous"


def test_state_invalid_observation_interrupts_confirmation_and_class_change_requires_hysteresis():
    state = StateManager(confirmations=2, photo_confirmations=1)
    assert state.update(1, "a", True) is None
    assert state.update(1, None, False) is None
    assert state.update(1, "a", True) is None
    assert state.update(1, "a", True) == "a"
    assert state.update(1, "b", True) is None
    assert state.update(1, "b", True) == "b"


def test_simulated_serial_missing_connection_watchdog_and_recovery():
    serial = SimulatedSerial(watchdog_seconds=0.01)
    serial.send_mask(7)
    serial.connected = False
    assert serial.send_mask(3) is False
    assert serial.status()["confirmed_mask"] is None
    serial.connected = True
    assert serial.send_mask(3) is True
    time.sleep(0.02)
    assert serial.status()["confirmed_mask"] == 0


class _FakePort:
    def __init__(self, responses=None, fail_write=False):
        self.responses = list(responses or [])
        self.writes = []
        self.fail_write = fail_write
        self.closed = False

    def write(self, value):
        if self.fail_write:
            raise OSError("device disconnected")
        self.writes.append(value.decode())

    def readline(self):
        return (self.responses.pop(0) if self.responses else "").encode()

    def close(self):
        self.closed = True


def test_serial_transport_requires_exact_ack_and_marks_missing_wrong_ack_unknown(monkeypatch):
    fake = _FakePort(["ACK:0\n", "ACK:4\n", "ACK:7\n"])
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: fake)
    monkeypatch.setattr("toollighthouse.serial_transport.time.sleep", lambda _: None)
    transport = SerialTransport("COM-test", retries=1)
    transport.open()
    assert fake.writes[0] == "MASK:0\n"
    assert transport.send_mask(7, retries=1) is True
    assert transport.status()["confirmed_mask"] == 7
    fake.responses = ["ACK:6\n", "garbage\n"]
    assert transport.send_mask(7, retries=1) is False
    assert transport.status()["confirmed_mask"] is None
    transport.close()


def test_serial_transport_io_failure_disconnects_and_close_sends_zero(monkeypatch):
    fake = _FakePort(["ACK:0\n"], fail_write=True)
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: fake)
    monkeypatch.setattr("toollighthouse.serial_transport.time.sleep", lambda _: None)
    transport = SerialTransport("COM-test", retries=0)
    transport._serial = fake
    assert transport.send_mask(1, retries=0) is False
    assert transport.status()["connected"] is False
    transport.close()


def test_latest_inference_worker_replaces_pending_requests_without_parallel_execution():
    started = threading.Event()
    release = threading.Event()
    running = 0
    max_running = 0
    calls = []

    def process(value):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        calls.append(value)
        started.set()
        release.wait(1)
        running -= 1
        return value

    worker = LatestInferenceWorker(process)
    first = worker.submit("first")
    assert started.wait(1)
    second = worker.submit("second")
    third = worker.submit("latest")
    release.set()
    assert first.result(timeout=1) == "first"
    with pytest.raises(CancelledError):
        second.result(timeout=1)
    assert third.result(timeout=1) == "latest"
    worker.close()
    assert calls == ["first", "latest"]
    assert max_running == 1


def test_config_rejects_unknown_fields_nan_and_unaligned_inventory(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"classifier_threshold": "nan"}), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        Settings.from_file(path)
    path.write_text(json.dumps({"unexpected": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        Settings.from_file(path)
    categories = [ToolCategory(f"t{i}", f"T{i}", "confirmed", i, i + 3 if i == 0 else i + 2) for i in range(9)]
    with pytest.raises(ValueError, match="pin"):
        Inventory(categories)


def test_gallery_rejects_stale_or_nonfinite_reference_cache(tmp_path):
    path = tmp_path / "gallery.json"
    Gallery({"tool": np.array([1.0, 0.0])}).save(path, "digest")
    with pytest.raises(ValueError, match="stale"):
        Gallery.load(path, "changed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["references"]["tool"] = [float("nan"), 0.0]
    path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        Gallery.load(path, "digest")


def test_gallery_fingerprint_changes_with_reference_content_and_text_weight(tmp_path):
    reference = tmp_path / "tool.jpg"
    reference.write_bytes(b"first")
    first = references_fingerprint([reference], "model", descriptions={"tool": "hammer"}, text_weight=0.0, model_identity="m1")
    reference.write_bytes(b"second")
    changed_content = references_fingerprint([reference], "model", descriptions={"tool": "hammer"}, text_weight=0.0, model_identity="m1")
    changed_text = references_fingerprint([reference], "model", descriptions={"tool": "hammer"}, text_weight=0.5, model_identity="m1")
    assert first != changed_content
    assert changed_content != changed_text


def _jpeg(width=160, height=120, exif_orientation=None):
    image = Image.new("RGB", (width, height), "black")
    ImageDraw.Draw(image).rectangle((20, 20, min(width - 1, 80), min(height - 1, 80)), fill="white")
    buffer = io.BytesIO()
    if exif_orientation:
        exif = image.getexif()
        exif[274] = exif_orientation
        image.save(buffer, format="JPEG", exif=exif.tobytes())
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _controller(client, key="secret", role="controller"):
    response = client.post("/v1/sessions", headers={"X-Pairing-Key": key}, json={"device_id": "test", "role": role})
    assert response.status_code == 200
    return response.json()


def test_api_rejects_missing_ticket_reader_control_and_invalid_uploads():
    settings = Settings(mode="mock", pairing_key="secret", max_upload_bytes=100)
    pipeline = Pipeline(settings)
    pipeline.serial.send_mask(3)
    client = TestClient(create_app(settings, pipeline))
    controller = _controller(client)
    headers = {"X-Session-Id": controller["session_id"]}
    response = client.post("/v1/analyze/photo", headers=headers, files={"file": ("x.jpg", _jpeg(), "image/jpeg")})
    assert response.status_code == 401
    reader = _controller(client, role="reader")
    reader_headers = {"X-Session-Id": reader["session_id"], "X-Session-Ticket": reader["ticket"]}
    assert client.post("/v1/analyze/photo", headers=reader_headers, files={"file": ("x.jpg", _jpeg(), "image/jpeg")}).status_code == 401
    control_headers = {"X-Session-Id": controller["session_id"], "X-Session-Ticket": controller["ticket"]}
    before_reader_release = client.get("/v1/status", headers=control_headers).json()["arduino"]["confirmed_mask"]
    assert client.delete(f"/v1/sessions/{reader['session_id']}", headers=reader_headers).status_code == 200
    after_reader_release = client.get("/v1/status", headers=control_headers).json()["arduino"]["confirmed_mask"]
    assert after_reader_release == before_reader_release
    assert client.get("/v1/status", headers=reader_headers).status_code == 401
    assert client.post("/v1/analyze/photo", headers=control_headers, files={"file": ("x.bin", b"bad", "application/octet-stream")}).status_code == 400
    assert client.post("/v1/analyze/photo", headers=control_headers, files={"file": ("x.jpg", _jpeg(), "image/jpeg")}).status_code == 400


def test_api_exif_normalizes_dimensions_and_returns_pixel_preview():
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    session = _controller(client)
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    response = client.post("/v1/analyze/photo", headers=headers, files={"file": ("rotated.jpg", _jpeg(160, 120, 6), "image/jpeg")})
    assert response.status_code == 200
    image = response.json()["image"]
    assert (image["width"], image["height"]) == (120, 160)
    assert image["orientation"] == "normalized"
    assert image["preview_data_url"].startswith("data:image/jpeg;base64,")
    for detection in response.json()["detections"]:
        x, y, width, height = detection["bbox"]
        assert 0 <= x <= image["width"] and 0 <= y <= image["height"]
        assert x + width <= image["width"] and y + height <= image["height"]


def test_api_release_generation_prevents_slow_stream_from_applying_stale_leds():
    class SlowPipeline(Pipeline):
        def process(self, *args, **kwargs):
            time.sleep(0.08)
            return super().process(*args, **kwargs)

    settings = Settings(mode="mock", pairing_key="secret", lease_ttl_seconds=1)
    pipeline = SlowPipeline(settings)
    client = TestClient(create_app(settings, pipeline))
    session = _controller(client)
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    accepted = client.post("/v1/stream/frame", headers=headers, files={"file": ("x.jpg", _jpeg(), "image/jpeg")})
    assert accepted.status_code == 200
    assert "frame_id" in accepted.json()
    assert client.delete(f"/v1/sessions/{session['session_id']}", headers={"X-Session-Ticket": session["ticket"]}).status_code == 200
    time.sleep(0.15)
    assert pipeline.serial.status()["confirmed_mask"] == 0
