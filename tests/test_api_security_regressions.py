"""Regression coverage for controller lease revocation boundaries."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from toollighthouse.api import create_app
from toollighthouse.config import Settings


def _jpeg():
    image = Image.new("RGB", (64, 64), "black")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _controller(client, device_id):
    response = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": device_id, "role": "controller"},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("route", ["photo", "calibration", "stream", "ws-ticket", "release"])
def test_released_controller_cannot_reuse_ticket_after_new_controller_acquires_lease(route):
    client = TestClient(create_app(Settings(mode="mock", pairing_key="secret")))
    old = _controller(client, "old-phone")
    old_headers = {
        "X-Session-Id": old["session_id"],
        "X-Session-Ticket": old["ticket"],
    }

    released = client.delete(f"/v1/sessions/{old['session_id']}", headers=old_headers)
    assert released.status_code == 200
    _controller(client, "new-phone")

    upload = {"file": ("frame.jpg", _jpeg(), "image/jpeg")}
    if route == "photo":
        response = client.post("/v1/analyze/photo", headers=old_headers, files=upload)
    elif route == "calibration":
        response = client.post("/v1/calibration/background", headers=old_headers, files=upload)
    elif route == "stream":
        response = client.post("/v1/stream/frame", headers=old_headers, files=upload)
    elif route == "ws-ticket":
        response = client.post(f"/v1/sessions/{old['session_id']}/ws-ticket", headers=old_headers)
    else:
        response = client.delete(f"/v1/sessions/{old['session_id']}", headers=old_headers)
    assert response.status_code == 401
