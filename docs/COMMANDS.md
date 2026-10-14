# ToolLighthouse commands

Run every command from the repository root with `.venv` activated (or replace
the executable with the Python interpreter used for the installation).

The CLI exposes the complete local workflow:

1. `doctor` — report Python, architecture, AI/OpenCV/FastAPI/serial versions,
   model/gallery/calibration presence, CUDA/GPU and serial inventory. Add
   `--check-cameras` for a bounded scan of camera indices 0 through 4.
2. `test-camera` — open one camera, video file or URL and report its first
   frame. Use `--scan` to enumerate camera indices 0 through 4 without opening
   the configured source.
3. `calibrate` — capture a background and optionally select an ROI, writing
   the configured calibration files.
4. `download-model` — download `google/embeddinggemma-2` explicitly with
   `huggingface_hub`; install the optional AI dependencies first.
5. `build-gallery` — build the default `text-only` gallery from the nine inventory
   names/descriptions, without reference images. `image-only` and `hybrid` are
   optional modes requiring photographs for all nine classes.
6. `test-model` — run finite image and text embedding smoke checks.
7. `test-leds` — exercise `MASK:0`, `MASK:511`, and `MASK:0` with simulator or
   confirmed physical inventory. It always sends a final zero mask and closes.
8. `run` — process a camera, file or URL through the latest-only capture and
   inference workers. `--max-frames N` is bounded; `0` means until stopped.
   JSON reports processed `frames` separately from `captured_frames`.
9. `serve` — start FastAPI and, when `frontend/dist` exists, serve its static
   dashboard from `/` as well. Set `TOOLLIGHTHOUSE_PAIRING_KEY` for LAN auth.
10. `capture-reference` — save a unique crop for a known inventory class. In
    headless mode use `--crop x,y,width,height`, or provide a calibrated scene
    containing exactly one detected tool.
11. `evaluate` — evaluate grouped known/unknown images against a built gallery
    and emit accuracy, unknown rejection, false acceptance and confusion data.

Examples:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai]"
.\.venv\Scripts\python.exe -m toollighthouse doctor --check-cameras
.\.venv\Scripts\python.exe -m toollighthouse test-camera --scan
.\.venv\Scripts\python.exe -m toollighthouse calibrate --camera 0 --headless --max-frames 1
.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --inventory config/inventory.json --output data/gallery.json --mode real --device cuda
.\.venv\Scripts\python.exe -m toollighthouse test-model --device cuda --model-path models/embeddinggemma-2
.\.venv\Scripts\python.exe -m toollighthouse test-leds --simulate-leds
.\.venv\Scripts\python.exe -m toollighthouse run --mode mock --simulate-leds --camera 0 --headless --max-frames 100
$env:TOOLLIGHTHOUSE_PAIRING_KEY = Read-Host 'Clave local para emparejar el teléfono'
$env:TOOLLIGHTHOUSE_ADMIN_KEY = Read-Host 'Clave de administración para el dashboard'
.\.venv\Scripts\python.exe -m toollighthouse serve --mode mock --simulate-leds --host 0.0.0.0 --port 8000
.\.venv\Scripts\python.exe -m toollighthouse capture-reference --class-id tool_1 --output data/references --headless --crop 20,20,160,160
.\.venv\Scripts\python.exe -m toollighthouse evaluate --mode real --gallery data/gallery.json --references data/evaluation
```

Physical serial mode requires `--mode real`, a confirmed non-example
inventory, `--inventory path/to/inventory.json`, `--confirm-inventory`, and an
explicit `--serial COMx`. `--simulate-leds` cannot be combined with a physical
serial port. For a public deployment, put TLS in a reverse proxy or run
Uvicorn with an application factory and certificate options; the local CLI
does not silently create HTTPS certificates.

The default `--gallery-mode text-only` uses each inventory name and its independent
`descriptions` strings. It averages normalized vectors by class and normalizes
the resulting prototype. Names work as fallback when descriptions are empty,
but the example inventory remains a template and is not an evaluated set of tools.
No model training or reference-photo folders are required in this mode. Captured
image crops are still embedded through the vision encoder and compared by cosine.

The gallery fingerprint includes inventory IDs/names/descriptions, mode, ensemble
recipe, model contents and preprocessing; reference images are included only for
image/hybrid modes. Rebuild after changing these inputs. Old caches are rejected.
Changing only classification thresholds/margins does not require a rebuild.

Optional photographic galleries (same mode must be selected when running):

```powershell
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --mode real --device cuda --gallery-mode image-only --inventory config/inventory.json --references data/references --output data/gallery-image.json
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --mode real --device cuda --gallery-mode hybrid --text-weight 0.5 --inventory config/inventory.json --references data/references --output data/gallery-hybrid.json
```

Image folders in image-only mode must cover all nine configured `class_id` values.
In hybrid mode, ToolLighthouse supports partial photo coverage: any subset (0 to 9) of
categories may have reference photographs. Categories with photos combine normalized image
and text prototypes with `text_weight` strictly between 0 and 1 (default 0.5); categories
without photos automatically retain their zero-shot normalized text prototypes.

`classifier_profiles` maps `text-only`, `image-only`, `hybrid` to independently
configurable `threshold`/`margin` values. CLI `--classifier-threshold` and
`--classifier-margin` override the active mode's limits; global fields remain
fallbacks. Example values .72/.08 are uncalibrated. See `DATA.md` for known/unknown
calibration, held-out evaluation and full-scene segmentation checks. Cosine scores
are not percentages of confidence. Example calibration command:

```powershell
.\.venv\Scripts\python.exe -m toollighthouse evaluate --config config/settings.json --mode real --device cuda --gallery-mode text-only --inventory config/inventory.json --gallery data/gallery.json --references data/calibration --classifier-threshold 0.55 --classifier-margin 0.04 --output data/calibration-report.json
```

For real zero-shot vision with simulated lights, after completing the inventory,
building its textual gallery and saving a calibrated empty-tray background:

```powershell
.\.venv\Scripts\python.exe -m toollighthouse serve --mode real --device cuda --inventory config/inventory.json --gallery data/gallery.json --simulate-leds --host 0.0.0.0 --port 8000
.\.venv\Scripts\python.exe -m toollighthouse run --mode real --device cuda --inventory config/inventory.json --gallery data/gallery.json --video demo.mp4 --simulate-leds
.\.venv\Scripts\python.exe -m toollighthouse run --mode real --device cuda --inventory config/inventory.json --gallery data/gallery.json --camera 'http://192.168.1.30:8080/video' --simulate-leds
```

Copy the example inventory to a separate file, replace the nine provisional
names and locations, keep `pin = led_index + 2`, and mark `example: false` only
after the assignments are confirmed. With the correct physical COM selected:

```powershell
.\.venv\Scripts\python.exe -m toollighthouse test-leds --mode real --serial COM5 --inventory config/inventory.json --confirm-inventory
.\.venv\Scripts\python.exe -m toollighthouse serve --mode real --device cuda --gallery data/gallery.json --serial COM5 --inventory config/inventory.json --confirm-inventory
```

For a software-only HTTP/WebSocket smoke, stop/release the dashboard controller,
keep the same pairing-key environment in the smoke terminal, then run:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_photo.py --url http://127.0.0.1:8000
```

This last script uses synthetic images and requires a mock server with simulated
Arduino. It proves transport, boxes and software ACK, not physical recognition.
