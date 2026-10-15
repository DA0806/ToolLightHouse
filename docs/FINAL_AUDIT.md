# ToolLightHouse: Informe de Auditoría Final e Integración

**Fecha:** 2026-10-15<br/>
**Rama de entrega:** `feat/unified-dashboard-multicamera`<br/>
**Rama base de integración:** `ba20878c7f66e9428006baf8a734c6fd7ff6734c` (`main` intacta, sin merge)<br/>
**Commits integrados en la entrega:**
1. `f61675e` - *fix(core): audit findings for multicamera streaming, serial lease fences, and pipeline freshness*
2. `6c1ff14` - *feat(demo-ops): implement Windows preflight, secure demo launcher, cert helper fixes, and operational docs* (cherry-pick de `audit/demo-ops` @ `add60ca16df45204fb324a5ec748d378483809bd`)

---

## 1. Matriz de Hallazgos Relevantes y Correcciones Auditadas

| Hallazgo Relevante | Archivo(s) | Causa Raíz | Riesgo Operativo | Corrección Aplicada | Prueba / Evidencia de Aceptación |
|---|---|---|---|---|---|
| **Streaming local continuo sin bucle JPEG** | `src/toollighthouse/api/app.py` | La captura de webcam permitía la recepción de uploads de preview desde la tablet o buffers redundantes en vez de fotogramas OpenCV nativos continuos. | Riesgo de loopback por subida de previews degradados desde la tablet en lugar de fotogramas prístinos nativos, consumo innecesario de CPU y desincronización de fotogramas. | Pipeline directo de fotogramas OpenCV nativos continuos con descarte atómico de uploads redundantes y buffers obsoletos. | `test_stream_local_webcam_pristine_buffer_actual_pixels`, `test_stream_local_webcam_ignores_uploaded_file_no_loopback` (PASS). |
| **Monotonic Freshness y limitación FPS** | `src/toollighthouse/api/app.py`, `frontend/src/videoFreshness.ts` | Ausencia de marcas de tiempo monótonas en fotogramas recibidos y falta de control de cadencia en uploads. | Procesamiento de fotogramas antiguos fuera de orden o desbordamiento por ráfagas de red. | Validación de marca de tiempo monótona y rate limiter estricto por FPS (`latest-only`) en backend y frontend. | `test_stream_rate_limit_fps`, `test_pipeline_freshness.py`, `videoFreshness.test.ts` (PASS). |
| **Códigos de error HTTP: 409 vs 401** | `src/toollighthouse/api/app.py`, `frontend/src/api.ts` | Vencimiento de lease físico o concurrencia arrojaba HTTP 401 Unauthorized en lugar de HTTP 409 Conflict. | El frontend cerraba la sesión del usuario deslogueándolo en vez de solicitar re-adquisición del lease. | Separación canónica: 401 solo para credenciales erróneas; 409 para colisiones de lease físico y concurrencia. | `test_unified_lease.py`, `test_api_security_regressions.py` (PASS). |
| **Heartbeat Lease Lost preservando autenticación** | `src/toollighthouse/api/app.py`, `frontend/src/App.tsx` | La expiración del lease físico durante el pulso de heartbeat purgaba el token de sesión del operador. | Pérdida intempestiva de la sesión operativa durante la demostración si expiraba la reserva física. | El fallo de renovación de lease marca el hardware como libre pero mantiene la sesión autenticada intacta. | `test_unified_lease.py`, `test_backend_safety_repair.py`, `audit-corrections-regression.test.tsx` (PASS). |
| **Lease de Mantenimiento y Guard Serializado** | `src/toollighthouse/api/app.py`, `src/toollighthouse/serial_transport.py` | Entrada concurrente al modo mantenimiento permitía intercalar comandos de prueba con inferencia en vivo. | Activación no intencionada de LEDs desfasados (stale LEDs) en hardware durante pruebas manuales de diagnóstico. | Guard serializado con lease exclusivo de mantenimiento; rechazo inmediato de cualquier inferencia concurrente. | `test_arduino_maintenance.py`, `test_delayed_inference_race_with_maintenance_enter` (PASS). |
| **Épocas en Serial Transport y revalidación de escena** | `src/toollighthouse/serial_transport.py`, `src/toollighthouse/api/app.py` | Comandos seriales pendientes en cola se ejecutaban tras cambiar de modo, desconexión o nueva captura. | Disparo de máscara LED desfasada correspondiente a una escena previa o herramienta ya retirada. | Generación de épocas (`capture_generation`), deadline estricto y revalidación de época antes de cada escritura/reintento. | `test_adversarial_async.py`, `test_delayed_inference_race.py`, `test_runtime_safety.py` (PASS). |
| **Fences de ciclo de flujo (`X-Stream-Run`)** | `src/toollighthouse/api/app.py`, `tests/test_stream_local_webcam.py` | Tras solicitar parada de streaming, fotogramas residuales en vuelo aún reactivaban el estado de captura. | Estado zombi del streaming local impidiendo el inicio de nuevos flujos o generando estados inconsistentes. | Identificador unívoco por ejecución de flujo (`X-Stream-Run`); rechazo atómico de fotogramas y paradas desfasadas. | `test_stream_run_fence_rejects_late_frame_and_old_stop`, `test_stream_stop_cancels_stream_and_resets_fps` (PASS). |
| **Parada confirmada de stream antes de escaneo manual** | `frontend/src/App.tsx` | La captura de foto manual no esperaba la confirmación de detención del stream continuo antes de analizar. | Disparo de escaneo concurrente con streaming activo o sobre un estado de parada fallido. | La toma de foto manual espera la confirmación de parada del stream y aborta el escaneo si la parada falla (en lugar de un restablecimiento genérico de interfaz). | `test_acceptance.py`, `App.freshness.test.tsx`, reporte Playwright/Edge (PASS). |
| **Gate de VideoFreshness en frontend (WebRTC/JPEG)** | `frontend/src/videoFreshness.ts`, `frontend/src/components/RemoteCameraView.tsx` | Visor remoto no verificaba decodificación real de fotogramas nuevos al alternar entre WebRTC y HTTP JPEG. | Falsos positivos de conexión mientras la imagen transmitida permanecía congelada en la tablet. | Gate de decodificación efectiva con verificación de avance en marcas temporales y dimensiones de fotograma. | `videoFreshness.test.ts`, `review-findings-regression.test.tsx`, `final_acceptance_report.json` (PASS). |
| **Reanudación remota (Freeze/Resume) sin nuevo QR** | `frontend/src/App.tsx`, `frontend/src/components/RemoteCameraView.tsx` | Congelar la transmisión para captura forzaba a regenerar una invitación y escanear el QR nuevamente. | Interrupción severa del flujo operativo en demostración física cada vez que se tomaba una foto de fondo. | Estado de reanudación sobre el mismo par WebRTC existente; el botón «Abrir cámara» reanuda sin nuevo QR. | `review-findings-regression.test.tsx`, `final_acceptance_report.json` (PASS). |
| **Cuadrícula 3x3 responsiva en tablet (1024px)** | `frontend/src/styles.css` | El contenedor del inventario de 9 casillas desbordaba horizontalmente en pantallas con viewport de 1024px. | Visualización defectuosa en tabletas de demostración (corte visual de la columna derecha de herramientas). | Reglas CSS con min-width: 0 y wrapping CSS (`overflow-wrap: anywhere`) para contención estricta en 1024px. | `final_acceptance_report.json` (`overflowPx: 0` en las 9 casillas) (PASS). |
| **CLI Admin Env y scripts de operación en Windows** | `src/toollighthouse/__main__.py`, `scripts/*` | El comando `serve` no leía la clave de administración desde variables de entorno y los scripts carecían de chequeos. | Imposibilidad de automatizar arranques limpios o colisiones de puertos sin diagnóstico no destructivo. | Soporte de variable `TOOLLIGHTHOUSE_ADMIN_KEY`, preflight no destructivo, helpers de red y validación de certificados. | `test_cli_admin_env.py`, `test_demo_ops_scripts.py` (PASS). |

---

## 2. Resultados Consolidados de Pruebas Post-Integración

Todas las suites fueron ejecutadas directamente en el árbol de trabajo consolidado tras la integración:

### A. Suite Completa Backend (Pytest)
- **Comando:** `.venv\Scripts\python.exe -m pytest -q`
- **Resultado:** **258 pasadas, 1 omitida** (493 advertencias de deprecación controladas en FastAPI/Starlette) en **101.85 s**.
- **Desglose:** 236 pruebas del núcleo funcional + 22 pruebas operacionales/CLI integradas de `audit/demo-ops`.
- **Prueba omitida:** `tests/test_embedder_gemma.py::test_embedding_gemma_real_weights` (esperado: requiere hardware GPU real y pesos locales cargados en sesión dedicada).

### B. Suite Focalizada de Seguridad y Concurrencia (Serial / Arduino / Stream / Adversarial)
- **Comando:** `.venv\Scripts\python.exe -m pytest -q tests/test_delayed_inference_race.py tests/test_adversarial_async.py tests/test_stream_local_webcam.py tests/test_arduino_maintenance.py tests/test_runtime_safety.py tests/test_pipeline_freshness.py tests/test_backend_safety_repair.py tests/test_unified_lease.py`
- **Resultado:** **84 pasadas**, 0 falladas en **47.05 s**.
- **Cronología de validación:** El subconjunto inicial de 34 pruebas de condiciones de carrera (`test_delayed_inference_race.py` [5], `test_adversarial_async.py` [6], `test_stream_local_webcam.py` [10], `test_runtime_safety.py` [13]) fue ejecutado y superado exitosamente 3 veces consecutivas antes del endurecimiento final. Con las capas añadidas de leases unificados, control de frescura de pipeline y protección de mantenimiento, el conjunto de seguridad consolidado alcanzó 84 pruebas completadas con éxito.

### C. Suite Completa Frontend (Vitest, Typecheck y Build)
- **Pruebas unitarias y de regresión:** `npm test -- --run` en `frontend/`
  - **Resultado:** **17 archivos pasados (17), 113 pruebas pasadas (113)** en **14.85 s**.
- **Verificación de tipos:** `npm run typecheck` (`tsc --noEmit`)
  - **Resultado:** **0 errores de TypeScript**.
- **Compilación de producción:** `npm run build` (`tsc -b && vite build`)
  - **Resultado:** Compilación exitosa en 470 ms (`dist/index.html` generado, 381.57 kB JS gzip 115.36 kB).

### D. Sintaxis y AST de Scripts Operativos en Windows PowerShell 5.1
- **Comando:** `.venv\Scripts\python.exe -m pytest -q tests/test_demo_ops_scripts.py`
- **Resultado:** **18 pasadas** en **36.93 s**.
- **Cobertura:** Validación del Abstract Syntax Tree (AST) sin errores de sintaxis en `scripts/network_helpers.ps1`, `scripts/preflight.ps1`, `scripts/setup_certs.ps1` y `scripts/start-demo.ps1`. Incluye pruebas de resolución de IP LAN, filtrado de interfaces virtuales, envelopes de configuración, validación de certificados y arranque interactivo seguro.

### E. Verificaciones de Calidad de Código y Dependencias
- **Integridad de paquetes:** `.venv\Scripts\python.exe -m pip check` -> `No broken requirements found.`
- **Compilación Python:** `python -m py_compile` sobre módulos modificados y suites de prueba -> 0 errores.
- **Espacios en blanco y saltos de línea (Git):** `git diff --check` -> **0 advertencias** (se corrigió el salto de línea sobrante en fin de archivo en `tests/test_unified_lease.py`, `tests/test_adversarial_async.py` y `tests/test_stream_local_webcam.py`).

### F. Diagnóstico Preflight No Destructivo en Windows
- **Comando:** `powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1`
- **Resultado:** **7 PASS, 5 WARN, 1 FAIL**.
- **Bloqueador real identificado:**
  - `[FAIL] TLS Certificate: Missing lan-cert.pem or lan-key.pem`: Se requiere ejecutar `.\scripts\setup_certs.ps1` previo al evento para habilitar HTTPS en la LAN y permitir `getUserMedia` en navegadores móviles.
- **Advertencias operacionales legítimas (condiciones de sala):**
  - Entorno local actual ejecutando PyTorch en CPU (la evidencia de GPU RTX 3060 fue validada offline separadamente).
  - Inventario de 9 herramientas configurado con plantilla base pendiente de confirmación física de casillas.
  - Activo de calibración ROI (`data/calibration/roi.json`) ausente (se genera al capturar bandeja vacía).
  - Perfil de red 'Public' y ausencia de regla pasiva en firewall (se documenta comando de configuración en guía).

---

## 3. Evidencias Externas Verificadas (Offline)

### A. Evidencia de Inferencia en GPU NVIDIA Real (Offline)
- **Identificador de ejecución:** `staffer-mv96ejoy-97ef329d` (artefactos en `real_gpu_smoke_audit.py`).
- **Hardware y entorno:** NVIDIA GeForce RTX 3060 (12 GB VRAM), PyTorch 2.11.0+cu128, CUDA 12.8, precisión BF16.
- **Resultados:** 29 aserciones superadas.
  - Galería 9 clases solo texto (zero-shot) y galería híbrida (9 clases, 2 con imagen, 7 texto fallback).
  - Lote de 4 crops procesado con tensores finitos y normalizados.
  - Umbrales originales rigurosamente conservados: similitud **0.72**, margen **0.08** (sin reducción artificial).
  - Tiempos y memoria: Carga de modelo en **12.18 s**, inferencia de lote en **0.747 s**, memoria pico asignada **1154.26 MB**, memoria residual tras limpieza **8.12 MB**.
- **Límite técnico explícito:** Constituye prueba de humo funcional en hardware real; no representa certificación formal contra fugas de memoria en sesiones de días ni garantía de exactitud con herramientas físicas no calibradas.

### B. Evidencia de Navegador Real Edge y WebRTC Sintético
- **Identificador de ejecución:** `staffer-mv97ohgx-fc7ed1c1` (reporte en `final_acceptance_report.json`).
- **Resultados clave validados:**
  - `stopBeforeScan: true`, `hasScan200: true` con imagen sintética de 600x400 px.
  - Reanudación remota (`resume` con 1 invitación inicial, 0 peticiones extra a `getUserMedia`, respuestas HTTP 200 exitosas de stream incrementadas de 4 a 8, `noExtraInvite/token: true`).
  - Detección de congelamiento (`freeze` con 0 llamadas a escaneo disparadas y alerta visible, seguido de recuperación 200).
  - Contención de cuadrícula 3x3 en 1024px: 9 casillas contenidas con `overflowPx: 0`.
  - Inspección de 21 vistas (5 páginas principales y vista de transmisión móvil bajo 4 viewports): verificación estricta de ancho horizontal y ausencia de desbordamiento (no constituye auditoría de accesibilidad WCAG completa).
- **Límite técnico explícito:** Ejecutado en Microsoft Edge con páginas locales y transmisión sintética; la validación con dispositivos móviles físicos mediante HTTPS real sobre Wi-Fi permanece como pendiente de sala.

---

## 4. Auditoría de Seguridad y Límites de Despliegue

1. **Autenticación y Leases:** Los endpoints protegidos y el canal de señalización WebSocket implementan validación rigurosa del rol (`controller` vs `admin`), titularidad del lease físico y origen del WebSocket (`signalOrigin`).
2. **Generación de QR y Encabezado Host:** La URL del código QR se construye dinámicamente a partir del encabezado `Host` de la petición (`requestHost`) sin una lista blanca estricta (`Host whitelist`).
3. **Límites de Despliegue (Fuera de Alcance):** El endurecimiento adicional mediante allowlist de hosts y la exposición en Internet público están formalmente fuera del alcance de este release. El software está diseñado para redes LAN privadas confiables (mediante IP numérica o router dedicado) y certificados TLS confiables. No se debe asumir ni publicitar seguridad pública completa en redes abiertas.
4. **Auditoría de Modificación de Archivos Sensibles:**
   - Verificación de marcas de modificación (`mtime`) en `config/`, `data/`, `models/` posteriores a `2026-10-15T04:53:00Z`: **0 archivos modificados**.
   - No se incorporaron claves privadas, certificados, pesos binarios no autorizados ni datos privados al control de versiones.

---

## 5. Prerrequisitos Físicos Pendientes en Sala de Demostración

Los siguientes aspectos no pueden resolverse en el entorno de desarrollo y deben ejecutarse físicamente antes de la presentación:
1. **Certificados TLS:** Ejecutar `.\scripts\setup_certs.ps1` en la laptop e instalar `rootCA.pem` público en tablet y teléfono.
2. **Conexión de Microcontrolador:** Conectar el Arduino Nano vía cable USB y seleccionar el puerto COM detectado en la pestaña *Arduino*. El ACK confirma únicamente la aplicación de la máscara en firmware; la iluminación física real de las casillas D2 a D10 debe observarse por separado.
3. **Calibración Óptica:** Capturar la foto de la bandeja vacía en la pestaña *Calibración* y delimitar la ROI bajo la iluminación ambiental real del salón.
