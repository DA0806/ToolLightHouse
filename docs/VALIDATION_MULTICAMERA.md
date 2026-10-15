# Matriz de Validación, Evidencia de Pruebas y Protocolo Físico (Multicámara)

**Fecha:** 2026-10-14
**Ámbito:** Validación integral de las Fases A–H para la integración multicámara y dashboard unificado de ToolLightHouse.

---

## 1. Delimitación entre Software Automatizado y Banco Físico

En conformidad con las directrices del proyecto:
- **Las pruebas automatizadas de software validan contratos lógicos:** bucles de señalización WebRTC mediante relays simulados, exclusión mutua de leases, serialización y deserialización de modelos, cálculo de coordenadas canónicas y máquinas de estado concurrentes.
- **Un mock o un test de señalización no constituye prueba física:**
  - El paso de tests de señalización JSON en memoria no demuestra la transmisión real de pistas de audio/video WebRTC entre dos navegadores móviles heterogéneos sobre una Wi-Fi física.
  - El paso de tests con `SimulatedSerial` no demuestra la continuidad eléctrica, caída de tensión ni iluminación óptica de los LEDs conectados al Arduino Nano.
  - El paso de tests sintéticos de inferencia no demuestra la precisión de clasificación sobre las nueve herramientas reales del equipo con iluminación de taller.
- **Estado de Hardware de Destino:** La inferencia en GPU fue comprobada en la máquina de desarrollo donde se observó el modelo NVIDIA GeForce RTX 3060. Las laptops de destino (RTX 4060 y RTX 5050) permanecen pendientes de validación en banco de pruebas físico.

---

## 2. Matriz de Requisitos y Estado de Evidencia

| Fase / Requisito | Descripción Funcional | Evidencia de Software Automatizada | Validación Física en Hardware Real |
|---|---|---|---|
| **Fase A: Diseño y Auditoría** | Verificación de baseline criptográfica, diseño de roles, eliminación de restricción de copia para integración del equipo y perfiles de cámara. | Documento aprobado [`docs/superpowers/specs/2026-10-14-unified-dashboard-multicamera-design.md`](file:///docs/superpowers/specs/2026-10-14-unified-dashboard-multicamera-design.md). | Completa (Revisión técnica). |
| **Fase B: Concesión y Registro** | Exclusividad de control físico (`UnifiedSessionStore`), takeover administrativo explícito, registro de 5 fuentes canónicas (`CameraSourceRegistry`). | Pytest: `test_unified_lease.py`, `test_camera_registry.py`. | Pendiente de prueba con múltiples operadores físicos. |
| **Fase C: Señalización y QR** | Invitaciones efímeras de un solo uso (180s), URL `#token=...`, scrubbing de historial, señalización WebRTC autenticada y vista SPA `/camera`. | Pytest: `test_signaling.py`, `test_webrtc_signaling_loopback.py`. Vitest: `QRModal.test.tsx`, `RemoteCameraView.test.tsx`. | Pendiente de negociación WebRTC en teléfonos iOS/Android reales. |
| **Fase D: Webcams OpenCV** | Enumeración de dispositivos DirectShow, adquisición en hilo único (`OpenCVCameraService`), streaming binario JPEG (`/v1/camera/preview-ws`) y escaneo directo en RAM (`/v1/analyze/scan`). | Pytest: `test_opencv_service.py`. Vitest: `CameraSelector.test.tsx`. | Pendiente de conexión con webcams USB físicas en Windows 11. |
| **Fase E: Calibración Per-Source** | Perfiles independientes por fuente en `config/app_config.json`, fondo de bandeja vacía por cámara, ROI interactivo $[0.0, 1.0]$, migración de perfiles legacy. | Pytest: `test_calibration_profiles.py`. Vitest: `CalibrationView.test.tsx`, `geometry.test.ts`. | Pendiente de calibración óptica con bandeja real. |
| **Fase F: Inferencia y Fencing** | Inferencia acotada (*latest-only* en `LatestInferenceWorker`), tupla de cerca `(source_id, lease_gen, scene_gen)` y descarte de resultados obsoletos. | Pytest: `test_backend_safety_repair.py`, `test_delayed_inference_race.py`. | Pendiente de prueba de descarte durante desconexión física. |
| **Fase G: Dashboard Unificado** | Adopción visual de tokens (morado, gris claro, tarjetas blancas, Albert Sans, Lucide), diseño tablet 2 columnas, 3×3 real y preservación de las 5 secciones administrativas. | Vitest: 14 suites, 87 pruebas aprobadas (`App.test.tsx`, `ToolManager.test.tsx`, etc.). Build: `npm run build` aprobado. Typecheck: `npm run typecheck` aprobado. | Pendiente de evaluación táctil en tablet iPad / Android física. |
| **Fase H: LAN HTTPS y Operación** | Asistente `scripts/setup_certs.ps1`, confianza de CA pública en móviles, regla de firewall acotada a subred privada y guías operativas. | Validación estática de script PowerShell (`AST Parser: 0 errors`). Guías operativas completadas. | Pendiente de despliegue en red Wi-Fi de demostración sin Internet. |

---

## 3. Estado Actual de Pruebas de Software

- **Backend Python:**
  - `pytest -q`: **202 pruebas aprobadas, 1 prueba omitida** (`test_embeddinggemma_smoke.py` omitida sin opt-in).
  - `python -m compileall -q src`: compilación de bytecode aprobada sin errores sintácticos.
  - Comprobación opt-in con modelo e inferencia CUDA real:
    `env TOOLLIGHTHOUSE_RUN_REAL_SMOKE=1 pytest tests/test_embeddinggemma_smoke.py`: **1 prueba aprobada** (32.60 s). Verificado directamente en GPU local NVIDIA GeForce RTX 3060 con CUDA activo (`torch.cuda.is_available() == True`). No se certifican modelos de GPU objetivo (RTX 4060/5050) ni hardware físico.

- **Frontend React + TypeScript:**
  - `npm --prefix frontend run test` (Vitest): **14 archivos de prueba, 87 pruebas aprobadas** en 17.00 s. Se observan advertencias normales de JSDOM para `canvas.getContext`, sin representar fallos de aserción.
  - `npm --prefix frontend run typecheck`: verificación estática de TypeScript aprobada sin errores.
  - `npm --prefix frontend run build`: empaquetado de producción con Vite completado exitosamente sin errores.

- **Bucle de Navegador Real (Headless Edge + WebRTC Sintético):**
  - Verificación en navegador Edge headless de 2 páginas (`frontend/scripts/review-loopback.mjs`) contra backend FastAPI en modo mock aislado:
    - Transmisor remoto (900×600) y receptor operador (600×400) alcanzaron `readyState: 4`.
    - Invocación única a `getUserMedia` con simulación vía `canvas.captureStream`.
    - Petición `POST /v1/stream/frame` respondió `200 OK` con cabeceras canónicas de fuente (`remote_phone`, versión 1).
    - Petición `POST /v1/analyze/scan` respondió `200 OK` (`source_id: remote_phone`, `source_version: 1`, `mode: mock`, `simulated: true`).
    - Cuadrícula de inspección renderizó la herramienta detectada y el ACK simulado.

- **Frontera de Calidad y Linter:**
  - No existe comando ni configuración de linter (`eslint`, `flake8` o `ruff`) en el repositorio actual; no se certifica conformidad de linter inexistente.

- **Integración Continua (GitHub Actions):**
  - El flujo de trabajo en GitHub Actions valida de forma independiente y aislada el backend (`pytest` + `compileall`), el frontend (`npm ci`, `typecheck`, `test`, `build`) y el firmware (`arduino-cli compile`).


---

## 4. Protocolo de Aceptación: Los 20 Casos de Prueba en Banco Físico

Para validar la integración final, se debe ejecutar el siguiente protocolo en el banco de pruebas físico con la disposición completa:
- Laptop Windows 11 con GPU RTX 4060 o RTX 5050 conectada a Arduino Nano por USB.
- Tablet conectada a la misma red Wi-Fi local.
- Teléfono móvil fijado cenitalmente sobre la bandeja de herramientas.
- Las nueve herramientas físicas del inventario universitario.

| N° | Caso de Prueba Físico | Procedimiento Operativo en Banco | Resultado Físico Esperado | Estado Actual |
|---|---|---|---|---|
| **1** | **Dashboard desde tablet** | Abrir `https://<IP-LAN>:8000/` en el navegador de la tablet e iniciar sesión como `controller`. | Carga del dashboard sin alertas SSL; visualización de las 5 secciones táctiles. | *Pendiente de validación en hardware real* |
| **2** | **QR generado en tablet** | Seleccionar *Teléfono Remoto* y pulsar *«Emparejar Teléfono»*. | Despliegue del modal con código QR y temporizador de 180s. | *Pendiente de validación en hardware real* |
| **3** | **Teléfono vinculado** | Escanear el QR con la cámara del teléfono y abrir la URL `/camera`. | El teléfono consume la invitación, limpia `#token` del historial y muestra la interfaz minimalista. | *Pendiente de validación en hardware real* |
| **4** | **Video visible en tablet** | Observar el área de video izquierda en la tablet tras establecerse WebRTC. | MediaStream en vivo con baja latencia visible en la tablet; teléfono con wake lock activo. | *Pendiente de validación en hardware real* |
| **5** | **Calibración de fondo desde tablet** | Retirar herramientas, ir a *Calibración* y pulsar *«Capturar fondo vacío»*. | Captura guardada para `remote_phone`; dibujo interactivo de ROI alrededor de la bandeja. | *Pendiente de validación en hardware real* |
| **6** | **Identificación de 1 herramienta** | Colocar una herramienta conocida en su casilla y pulsar *«Escanear herramientas»*. | Recuadro SVG sobre la herramienta detectada en la tablet; similitud finita mostrada. | *Pendiente de validación en hardware real* |
| **7** | **Identificación de varias herramientas** | Colocar 3 a 5 herramientas distintas en diferentes posiciones de la bandeja. | Detección simultánea multiobjeto; recuadros independientes sin solapamiento de identidades. | *Pendiente de validación en hardware real* |
| **8** | **Confirmación de LED correcto** | Verificar físicamente los LEDs de la bandeja tras el escaneo. | El LED correspondiente (D2..D10) se ilumina físicamente; dashboard reporta `ACK` recibido. | *Pendiente de validación en hardware real* |
| **9** | **Cambio a webcam USB desde tablet** | Conectar webcam USB a la laptop y seleccionarla en el selector de la tablet. | Se detiene el streaming del teléfono; se activa la vista previa binaria de la webcam en la tablet. | *Pendiente de validación en hardware real* |
| **10** | **Recalibración de webcam** | Despejar la bandeja y capturar fondo específico para `usb_webcam`. | Perfil independiente guardado sin sobreescribir la calibración del teléfono remoto. | *Pendiente de validación en hardware real* |
| **11** | **Reconocimiento con webcam** | Colocar herramientas y escanear desde la tablet. | Inferencia ejecutada directamente sobre el fotograma de la laptop en RAM; LEDs activados. | *Pendiente de validación en hardware real* |
| **12** | **Cámara integrada de laptop** | Conmutar a `laptop_builtin` desde el selector de la tablet. | Transición ordenada; apagado seguro de LEDs (`MASK:0`); vista previa de la cámara integrada. | *Pendiente de validación en hardware real* |
| **13** | **Retorno a teléfono remoto** | Re-seleccionar `remote_phone` en la tablet. | Conexión restablecida o re-emparejamiento QR; carga del perfil de calibración previo. | *Pendiente de validación en hardware real* |
| **14** | **Desconexión de teléfono durante inferencia** | Apagar la pantalla del teléfono o desconectarlo mientras la inferencia está activa. | Descarte de resultados en vuelo; apagado seguro de los LEDs (`MASK:0`). | *Pendiente de validación en hardware real* |
| **15** | **Desconexión de tablet** | Cerrar el navegador de la tablet u operar recarga forzada. | El lease expira tras 30s sin heartbeat; el backend ejecuta apagado seguro de LEDs en Arduino. | *Pendiente de validación en hardware real* |
| **16** | **Desconexión de Wi-Fi** | Desconectar el punto de acceso Wi-Fi local durante la operación. | La pérdida de red interrumpe el flujo hacia la tablet; las políticas de vencimiento de lease/TTL del backend ordenan el apagado de LEDs por USB. El watchdog de firmware (2500 ms) en Arduino Nano actúa ante la ausencia de comandos serie USB desde la laptop; tiempos medidos pendientes de banco. | *Pendiente de validación en hardware real* |
| **17** | **Fallo de Arduino / USB pull** | Desconectar el cable USB de Arduino Nano durante una operación. | Detección de error de comunicación; dashboard muestra estado `"arduino_error"` sin congelar la UI. | *Pendiente de validación en hardware real* |
| **18** | **Reconexión de componentes** | Reconectar el cable USB de Arduino y pulsar *«Conectar»* en el dashboard. | Reconexión limpia; sincronización de máscara cero (`MASK:0`) antes de admitir nuevas inferencias. | *Pendiente de validación en hardware real* |
| **19** | **Operación LAN sin Internet** | Desconectar el cable WAN del router Wi-Fi (red local aislada). | Funcionamiento de API, WebSockets, WebRTC P2P, IA en GPU y Arduino en red local sin salida a Internet. | *Pendiente de validación en hardware real* |
| **20** | **Operación directa desde laptop** | Abrir `https://localhost:8000/` en la laptop si no hay tablet disponible. | El dashboard funciona en la pantalla de la laptop con ratón y teclado. | *Pendiente de validación en hardware real* |
