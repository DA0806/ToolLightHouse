import json

import pytest

from toollighthouse.tracking import CentroidTracker


def test_tracker_global_assignment_preserves_ids_when_detections_reorder():
    tracker = CentroidTracker(max_distance=120, max_missed=2)
    initial = tracker.update([(0, 0, 10, 10), (100, 0, 10, 10)])
    first_id, second_id = (track.track_id for track in initial)

    reordered = tracker.update([(105, 0, 10, 10), (75, 0, 10, 10)])
    by_x = {track.bbox[0]: track.track_id for track in reordered}
    assert by_x[75] == first_id
    assert by_x[105] == second_id


def test_tracker_expires_lost_tracks_before_reappearance():
    tracker = CentroidTracker(max_distance=50, max_missed=1)
    original = tracker.update([(10, 10, 20, 20)])[0].track_id
    assert tracker.update([]) == []
    assert tracker.update([]) == []
    replacement = tracker.update([(10, 10, 20, 20)])[0].track_id
    assert replacement != original


def test_cli_rejects_negative_frame_limit_and_unconfigured_physical_leds():
    from toollighthouse.__main__ import main

    with pytest.raises(SystemExit):
        main(["run", "--max-frames", "-1"])
    with pytest.raises(SystemExit, match="serial"):
        main(["test-leds"])


def test_cli_rejects_simulated_and_physical_serial_together():
    from toollighthouse.__main__ import main

    with pytest.raises(SystemExit):
        main(["test-leds", "--simulate-leds", "--serial", "COM-test"])


def _inventory_file(path):
    payload = {
        "example": False,
        "categories": [
            {
                "class_id": f"tool_{index + 1}",
                "name": f"Tool {index + 1}",
                "location": f"slot-{index + 1}",
                "led_index": index,
                "pin": index + 2,
            }
            for index in range(9)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_test_leds_always_fails_safe_and_closes_after_ack_error(monkeypatch, tmp_path):
    from toollighthouse.__main__ import main

    inventory = tmp_path / "inventory.json"
    _inventory_file(inventory)

    class FakeSerial:
        simulated = False
        instances = []

        def __init__(self, port):
            self.calls = []
            self.closed = False
            self.__class__.instances.append(self)

        def open(self):
            self.calls.append("open")

        def send_mask(self, mask):
            self.calls.append(mask)
            return mask != 511

        def close(self):
            self.closed = True

        def status(self):
            return {"connected": True}

    monkeypatch.setattr("toollighthouse.serial_transport.SerialTransport", FakeSerial)
    with pytest.raises(SystemExit, match="ACK"):
        main(["test-leds", "--mode", "real", "--serial", "COM-test", "--inventory", str(inventory), "--confirm-inventory"])
    assert FakeSerial.instances[0].calls == ["open", 0, 511, 0]
    assert FakeSerial.instances[0].closed is True


def test_run_closes_camera_if_pipeline_initialization_fails(monkeypatch):
    from toollighthouse.__main__ import main

    class FakeCamera:
        instances = []

        def __init__(self, source):
            self.closed = False
            self.__class__.instances.append(self)

        def open(self):
            return self

        def close(self):
            self.closed = True

    class FailingPipeline:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("model/gallery failed")

    monkeypatch.setattr("toollighthouse.camera.OpenCVCamera", FakeCamera)
    monkeypatch.setattr("toollighthouse.pipeline.Pipeline", FailingPipeline)
    with pytest.raises(RuntimeError, match="model/gallery"):
        main(["run", "--headless", "--max-frames", "1"])
    assert FakeCamera.instances[0].closed is True


def test_cli_serve_uses_saved_real_config_and_enters_safe_preparation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from toollighthouse.__main__ import main
    from toollighthouse.config import ConfigStore, Settings

    config_file = tmp_path / "saved" / "app_config.json"
    settings = Settings(
        mode="real",
        pairing_key="serve-secret",
        admin_key="admin_secret",
        gallery_path=str(tmp_path / "saved" / "missing-gallery.json"),
        background_path=str(tmp_path / "saved" / "missing-background.png"),
        model_path=str(tmp_path / "saved" / "missing-model"),
    )
    ConfigStore(config_file).save_atomic(settings, settings.inventory)
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs
        with TestClient(app) as client:
            assert client.get("/health").json()["mode"] == "preparation"
            session = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "admin_secret"},
                json={"device_id": "serve-test", "role": "admin"},
            ).json()
            readiness = client.get(
                "/v1/system/readiness",
                headers={"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]},
            ).json()
            assert readiness["can_activate_real"] is False

    monkeypatch.setattr("uvicorn.run", fake_run)
    assert main(["serve", "--config", str(config_file), "--pairing-key", "serve-secret", "--host", "127.0.0.1", "--port", "8123"]) == 0
    assert captured["kwargs"] == {"host": "127.0.0.1", "port": 8123}


def test_cli_serve_passes_validated_tls_files(monkeypatch, tmp_path):
    from toollighthouse.__main__ import main

    cert = tmp_path / "lan-cert.pem"
    key = tmp_path / "lan-key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("private key", encoding="utf-8")
    captured = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    assert main([
        "serve",
        "--pairing-key",
        "secret",
        "--ssl-certfile",
        str(cert),
        "--ssl-keyfile",
        str(key),
    ]) == 0
    assert captured["ssl_certfile"] == str(cert)
    assert captured["ssl_keyfile"] == str(key)


def test_cli_rejects_unpaired_tls_options(tmp_path):
    from toollighthouse.__main__ import main

    with pytest.raises(SystemExit):
        main(["serve", "--ssl-certfile", str(tmp_path / "cert.pem")])
