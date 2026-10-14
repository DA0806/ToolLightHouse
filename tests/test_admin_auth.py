import json
import pytest
from fastapi.testclient import TestClient

from toollighthouse.api import create_app
from toollighthouse.config import Settings


def _create_session(client, role, key=None, admin_key=None):
    headers = {}
    if key:
        headers["X-Pairing-Key"] = key
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    resp = client.post("/v1/sessions", headers=headers, json={"device_id": f"dev_{role}", "role": role})
    return resp


def test_admin_role_requires_admin_key():
    settings = Settings(mode="mock", pairing_key="reader_pairing_key", admin_key="top_secret_admin_key")
    client = TestClient(create_app(settings))

    # Wrong or missing admin key -> 401
    bad_resp = _create_session(client, "admin", key="reader_pairing_key")
    assert bad_resp.status_code == 401

    # Correct admin key -> 200 with role 'admin'
    ok_resp = _create_session(client, "admin", admin_key="top_secret_admin_key")
    assert ok_resp.status_code == 200
    assert ok_resp.json()["role"] == "admin"


def test_pairing_key_suffix_is_not_an_admin_fallback():
    settings = Settings(mode="mock", pairing_key="reader_pairing_key", admin_key="")
    client = TestClient(create_app(settings))

    # Reader authentication remains available with the pairing secret.
    reader_resp = _create_session(client, "reader", key="reader_pairing_key")
    assert reader_resp.status_code == 200

    # A predictable pairing-derived value must never grant admin access.
    missing_resp = _create_session(client, "admin", key="reader_pairing_key")
    guessed_resp = _create_session(client, "admin", admin_key="reader_pairing_key-admin")
    assert missing_resp.status_code == 401
    assert guessed_resp.status_code == 401


def test_reader_and_controller_rejected_from_mutation_routes():
    settings = Settings(mode="mock", pairing_key="reader_pairing_key", admin_key="top_secret_admin_key")
    client = TestClient(create_app(settings))

    # Create reader and controller sessions
    reader = _create_session(client, "reader", key="reader_pairing_key").json()
    reader_headers = {"X-Session-Id": reader["session_id"], "X-Session-Ticket": reader["ticket"]}

    controller = _create_session(client, "controller", key="reader_pairing_key").json()
    ctrl_headers = {"X-Session-Id": controller["session_id"], "X-Session-Ticket": controller["ticket"]}

    # Mutation routes must return 403 Forbidden for both reader and controller
    mutation_routes = [
        ("PUT", "/v1/config", {"settings": {}, "inventory": {}}),
        ("POST", "/v1/config/apply", {}),
        ("POST", "/v1/config/rollback", {}),
        ("GET", "/v1/config/export", None),
        ("POST", "/v1/config/import", {}),
        ("POST", "/v1/system/mode", {"mode": "mock"}),
        ("PUT", "/v1/calibration/roi", {"roi": [0, 0, 10, 10]}),
        ("POST", "/v1/arduino/connect", {"port": "COM1"}),
        ("POST", "/v1/arduino/maintenance/enter", {}),
    ]

    for method, path, body in mutation_routes:
        # Check reader
        if method == "GET":
            r_resp = client.get(path, headers=reader_headers)
            c_resp = client.get(path, headers=ctrl_headers)
        elif method == "PUT":
            r_resp = client.put(path, headers=reader_headers, json=body)
            c_resp = client.put(path, headers=ctrl_headers, json=body)
        else:
            r_resp = client.post(path, headers=reader_headers, json=body)
            c_resp = client.post(path, headers=ctrl_headers, json=body)

        assert r_resp.status_code == 403, f"Reader was not 403 on {method} {path}"
        assert c_resp.status_code == 403, f"Controller was not 403 on {method} {path}"


def test_no_secrets_leaked_in_status_or_config_or_export():
    settings = Settings(mode="mock", pairing_key="my_pairing_secret", admin_key="my_admin_secret")
    client = TestClient(create_app(settings))

    admin = _create_session(client, "admin", admin_key="my_admin_secret").json()
    headers = {"X-Session-Id": admin["session_id"], "X-Session-Ticket": admin["ticket"]}

    status_data = json.dumps(client.get("/v1/status", headers=headers).json())
    config_data = json.dumps(client.get("/v1/config", headers=headers).json())
    export_data = json.dumps(client.get("/v1/config/export", headers=headers).json())

    for secret in ["my_pairing_secret", "my_admin_secret"]:
        assert secret not in status_data
        assert secret not in config_data
        assert secret not in export_data
