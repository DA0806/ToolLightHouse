# Guía Operativa del Sistema Multicámara (ToolLightHouse)

Este manual describe el funcionamiento, configuración y operación del sistema multicámara de ToolLightHouse en red de área local (LAN), cubriendo las cinco fuentes canónicas de imagen, la vinculación WebRTC mediante código QR, la vista previa de webcams locales desde una tablet y el procedimiento de calibración espacial independiente.

---

## 1. Arquitectura de Tres Dispositivos y Cinco Fuentes

El sistema opera distribuyendo responsabilidades de forma coordinada en tres dispositivos físicos:

```text
               [ Teléfono Transmisor ]
                 (Soporte cenital fijo)
                /                      \
    WebRTC P2P /                        \ HTTP Fallback (2 FPS)
   MediaStream/                          \ & Señalización WS
             v                            v
   [ Tablet Operadora ] <=============> [ Laptop Servidora ]
     (Dashboard táctil)   HTTP API /      (FastAPI + OpenCV + Gemma 2)
                          WebSockets               | USB Serial
                                                   v
                                          [ Arduino Nano + LEDs ]
```

### Las Cinco Fuentes Canónicas de Imagen

El backend y el selector de fuentes del dashboard identifican unívocamente cinco orígenes de captura:

| Identificador Canónico (`source_id`) | Nombre en Pantalla | Mecanismo de Transporte | Rol y Ubicación |
|---|---|---|---|
| `remote_phone` | Teléfono Remoto (WebRTC) | WebRTC P2P directo a navegador operador; fallback HTTP a FastAPI | Teléfono móvil montado cenitalmente apuntando a la bandeja de herramientas. |
| `laptop_builtin` | Cámara Integrada de Laptop | Adquisición DirectShow/OpenCV en backend; preview WS binario a tablet | Cámara web nativa de la laptop servidora. |
| `usb_webcam` | Webcam USB Externa | Adquisición DirectShow/OpenCV en backend; preview WS binario a tablet | Cámara USB conectada físicamente a la laptop servidora. |
| `operator_device` | Cámara de este Dispositivo | `navigator.mediaDevices.getUserMedia` en el navegador del operador | Cámara del dispositivo donde está abierto el dashboard (tablet o laptop). |
| `manual_upload` | Fotografía Manual | Carga de archivo estático (`multipart/form-data`) | Archivo JPEG/PNG/WebP subido desde almacenamiento local. |

---

## 2. Disposición Principal: Tablet Operadora + Teléfono Remoto Cenital

Esta configuración es la recomendada para demostraciones y operación habitual en banco de trabajo.

### 2.1 Flujo Paso a Paso de Vinculación por Código QR

1. **Selección de Fuente:** En la tablet, abra la sección **Inspección**, despliegue el selector de cámaras y elija **Teléfono Remoto (WebRTC)**.
2. **Generación de Invitación:** Pulse el botón **«Emparejar Teléfono / Mostrar QR»**.
   - El backend genera una invitación temporal de un solo uso (`POST /v1/camera/invite`) con expiración por defecto de **180 segundos** (configurable entre 30 y 600 segundos).
   - La invitación contiene una URL segura generada localmente:
     ```text
     https://<IP-LAN-LAPTOP>:8000/camera#token=<INVITE_TOKEN>
     ```
   - El código QR se genera y renderiza de forma 100% offline mediante la biblioteca `qrcode` del frontend; ningún servicio externo ni API de nube es contactada.
3. **Escaneo desde el Teléfono:**
   - Abra la aplicación nativa de cámara del teléfono móvil y apunte al QR desplegado en la tablet.
   - Abra el enlace en el navegador del teléfono (Chrome o Safari).
4. **Consumo Atómico y Protección de Credenciales:**
   - El cliente ligero `/camera` extrae el `token` del fragmento de hash de la URL (`#token=...`).
   - De inmediato ejecuta `window.history.replaceState` para borrar el token de la barra de direcciones y del historial del navegador antes de realizar conexiones.
   - El teléfono abre la conexión WebSocket con el centro de señalización local (`WS /v1/camera/signal`) y envía la autenticación inicial:
     ```json
     {"type": "auth_transmitter", "invite_token": "<INVITE_TOKEN>"}
     ```
   - El backend valida y **consume atómicamente la invitación** (marcando `used = True`). La invitación queda invalidada para cualquier otro cliente.
   - El servidor devuelve credenciales de transmisor efímeras (`transmitter_id`, `transmitter_ticket`), válidas exclusivamente para transmitir video.
5. **Captura y Negociación WebRTC:**
   - La pantalla `/camera` solicita acceso a la cámara trasera (`facingMode: "environment"`).
   - Intenta activar el bloqueo de suspensión (`navigator.wakeLock.request('screen')`) para evitar que la pantalla se apague durante la prueba.
   - La tablet y el teléfono intercambian ofertas SDP, respuestas y candidatos ICE locales a través del hub de señalización de FastAPI.
   - Se establece la conexión `RTCPeerConnection` P2P directa entre ambos navegadores dentro de la red Wi-Fi.
   - El video aparece en vivo en la tablet en el área de inspección izquierda.
6. **Fijación en Soporte Cenital:**
   - Una vez confirmado el video, el teléfono se coloca en el soporte fijo cenital sobre la bandeja de herramientas.
   - No se requiere interacción física adicional con el teléfono durante el resto de la sesión.

### 2.2 Modo Alternativo de Contingencia (Fallback HTTP)

Si la red Wi-Fi tiene activado aislamiento de clientes (Client Isolation) o las políticas de firewall de la subred impiden la negociación P2P de candidatos ICE:

1. El cliente del teléfono detecta el fallo de conexión ICE tras agotarse los intentos y notifica `ice_failed` al centro de señalización.
2. Automáticamente conmuta al **Modo Fallback HTTP**:
   - Extrae fotogramas a una tasa conservadora regulada de **2 FPS** utilizando `requestVideoFrameCallback` (con fallback a `setInterval`).
   - Comprime y escala el fotograma en el cliente a dimensiones $\le 1280 \times 720$ píxeles y tamaño $\le 350$ KB.
   - Transmite el fotograma mediante `POST /v1/camera/frame-upload` enviando los encabezados autenticados:
     - `X-Transmitter-Id`: Identificador del transmisor.
     - `X-Transmitter-Token`: Credencial efímera de transmisión.
     - `X-Capture-Sequence`: Número de secuencia monotónico incremental local.
     - `X-Source-Id`: `remote_phone`.
     - `X-Source-Version`: Versión de la fuente activa.
3. El backend verifica la tasa de subida (límite estricto de 2 FPS), valida la frescura del fotograma y almacena el fotograma más reciente en memoria.
4. La tablet recibe la vista previa a través del canal de streaming binario `/v1/camera/preview-ws` o mediante actualización periódica, permitiendo continuar la calibración y el análisis sin interrupción de la demo.

---

## 3. Disposición Secundaria: Webcams USB y Cámara Integrada de Laptop

Cuando se utiliza una cámara USB conectada físicamente a la laptop servidora (o su cámara integrada) mientras el operador administra el sistema desde la tablet:

### 3.1 Adquisición Local en Backend y Streaming Binario

1. **Enumeración Segura de Webcams:**
   - La tablet consulta las cámaras conectadas a la laptop mediante `GET /v1/camera/webcams`.
   - El backend sondea los dispositivos DirectShow de Windows de forma segura en un hilo secundario sin interrumpir la cámara activa.
   - La lista informa índice de dispositivo, nombre amigable y resoluciones compatibles.
2. **Selección y Activación:**
   - El operador selecciona la webcam en el desplegable de fuentes (`POST /v1/camera/sources/active`).
   - El backend inicializa `OpenCVCameraService` con un hilo dedicado de captura en bucle (`_acquisition_loop`) en la laptop.
3. **Vista Previa Remota en Tablet:**
   - La tablet abre el canal WebSocket binario `WS /v1/camera/preview-ws`.
   - Autentica la conexión enviando como primer mensaje JSON:
     ```json
     {"type": "auth", "session_id": "<SESSION_ID>", "ticket": "<SESSION_TICKET>"}
     ```
   - El servidor transmite fotogramas binarios JPEG comprimidos con límites predeterminados configurables (por defecto **640×360 a 15 FPS**), manteniendo el tráfico Wi-Fi liviano y el bucle de eventos fluido.
4. **Escaneo de Herramientas sin Re-Subida de Pixeles:**
   - Cuando el operador pulsa **«Escanear herramientas»** (`POST /v1/analyze/scan`):
   - El backend extrae directamente el último fotograma congelado en RAM en su resolución nativa (ej. 1280×720 o 1920×1080).
   - **Ningún fotograma viaja de la laptop a la tablet para ser devuelto a la laptop.** La inferencia con EmbeddingGemma 2 se ejecuta de inmediato en la GPU local.
   - La respuesta devuelve las coordenadas de detección y la imagen de referencia para dibujar los recuadros SVG con precisión milimétrica.

---

## 4. Calibración Espacial Independiente por Fuente

Cada cámara posee una distancia focal, distorsión geométrica, ángulo cenital y resolución propios. ToolLightHouse almacena un perfil de calibración separado para cada fuente:

```json
{
  "source_id": "remote_phone",
  "resolution": {"width": 1920, "height": 1080},
  "orientation": "normal",
  "crop": null,
  "processing_version": 2,
  "background_filename": "bg_remote_phone_1920x1080.jpg",
  "roi": [0.08, 0.12, 0.84, 0.78],
  "calibrated_at": 1728935000.0,
  "status": "ready"
}
```

### 4.1 Flujo de Calibración Paso a Paso

1. **Seleccionar la Fuente Activa:** En la pestaña **Calibración**, seleccione la cámara que desea calibrar (`remote_phone`, `usb_webcam`, etc.).
2. **Despejar la Bandeja:** Retire todas las herramientas del organizador. Compruebe que la iluminación ambiental sea la misma que se utilizará durante el reconocimiento.
3. **Capturar Fondo Vacío:**
   - Pulse **«Capturar fondo vacío»** (`POST /v1/calibration/profiles/{source_id}/capture`).
   - Si la fuente es local (`usb_webcam` o `laptop_builtin`), el servidor toma la muestra directamente de memoria.
   - Si la fuente es remota (`remote_phone`), el navegador de la tablet captura el fotograma del stream de video y lo envía en la petición.
   - El fondo se guarda en `data/calibration/` y se asocia al perfil de esa fuente.
4. **Dibujar Región de Interés (ROI):**
   - Sobre el lienzo interactivo del dashboard, arrastre el recuadro para encerrar exactamente la cuadrícula de las 9 herramientas.
   - Las coordenadas se normalizan en el rango canónico $[0.0, 1.0]$:
     $$\text{ROI} = [x_{\text{norm}}, y_{\text{norm}}, w_{\text{norm}}, h_{\text{norm}}]$$
   - El frontend envía las coordenadas al backend (`PUT /v1/calibration/profiles/{source_id}/roi`).
5. **Consistencia Espacial:**
   - Tanto el fondo capturado como cada fotograma de inferencia pasan por la misma función `apply_canonical_transform(image, orientation, crop)`.
   - Si la cámara cambia de orientación o resolución, el perfil se marca automáticamente como `incompatible` y exige nueva calibración antes de permitir el reconocimiento.

### 4.2 Migración de Calibraciones Anteriores (Legacy)

Si el sistema detecta un fondo previo sin identificador de cámara (`unbound_legacy`):
- El perfil se marca con `needs_confirmation: true`.
- El sistema **nunca asigna silenciosamente** ese fondo a otra cámara.
- El operador debe pulsar **«Confirmar Perfil»** tras verificar visualmente la compatibilidad geométrica.

---

## 5. Resiliencia Operativa y Seguridad Física

### 5.1 Protocolo de Conmutación Segura de Fuentes

Al cambiar de cámara desde el dashboard (`POST /v1/camera/sources/active`):
1. **Detención de Flujos:** Se cancela de inmediato la inferencia continua.
2. **Cerca de Generación:** Se incrementa `scene_generation += 1` y `source_version += 1`.
3. **Apagado Seguro:** Se transmite de inmediato `MASK:0` a Arduino Nano para apagar todos los LEDs de la bandeja.
4. **Liberación de Hardware:** Si la cámara previa era OpenCV, se invoca `capture.release()` y se cierra su hilo. Si era WebRTC, se revocan las credenciales del transmisor.
5. **Carga de Perfil:** Se activa el perfil de calibración asociado a la nueva fuente.
6. **Reanudación Condicionada:** La inferencia solo se reanuda cuando la nueva fuente está conectada y su perfil está en estado `ready`.

### 5.2 Descarte de Resultados Obsoletos (Stale Fencing)

Cada fotograma e inferencia se identifica mediante la tupla:
$$\mathcal{T} = (\text{frame\_id}, \text{source\_id}, \text{source\_version}, \text{lease\_generation}, \text{scene\_generation})$$

Antes de enviar cualquier comando serie a Arduino Nano (`serial.send_mask(mask)`), el backend valida que la tupla coincida exactamente con el estado activo. Si durante la inferencia en GPU el teléfono se desconectó, la sesión expiró o se cambió de cámara, el resultado se descarta y los LEDs no se encienden.

### 5.3 Diagnóstico de Fallas Habituales

| Síntoma | Causa Probable | Solución |
|---|---|---|
| Video negro en teléfono al emparejar | Permiso de cámara denegado en el navegador | Conceda permiso a la cámara en el navegador móvil y recargue la URL `/camera`. |
| ICE falla y no conecta P2P | Aislamiento Wi-Fi en router / subredes distintas | Verifique que ambos dispositivos estén en la misma red Wi-Fi; el sistema activará automáticamente el Fallback HTTP (2 FPS). |
| Webcam USB no abre en laptop | Otro programa (Teams, Zoom, Camera app) tiene el dispositivo ocupado | Cierre programas que usen la cámara; en Windows `DirectShow` otorga acceso exclusivo a un solo proceso. |
| Inferencia devuelve `409 Conflict` | Falta calibrar fondo o el sistema está en mantenimiento | Despeje la bandeja y capture el fondo en **Calibración**, o salga del modo mantenimiento en **Arduino**. |
| LEDs se apagan tras 2.5 segundos | Watchdog de seguridad del firmware en Arduino | Comportamiento esperado si se interrumpe la comunicación serie USB con el backend (el watchdog vigila el enlace serie USB, no la red Wi-Fi directamente). |
