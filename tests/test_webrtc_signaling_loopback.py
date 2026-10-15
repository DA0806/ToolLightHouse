import io
import time
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from PIL import Image, ImageDraw

from toollighthouse.api import create_app
from toollighthouse.config import Settings


def make_test_jpeg():
    img = Image.new("RGB", (320, 240), "black")
    ImageDraw.Draw(img).rectangle((50, 50, 150, 150), fill="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_operator_signaling_auth_enforcement():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    # 1. Reader role is denied operator signaling
    reader_sess = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "reader-dev", "role": "reader"},
    ).json()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/camera/signal") as ws:
            ws.send_json({
                "type": "auth_operator",
                "session_id": reader_sess["session_id"],
                "ticket": reader_sess["ticket"],
            })
            resp = ws.receive_json()
            assert resp.get("type") == "error"
            # next receive should raise disconnect
            ws.receive_text()
    assert exc.value.code == 1008

    # 2. Controller role with active physical lease succeeds
    ctrl_sess = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()

    with client.websocket_connect("/v1/camera/signal") as ws:
        ws.send_json({
            "type": "auth_operator",
            "session_id": ctrl_sess["session_id"],
            "ticket": ctrl_sess["ticket"],
        })
        resp = ws.receive_json()
        assert resp["type"] == "auth_ok"
        assert resp["role"] == "operator"


def test_transmitter_signaling_invite_consumption_and_peer_notification():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()
    ctrl_headers = {"X-Session-Id": ctrl["session_id"], "X-Session-Ticket": ctrl["ticket"]}

    # Create invite
    inv_res = client.post("/v1/camera/invite", headers=ctrl_headers, json={"expires_in_seconds": 60})
    assert inv_res.status_code == 200
    invite = inv_res.json()
    token = invite["invite_token"]

    # Open operator connection first
    with client.websocket_connect("/v1/camera/signal") as op_ws:
        op_ws.send_json({
            "type": "auth_operator",
            "session_id": ctrl["session_id"],
            "ticket": ctrl["ticket"],
        })
        assert op_ws.receive_json()["type"] == "auth_ok"

        # Open transmitter connection with invalid token -> receives error then disconnected
        with client.websocket_connect("/v1/camera/signal") as bad_tx_ws:
            bad_tx_ws.send_json({"type": "auth_transmitter", "invite_token": "bogus-token"})
            err_msg = bad_tx_ws.receive_json()
            assert err_msg["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as exc:
                bad_tx_ws.receive_text()
            assert exc.value.code == 1008

        # Open transmitter connection with valid token -> succeeds
        with client.websocket_connect("/v1/camera/signal") as tx_ws:
            tx_ws.send_json({"type": "auth_transmitter", "invite_token": token})
            tx_auth = tx_ws.receive_json()
            assert tx_auth["type"] == "auth_ok"
            assert tx_auth["role"] == "transmitter"
            assert "transmitter_ticket" in tx_auth

            # Operator is notified of transmitter connection
            op_event = op_ws.receive_json()
            assert op_event["type"] == "peer_connected"
            assert op_event["role"] == "transmitter"


def test_signaling_offer_answer_candidate_relay():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()
    ctrl_headers = {"X-Session-Id": ctrl["session_id"], "X-Session-Ticket": ctrl["ticket"]}

    inv_res = client.post("/v1/camera/invite", headers=ctrl_headers, json={"expires_in_seconds": 60})
    token = inv_res.json()["invite_token"]

    with client.websocket_connect("/v1/camera/signal") as op_ws:
        op_ws.send_json({
            "type": "auth_operator",
            "session_id": ctrl["session_id"],
            "ticket": ctrl["ticket"],
        })
        assert op_ws.receive_json()["type"] == "auth_ok"

        with client.websocket_connect("/v1/camera/signal") as tx_ws:
            tx_ws.send_json({"type": "auth_transmitter", "invite_token": token})
            assert tx_ws.receive_json()["type"] == "auth_ok"
            assert op_ws.receive_json()["type"] == "peer_connected"

            # 1. Operator sends offer -> transmitter receives
            offer_sdp = {"type": "offer", "sdp": "v=0\r\no=- 12345 2 IN IP4 127.0.0.1"}
            op_ws.send_json({"type": "offer", "sdp": offer_sdp})
            relayed_offer = tx_ws.receive_json()
            assert relayed_offer["type"] == "offer"
            assert relayed_offer["sdp"] == offer_sdp

            # 2. Transmitter sends answer -> operator receives
            answer_sdp = {"type": "answer", "sdp": "v=0\r\no=- 67890 2 IN IP4 127.0.0.1"}
            tx_ws.send_json({"type": "answer", "sdp": answer_sdp})
            relayed_answer = op_ws.receive_json()
            assert relayed_answer["type"] == "answer"
            assert relayed_answer["sdp"] == answer_sdp

            # 3. Transmitter sends ICE candidate -> operator receives
            cand = {"candidate": "candidate:1 1 UDP 2122260223 192.168.1.50 50000 typ host"}
            tx_ws.send_json({"type": "candidate", "candidate": cand})
            relayed_cand = op_ws.receive_json()
            assert relayed_cand["type"] == "candidate"
            assert relayed_cand["candidate"] == cand


def test_revoke_transmitters_closes_websocket_and_cancels():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()
    ctrl_headers = {"X-Session-Id": ctrl["session_id"], "X-Session-Ticket": ctrl["ticket"]}

    inv_res = client.post("/v1/camera/invite", headers=ctrl_headers, json={"expires_in_seconds": 60})
    token = inv_res.json()["invite_token"]

    with client.websocket_connect("/v1/camera/signal") as tx_ws:
        tx_ws.send_json({"type": "auth_transmitter", "invite_token": token})
        assert tx_ws.receive_json()["type"] == "auth_ok"

        # Revoke all transmitters as owner
        rev_res = client.post("/v1/camera/revoke", headers=ctrl_headers)
        assert rev_res.status_code == 200
        assert rev_res.json()["disconnected_transmitters"] >= 1

        # Transmitter socket is closed by server
        with pytest.raises(WebSocketDisconnect) as exc:
            tx_ws.receive_text()
        assert exc.value.code == 1008


def test_fallback_frame_upload_validation_and_fencing():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()
    ctrl_headers = {"X-Session-Id": ctrl["session_id"], "X-Session-Ticket": ctrl["ticket"]}

    inv_res = client.post("/v1/camera/invite", headers=ctrl_headers, json={"expires_in_seconds": 60})
    token = inv_res.json()["invite_token"]

    # Consume invite to get transmitter ticket and keep the authorized socket
    # open while fallback frames are uploaded.
    with client.websocket_connect("/v1/camera/signal") as tx_ws:
        tx_ws.send_json({"type": "auth_transmitter", "invite_token": token})
        auth_data = tx_ws.receive_json()
        tx_id = auth_data["transmitter_id"]
        tx_ticket = auth_data["transmitter_ticket"]
        # 1. Uploading with correct ticket succeeds
        jpeg = make_test_jpeg()
        res = client.post(
            "/v1/camera/frame-upload",
            headers={"X-Transmitter-Id": tx_id, "X-Transmitter-Token": tx_ticket},
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
        )
        assert res.status_code == 200
        assert res.json()["accepted"] is True

        # 2. Uploading with wrong ticket fails with 403
        res_bad = client.post(
            "/v1/camera/frame-upload",
            headers={"X-Transmitter-Id": tx_id, "X-Transmitter-Token": "invalid-ticket"},
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
        )
        assert res_bad.status_code == 403

        # 3. Uploading frame > 350KB fails with 400
        big_bytes = b"0" * (350 * 1024 + 10)
        res_big = client.post(
            "/v1/camera/frame-upload",
            headers={"X-Transmitter-Id": tx_id, "X-Transmitter-Token": tx_ticket},
            files={"file": ("frame.jpg", big_bytes, "image/jpeg")},
        )
        assert res_big.status_code == 400

    # A disconnected transmitter cannot reactivate the fallback scene using a
    # grace credential. Reconnection must explicitly re-establish the socket.
    stale = client.post(
        "/v1/camera/frame-upload",
        headers={"X-Transmitter-Id": tx_id, "X-Transmitter-Token": tx_ticket},
        files={"file": ("frame.jpg", jpeg, "image/jpeg")},
    )
    assert stale.status_code == 403

    with client.websocket_connect("/v1/camera/signal") as reconnect_ws:
        reconnect_ws.send_json({
            "type": "reconnect_transmitter",
            "transmitter_id": tx_id,
            "transmitter_ticket": tx_ticket,
        })
        assert reconnect_ws.receive_json()["type"] == "auth_ok"
        time.sleep(0.55)
        restored = client.post(
            "/v1/camera/frame-upload",
            headers={"X-Transmitter-Id": tx_id, "X-Transmitter-Token": tx_ticket},
            files={"file": ("frame.jpg", jpeg, "image/jpeg")},
        )
        assert restored.status_code == 200


def test_webcam_enumeration_and_source_selection_flow():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    ctrl = client.post(
        "/v1/sessions",
        headers={"X-Pairing-Key": "secret"},
        json={"device_id": "ctrl-dev", "role": "controller"},
    ).json()
    ctrl_headers = {"X-Session-Id": ctrl["session_id"], "X-Session-Ticket": ctrl["ticket"]}

    # GET webcams endpoint responds with list
    cams_res = client.get("/v1/camera/webcams", headers=ctrl_headers)
    assert cams_res.status_code == 200
    assert "devices" in cams_res.json()

    # Sources list contains 5 sources
    src_res = client.get("/v1/camera/sources", headers=ctrl_headers)
    assert src_res.status_code == 200
    sources = src_res.json()["sources"]
    assert len(sources) == 5

    # Switch active source via POST /v1/camera/sources/active
    switch_res = client.post(
        "/v1/camera/sources/active",
        headers=ctrl_headers,
        json={"source_id": "laptop_builtin"},
    )
    assert switch_res.status_code == 200
    assert switch_res.json()["active_source_id"] == "laptop_builtin"


def test_camera_spa_route_resolves_index_html():
    settings = Settings(mode="mock", pairing_key="secret", auth_required=True)
    client = TestClient(create_app(settings))

    res = client.get("/camera")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

    res_slash = client.get("/camera/")
    assert res_slash.status_code == 200
    assert "text/html" in res_slash.headers.get("content-type", "")
