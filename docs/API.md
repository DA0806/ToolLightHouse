# ToolLighthouse local API

The phone sends normalized, EXIF-transposed photographs to the laptop. The laptop is the only process that runs OpenCV and the optional EmbeddingGemma model.

## Session and exclusive control

`POST /v1/sessions` accepts `{"device_id":"phone-1","role":"controller"}` and the `X-Pairing-Key` header.
To pair as an administrator, provide `"role": "admin"` and the `X-Admin-Key` header (matching `TOOLLIGHTHOUSE_ADMIN_KEY`).
A successful response contains `session_id`, `ticket`, `role`, and expiration values. Only one controller lease exists at a time; readers may inspect status but cannot send photographs or LED commands. Administrative clients can mutate configuration, upload visual references, calibrate ROI/background, and access Arduino maintenance.

Send `X-Session-Id` and `X-Session-Ticket` on protected HTTP requests. Obtain a one-use WebSocket ticket with `POST /v1/sessions/{session_id}/ws-ticket`, then connect using `/v1/ws?session_id=...&ticket=...`; the permanent session ticket is rejected by WebSocket. Lease expiration increments a generation fence, releases the Arduino state, and prevents a late inference result from changing LEDs.

## System Readiness and Modes

- `GET /v1/system/readiness` (Authenticated)
  Reports honest system capability, active mode (`mock`, `preparation`, `real`), availability of Torch/CUDA/VRAM, model weights presence, inventory confirmation, gallery validity, background calibration status, and Arduino readiness. When `can_activate_real` is false, `preparation_reasons` details all blocking requirements.

- `POST /v1/system/mode` (Admin)
  Switches active mode (`{"mode": "mock" | "preparation" | "real"}`). Switching to `real` strictly validates readiness and returns `409 Conflict` if any requirements are unmet.

- `POST /v1/system/download-model` (Admin)
  Returns instructions to run the repository downloader on the laptop: `python -m toollighthouse download-model --model-path models/embeddinggemma-2`.

## Configuration Management

Configuration is persisted atomically in a single versioned envelope (`config/app_config.json` with automatic backup at `config/app_config.backup.json`).

- `GET /v1/config` (Authenticated)
  Returns `saved_version`, `active_version`, `is_dirty`, `settings`, and `inventory`.

- `PUT /v1/config` (Admin)
  Saves a new draft configuration atomically. Supports optimistic concurrency via `"expected_version": N` (returns `409 Conflict` on mismatch). Updating `display_name`, `location`, or thresholds marks the draft dirty but does not invalidate visual embeddings.

- `POST /v1/config/apply` (Admin)
  Hot-applies the draft configuration into the active pipeline. If descriptions or gallery mode changed, rebuilds the candidate gallery off the event loop and promotes it atomically. Clears active scenes and issues `MASK:0` during transition.

- `POST /v1/config/rollback` (Admin)
  Restores the previous configuration from backup.

- `GET /v1/config/export` (Admin)
  Exports portable configuration JSON (secrets and local absolute filesystem paths are stripped).

- `POST /v1/config/import` (Admin)
  Validates and imports portable configuration JSON into the draft state.

- `GET /v1/config/status` (Authenticated)
  Returns configuration versions, dirty flag, and ongoing preparation task status:
  ```json
  {
    "saved_version": 2,
    "active_version": 1,
    "is_dirty": true,
    "task": {
      "state": "idle",
      "stage": "completed",
      "progress": 1.0,
      "error": null
    }
  }
  ```

## Visual References

References are stored in `data/references/{class_id}/{uuid}.{ext}`. Categories are validated against path traversal (`..`, slashes) and known inventory identifiers.

- `POST /v1/tools/{class_id}/references` (Admin, multipart)
  Uploads a reference photo (JPEG/PNG/WebP, up to 12 MB, max 20 MP, EXIF-normalized). Returns `image_id` and embedded `thumbnail_data_url`. Marks gallery dirty.

- `GET /v1/tools/{class_id}/references` (Authenticated)
  Lists all reference images for a category with thumbnail data URLs. Readers may
  inspect references; uploads and deletion remain administrator-only.

- `DELETE /v1/tools/{class_id}/references/{image_id}` (Admin)
  Deletes the specified reference photo and marks gallery dirty.

## Photograph

`POST /v1/analyze/photo` is `multipart/form-data` with a `file` field (`image/jpeg`, `image/png`, or `image/webp`). EXIF orientation is applied before inference. The response image dimensions and all `[x,y,w,h]` boxes refer to the returned normalized image pixels; the frontend must apply one identical transform to image and SVG overlay.

In `preparation` mode or during Arduino maintenance, this endpoint returns `409 Conflict`.

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

The default photo output retention is 30 seconds. Continuous output expires after 2 seconds without a fresh committed frame even when the phone keeps the session lease alive. TTL clearing and ACK loss/recovery emit `system` and `arduino_ack` events.

## Calibration

- `POST /v1/calibration/background` (Admin, multipart)
  Uploads and stores the normalized empty-tray background image. Records dimensions and returns calibrated status.

- `PUT /v1/calibration/roi` (Admin)
  Sets Region of Interest `{"roi": [x, y, width, height]}`. Validates bounds against background dimensions.

- `GET /v1/calibration/background.jpg` (Authenticated)
  Serves the raw calibrated background JPEG.

## Arduino Hardware & Maintenance

Manual LED tests run strictly in maintenance mode to prevent conflicts with inference.

- `GET /v1/arduino/ports` (Admin)
  Lists enumerated system COM ports (`[{"port": "COM3", "description": "..."}]`).

- `POST /v1/arduino/connect` (Admin)
  Connects to a serial port (`{"port": "COM3"}`) or activates simulated serial (`{"simulated": true}`).

- `POST /v1/arduino/disconnect` (Admin)
  Closes serial connection; reports `confirmed_mask = None`.

- `POST /v1/arduino/maintenance/enter` (Admin)
  Enters maintenance mode: fences inference with `409 Conflict`, sends `MASK:0`, and clears active scene.

- `POST /v1/arduino/maintenance/leave` (Admin)
  Exits maintenance mode.

- `POST /v1/arduino/maintenance/test-led/{led_index}?duration=1.5` (Admin)
  Lights a single LED ($0 \le \text{led\_index} \le 8$, mask $= 1 \ll \text{led\_index}$) with automatic timer shutoff ($\le 3.0\,\text{s}$). Safely replaces any active timer.

- `POST /v1/arduino/maintenance/all-off` (Admin)
  Immediately cancels active timer and sends `MASK:0`.

## WebSocket and Status

- `GET /v1/status` returns connection, lease, and Arduino state.
- `WS /v1/ws?session_id=...&ticket=...` emits JSON events with `type` values:
  - `detection`: committed frame detection result
  - `arduino_ack`: serial state, requested/confirmed mask, and ACK status
  - `system`:
    - `config_changed` (`saved_version`, `active_version`, `is_dirty`)
    - `config_applied` (`active_version`)
    - `config_rolled_back` (`saved_version`, `active_version`)
    - `gallery_building` (`progress`, `stage`)
    - `gallery_ready` (`active_version`)
    - `gallery_error` (`detail`)
    - `mode_changed` (`mode`)
    - `roi_updated` (`roi`)
    - `task_status` (`task` object with `state`, `stage`, `progress`, `error`)

The connection is closed when its session expires.

## Continuous Mode Preparation

`POST /v1/stream/frame` accepts the same image body and acknowledges the latest-only slot. A new frame cancels the previous pending task; stale results are discarded before LED application rather than queued.

When `frontend/dist/index.html` exists, FastAPI serves it at `/`; otherwise the API remains backend-only. Start with:
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = "reader-secret"
$env:TOOLLIGHTHOUSE_ADMIN_KEY = "admin-secret"
python -m toollighthouse serve --mode mock --simulate-leds --host 0.0.0.0 --port 8000
```
