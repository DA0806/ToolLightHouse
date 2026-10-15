import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from toollighthouse.__main__ import main


def test_cli_reads_admin_key_from_env_for_serve(monkeypatch):
    monkeypatch.setenv("TOOLLIGHTHOUSE_PAIRING_KEY", "test-operator-key")
    monkeypatch.setenv("TOOLLIGHTHOUSE_ADMIN_KEY", "test-admin-secret-999")

    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs
        with TestClient(app) as client:
            # Operator authentication
            op_resp = client.post(
                "/v1/sessions",
                headers={"X-Pairing-Key": "test-operator-key"},
                json={"device_id": "tablet", "role": "controller"},
            )
            assert op_resp.status_code == 200
            assert op_resp.json()["role"] == "controller"

            # Admin authentication with correct key
            admin_resp = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "test-admin-secret-999"},
                json={"device_id": "admin-station", "role": "admin"},
            )
            assert admin_resp.status_code == 200
            assert admin_resp.json()["role"] == "admin"

            # Admin authentication with wrong key fails
            bad_admin_resp = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "wrong-secret"},
                json={"device_id": "admin-station", "role": "admin"},
            )
            assert bad_admin_resp.status_code == 401

    monkeypatch.setattr("uvicorn.run", fake_run)
    exit_code = main(["serve", "--host", "127.0.0.1", "--port", "8125"])
    assert exit_code == 0
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8125,
        "log_level": "warning",
        "access_log": False,
    }


def test_cli_reads_admin_key_from_fallback_env(monkeypatch):
    monkeypatch.delenv("TOOLLIGHTHOUSE_ADMIN_KEY", raising=False)
    monkeypatch.setenv("TOOLLIGHTHOUSE_PAIRING_KEY", "test-operator-key")
    monkeypatch.setenv("TOOL_LIGHTHOUSE_ADMIN_KEY", "fallback-admin-secret-111")

    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        with TestClient(app) as client:
            admin_resp = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "fallback-admin-secret-111"},
                json={"device_id": "admin-station", "role": "admin"},
            )
            assert admin_resp.status_code == 200
            assert admin_resp.json()["role"] == "admin"

    monkeypatch.setattr("uvicorn.run", fake_run)
    exit_code = main(["serve", "--host", "127.0.0.1", "--port", "8125"])
    assert exit_code == 0


def test_cli_omits_admin_key_when_env_not_set(monkeypatch):
    monkeypatch.delenv("TOOLLIGHTHOUSE_ADMIN_KEY", raising=False)
    monkeypatch.delenv("TOOL_LIGHTHOUSE_ADMIN_KEY", raising=False)
    monkeypatch.setenv("TOOLLIGHTHOUSE_PAIRING_KEY", "test-operator-key")

    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        with TestClient(app) as client:
            admin_resp = client.post(
                "/v1/sessions",
                headers={"X-Admin-Key": "any-attempt"},
                json={"device_id": "admin-station", "role": "admin"},
            )
            assert admin_resp.status_code == 401

    monkeypatch.setattr("uvicorn.run", fake_run)
    exit_code = main(["serve", "--host", "127.0.0.1", "--port", "8125"])
    assert exit_code == 0


def test_cli_does_not_add_command_line_secret_flags():
    with pytest.raises(SystemExit):
        main(["serve", "--admin-key", "plaintext-secret"])
