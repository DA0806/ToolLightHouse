"""HTTP/WS software smoke: synthetic photo -> JSON boxes -> simulated ACK.

Run against a MOCK server. This is never an accuracy or hardware benchmark.
The pairing key is read from the environment and is not printed.
"""
import argparse
import asyncio
import io
import json
import os
from contextlib import suppress
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageDraw
from websockets.asyncio.client import connect


def image_bytes(count: int) -> bytes:
    image = Image.new("RGB", (900, 600), "#162431")
    draw = ImageDraw.Draw(image)
    for index in range(count):
        x, y = 90 + index % 3 * 260, 65 + index // 3 * 170
        draw.rectangle((x, y, x + 120, y + 75), fill="#39a6ab")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    key = os.environ.get("TOOLLIGHTHOUSE_PAIRING_KEY")
    if not key:
        parser.error("TOOLLIGHTHOUSE_PAIRING_KEY must match the running mock server")
    async with httpx.AsyncClient(base_url=base, timeout=40) as client:
        health = (await client.get("/health")).json()
        if health.get("mode") != "mock" or not health.get("simulated"):
            parser.error("this synthetic smoke requires --mode mock; refusing a real pipeline")
        response = await client.post("/v1/sessions", json={"device_id": "software-smoke", "role": "controller"}, headers={"X-Pairing-Key": key})
        response.raise_for_status()
        session = response.json()
        headers = {"X-Session-Id": session["session_id"], "X-Session-Ticket": session["ticket"]}
        async def heartbeat():
            while True:
                await asyncio.sleep(2)
                status = await client.get('/v1/status', headers=headers)
                status.raise_for_status()
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            second = await client.post("/v1/sessions", json={"device_id": "second-smoke", "role": "controller"}, headers={"X-Pairing-Key": key})
            assert second.status_code == 409, "simultaneous controller must be rejected"
            ticket_response = await client.post(f"/v1/sessions/{session['session_id']}/ws-ticket", headers=headers)
            ticket_response.raise_for_status()
            parts = urlsplit(base)
            ws_url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, parts.path + "/v1/ws", urlencode({"session_id": session["session_id"], "ticket": ticket_response.json()["ticket"]}), ""))
            async with connect(ws_url, max_size=4 * 1024 * 1024) as websocket:
                event = json.loads(await asyncio.wait_for(websocket.recv(), 5))
                assert event["type"] == "system"
                background = await client.post("/v1/calibration/background", headers=headers, files={"file": ("empty.png", image_bytes(0), "image/png")})
                background.raise_for_status()
                cases = []
                for count in (0, 1, 2, 3, 5, 9):
                    photo = await client.post("/v1/analyze/photo", headers=headers, files={"file": ("synthetic.png", image_bytes(count), "image/png")})
                    photo.raise_for_status()
                    result = photo.json()
                    assert len(result["detections"]) == count
                    assert result["image"]["width"] == 900 and result["image"]["height"] == 600
                    assert result["arduino"]["simulated"] and result["arduino"]["ack"]
                    assert result["arduino"]["requested_mask"] == result["arduino"]["confirmed_mask"]
                    cases.append({"objects": count, "boxes": len(result["detections"]), "simulated_mask": result["arduino"]["confirmed_mask"]})
                received = set()
                for _ in range(30):
                    if {"detection", "arduino_ack"} <= received:
                        break
                    event = json.loads(await asyncio.wait_for(websocket.recv(), 5))
                    received.add(event["type"])
                assert {"detection", "arduino_ack"} <= received
                print(json.dumps({"simulation": True, "cases": cases, "exclusive_control": True, "websocket": sorted(received)}, indent=2))
        finally:
            try:
                release = await client.delete(f"/v1/sessions/{session['session_id']}", headers=headers)
                release.raise_for_status()
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task


if __name__ == "__main__":
    asyncio.run(main())
