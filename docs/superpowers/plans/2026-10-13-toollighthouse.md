# ToolLighthouse Implementation Plan

> **For agentic workers:** use superpowers:subagent-driven-development and test-driven-development for bounded implementation and verification. Do not commit/push; artifacts remain in shared workspace.

**Goal:** ejecutar fotografía móvil → API local → detecciones multiobjeto → ACK LEDs, con simulación verificable y ruta real preparada.
**Architecture:** un paquete Python reutilizable por FastAPI y CLI, frontend React web, firmware Nano; captura y transporte independientes de inferencia. Sesión exclusiva y apagado seguro independiente del clasificador.
**Tech Stack:** Python 3.11+, OpenCV, NumPy, Pillow, FastAPI/Uvicorn, SentenceTransformers/PyTorch opcionales, pyserial, React/TypeScript/Vite.
**Spec:** `docs/superpowers/specs/2026-10-13-toollighthouse-design.md`.

## Global Constraints
- Conservar `probar_gemma.py`, `.venv`, `models` y `samples`; no descargar modelos implícitamente.
- Modelo `google/embeddinggemma-2`, imágenes y texto, audio desactivado; BF16 compatible o FP32, nunca FP16.
- Nueve categorías de ejemplo explícitas, nueve bits 0–8/pines D2–D10 únicos; inventario real confirmado antes de hardware.
- `MASK:N\n` / `ACK:N\n`, 115200, rango 0–511, watchdog 2500 ms, ACK exacto y refresco < watchdog.
- Modo simulado identificado; no afirmar hardware/precisión/GPU físico validado sin evidencia.
- React existente conservado; si no existe crear web. FastAPI y CLI comparten pipeline.
- No GitHub, commit/push, secretos/pesos/datos privados en Git.

## Review Focus
- Release/expiry durante inferencia: resultado antiguo no puede volver a encender LEDs.
- Foto cambiada: no heredar LEDs/tracks de escena anterior y no fingir consenso temporal.
- EXIF y recorte de visualización: cajas sobre la imagen exacta normalizada.
- Arduino ACK faltante/incorrecto: no mostrar estado aplicado como confirmado.
- GPU ausente: import/start de mock funciona sin cargar Torch/modelo.

## Task 1: paquete y flujo API
**Files:** `pyproject.toml`, `.gitignore`, `config/*.json`, `src/toollighthouse/*.py`, `src/toollighthouse/api/*.py`, `tests/*.py`, `firmware/nano/nano.ino`, `scripts/*.py`.
**Interfaces:** `create_app(settings=None) -> FastAPI`; `Pipeline.process(image, timestamp, photo=False) -> dict`; API documentada en `docs/API.md` para frontend.
- [ ] Escribir primero tests sintéticos de segmentación, tracking, rechazo, histéresis, máscaras y ACK. Ejecutar `python -m pytest -q`; registrar fallo antes de implementar.
- [ ] Implementar módulos con responsabilidades separadas, configuración validada e imports opcionales diferidos.
- [ ] Añadir galería real/caché y comandos doctor/test-camera/calibrate/download-model/build-gallery/test-model/test-leds/run/serve/capture-reference/evaluate con errores accionables.
- [ ] Añadir FastAPI: sesión, lease, multipart foto/fondo, status/inventory, WebSocket y transporte de frames con límite estricto; estandarizar JSON en API.md.
- [ ] Probar exclusión de teléfonos, vencimiento y resultados tardíos, archivos inválidos/límites/EXIF, eventos WS y ACK; corregir hasta suite verde.
- [ ] Firmware parser acotado y watchdog; documentar si no hay compilador.
**Acceptance:** suite CPU verde, mock sin CUDA, ejemplo multipart devuelve cajas y ACK simulado; estado real no se inventa.

## Task 2: dashboard móvil
**Files:** `frontend/package.json`, `frontend/src/*`, `frontend/index.html`, `frontend/vite.config.ts`, tests frontend.
**Interfaces:** consume exactamente API.md; cajas `[x,y,w,h]` y dimensiones imagen normalizada.
- [ ] Tests de transformación contain/cover, rotación y reconexión/estado crítico antes de lógica nueva; ejecutar con Vitest.
- [ ] React accesible en español: emparejamiento, URL, verificación, captura/upload, vista anotada, resultados, nueve LEDs, simulación, liberación y errores/timeout/reconexión.
- [ ] Captura cámara bajo HTTPS/localhost y fallback captura nativa por archivo. Normalizar imagen recibida para evitar EXIF divergente.
- [ ] Modo continuo experimental usa un request activo con frame más reciente y descarte por antigüedad; no colas. WebRTC opcional documentado con lifecycle/backpressure.
- [ ] Ejecutar `npm test` y `npm run build`, verificar UI y flujo HTTP/WS local. Integrar build servido por FastAPI.
**Acceptance:** foto real del selector llega al API y cajas/ACK mostrados, control exclusivo y estado explícito, layout móvil y escritorio.

## Task 3: guías y revisión final
**Files:** `README.md`, `docs/DEVELOPMENT_THINKPAD.md`, `docs/DEPLOY_RTX5050.md`, `docs/DEPLOY_RTX4060.md`, `docs/NETWORK.md`, `docs/HARDWARE.md`, `docs/VALIDATION.md`, `.github/workflows/ci.yml`.
- [ ] Comandos PowerShell reproducibles, datos y pesos fuera de Git, instalación CPU y CUDA separada.
- [ ] Guías Wi-Fi privada/firewall puertos/HTTPS/offline y autenticación; prueba 1/2/3/5/9, retiro/duplicados/unknown.
- [ ] Revisión independiente del código contra prompts/spec; corregir errores concretos con tests de regresión.
- [ ] Ejecutar suite completa, frontend build/tests, doctor, smoke HTTP, inspección visual y diff/manifiesto de archivos.
- [ ] Registrar resultados exactos y pendientes reales; entregar pasos para laptop de amigo.

## Execution ledger
- Discovery AGY: terminó con error de hook global; no evidencia de herramientas usable. Cambio autorizado a Scout/Implementer de Codex, sin cambiar configuración global.
- Especificación/plan: registrados con autorización explícita de ejecución continua del usuario; no se agrega una aprobación intermedia.
- Task 1: completada tras correcciones de galería, CLI, tracking, sesión, runtime, HUD y aislamiento de pruebas. 84 tests aprobados en entorno CPU sin Torch, smoke CUDA de imagen/texto finito y firmware Nano compilado.
- Task 2: completada. React 15 tests/build/typecheck; fotografía y WebSocket reales por localhost en modo mock; cajas/ACK y geometría 320–1280 px comprobados.
- Task 3: completada. Guías, CI y reporte; revisión final independiente sin defectos Critical/Important abiertos. Publicación futura y pruebas físicas no realizadas.
