import json

import cv2
import numpy as np

from toollighthouse.overlay import draw_overlay


def test_overlay_keeps_source_and_pixel_geometry_with_unknown_ack(monkeypatch):
    source = np.zeros((240, 640, 3), dtype=np.uint8)
    detection = {"track_id": 1, "bbox": [20, 100, 40, 50], "name": "Hammer", "class_id": "hammer", "validation": "confirmed"}
    texts = []
    original = cv2.putText
    def record(image, text, *args, **kwargs):
        texts.append(text)
        return original(image, text, *args, **kwargs)
    monkeypatch.setattr(cv2, "putText", record)
    output = draw_overlay(source, [detection], metrics={}, arduino={"simulated": False, "connected": True, "requested_mask": 1, "confirmed_mask": None, "ack": False})
    assert output.shape == source.shape and output.dtype == source.dtype
    assert not source.any()
    assert output[100, 20].tolist() == [0, 220, 0]
    assert any("Present: Hammer" in text for text in texts)
    assert any("req 000000001" in text and "ack ?????????" in text and "ACK n/a" in text for text in texts)
    assert any("cap n/a" in text and "infer n/a" in text for text in texts)


def test_overlay_never_displays_simulated_ack_latency(monkeypatch):
    texts = []
    monkeypatch.setattr(cv2, "putText", lambda image, text, *args, **kwargs: texts.append(text))
    draw_overlay(np.zeros((240, 640, 3), dtype=np.uint8), [], metrics={"capture_to_ack_ms": 123}, arduino={"simulated": True, "connected": True, "ack": True, "confirmed_mask": 0, "requested_mask": 0})
    assert any("toACK n/a" in text for text in texts)
    assert not any("123" in text for text in texts)


def test_run_reports_actual_capture_events_without_headless_display_or_mock_inference(tmp_path, capsys):
    from toollighthouse.__main__ import main
    path = tmp_path / "camera.avi"
    video = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (160, 120))
    assert video.isOpened()
    for _ in range(21):
        video.write(np.zeros((120, 160, 3), dtype=np.uint8))
    video.release()
    assert main(["run", "--mode", "mock", "--simulate-leds", "--headless", "--video", str(path), "--max-frames", "21"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["captured_frames"] == 21
    assert result["metrics"]["capture_fps"] > 0
    assert result["metrics"]["display_fps"] is None
    assert result["metrics"]["inference_hz"] is None
    assert result["metrics"]["capture_to_ack_ms"] is None
