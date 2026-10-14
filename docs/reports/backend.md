# Backend implementation report

Implemented the local ToolLighthouse pipeline used by the API and CLI. The package is split across segmentation, centroid tracking, optional EmbeddingGemma, gallery cache, classification/rejection, hysteresis state, serial transport, metrics, camera worker, overlay and FastAPI modules. The mock mode never imports Torch and returns explicitly simulated results; real mode uses the local model path only and leaves empty galleries as unknown.

The photograph path applies EXIF orientation before converting to OpenCV BGR, resets scene tracks for each photograph, returns pixel bounding boxes plus normalized dimensions, and computes a complete LED mask. Unknown/ambiguous predictions cannot enable LEDs. The API uses a pairing key, controller/reader sessions, a single controller lease with generation fencing, multipart size and image validation, status/inventory routes, WebSocket detection/system/ACK events, background calibration, and a latest-only stream endpoint. The serial implementation validates `MASK:0..511`, requires an exact `ACK`, retries bounded times, sends initial zero after opening, and reports confirmed state separately. The Nano sketch uses D2-D10, rejects malformed lines and applies a 2.5 second watchdog.

Verification from the development machine:

- `rtk proxy .venv\Scripts\python.exe -m pytest -q` → **15 passed**. The installed FastAPI/Starlette stack emits deprecation warnings for `on_event` and the TestClient/httpx adapter; no test failures or unhandled worker exceptions remain.
- `rtk proxy .venv\Scripts\python.exe -m compileall -q src` → passed.
- `rtk proxy .venv\Scripts\python.exe -m toollighthouse doctor` → Python 3.11.9, OpenCV 5.0.0, CUDA detected, local model path detected.
- API tests exercise pairing rejection, exclusive controller sessions, multipart photo JSON/boxes/ACK, and late inference after lease expiry.
- `.tools\arduino\arduino-cli.exe compile --fqbn arduino:avr:nano firmware\nano` → passed (13% flash, 11% SRAM).

Hardware serial, physical camera behavior, model inference latency/accuracy and real inventory calibration remain unvalidated here. The WebRTC path is intentionally only a transport seam; the implemented continuous endpoint is latest-only HTTP and must not be described as WebRTC-ready at runtime. `test-model` and `build-gallery` are explicit real-model commands and were not run during this CPU/API verification to avoid a heavy model load.
