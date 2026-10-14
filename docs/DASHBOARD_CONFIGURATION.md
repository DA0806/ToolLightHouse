# ToolLighthouse Dashboard Configuration & Backend Architecture Guide

This document describes the backend architecture, API contracts, lifecycle guarantees, and administration model for the ToolLighthouse dashboard.

---

## 1. Architectural Principles

1. **Backend as Source of Truth:**
   The backend stores all canonical configuration inside a single versioned atomic envelope (`config/app_config.json` with automatic backup at `config/app_config.backup.json`). The web frontend never stores canonical inventory, model, or hardware state in `localStorage`.
2. **Stable Class Identifiers with Separate Display Names:**
   Categories use stable, filesystem-safe opaque identifiers (validated via `^[a-zA-Z0-9_-]{1,64}$`, rejecting path separators and traversal tokens). To preserve existing inventories and models, categories are **not** forced into `tool_1..tool_9`. Display renames in the UI modify `display_name` without altering classification `name`, completely avoiding unnecessary and costly embedding rebuilds.
3. **Segregated Administrative Authentication:**
   Administrative operations require explicit server-side authorization via `admin` role and `admin_key` (configured via `TOOLLIGHTHOUSE_ADMIN_KEY` or `--admin-key`). Submitting `role="admin"` alone without the corresponding admin key is strictly rejected (403 Forbidden). Secrets (`pairing_key`, `admin_key`) and local machine filesystem paths are never leaked in status responses or portable exports.
4. **Safe Startup & Preparation Mode:**
   When ToolLighthouse starts in `real` mode without confirmed inventory, background calibration, or validated gallery caches, it does not crash `create_app`. Instead, it safely boots in `preparation` mode, exposes diagnostic endpoints, and reports exact preparation reasons via `GET /v1/system/readiness`. Inference endpoints return `409 Conflict` until readiness requirements are satisfied and real mode is activated.
5. **Partial Hybrid Visual Gallery:**
   The hybrid prototype builder supports arbitrary subsets of tools (from 0 to 9) having reference photos. Categories without photos retain pure normalized text prototypes; categories with photos compute unit-normalized blends:
   $$\mathbf{p}_i = \frac{w \cdot \mathbf{t}_i + (1 - w) \cdot \mathbf{v}_i}{\|w \cdot \mathbf{t}_i + (1 - w) \cdot \mathbf{v}_i\|}$$
6. **Hardware Safety & Maintenance Mode:**
   Physical Arduino testing is isolated to an explicit maintenance mode. Entering maintenance fences active inference (returning `409 Conflict`) and issues `MASK:0`. LED tests are strictly single-bit (`1 << led_index`) with automatic timer shutoff ($T \le 3.0\,\text{s}$). Disconnection reports `confirmed_mask: None`.
7. **Generation Fencing & Scene Invalidation:**
   Hot-applying configuration, changing modes, calibrating ROI, or entering maintenance invalidates in-flight frames via monotonic `scene_generation` increments, draining workers, resetting centroid trackers, and sending `MASK:0` before new frames can be processed.

---

## 2. Authentication & Session Pairing Flow

ToolLighthouse supports three segregated roles:

| Role | Permissions | Required Auth |
| :--- | :--- | :--- |
| `reader` | Inspect system status, view configuration, listen to WebSocket events | `X-Pairing-Key` |
| `controller` | Capture photos (`/v1/analyze/photo`), stream video frames | `X-Pairing-Key` (exclusive lease) |
| `admin` | Full mutation rights: edit config, upload references, calibrate ROI/background, Arduino COM/maintenance, switch modes | Explicit `X-Admin-Key` configured via `TOOLLIGHTHOUSE_ADMIN_KEY` or `--admin-key` |

### Pairing as Admin

```http
POST /v1/sessions HTTP/1.1
Content-Type: application/json
X-Admin-Key: <TOOLLIGHTHOUSE_ADMIN_KEY>

{
  "device_id": "laptop-browser",
  "role": "admin"
}
```

Response:
```json
{
  "session_id": "admin-session-uuid",
  "ticket": "session-ticket-token",
  "role": "admin",
  "expires_at": 1730003600.0,
  "lease_expires_at": 1730003600.0,
  "ws_ticket_url": "/v1/sessions/admin-session-uuid/ws-ticket"
}
```

Pass the session credentials on all subsequent requests:
```http
X-Session-Id: admin-session-uuid
X-Session-Ticket: session-ticket-token
```

---

## 3. Configuration Management & Hot-Apply

### Envelope Structure (`config/app_config.json`)

```json
{
  "schema_version": 1,
  "config_version": 2,
  "updated_at": 1730000000.0,
  "settings": {
    "mode": "mock",
    "gallery_mode": "hybrid",
    "text_weight": 0.5,
    "classifier_threshold": 0.72,
    "classifier_margin": 0.08,
    "classifier_profiles": {
      "text-only": {"threshold": 0.65, "margin": 0.05},
      "image-only": {"threshold": 0.75, "margin": 0.10},
      "hybrid": {"threshold": 0.72, "margin": 0.08}
    },
    "roi": [20, 20, 600, 440]
  },
  "inventory": {
    "categories": [
      {
        "class_id": "tool_1",
        "name": "phillips_screwdriver",
        "display_name": "Phillips #2 Screwdriver",
        "location": "A1",
        "led_index": 0,
        "pin": 2,
        "descriptions": ["cross-head screwdriver", "magnetic tip"],
        "reference_count": 2
      }
    ]
  }
}
```

### Endpoints

- `GET /v1/config` — Retrieve draft and active configuration versions, dirty flag, settings, and inventory.
- `PUT /v1/config` — Atomically save new draft configuration. Accepts `expected_version` for optimistic concurrency locking (returns `409 Conflict` on version mismatch).
- `POST /v1/config/apply` — Hot-apply configuration. If descriptions or mode changed, builds candidate gallery off event loop and atomically promotes it; cheap changes (display name, thresholds, ROI) update parameters instantly without rebuilding embeddings.
- `POST /v1/config/rollback` — Restores previous known-good backup (`config/app_config.backup.json`).
- `GET /v1/config/status` — Retrieves saved and active configuration versions, `is_dirty` status, and current background preparation task state (`stage`, `progress`, `error`).
- `GET /v1/config/export` — Downloads portable JSON configuration (secrets and local absolute filesystem paths stripped).
- `POST /v1/config/import` — Imports portable JSON configuration into draft state with schema validation.

### Draft, active snapshot, and rollback guarantees

The envelope may also contain `active_settings`, `active_inventory`, and
`active_version`. These fields identify the last runtime configuration that was
successfully applied; a saved draft can therefore remain dirty across a
restart without being reported as active. On the first draft save, the backup
is initialized from that runtime baseline so rollback restores the real
pre-edit state. Subsequent saves replace the backup only with a validated
primary envelope.

Both primary and backup files are written through temporary files and atomic
replacement. Version counters and the in-memory active snapshot are committed
only after the corresponding replacement succeeds. A failed replacement
cleans its temporary file and leaves the previous primary/backup pair and
active runtime unchanged.

---

## 4. Visual References & Containment

Tool reference images are stored in `data/references/{class_id}/{uuid}.{ext}`.

- `POST /v1/tools/{class_id}/references` — Upload reference photo (`image/jpeg`, `image/png`, `image/webp`). Max 12 MB, max 20 MP. Validates `class_id` against directory containment and inventory. Returns `image_id` and an embedded authenticated `thumbnail_data_url`.
- `GET /v1/tools/{class_id}/references` — List all stored reference images and thumbnail data URLs for a category.
- `DELETE /v1/tools/{class_id}/references/{image_id}` — Deletes reference photo and marks gallery configuration dirty.

---

## 5. Background Calibration & ROI

- `POST /v1/calibration/background` — Sets reference empty-tray background image. Records pixel dimensions (`width`, `height`). Requires admin role.
- `PUT /v1/calibration/roi` — Calibrates Region of Interest `[x, y, width, height]`. Strictly validates bounds against current background dimensions and finite integers ($x, y \ge 0, w, h > 0, x+w \le W, y+h \le H$).

---

## 6. Arduino Hardware & Maintenance Mode

- `GET /v1/arduino/ports` — Lists enumerated system COM ports with descriptions.
- `POST /v1/arduino/connect` — Connects to specified port or simulated transport.
- `POST /v1/arduino/disconnect` — Disconnects and marks `confirmed_mask = None`.
- `POST /v1/arduino/maintenance/enter` — Enters maintenance mode: fences inference, turns off LEDs (`MASK:0`), and invalidates active scenes.
- `POST /v1/arduino/maintenance/leave` — Exits maintenance mode.
- `POST /v1/arduino/maintenance/test-led/{led_index}?duration=1.5` — Lights single LED ($0 \le \text{led\_index} \le 8$, mask $= 1 \ll \text{led\_index}$) with automatic timer shutoff ($\le 3.0\,\text{s}$). Replaces any active test timer safely.
- `POST /v1/arduino/maintenance/all-off` — Cancels active test timer and immediately sends `MASK:0`.

---

## 7. Real-Time WebSocket Events (`/v1/ws`)

| Event Type | Event Name | Payload Fields | Description |
| :--- | :--- | :--- | :--- |
| `system` | `config_changed` | `saved_version`, `active_version`, `is_dirty` | Draft configuration updated |
| `system` | `config_applied` | `active_version` | Configuration hot-swapped into pipeline |
| `system` | `config_rolled_back` | `saved_version`, `active_version` | Backup restored |
| `system` | `gallery_building` | `progress`, `stage` | Progress of background embedding rebuild |
| `system` | `gallery_ready` | `active_version` | Embedding rebuild complete and promoted |
| `system` | `gallery_error` | `detail` | Rebuild failed (previous gallery preserved) |
| `system` | `mode_changed` | `mode` | Active mode changed (`mock`, `preparation`, `real`) |
| `system` | `roi_updated` | `roi` | ROI bounds updated |
| `system` | `task_status` | `task` | Background preparation/apply task progress (`state`, `stage`, `progress`, `error`) |
| `arduino_ack` | - | `arduino` | Serial state, confirmed mask, and ACK status |

---

## 8. Mobile Dashboard User Guide & Operator Walkthrough

### 8.1 Network, Secure Context & Access Prerequisites

- **Local Network Isolation:** ToolLighthouse is designed to run exclusively on a private local network (LAN) or localhost. **Do not expose backend or dashboard ports to the public internet.**
- **Phone Camera Access (Secure Context Requirement):** Modern mobile browsers (iOS Safari, Android Chrome) strictly require a **Secure Context (`HTTPS` or `http://localhost`)** to access phone cameras (`getUserMedia`). When opening the dashboard from a phone on your local Wi-Fi:
  - Connect your phone and laptop to the same local Wi-Fi router.
  - Run the server with HTTPS, or configure your mobile browser to treat your local IP (`http://192.168.x.x:8000`) as a trusted origin (e.g. in Android Chrome via `chrome://flags/#unsafely-treat-insecure-origin-as-secure`).
  - Alternatively, you can use file photo upload on standard HTTP without camera permissions.
- **Administrative Authentication:** Administrative mutations (editing inventory, uploading references, calibrating background/ROI, switching system mode, and Arduino diagnostics) require the administrator key:
  - Configured on the laptop backend via environment variable `TOOLLIGHTHOUSE_ADMIN_KEY` or `--admin-key`.
  - Enter the key in the dashboard connection panel under role **Administrador**.
  - Secrets and session tickets are held in memory only and never written to `localStorage`.

---

### 8.2 Five Sections Overview

#### 1. Inspección
- **Captura y Análisis:** Choose photos or stream live video from a phone mounted overhead.
- **Detecciones Normalizadas:** Bounding boxes are rendered with SVG coordinate normalization, ensuring accurate placement regardless of screen aspect ratio or phone orientation.
- **Salida Física y Confirmación ACK:** Real-time feedback showing requested LED mask vs confirmed ACK from Arduino. Missing ACK is honestly displayed without invented states.
- **Métricas Reales:** Displays genuine p50/p95 latency and sample counts without fake confidence numbers.

#### 2. Herramientas
- **9 Tarjetas Editables:** Inspect and modify the nine physical tool categories. Stable internal identifiers (`class_id`) remain read-only.
- **Nombre Visible vs Nombre para IA:** Modifying the visible display name updates the user interface immediately. Initial generic names (`tool_1`..`tool_9`) are automatically sanitized into semantic classification names upon first inventory confirmation.
- **Guía de Rasgos Visuales (Zero-Shot):** Structured guide prompts operators to describe shape, materials, colors, distinctive parts, and distinguishing traits relative to the other 8 tools.
- **Intercambio Atómico de LEDs:** Changing an LED assignment from tool $A$ to tool $B$ automatically swaps the assignments between both tools atomically, ensuring that LED indices 0..8 and Arduino pins D2..D10 never have duplicate or missing pins.
- **Cuadrícula Física 3×3:** An interactive 3×3 grid map mirroring physical tray layout lets operators tap any cell to focus and edit the corresponding tool.
- **Confirmación del Inventario:** Setting "Confirmar inventario real" toggles `inventory_confirmed=true` and marks `example=false`.
- **Fotografías de Referencia:** Collapsible photo manager for each tool with sequential upload, thumbnail preview, and deletion with confirmation.

#### 3. Calibración
- **Modos de Reconocimiento:** Switch between **Solo Texto (Predeterminado)**, **Mejorar con fotografías (Híbrido)**, and **Solo Imágenes**.
- **Ponderación Híbrida:** When in hybrid mode, adjust the text vs photo weight slider between 10% and 90%. (Note: Hybrid prototypes should be evaluated under actual ambient lighting).
- **Calibración de Bandeja Vacía:** Upload or capture an empty tray photograph. Background JPEG is retrieved securely as an authenticated `Blob` and rendered without token leakage.
- **Región de Interés (ROI):** Interactive SVG canvas with `touch-action: none` to prevent mobile scroll gestures while drawing, plus precise numeric coordinate inputs ($X, Y, \text{Ancho}, \text{Alto}$) enforcing minimum size bounds.

#### 4. Arduino
- **Conexión Serial USB:** Enumerate host COM ports or toggle simulated Arduino mode for development without physical hardware.
- **Modo de Mantenimiento Seguro:** Suspends camera inference and resets LEDs (`MASK:0`) before allowing manual diagnostics.
- **Pruebas Unitarias de LEDs:** Nine individual buttons corresponding to pins D2 through D10. Each test turns on strictly one bit with an automatic countdown timer ($\le 3.0\,\text{s}$) and automatic shutoff. No simultaneous all-9 test is permitted.
- **Apagar Todo (MASK:0):** Emergency command sends `MASK:0` and displays the transport ACK/state; a disconnected transport remains explicitly unconfirmed.
- **Guía de Firmware:** Complete step-by-step instructions for flashing `firmware/nano/nano.ino` via Arduino IDE, including ATmega328P Old Bootloader notes and 115200 baud configuration.

#### 5. Sistema
- **Asistente de Configuración Inicial (7 Puntos):** Live checklist verifying Backend, Python/PyTorch/CUDA/GPU environment, EmbeddingGemma 2 weights, 9 Tools inventory confirmation, Embedding Gallery cache, Tray background & ROI calibration, and Arduino connection.
- **Acción Única "Preparar IA":** One-click button transitions the system from mock mode to preparation mode, builds gallery embeddings, and hot-applies configuration without requiring manual command-line scripts.
- **Instrucciones de Descarga del Modelo:** If model weights are missing, displays the repository CLI command (`python -m toollighthouse download-model --model-path models/embeddinggemma-2`) to execute on the laptop host.
- **Selector de Modo de Ejecución:** Switch between `mock`, `preparation`, and `real` (with comprehensive blocker explanations if prerequisites are unmet).
- **Control de Versiones y Reversión (Rollback):** View saved vs active versions, track background compilation tasks, and restore known-good backups.
- **Exportación e Importación Portable (JSON):** Export portable configuration without secrets or absolute paths, and import with preview verification.

---

### 8.3 Post-Installation Operational Routine

After initial physical setup and calibration:
1. Turn on laptop, connect Arduino Nano via USB, and start ToolLighthouse backend.
2. Open phone browser to dashboard, connect as Admin or Controller.
3. Place phone overhead in fixed mount above tray.
4. Tray setup is persistent: no repeated calibration commands or CLI scripts are needed during daily tool return and inspection operations.

