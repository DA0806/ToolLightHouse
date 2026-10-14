# ToolLighthouse: Plan de Implementación de Configuración y Calibración

> **For AGY Workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development and test-driven-development for bounded implementation and verification. Follow the three sequential assignments outlined below. Do not commit/push without explicit authorization at each stage; maintain git-revertible artifacts.

**Goal:** Transformar ToolLighthouse en un sistema completamente configurable y calibrable desde el dashboard web React móvil/escritorio, permitiendo gestionar las 9 categorías, descripciones, fotos de referencia opcionales, modo híbrido parcial, calibración ROI de bandeja, mantenimiento Arduino Nano y preparación segura en primer arranque, sin editar código ni reiniciar manualmente FastAPI.

**Architecture:** Fuente de verdad en Python (FastAPI), configuración persistida con escritura atómica y versión activa en memoria, soporte para galería híbrida parcial, worker de reconstrucción asíncrona de embeddings sin bloqueo de asyncio, modo de preparación seguro frente a recursos faltantes, modo de mantenimiento exclusivo de hardware serial y frontend modular React + TypeScript con navegación adaptable móvil/escritorio.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, OpenCV, Pillow, NumPy, SentenceTransformers (EmbeddingGemma 2 opcional), PySerial, React 19, TypeScript 7.0.2, Vite 8, Vitest, Arduino C++ (firmware Nano).

**Spec Reference:** `docs/superpowers/specs/2026-10-14-dashboard-configuration-design.md`

---

## Restricciones y Reglas Globales de Ejecución

1. **Sin Cambios de Modelo:** Mantener `google/embeddinggemma-2` local; no agregar librerías de IA externas ni alterar pesos.
2. **Nueve Categorías Invariantes:** Exactamente 9 clases con IDs estables arbitrarios (no forzados a `tool_1..tool_9`), bits 0 a 8 y pines D2 a D10 exclusivos sin duplicados. Separación estricta de `display_name` (dashboard) y `name` (clasificación).
3. **Modo Predeterminado Solo Texto:** El sistema debe operar inmediatamente en zero-shot textual sin obligar a tomar fotografías.
4. **Híbrido Parcial Tolerante:** Categorías con imágenes combinan embeddings; categorías sin imágenes preservan su prototipo textual.
5. **No Bloquear el Event Loop:** Las operaciones GPU de reconstrucción deben correr en hilos secundarios o executors dedicados.
6. **Seguridad y Cero Fugas de Secretos:** Nombres de archivo sanitizados con UUID, verificación de path traversal (`data/references/...`), exportaciones libres de claves y rutas absolutas locales.
7. **Veracidad de Hardware:** No reportar estados confirmados de LEDs o conexiones sin confirmación real por protocolo `ACK:N`. Pruebas de simulación deben identificarse como simuladas.
8. **Ejecución de Comandos Shell:** Recordar prefijar comandos con `rtk` cuando corresponda (`rtk .\.venv\Scripts\python.exe -m pytest`, `rtk npm test`).

---

## Asignación 1: Backend, Configuración, Ciclo de Vida y Seguridad [x] COMPLETADO

**Estado:** 100% COMPLETADO Y VERIFICADO (166 tests passed, 1 skipped en pytest).
La reparación final añade regresiones persistentes para expiración durante
inferencia y escrituras transaccionales.
**Resumen:**
- ConfigStore atómico versionado con backup y recuperación automática de corrupción (`tests/test_config_persistence.py`).
- Autenticación administrativa segregada con `X-Admin-Key` (`tests/test_admin_auth.py`).
- Galería híbrida parcial para 0 a 9 categorías con fotos (`tests/test_partial_hybrid_gallery.py`).
- Modo de preparación seguro y honesto en arranque (`tests/test_preparation_mode.py`).
- Diagnóstico Arduino y pruebas de mantenimiento de un solo LED con auto-off $\le 3.0\,\text{s}$ (`tests/test_arduino_maintenance.py`).
- Subida de referencias con contención de paths y calibración ROI (`tests/test_uploads_and_roi.py`).
- Hot-apply transaccional con cerca de generación y rollback (`tests/test_hot_apply.py`).
- Carreras de inferencia diferida y desvinculación serial protegidas (`tests/test_delayed_inference_race.py`).
- Ciclo de vida integral e2e de configuración (`tests/test_e2e_configuration_lifecycle.py`).

### Tarea 1.1: Persistencia de Configuración, Versionado Atómico y Export/Import Portable
**Archivos:**
- Modificar: `src/toollighthouse/config.py`
- Test: `tests/test_config_persistence.py`

**Paso 1: Escribir tests de persistencia y exportación**
- Crear `tests/test_config_persistence.py` validando:
  - Guardado atómico en `config/settings.json` y `config/inventory.json` mediante archivo temporal y reemplazo atómico (`os.replace`).
  - Detección de colisión si se intentan asignar LEDs duplicados o pines incoherentes.
  - Creación de backup `config/settings.backup.json` antes de sobreescritura para rollback.
  - Exportación portable: omisión de `pairing_key`, normalización de rutas a relativas, advertencia de omisión de fotos.
  - Importación y validación de esquema de archivo exportado.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_config_persistence.py`
- Esperado: FAIL (módulos o métodos de persistencia atómica no implementados).

**Paso 3: Implementar métodos en `config.py`**
- Agregar métodos `ConfigStore`:
  - `save_atomic(settings: Settings, inventory: Inventory, target_dir: Path)`
  - `create_backup(target_dir: Path)`
  - `rollback_from_backup(target_dir: Path) -> tuple[Settings, Inventory]`
  - `export_portable_dict(settings: Settings, inventory: Inventory) -> dict`
  - `import_portable_dict(data: dict) -> tuple[Settings, Inventory]`

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_config_persistence.py`
- Esperado: PASS.

---

### Tarea 1.2: Autorización Administrativa Segregada y Control de Roles
**Archivos:**
- Modificar: `src/toollighthouse/api/app.py`
- Test: `tests/test_admin_auth.py`

**Paso 1: Escribir tests de roles y permisos admin**
- Crear `tests/test_admin_auth.py` comprobando:
  - Creación de sesión con rol `admin` exigiendo clave de emparejamiento / admin key.
  - Rutas de lectura permitidas a `reader`, `controller` y `admin`.
  - Rutas de inspección (`/v1/analyze/photo`) permitidas a `controller` y `admin`.
  - Rutas de mutación (cambio de inventario, subida de fotos, reconstrucción de galería, mantenimiento Arduino) restringidas exclusivamente a `admin`. Peticiones desde `controller` o `reader` deben recibir `403 Forbidden`.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_admin_auth.py`
- Esperado: FAIL.

**Paso 3: Implementar validación en `SessionStore` y dependencias FastAPI**
- Actualizar `SessionStore` en `app.py` para soportar rol `admin`.
- Definir decoradores/verificadores de dependencia `require_role("admin")`.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_admin_auth.py`
- Esperado: PASS.

---

### Tarea 1.3: Galería Híbrida Parcial, Huella Digital Robusta y Background Rebuild Worker
**Archivos:**
- Modificar: `src/toollighthouse/gallery.py`
- Modificar: `src/toollighthouse/embeddings.py`
- Test: `tests/test_partial_hybrid_gallery.py`

**Paso 1: Escribir tests de modo híbrido parcial**
- Crear `tests/test_partial_hybrid_gallery.py` validando:
  - `build_gallery(mode="hybrid")` con 0, 1, 3 y 9 herramientas con fotos.
  - Para herramientas sin fotos en modo híbrido, el vector resultante es exactamente el embedding textual normalizado.
  - Para herramientas con fotos, el vector es `_unit(w * text + (1 - w) * img)`.
  - `references_fingerprint` y `load_validated_gallery` soportan carpetas de fotos parciales sin lanzar error de clases faltantes.
  - Reconstrucción asíncrona sin bloquear el event loop, reportando eventos de progreso.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_partial_hybrid_gallery.py`
- Esperado: FAIL (`build_gallery` rechaza imágenes que no cubran las 9 categorías).

**Paso 3: Adaptar lógica en `gallery.py` y `embeddings.py`**
- Flexibilizar validación de imágenes en modo híbrido.
- Implementar `GalleryRebuildWorker` que ejecute el cálculo de embeddings en background thread y emita callbacks de progreso.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_partial_hybrid_gallery.py`
- Esperado: PASS.

---

### Tarea 1.4: Modo de Preparación Seguro en Arranque y Diagnóstico del Sistema
**Archivos:**
- Modificar: `src/toollighthouse/pipeline.py`
- Modificar: `src/toollighthouse/api/app.py`
- Test: `tests/test_preparation_mode.py`

**Paso 1: Escribir tests de arranque sin recursos previos**
- Crear `tests/test_preparation_mode.py` comprobando:
  - Inicialización de `Pipeline` en modo `real` cuando `background.png` o `gallery.json` no existen en disco.
  - El sistema entra en estado `preparation` sin colapsar el proceso (`no exception`).
  - `GET /v1/system/readiness` devuelve desglose claro (`backend_ok: True`, `model_present: True/False`, `calibration_ready: False`, `gallery_ready: False`, `can_activate_real: False`).
  - Intento de inferencia en modo preparación devuelve `409 Conflict` con mensaje explicativo para el usuario.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_preparation_mode.py`
- Esperado: FAIL (`Pipeline` lanza `FileNotFoundError`).

**Paso 3: Refactorizar inicialización tolerante en `Pipeline` y endpoints de readiness**
- Permitir segmentador y clasificador en modo no preparado (lazy/placeholder).
- Añadir endpoints `GET /v1/system/readiness` y `POST /v1/system/mode`.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_preparation_mode.py`
- Esperado: PASS.

---

### Tarea 1.5: Diagnóstico Serial, Enumeración de Puertos y Modo de Mantenimiento Arduino
**Archivos:**
- Modificar: `src/toollighthouse/serial_transport.py`
- Modificar: `src/toollighthouse/api/app.py`
- Test: `tests/test_arduino_maintenance.py`

**Paso 1: Escribir tests de mantenimiento serial y pruebas individuales**
- Crear `tests/test_arduino_maintenance.py` validando:
  - `list_com_ports()` devuelve lista estructurada usando `serial.tools.list_ports`.
  - Entrada a modo de mantenimiento envía `MASK:0` y bloquea inferencia.
  - Endpoint de prueba individual de LED (`/v1/arduino/maintenance/test-led/{idx}`) activa únicamente el bit correspondiente (`1 << idx`).
  - Temporizador de apagado automático devuelve `MASK:0` tras expiración (ej. 3.0 segundos).
  - Rechazo de prueba simultánea de los 9 LEDs como diagnóstico inicial.
  - Reporte estricto de ACK: `status()["confirmed_mask"]` solo refleja datos recibidos del dispositivo.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_arduino_maintenance.py`
- Esperado: FAIL.

**Paso 3: Implementar controladores y rutas de mantenimiento en `serial_transport.py` y `app.py`**
- Agregar soporte dinámico para cambio de puerto y reconexión en `SerialTransport`.
- Crear rutas `/v1/arduino/*` protegidas con rol `admin`.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_arduino_maintenance.py`
- Esperado: PASS.

---

### Tarea 1.6: Subida Segura de Referencias, Gestión de Fotos y Calibración ROI
**Archivos:**
- Modificar: `src/toollighthouse/api/app.py`
- Test: `tests/test_uploads_and_roi.py`

**Paso 1: Escribir tests de subida de fotos y ROI**
- Crear `tests/test_uploads_and_roi.py` validando:
  - Subida de fotos en `POST /v1/tools/{class_id}/references` con validación estricta de extensiones y decodificación con Pillow.
  - Prevención de path traversal: rechazo de `class_id` o nombres con `..` o separadores de ruta.
  - Eliminación de fotos con `DELETE /v1/tools/{class_id}/references/{image_id}`.
  - Actualización de ROI vía `PUT /v1/calibration/roi` validando coordenadas dentro de las dimensiones de la imagen de fondo.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_uploads_and_roi.py`
- Esperado: FAIL.

**Paso 3: Implementar endpoints y almacenamiento en `app.py`**
- Guardar fotos en `data/references/{class_id}/{uuid}.jpg`.
- Implementar actualización de ROI desacoplada del upload de fondo.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_uploads_and_roi.py`
- Esperado: PASS.

---

### Tarea 1.7: Aplicación en Caliente Transaccional, Rollback y Prevención de Inferencias Obsoletas
**Archivos:**
- Modificar: `src/toollighthouse/api/app.py`
- Test: `tests/test_hot_apply.py`

**Paso 1: Escribir tests de hot-apply y reversión**
- Crear `tests/test_hot_apply.py` comprobando:
  - `POST /v1/config/apply` valida configuración, compila galería en staging si es necesario, y realiza intercambio atómico.
  - Si solo cambian nombres visibles o ubicaciones, se evita la reconstrucción de embeddings.
  - Durante el intercambio: LEDs se apagan (`MASK:0`), se incrementa `scene_generation`, se limpia el caché de predicciones y se invalidan tareas en curso.
  - En caso de error en compilación de galería, la versión activa previa permanece operativa y se conserva el estado limpio.
  - `POST /v1/config/rollback` revierte al estado previo guardado en backup.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_hot_apply.py`
- Esperado: FAIL.

**Paso 3: Implementar ciclo de vida de hot-apply en `app.py`**
- Coordinar locks, fences de generación y actualización segura de `Pipeline`.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_hot_apply.py`
- Esperado: PASS.

---

## Asignación 2: Dashboard Móvil, Componentes e Integración

**Objetivo:** Desarrollar la interfaz responsiva en React con navegación móvil/escritorio, editor visual de las 9 herramientas, selector de modo de reconocimiento con switch intuitivo, visor y canvas interactivo de ROI, panel de diagnóstico de Arduino y asistente de primera configuración.

### Tarea 2.1: Navegación Adaptable y Estructura por Pestañas
**Archivos:**
- Modificar: `frontend/src/App.tsx`
- Modificar: `frontend/src/styles.css`
- Modificar: `frontend/src/types.ts`
- Test: `frontend/src/App.test.tsx`

**Paso 1: Escribir tests de navegación**
- Añadir tests en `App.test.tsx` comprobando que las 5 secciones existen y responden a la selección:
  - `Inspección`, `Herramientas`, `Calibración`, `Arduino`, `Sistema`.
  - Presencia del botón persistente "Volver a Inspección".
  - Renderizado de barra inferior en vista móvil.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL (rutas de navegación no encontradas).

**Paso 3: Implementar navegación y layout responsivo**
- Crear componentes de shell con barra de navegación inferior en móviles (`<nav className="bottom-nav">`) y selector en escritorio.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.2: Extensión del Cliente API Frontend
**Archivos:**
- Modificar: `frontend/src/api.ts`
- Modificar: `frontend/src/types.ts`
- Test: `frontend/src/api.test.ts`

**Paso 1: Escribir tests para nuevos endpoints**
- Testear funciones de API en `api.test.ts`:
  - `getConfig()`, `saveConfig()`, `applyConfig()`, `rollbackConfig()`, `exportConfig()`, `importConfig()`.
  - `getReadiness()`, `downloadModel()`.
  - `uploadReferencePhoto()`, `deleteReferencePhoto()`.
  - `updateRoi()`.
  - `getComPorts()`, `connectArduino()`, `disconnectArduino()`, `enterMaintenanceMode()`, `testLed()`, `allOff()`.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL.

**Paso 3: Implementar llamadas en `api.ts` con manejo de errores claro**
- Agregar funciones tipadas y soporte para cabeceras de rol administrativo.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.3: Vista de Herramientas y Organizador Físico
**Archivos:**
- Crear: `frontend/src/components/ToolManager.tsx`
- Modificar: `frontend/src/styles.css`
- Test: `frontend/src/components/ToolManager.test.tsx`

**Paso 1: Escribir tests de edición de herramientas**
- Testear:
  - Renderizado de las 9 tarjetas.
  - Adición y eliminación de descripciones visuales.
  - Validación visual inmediata ante colisión de asignación de LEDs (0..8).
  - Apertura de modal/sección de fotos de referencia para subir y borrar miniaturas.
  - Cuadrícula del organizador físico donde pulsar una celda resalta la herramienta y LED vinculados.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL.

**Paso 3: Implementar componente `ToolManager`**
- Formulario accesible, guía visual breve de descripción, controles sencillos de `+` y `-`, y previsualización de referencias.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.4: Vista de Calibración, Modos de Reconocimiento y Canvas ROI
**Archivos:**
- Crear: `frontend/src/components/CalibrationView.tsx`
- Modificar: `frontend/src/styles.css`
- Test: `frontend/src/components/CalibrationView.test.tsx`

**Paso 1: Escribir tests de calibración y ROI**
- Testear:
  - Conmutador intuitivo "Mejorar con fotografías" para activar/desactivar modo híbrido.
  - Slider para ajuste de ponderación texto vs imagen (visible en opciones avanzadas).
  - Visualización del fondo actual de la bandeja.
  - Interacción de selección de ROI dibujando rectángulo sobre la imagen.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL.

**Paso 3: Implementar componente `CalibrationView`**
- Soporte para eventos táctiles y de ratón sobre SVG/canvas para definir coordenadas ROI normalizadas.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.5: Panel de Mantenimiento y Diagnóstico de Arduino Nano
**Archivos:**
- Crear: `frontend/src/components/ArduinoPanel.tsx`
- Modificar: `frontend/src/styles.css`
- Test: `frontend/src/components/ArduinoPanel.test.tsx`

**Paso 1: Escribir tests de diagnóstico de hardware**
- Testear:
  - Lista desplegable de puertos COM y botón de refrescar.
  - Botones conectar/desconectar con estado visual.
  - Activación obligatoria de "Modo Mantenimiento" antes de permitir pruebas de LEDs.
  - Nueve botones de prueba unitaria con temporizador regresivo visible.
  - Botón de emergencia "Apagar todos".
  - Mapa de conexiones a pines D2–D10.

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL.

**Paso 3: Implementar componente `ArduinoPanel`**
- Conexión con endpoints `/v1/arduino/*`, guía para Arduino IDE y bootloader.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.6: Panel de Sistema, Asistente de Primera Configuración y Export/Import
**Archivos:**
- Crear: `frontend/src/components/SystemPanel.tsx`
- Modificar: `frontend/src/styles.css`
- Test: `frontend/src/components/SystemPanel.test.tsx`

**Paso 1: Escribir tests del asistente inicial**
- Testear:
  - Checklist interactivo de 7 puntos (Backend, Python/CUDA, Modelo, Inventario, Galería, Calibración, Arduino).
  - Botón de descarga de modelo con barra de progreso reactiva a eventos WebSocket.
  - Exportación y carga de configuración portátil.
  - Resumen de versión activa vs guardada y botón "Guardar y aplicar".

**Paso 2: Ejecutar test para verificar fallo**
- Ejecutar: `rtk npm test`
- Esperado: FAIL.

**Paso 3: Implementar componente `SystemPanel`**
- Presentación limpia, badges de estado reales y diálogos de confirmación claros.

**Paso 4: Ejecutar test y verificar paso**
- Ejecutar: `rtk npm test`
- Esperado: PASS.

---

### Tarea 2.7: Integración Global, Build y Verificación Visual
**Archivos:**
- Modificar: `frontend/src/App.tsx`
- Modificar: `frontend/src/styles.css`

**Paso 1: Ejecutar suite completa frontend**
- Ejecutar: `rtk npm test`
- Esperado: Todos los tests pasan.

**Paso 2: Ejecutar build de producción**
- Ejecutar: `rtk npm run build`
- Esperado: Build exitoso en `frontend/dist/` sin advertencias de tipos (`tsc -b`).

---

## Asignación 3: Revisión Independiente, Verificación, Documentación y Cierre

**Objetivo:** Auditar la seguridad y concurrencia del sistema completo, ejecutar una prueba de extremo a extremo con datos simulados, actualizar la documentación técnica y de usuario, y dejar el repositorio preparado para entrega.

### Tarea 3.1: Prueba de Extremo a Extremo (E2E) con Datos Simulados
**Archivos:**
- Crear: `tests/test_e2e_configuration_lifecycle.py`

**Paso 1: Escribir prueba completa del ciclo de vida**
- Simular el flujo real completo de un usuario nuevo:
  1. Arranque en modo preparación sin galería ni fondo previo.
  2. Consulta de readiness e inicialización de inventario con 9 categorías personalizadas.
  3. Subida de fondo vacío y calibración de ROI.
  4. Subida de fotos de referencia para 2 herramientas (híbrido parcial).
  5. Acción "Guardar y aplicar", compilación de galería en background y activación en caliente.
  6. Análisis de fotografía con detección multiobjeto confirmada y reporte de ACK.
  7. Conexión serial simulada y prueba de LED en modo mantenimiento.
  8. Exportación de configuración portable e importación posterior.

**Paso 2: Ejecutar test E2E**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_e2e_configuration_lifecycle.py -v`
- Esperado: PASS.

---

### Tarea 3.2: Auditoría de Seguridad, Fugas y Regresiones de Concurrencia
**Archivos:**
- Revisar y verificar: `src/toollighthouse/api/app.py`, `src/toollighthouse/gallery.py`
- Test: `tests/test_api_security_regressions.py`

**Paso 1: Añadir casos de regresión de seguridad**
- Intentos de manipulación de archivos fuera de `data/references/` usando caracteres nulos o secuencias `../../`.
- Verificación de que una sesión que pierde el rol admin no puede alterar la configuración.
- Verificación de que un resultado de inferencia diferido no puede alterar LEDs tras la activación de una nueva configuración.

**Paso 2: Ejecutar suite de seguridad**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest tests/test_api_security_regressions.py tests/test_runtime_safety.py`
- Esperado: PASS.

---

### Tarea 3.3: Actualización Exhaustiva de Documentación
**Archivos:**
- Modificar: `README.md`
- Modificar: `docs/API.md`
- Modificar: `docs/COMMANDS.md`
- Crear: `docs/DASHBOARD_CONFIGURATION.md`

**Paso 1: Documentar nuevas capacidades**
- `docs/DASHBOARD_CONFIGURATION.md`: Guía paso a paso para configurar el sistema desde el teléfono (primer inicio, editor de herramientas, descripciones, carga de fotos, ROI, mantenimiento Arduino).
- `docs/API.md`: Contratos actualizados de los nuevos endpoints REST y eventos WebSocket.
- `docs/COMMANDS.md`: Comandos PowerShell y flags CLI actualizados.
- `README.md`: Resumen de las 5 secciones del dashboard, instrucciones de despliegue local y notas sobre lo que requiere hardware físico real.

---

### Tarea 3.4: Verificación Integral de CI y Preparación de Entrega
**Archivos:**
- Revisar: `.github/workflows/ci.yml`

**Paso 1: Ejecutar verificación backend completa**
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m pytest -q`
- Ejecutar: `rtk .\.venv\Scripts\python.exe -m compileall -q src`
- Esperado: Toda la suite verde, compilación limpia.

**Paso 2: Ejecutar verificación frontend completa**
- En `frontend`: `rtk npm test` y `rtk npm run build`.
- Esperado: Tests y bundle de producción limpios.

**Paso 3: Verificación de estado Git**
- Comprobar que no hay archivos temporales, secretos, credenciales ni descargas pesadas en la raíz del repositorio.
- Confirmar que los cambios están listos para revisión y posterior commit/push cuando sea ordenado.
