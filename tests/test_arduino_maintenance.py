import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.config import Settings
from toollighthouse.pipeline import Pipeline
from toollighthouse.serial_transport import SimulatedSerial, list_com_ports


def _admin_headers(client, admin_key="admin_secret"):
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": admin_key},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    return {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}


def test_list_com_ports_returns_list():
    ports = list_com_ports()
    assert isinstance(ports, list)
    for p in ports:
        assert "port" in p
        assert "description" in p


def test_list_com_ports_filters_empty_devices_and_deduplicates(monkeypatch):
    import serial.tools.list_ports

    monkeypatch.setattr(
        serial.tools.list_ports,
        "comports",
        lambda: [
            SimpleNamespace(device="", description="Bluetooth", hwid="bt-empty"),
            SimpleNamespace(device="  COM7  ", description="Arduino", hwid="usb-7"),
            SimpleNamespace(device="COM7", description="duplicate", hwid="duplicate"),
            SimpleNamespace(device="COM8", description=None, hwid=None),
        ],
    )

    assert list_com_ports() == [
        {"port": "COM7", "description": "Arduino", "hwid": "usb-7"},
        {"port": "COM8", "description": "", "hwid": ""},
    ]


def test_maintenance_mode_blocks_inference_and_clears_scene(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # Enter maintenance mode
    enter_resp = client.post("/v1/arduino/maintenance/enter", headers=headers)
    assert enter_resp.status_code == 200
    assert enter_resp.json()["maintenance_mode"] is True

    # Inference must be rejected with 409 Conflict
    infer_resp = client.post(
        "/v1/analyze/photo",
        headers=headers,
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg")},
    )
    assert infer_resp.status_code == 409
    assert "maintenance" in infer_resp.json()["detail"].lower()

    # Leave maintenance mode
    leave_resp = client.post("/v1/arduino/maintenance/leave", headers=headers)
    assert leave_resp.status_code == 200
    assert leave_resp.json()["maintenance_mode"] is False


def test_test_led_activates_single_bit_with_auto_off(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # Cannot test LED without entering maintenance mode
    bad_test = client.post("/v1/arduino/maintenance/test-led/0", headers=headers)
    assert bad_test.status_code == 409

    # Enter maintenance mode
    client.post("/v1/arduino/maintenance/enter", headers=headers)

    # Test LED 2 (bit 2 -> mask 4) with 0.15s auto-off
    resp = client.post("/v1/arduino/maintenance/test-led/2?duration=0.15", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["led_index"] == 2
    assert payload["mask"] == 4
    assert payload["arduino"]["confirmed_mask"] == 4

    # Wait for auto-off timer to trigger
    time.sleep(0.25)
    status = client.get("/v1/status", headers=headers).json()
    assert status["arduino"]["requested_mask"] == 0
    assert status["arduino"]["confirmed_mask"] == 0

    # Reject out of bounds LED index
    assert client.post("/v1/arduino/maintenance/test-led/9", headers=headers).status_code in (400, 422)
    assert client.post("/v1/arduino/maintenance/test-led/-1", headers=headers).status_code in (400, 422)


def test_sequential_led_test_cancels_prior_timer(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    client.post("/v1/arduino/maintenance/enter", headers=headers)

    # Test LED 1 with 1.0s duration
    client.post("/v1/arduino/maintenance/test-led/1?duration=1.0", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 2

    # Immediately test LED 5 with 0.15s duration (cancels LED 1)
    client.post("/v1/arduino/maintenance/test-led/5?duration=0.15", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 32

    # All-off turns off immediately
    client.post("/v1/arduino/maintenance/all-off", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["requested_mask"] == 0


def test_disconnect_reports_unconfirmed_mask(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    resp = client.post("/v1/arduino/disconnect", headers=headers)
    assert resp.status_code == 200
    arduino = resp.json()["arduino"]
    assert arduino["connected"] is False
    assert arduino["confirmed_mask"] is None


def test_connect_rejects_non_enumerated_physical_com_port(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    resp = client.post("/v1/arduino/connect", headers=headers, json={"port": "COM999_NON_EXISTENT", "simulated": False})
    assert resp.status_code == 400
    assert "not an enumerated com port" in resp.json()["detail"].lower()


def test_connect_closes_previous_transport_safely(tmp_path):
    from toollighthouse.pipeline import Pipeline
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial1 = SimulatedSerial()
    client = TestClient(create_app(settings, pipeline=Pipeline(settings, serial=serial1, safe_startup=True), config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # Reconnect with simulated=True
    resp = client.post("/v1/arduino/connect", headers=headers, json={"simulated": True})
    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    assert not serial1.connected  # previous serial closed


def test_timer_generation_prevents_stale_timer_clobbering(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    client.post("/v1/arduino/maintenance/enter", headers=headers)

    # Test LED 0 with 0.1s duration
    client.post("/v1/arduino/maintenance/test-led/0?duration=0.1", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 1

    # Leave maintenance mode immediately
    client.post("/v1/arduino/maintenance/leave", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 0

    # Wait for the first timer to expire (0.15s)
    time.sleep(0.18)

    # Re-enter maintenance and test LED 3
    client.post("/v1/arduino/maintenance/enter", headers=headers)
    client.post("/v1/arduino/maintenance/test-led/3?duration=2.0", headers=headers)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 8

    # Sleep another 0.1s - old timer must NOT turn off LED 3
    time.sleep(0.1)
    assert client.get("/v1/status", headers=headers).json()["arduino"]["confirmed_mask"] == 8


def test_auto_off_publishes_confirmed_zero_ack_over_websocket(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)
    ws_ticket = client.post(
        f"/v1/sessions/{headers['X-Session-Id']}/ws-ticket",
        headers={"X-Session-Ticket": headers["X-Session-Ticket"]},
    ).json()["ticket"]

    with client.websocket_connect(f"/v1/ws?session_id={headers['X-Session-Id']}&ticket={ws_ticket}") as websocket:
        assert websocket.receive_json()["event"] == "connected"
        assert client.post("/v1/arduino/maintenance/enter", headers=headers).status_code == 200
        assert client.post("/v1/arduino/maintenance/test-led/2?duration=0.1", headers=headers).status_code == 200
        ack_masks = []
        while len(ack_masks) < 2:
            event = websocket.receive_json()
            if event["type"] == "arduino_ack":
                ack_masks.append(event["arduino"]["confirmed_mask"])
        assert 4 in ack_masks
        auto_off_ack = websocket.receive_json()
        while auto_off_ack["type"] != "arduino_ack" or auto_off_ack["arduino"]["confirmed_mask"] != 0:
            auto_off_ack = websocket.receive_json()
        assert auto_off_ack["arduino"]["requested_mask"] == 0
        assert auto_off_ack["arduino"]["confirmed_mask"] == 0
        assert auto_off_ack["arduino"]["ack"] is True


def test_auto_off_ack_failure_is_published_without_claiming_physical_zero(tmp_path):
    class FailingAutoOffSerial:
        simulated = False

        def __init__(self):
            self.requested_mask = 0
            self.confirmed_mask = 0
            self.last_ack_at = time.time()

        def set_scene_deadline(self, _deadline):
            return None

        def send_mask(self, mask):
            self.requested_mask = mask
            if mask == 0 and self.confirmed_mask:
                return False
            self.confirmed_mask = mask
            self.last_ack_at = time.time()
            return True

        def status(self):
            return {
                "connected": True,
                "simulated": False,
                "requested_mask": self.requested_mask,
                "confirmed_mask": self.confirmed_mask,
                "ack": self.confirmed_mask is not None,
                "last_ack_at": self.last_ack_at,
            }

        def close(self):
            return None

    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = FailingAutoOffSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    client = TestClient(create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)
    ws_ticket = client.post(
        f"/v1/sessions/{headers['X-Session-Id']}/ws-ticket",
        headers={"X-Session-Ticket": headers["X-Session-Ticket"]},
    ).json()["ticket"]

    with client.websocket_connect(f"/v1/ws?session_id={headers['X-Session-Id']}&ticket={ws_ticket}") as websocket:
        assert websocket.receive_json()["event"] == "connected"
        client.post("/v1/arduino/maintenance/enter", headers=headers)
        client.post("/v1/arduino/maintenance/test-led/1?duration=0.1", headers=headers)
        while True:
            event = websocket.receive_json()
            if event["type"] == "arduino_ack" and event["arduino"]["confirmed_mask"] == 2:
                break
        auto_off_ack = websocket.receive_json()
        while (
            auto_off_ack["type"] != "arduino_ack"
            or auto_off_ack["arduino"]["confirmed_mask"] != 2
            or auto_off_ack["arduino"]["ack"] is not False
        ):
            auto_off_ack = websocket.receive_json()
        assert auto_off_ack["arduino"]["ack"] is False
        assert auto_off_ack["arduino"]["confirmed_mask"] == 2

