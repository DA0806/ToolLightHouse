# Referencia del Contrato de API de ToolLightHouse (v1)

Este documento especifica los endpoints, esquemas de solicitud, encabezados de autenticación, flujos de WebSocket y códigos de estado HTTP implementados en el backend FastAPI de ToolLightHouse.

---

## 1. Encabezados de Autenticación y Autorización

Todas las rutas protegidas requieren credenciales explícitas en encabezados HTTP. El backend no utiliza tokens en la cadena de consulta (*query string*) para llamadas REST o WebSockets de video:

| Encabezado | Descripción | Requerido en |
|---|---|---|
| `X-Pairing-Key` | Clave compartida para crear sesiones de operador (`controller`) o lector (`reader`) | `POST /v1/sessions` |
| `X-Admin-Key` | Clave maestra administrativa coincidente con `TOOLLIGHTHOUSE_ADMIN_KEY` | `POST /v1/sessions` (rol `admin`) |
| `X-Session-Id` | UUID único de la sesión activa | Todos los endpoints protegidos |
| `X-Session-Ticket` | Ticket criptográfico emitido al crear la sesión | Todos los endpoints protegidos |
| `X-Transmitter-Id` | Identificador único emitido a un teléfono transmisor tras consumir una invitación | `POST /v1/camera/frame-upload` |
| `X-Transmitter-Token` | Credencial secreta de un solo uso vinculada a la transmisión de cámara | `POST /v1/camera/frame-upload` |
| `X-Capture-Sequence` | Entero monotónico incremental generado por el teléfono para ordenar frames | `POST /v1/camera/frame-upload` |
| `X-Source-Id` | Identificador de la fuente canónica (`remote_phone`) | `POST /v1/camera/frame-upload` |
| `X-Source-Version` | Versión vigente de la fuente activa | `POST /v1/camera/frame-upload` |

---

## 2. Sesiones y Concesión de Control Físico (Unified Control Lease)

### `POST /v1/sessions`
Crea una nueva sesión en el servidor.
- **Acceso:** Público (requiere clave correspondiente en encabezado).
- **Encabezados:** `X-Pairing-Key` (roles `controller`, `reader`) o `X-Admin-Key` (rol `admin`).
- **Cuerpo JSON:**
  ```json
  {
    "device_id": "tablet-samsung-1",
    "role": "controller"
  }
  ```
  *Valores válidos de rol:* `"admin"`, `"controller"`, `"reader"`. El rol `"camera_transmitter"` no puede crearse públicamente por esta vía.
- **Respuesta exitosa (`200 OK`):**
  ```json
  {
    "session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "ticket": "dGhpcy1pcy1hLXNlY3JldC10b2tlbg",
    "role": "controller",
    "expires_at": 1729003600.0,
    "lease_expires_at": 1729000030.0
  }
  ```
- **Errores:**
  - `401 Unauthorized`: Clave de emparejamiento o clave de administrador inválida o ausente.
  - `409 Conflict`: Ya existe otra sesión con lease de controlador activo.

### `POST /v1/sessions/{session_id}/ws-ticket`
Genera un ticket efímero de un solo uso para autenticar la conexión al WebSocket de eventos del sistema (`/v1/ws`).
- **Encabezados:** `X-Session-Ticket`.
- **Respuesta (`200 OK`):**
  ```json
  {
    "ticket": "efimero_ws_ticket_abc123"
  }
  ```

### `DELETE /v1/sessions/{session_id}`
Cierra la sesión y libera inmediatamente cualquier lease de control físico activo.
- **Encabezados:** `X-Session-Ticket`.
- **Respuesta (`200 OK`):** `{"released": true}`.

### `POST /v1/sessions/lease/takeover`
Expropiación administrativa forzada del control físico.
- **Acceso:** Restringido a rol `admin`.
- **Encabezados:** `X-Session-Id`, `X-Session-Ticket`.
- **Comportamiento:** Desasigna de inmediato al operador actual, cancela invitaciones QR pendientes, revoca transmisores de cámara activos, apaga los LEDs de Arduino (`MASK:0`) y otorga el lease exclusivo a la sesión de administrador.
- **Respuesta (`200 OK`):**
  ```json
  {
    "success": true,
    "lease_generation": 2,
    "holder": "admin_session_id"
  }
  ```

### `POST /v1/sessions/heartbeat`
Renueva el lease de control físico y la sesión activa.
- **Encabezados:** `X-Session-Id`, `X-Session-Ticket`.
- **Respuesta (`200 OK`):**
  ```json
  {
    "alive": true,
    "lease_held": true,
    "lease_generation": 2,
    "expires_in": 30.0
  }
  ```

---

## 3. Fuentes de Cámara y Señalización WebRTC

### `GET /v1/camera/sources`
Lista las 5 fuentes de cámara canónicas y el estado de la fuente activa.
- **Acceso:** Autenticado (`reader`, `controller`, `admin`).
- **Respuesta (`200 OK`):**
  ```json
  {
    "sources": [
      {
        "source_id": "remote_phone",
        "display_name": "Teléfono Remoto (WebRTC)",
        "kind": "webrtc_remote",
        "connection_type": "webrtc",
        "status": "standby",
        "current_resolution": {"width": 1920, "height": 1080},
        "current_fps": 30.0,
        "is_active": true,
        "calibration_profile_available": true,
        "last_error": null
      },
      {
        "source_id": "laptop_builtin",
        "display_name": "Cámara Integrada de Laptop",
        "kind": "opencv_builtin",
        "connection_type": "builtin",
        "status": "standby",
        "current_resolution": {"width": 1280, "height": 720},
        "current_fps": 30.0,
        "is_active": false,
        "calibration_profile_available": false,
        "last_error": null
      },
      {
        "source_id": "usb_webcam",
        "display_name": "Webcam USB Externa",
        "kind": "opencv_usb",
        "connection_type": "usb",
        "status": "standby",
        "current_resolution": {"width": 1280, "height": 720},
        "current_fps": 30.0,
        "is_active": false,
        "calibration_profile_available": false,
        "last_error": null
      },
      {
        "source_id": "operator_device",
        "display_name": "Cámara de este Dispositivo",
        "kind": "browser_local",
        "connection_type": "browser",
        "status": "standby",
        "current_resolution": {"width": 1280, "height": 720},
        "current_fps": 30.0,
        "is_active": false,
        "calibration_profile_available": false,
        "last_error": null
      },
      {
        "source_id": "manual_upload",
        "display_name": "Fotografía Manual",
        "kind": "manual_upload",
        "connection_type": "file",
        "status": "active",
        "current_resolution": {"width": 0, "height": 0},
        "current_fps": 0.0,
        "is_active": false,
        "calibration_profile_available": true,
        "last_error": null
      }
    ],
    "active_source_id": "remote_phone",
    "source_version": 1
  }
  ```

### `POST /v1/camera/sources/active`
Conmuta atómicamente la cámara activa del sistema.
- **Acceso:** `controller` o `admin`.
- **Cuerpo JSON:** `{"source_id": "usb_webcam"}`.
- **Acción:** Detiene la inferencia continua, incrementa `scene_generation`, envía `MASK:0` a Arduino, libera los recursos de la cámara previa y activa el perfil de calibración de la nueva fuente.
- **Respuesta (`200 OK`):**
  ```json
  {
    "success": true,
    "active_source_id": "usb_webcam",
    "source_version": 2,
    "calibration_status": "ready"
  }
  ```

### `GET /v1/camera/webcams`
Enumera físicamente los dispositivos DirectShow (Windows) o V4L2 (Linux) disponibles en la laptop.
- **Respuesta (`200 OK`):**
  ```json
  {
    "devices": [
      {
        "index": 0,
        "name": "Integrated Camera (04f2:b6d9)",
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "is_active": false
      },
      {
        "index": 1,
        "name": "USB 2.0 FHD Webcam",
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "is_active": true
      }
    ]
  }
  ```

### `POST /v1/camera/invite`
Genera una invitación QR criptográfica de un solo uso para vincular el teléfono.
- **Acceso:** `controller` o `admin`.
- **Cuerpo JSON:** `{"expires_in_seconds": 180}` (por defecto 180s, rango permitido 30 a 600s).
- **Respuesta (`200 OK`):**
  ```json
  {
    "invite_id": "inv_9b8a7c6d5e4f",
    "invite_token": "token_criptografico_32_bytes",
    "invite_url": "https://192.168.1.50:8000/camera#token=token_criptografico_32_bytes",
    "expires_at": 1729000180.0
  }
  ```

### `DELETE /v1/camera/invite/{invite_id}`
Cancela una invitación antes de que sea consumida.
- **Respuesta (`200 OK`):** `{"cancelled": true}`.

### `POST /v1/camera/revoke`
Revoca inmediatamente todos los transmisores de cámara activos y limpia la escena.
- **Respuesta (`200 OK`):**
  ```json
  {
    "revoked": true,
    "disconnected_transmitters": 1
  }
  ```

### `WS /v1/camera/signal`
Centro de señalización WebRTC local.
- **URL Limpia:** `wss://<IP-LAN>:8000/v1/camera/signal` (sin tokens en URL).
- **Autenticación en Primer Mensaje (Plazo de 5.0 segundos):**
  - **Operador (Tablet):**
    ```json
    {"type": "auth_operator", "session_id": "...", "ticket": "..."}
    ```
  - **Transmisor Inicial (Teléfono con QR):**
    ```json
    {"type": "auth_transmitter", "invite_token": "..."}
    ```
  - **Reconexión de Transmisor:**
    ```json
    {"type": "reconnect_transmitter", "transmitter_id": "...", "transmitter_ticket": "..."}
    ```
- **Mensajes de Señalización Retransmitidos:**
  - `offer`: `{"type": "offer", "sdp": "..."}`
  - `answer`: `{"type": "answer", "sdp": "..."}`
  - `candidate`: `{"type": "candidate", "candidate": "..."}`
  - `disconnect`: `{"type": "disconnect"}`
  - `ice_failed`: `{"type": "ice_failed", "reason": "..."}`
  - `capabilities`: `{"type": "capabilities", "capabilities": {...}}`

### `POST /v1/camera/frame-upload`
Carga autenticada de fotogramas JPEG en Modo Alternativo de Contingencia (Fallback HTTP).
- **Encabezados:** `X-Transmitter-Id`, `X-Transmitter-Token`, `X-Capture-Sequence`, `X-Source-Id`, `X-Source-Version`.
- **Cuerpo multipart:** `file: capture.jpg`.
- **Límites:** Máximo 350 KB, dimensiones $\le 1280 \times 720$, tasa limitada estrictamente a **2 FPS**.
- **Respuesta (`200 OK`):**
  ```json
  {
    "accepted": true,
    "server_monotonic": 12345.67
  }
  ```

### `WS /v1/camera/preview-ws`
Streaming de vista previa binaria JPEG para webcams locales de la laptop servidora.
- **URL Limpia:** `wss://<IP-LAN>:8000/v1/camera/preview-ws`.
- **Autenticación en Primer Mensaje JSON:**
  ```json
  {"type": "auth", "session_id": "...", "ticket": "..."}
  ```
- **Flujo Posterior:** El servidor emite frames binarios JPEG puros (`bytes`) con límites configurables (por defecto **640×360 a 15 FPS**).

### `POST /v1/analyze/scan`
Dispara la inferencia de reconocimiento inmediato sobre el último fotograma congelado.
- **Acceso:** `controller` o `admin`.
- **Comportamiento:**
  - Para `laptop_builtin` y `usb_webcam`: Toma directamente el fotograma en RAM de `OpenCVCameraService` en la laptop. No requiere carga de archivo por red.
  - Para fuentes remotas o manuales: Acepta opcionalmente un campo multipart `file`.
- **Respuesta:** Objeto `AnalysisResponse` idéntico a `/v1/analyze/photo`.

---

## 4. Perfiles de Calibración Espacial

### `GET /v1/calibration/profiles`
Retorna todos los perfiles de calibración configurados por fuente.
- **Respuesta (`200 OK`):**
  ```json
  {
    "profiles": {
      "remote_phone": {
        "source_id": "remote_phone",
        "resolution": {"width": 1920, "height": 1080},
        "orientation": "normal",
        "crop": null,
        "processing_version": 2,
        "background_filename": "bg_remote_phone_1920x1080.jpg",
        "roi": [0.08, 0.12, 0.84, 0.78],
        "calibrated_at": 1729000000.0,
        "status": "ready"
      }
    }
  }
  ```

### `POST /v1/calibration/profiles/{source_id}/capture`
Captura y almacena el fondo de bandeja vacía para la fuente especificada.
- **Acceso:** `admin`.
- **Cuerpo multipart:** `file: bg.jpg` (opcional si la fuente es local en la laptop).
- **Respuesta (`200 OK`):** Objeto `CalibrationProfile` actualizado.

### `PUT /v1/calibration/profiles/{source_id}/roi`
Actualiza el recuadro de Región de Interés (ROI) en coordenadas normalizadas $[0.0, 1.0]$.
- **Acceso:** `admin`.
- **Cuerpo JSON:**
  ```json
  {
    "roi": [0.08, 0.12, 0.84, 0.78]
  }
  ```

### `POST /v1/calibration/profiles/{source_id}/confirm-legacy`
Confirma la asignación de un fondo legacy no vinculado a un dispositivo físico determinado.

---

## 5. Inferencia de Reconocimiento y Detección

### `POST /v1/analyze/photo`
Analiza una fotografía estática enviada como archivo multipart.
- **Cuerpo multipart:** `file: image.jpg` (JPEG, PNG o WebP, hasta 12 MB y 20 MP).
- **Respuesta exitosa (`200 OK`):**
  ```json
  {
    "frame_id": "photo-uuid-1234",
    "timestamp": 1729000500.0,
    "mode": "real",
    "simulated": false,
    "image": {
      "width": 1920,
      "height": 1080,
      "orientation": "normalized",
      "preview_data_url": "data:image/jpeg;base64,..."
    },
    "detections": [
      {
        "track_id": 1,
        "bbox": [210, 180, 150, 95],
        "center": [285, 227],
        "class_id": "tool_1",
        "name": "Destornillador Phillips",
        "location": "A1",
        "similarity_top1": 0.842,
        "similarity_top2": 0.310,
        "margin": 0.532,
        "validation": "confirmed"
      }
    ],
    "arduino": {
      "requested_mask": 1,
      "confirmed_mask": 1,
      "connected": true,
      "simulated": false,
      "ack": true
    },
    "metrics": {
      "inference_ms_p50": 42.5,
      "inference_ms_p95": 58.2,
      "capture_to_ack_ms": 78.4
    }
  }
  ```

### `POST /v1/stream/frame`
Envío de fotogramas continuos para streaming con política *latest-only*.
- **Comportamiento:** Si la GPU se encuentra ocupada, el fotograma reemplaza el slot pendiente anterior sin crear colas acumulativas.

---

## 6. Control y Mantenimiento de Arduino Nano

Manual LED tests run strictly in maintenance mode to prevent conflicts with inference.

### `GET /v1/arduino/ports`
Lista los puertos serie COM disponibles en la laptop servidora.
- **Respuesta (`200 OK`):** `[{"port": "COM3", "description": "USB Serial CH340"}]`.

### `POST /v1/arduino/connect`
Conecta la comunicación serie al puerto indicado o activa el simulador.
- **Cuerpo JSON:** `{"port": "COM3"}` o `{"simulated": true}`.

### `POST /v1/arduino/disconnect`
Cierra la conexión serial.

### `POST /v1/arduino/maintenance/enter`
Ingresa a modo de mantenimiento (cancela inferencias automáticas, emite `MASK:0` y cerca la escena).

### `POST /v1/arduino/maintenance/leave`
Sale del modo de mantenimiento.

### `POST /v1/arduino/maintenance/test-led/{idx}?duration=1.5`
Enciende un único LED de prueba ($0 \le idx \le 8$, correspondiente a pines D2..D10) con temporizador de apagado automático ($\le 3.0$ s).

### `POST /v1/arduino/maintenance/all-off`
Apagado de emergencia inmediato de todos los LEDs (`MASK:0`).

---

## 7. Configuración Persistente e Inventario

- `GET /v1/inventory`: Obtiene las 9 herramientas, descripciones, ubicaciones y pines D2–D10.
- `GET /v1/config`: Obtiene la configuración completa draft y activa.
- `PUT /v1/config`: Actualiza el borrador draft con concurrencia optimista (`expected_version`).
- `POST /v1/config/apply`: Aplica el borrador draft en caliente (hot-apply) reconstruyendo la galería en segundo plano.
- `POST /v1/config/rollback`: Restaura la versión anterior desde la copia de seguridad.
- `GET /v1/config/export`: Exporta JSON portable sin secretos ni rutas absolutas.
- `POST /v1/config/import`: Importa un JSON de configuración al borrador draft.

---

## 8. WebSocket de Eventos del Sistema (`WS /v1/ws`)

> [!NOTE] **Separación de Esquemas de Autenticación de WebSockets**
> El WebSocket general de eventos del sistema (`/v1/ws`) conserva la autenticación mediante parámetros de consulta (`?session_id=...&ticket=...`) utilizando el ticket efímero de un solo uso emitido por `/v1/sessions/{session_id}/ws-ticket`. Por contraste, los sockets dedicados de multicámara (`/v1/camera/signal` y `/v1/camera/preview-ws`) emplean URL limpia sin parámetros y autenticación en el primer mensaje JSON, discriminando estrictamente los roles de operador (`auth_operator`) y transmisor (`auth_transmitter` / `reconnect_transmitter`).


- **Conexión:** `wss://<IP-LAN>:8000/v1/ws?session_id=...&ticket=...` (usando el ticket efímero obtenido en `/v1/sessions/{id}/ws-ticket`).
- **Eventos emitidos (JSON con clave `type`):**
  - `detection`: Resultados de detección de fotogramas confirmados.
  - `arduino_ack`: Cambios en el estado serie, máscaras solicitadas/confirmadas y recepción de ACK.
  - `system`:
    - `config_changed`: Modificaciones en el borrador draft.
    - `config_applied`: Promoción exitosa a la versión activa.
    - `gallery_building` / `gallery_ready` / `gallery_error`: Progreso de construcción de galería.
    - `mode_changed`: Cambio de modo del sistema (`mock`, `preparation`, `real`).
