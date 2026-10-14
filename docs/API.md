# ToolLighthouse local API

The phone sends normalized, EXIF-transposed photographs to the laptop. The laptop is the only process that runs OpenCV and the optional EmbeddingGemma model.

## Session and exclusive control

`POST /v1/sessions` accepts `{"device_id":"phone-1","role":"controller"}` and the `X-Pairing-Key` header. A successful response contains `session_id`, `ticket`, and expiration values. Only one controller lease exists at a time; readers may inspect status but cannot send photographs or LED commands.

Send `X-Session-Id` and `X-Session-Ticket` on protected HTTP requests. Obtain a one-use WebSocket ticket with `POST /v1/sessions/{session_id}/ws-ticket`, then connect using `/v1/ws?session_id=...&ticket=...`; the permanent session ticket is rejected by WebSocket. Lease expiration increments a generation fence, releases the Arduino state, and prevents a late inference result from changing LEDs.

## Photograph

`POST /v1/analyze/photo` is `multipart/form-data` with a `file` field (`image/jpeg`, `image/png`, or `image/webp`). EXIF orientation is applied before inference. The response image dimensions and all `[x,y,w,h]` boxes refer to the returned normalized image pixels; the frontend must apply one identical transform to image and SVG overlay.

```json
{
  "frame_id": "photo-uuid",
  "timestamp": 1730000000.0,
  "mode": "mock",
  "simulated": true,
  "image": {"width": 1280, "height": 960, "orientation": "normalized", "preview_data_url": "data:image/jpeg;base64,..."},
  "detections": [{
    "track_id": 1, "bbox": [10, 20, 120, 80], "center": [70, 60],
    "class_id": "tool_1", "name": "Tool 1", "location": "unconfirmed",
    "similarity_top1": 1.0, "similarity_top2": 0.0, "margin": 1.0,
    "validation": "simulated"
  }],
  "arduino": {"requested_mask": 1, "confirmed_mask": 1, "connected": true, "simulated": true, "ack": true},
  "metrics": {"inference_ms_p50": null, "inference_ms_p95": null, "capture_to_ack_ms": null}
}
```

`similarity_top1` and `similarity_top2` are cosine similarities, never calibrated probabilities. Unknown or ambiguous predictions do not turn on a new LED. A photo resets scene tracks and only its own valid observation can confirm a result.

The default photo output retention is 30 seconds. Continuous output expires
after 2 seconds without a fresh committed frame even when the phone keeps the
session lease alive. TTL clearing and ACK loss/recovery emit system and
arduino_ack events. Percentiles stay null until sufficient samples exist;
simulated ACK latency stays null.

For API photographs, `capture_to_ack_ms` uses the backend image-ingress timestamp
through the matching hardware ACK; it does not include physical phone capture
or clock synchronization across devices. For the local camera CLI, the source
timestamp is the successful OpenCV frame read. Phone end-to-end latency remains
unmeasured until a real device test defines that measurement boundary.

`POST /v1/calibration/background` accepts the same multipart image and stores the normalized empty tray as the OpenCV background. It requires the controller session.

## WebSocket and status

`GET /v1/status` returns connection, lease, and Arduino state. `WS /v1/ws?session_id=...&ticket=...` emits JSON events with `type` values `detection`, `system`, and `arduino_ack`. The connection is closed when its session expires. `/v1/inventory` returns the nine example assignments; hardware operation requires replacing and confirming that inventory.

## Continuous mode preparation

`POST /v1/stream/frame` accepts the same image body and acknowledges the latest-only slot. A new frame cancels the previous pending task; stale results are discarded before LED application rather than queued. The current implementation keeps capture/transport separate from inference. WebRTC is an integration seam for a later deployment and is not claimed as operational here.

When `frontend/dist/index.html` exists, FastAPI serves it at `/`; otherwise the API remains backend-only. Start with `TOOLLIGHTHOUSE_PAIRING_KEY=<local-secret> python -m toollighthouse serve --mode mock --simulate-leds --host 0.0.0.0 --port 8000` for a laptop-network simulation. `TOOL_LIGHTHOUSE_PAIRING_KEY` is accepted as an alias. Physical serial mode additionally requires `--serial COMx --inventory config/inventory.json --confirm-inventory`.
