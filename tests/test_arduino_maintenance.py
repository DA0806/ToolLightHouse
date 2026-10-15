import threading
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
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
    client.post("/v1/sessions/lease/takeover", headers=headers)
    return headers


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


def test_maintenance_requires_held_physical_lease(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))

    # Admin session without takeover / without held physical lease
    session = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "laptop", "role": "admin"},
    ).json()
    headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}

    # enter_maintenance without held lease must return 409
    resp = client.post("/v1/arduino/maintenance/enter", headers=headers)
    assert resp.status_code == 409
    assert "lease" in resp.json()["detail"].lower()

    # test-led without held lease must return 409
    resp_led = client.post("/v1/arduino/maintenance/test-led/0", headers=headers)
    assert resp_led.status_code == 409
    assert "lease" in resp_led.json()["detail"].lower()

    # all-off without held lease must return 409
    resp_off = client.post("/v1/arduino/maintenance/all-off", headers=headers)
    assert resp_off.status_code == 409
    assert "lease" in resp_off.json()["detail"].lower()


def test_maintenance_rejected_when_operator_holds_lease(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    client = TestClient(create_app(settings, config_path=tmp_path / "app_config.json"))

    # Operator gets controller lease
    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "phone-op", "role": "controller"},
    ).json()

    # Admin creates session without takeover
    admin = client.post(
        "/v1/sessions",
        headers={"X-Admin-Key": "admin_secret"},
        json={"device_id": "laptop-admin", "role": "admin"},
    ).json()
    admin_headers = {"X-Session-Id": admin["session_id"], "X-Session-Ticket": admin["ticket"]}

    # Admin is blocked from maintenance because operator holds the lease
    enter_resp = client.post("/v1/arduino/maintenance/enter", headers=admin_headers)
    assert enter_resp.status_code == 409
    assert "lease" in enter_resp.json()["detail"].lower()

    # Once admin takes over, admin holds lease and can enter maintenance
    takeover = client.post("/v1/sessions/lease/takeover", headers=admin_headers)
    assert takeover.status_code == 200
    enter_ok = client.post("/v1/arduino/maintenance/enter", headers=admin_headers)
    assert enter_ok.status_code == 200
    assert enter_ok.json()["maintenance_mode"] is True


def test_maintenance_exit_emergency_zero_semantics(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    client = TestClient(create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # Enter maintenance and turn on an LED
    client.post("/v1/arduino/maintenance/enter", headers=headers)
    client.post("/v1/arduino/maintenance/test-led/3?duration=2.0", headers=headers)
    assert serial.status()["confirmed_mask"] == 8

    # Leave maintenance immediately - must zero LEDs and reset maintenance mode safely
    leave_resp = client.post("/v1/arduino/maintenance/leave", headers=headers)
    assert leave_resp.status_code == 200
    assert leave_resp.json()["maintenance_mode"] is False
    assert serial.status()["requested_mask"] == 0
    assert serial.status()["confirmed_mask"] == 0


def test_maintenance_lease_expiry_rejects_further_actuation(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", lease_ttl_seconds=0.2)
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    client = TestClient(create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    # Enter maintenance while holding lease
    client.post("/v1/arduino/maintenance/enter", headers=headers)

    # Wait for lease to expire
    time.sleep(0.3)

    # Actuation after lease expiry must be rejected with 409
    test_resp = client.post("/v1/arduino/maintenance/test-led/1", headers=headers)
    assert test_resp.status_code == 409
    assert "lease" in test_resp.json()["detail"].lower()

    # But leaving maintenance to safely zero state is always allowed
    leave_resp = client.post("/v1/arduino/maintenance/leave", headers=headers)
    assert leave_resp.status_code == 200
    assert leave_resp.json()["maintenance_mode"] is False
    assert serial.status()["confirmed_mask"] == 0


def test_maintenance_expired_session_cannot_actuate_with_live_lease(tmp_path):
    settings = Settings(
        mode="mock",
        pairing_key="secret",
        admin_key="admin_secret",
        session_ttl_seconds=0.05,
        lease_ttl_seconds=2.0,
    )
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    client = TestClient(create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json"))
    headers = _admin_headers(client)

    assert client.post("/v1/arduino/maintenance/enter", headers=headers).status_code == 200
    time.sleep(0.1)

    response = client.post("/v1/arduino/maintenance/test-led/1", headers=headers)
    assert response.status_code == 401
    assert serial.status()["requested_mask"] == 0
    assert serial.status()["confirmed_mask"] == 0


def test_admin_takeover_during_maintenance_resets_safe_state(tmp_path):
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret")
    serial = SimulatedSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    client = TestClient(create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json"))

    # Admin 1 acquires lease and enters maintenance with LED 2 on
    admin1 = client.post("/v1/sessions", headers={"X-Admin-Key": "admin_secret"}, json={"device_id": "adm1", "role": "admin"}).json()
    h1 = {"X-Session-Id": admin1["session_id"], "X-Session-Ticket": admin1["ticket"]}
    client.post("/v1/sessions/lease/takeover", headers=h1)
    client.post("/v1/arduino/maintenance/enter", headers=h1)
    client.post("/v1/arduino/maintenance/test-led/2?duration=2.0", headers=h1)
    assert serial.status()["confirmed_mask"] == 4

    # Admin 2 performs takeover
    admin2 = client.post("/v1/sessions", headers={"X-Admin-Key": "admin_secret"}, json={"device_id": "adm2", "role": "admin"}).json()
    h2 = {"X-Session-Id": admin2["session_id"], "X-Session-Ticket": admin2["ticket"]}
    takeover = client.post("/v1/sessions/lease/takeover", headers=h2)
    assert takeover.status_code == 200

    # Safety invariant: takeover must cancel maintenance timer and zero Arduino mask
    assert serial.status()["requested_mask"] == 0
    assert serial.status()["confirmed_mask"] == 0


class RecordingBlockingSerial(SimulatedSerial):
    def __init__(self):
        super().__init__()
        self.history: list[tuple[int, float]] = []
        self.block_event = threading.Event()
        self.entered_event = threading.Event()
        self.should_block = False

    def send_mask(self, mask: int, retries: int = 2, expected_generation: int | None = None) -> bool:
        if self.should_block:
            self.should_block = False
            self.entered_event.set()
            self.block_event.wait(timeout=5.0)
        self.history.append((mask, time.monotonic()))
        return super().send_mask(mask, retries, expected_generation)


@pytest.mark.parametrize("fence_type", ["takeover", "leaseexpiry", "leave", "sourceswitch"])
def test_maintenance_deterministic_barrier_queued_nonzero_vs_fence(tmp_path, fence_type):
    from concurrent.futures import ThreadPoolExecutor

    lease_ttl = 0.3 if fence_type == "leaseexpiry" else 30.0
    settings = Settings(mode="mock", pairing_key="secret", admin_key="admin_secret", lease_ttl_seconds=lease_ttl)
    serial = RecordingBlockingSerial()
    pipeline = Pipeline(settings, serial=serial, safe_startup=True)
    app = create_app(settings, pipeline=pipeline, config_path=tmp_path / "app_config.json")

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=3) as executor:
        # Admin 1 acquires lease and enters maintenance
        admin1 = client.post(
            "/v1/sessions",
            headers={"X-Admin-Key": "admin_secret"},
            json={"device_id": "adm1", "role": "admin"},
        ).json()
        h1 = {"X-Session-Id": admin1["session_id"], "X-Session-Ticket": admin1["ticket"]}
        client.post("/v1/sessions/lease/takeover", headers=h1)
        client.post("/v1/arduino/maintenance/enter", headers=h1)

        # 1. Start command 1 in background: all-off (mask 0) blocks hardware_worker inside send_mask
        serial.should_block = True
        fut1 = executor.submit(
            lambda: client.post("/v1/arduino/maintenance/all-off", headers=h1)
        )
        assert serial.entered_event.wait(timeout=3.0), "Command 1 did not enter send_mask"

        # 2. Queue command 2 (non-zero mask) while hardware_worker is blocked
        fut2 = executor.submit(
            lambda: client.post("/v1/arduino/maintenance/test-led/3?duration=1.0", headers=h1)
        )
        # Brief pause to ensure ASGI task reaches hardware_worker submission
        time.sleep(0.05)

        # 3. Trigger fence while command 2 is queued
        fence_time = time.monotonic()
        if fence_type == "takeover":
            admin2 = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "admin_secret"},
                json={"device_id": "adm2", "role": "admin"},
            ).json()
            h2 = {"X-Session-Id": admin2["session_id"], "X-Session-Ticket": admin2["ticket"]}
            res = client.post("/v1/sessions/lease/takeover", headers=h2)
            assert res.status_code == 200
        elif fence_type == "leaseexpiry":
            # Wait for lease to expire past lease_ttl (0.3s)
            time.sleep(0.35)
        elif fence_type == "leave":
            res = client.post("/v1/arduino/maintenance/leave", headers=h1)
            assert res.status_code == 200
        elif fence_type == "sourceswitch":
            res = client.post("/v1/camera/sources/active", headers=h1, json={"source_id": "operator_device"})
            assert res.status_code == 200

        # 4. Now unblock command 1
        serial.block_event.set()

        # Both commands finish
        resp1 = fut1.result(timeout=5.0)
        resp2 = fut2.result(timeout=5.0)

        # Command 2 was queued before fence, so when hardware_worker picked it up,
        # it was guarded and rejected with 409
        assert resp2.status_code == 409, f"Expected 409 for queued command after {fence_type}, got {resp2.status_code}"

        # 5. Assert recorded serial writes: NO non-zero writes occurred after fence_time
        writes_after_fence = [mask for mask, ts in serial.history if ts >= fence_time]
        assert all(mask == 0 for mask in writes_after_fence), (
            f"Expected no non-zero writes after {fence_type} fence, but got: {writes_after_fence}"
        )

