# ToolLighthouse: Diseño Técnico y Arquitectura de Integración Multicámara y Dashboard Unificado (Revisión Final)

**Fecha:** 2026-10-14
**Fase:** Fases B–H Aprobadas — Integración Completa Multicámara y Dashboard Unificado
**Rol:** AGY Discovery and Technical-Design Staffer
**Rama de trabajo:** `main` (Auditoría/Diseño) $\rightarrow$ Implementación en `feat/unified-dashboard-multicamera`
**Repositorio Principal:** `https://github.com/DA0806/ToolLightHouse`
**Repositorio de Referencia Conceptual:** `https://github.com/Hopeelex/keytool-tracker` (Autor conceptual y compañero de equipo: `adavid` / `Hopeelex`)

> [!NOTE] **Aprobación de Arquitectura e Integración Completa (Fases B–H)**
> El usuario aprobó formalmente esta arquitectura general e instruyó la incorporación de correcciones normativas: levantamiento de la restricción de copia para integración del equipo (autorizada por el compañero adavid / Hopeelex), dominios separados de frescura temporal (navegador vs servidor), precisión en el contrato ACK de Arduino (digitalWrite D2–D10, no exclusivamente PORTD) y límites configurables de vista previa.

---

## 1. Auditoría del Repositorio y Verificación Factual de Línea Base

### 1.1 Verificación Criptográfica de HEADs
Se verificaron los identificadores SHA-1 de los repositorios frente a los commits de referencia:

| Repositorio | Ref Inicial | HEAD Local Actual | HEAD Remoto (`origin/main`) | Estado de Sincronización |
|---|---|---|---|---|
| **ToolLightHouse** | `ba20878c7` | `ba20878c7f66e9428006baf8a734c6fd7ff6734c` | `ba20878c7f66e9428006baf8a734c6fd7ff6734c` | Sincronizado, árbol limpio |
| **KeyTool Tracker** (ref) | `9552053c2` | — *(Clon sandbox temporal en `$env:TEMP/keytool-tracker-ref`)* | `9552053c265bc840d7fde666ea9a952518eb4583` | Sincronizado (`adavid`) |

*Aislamiento:* El repositorio de referencia conceptual `keytool-tracker` se clonó exclusivamente en un directorio temporal fuera del workspace (`C:\Users\Diego\AppData\Local\Temp\keytool-tracker-ref`) en modo de solo lectura; no ha sido alterado ni referenciado en el historial del producto.

---

### 1.2 Auditoría Legal, Atribución y Licencias
1. **Código fuente y recursos de `KeyTool Tracker` (`adavid` / `Hopeelex`):**
   - El desarrollador de `Hopeelex/keytool-tracker` es integrante del equipo y otorgó autorización personal expresa para reutilizar e integrar su trabajo (componentes React, tokens visuales, clases de estilo, experiencia QR/WebRTC y diseño de inventario 3×3) en ToolLightHouse para este proyecto universitario.
   - **Actualización normativa de Fase A:** Se elimina formalmente la restricción de «no copia» establecida en la auditoría inicial. Se autoriza la migración de componentes JSX a TSX y su adaptación directa en `frontend/`, manteniendo la atribución a `adavid` en README y documentación de procedencia ([`frontend/src/PROVENANCE.md`](file:///frontend/src/PROVENANCE.md)).
   - Esta autorización aplica exclusivamente a la integración del proyecto del equipo y no implica autorización para relicenciar arbitrariamente el repositorio de origen bajo otras licencias. El repositorio original no se modifica.
2. **Logotipo y Gráficos:**
   - El archivo `public/logo.png` de la referencia contiene la marca gráfica "Key". No se incorporará en el repositorio de producto; en su lugar, se implementará un componente SVG neutro e independiente para ToolLighthouse.
3. **Tipografía `Albert Sans`:**
   - Diseñada por Andreas Rasmussen, bajo **SIL Open Font License 1.1 (OFL-1.1)**. Se empaquetan los binarios WOFF2 directamente en `frontend/public/fonts/` con `@font-face` local para operación 100% offline.
4. **Iconos `Lucide React`:**
   - Licencia permisiva **ISC** (compatible con MIT). Uso autorizado en `frontend/`.
5. **Modelo `EmbeddingGemma 2` — Licencia y Metadatos Exactos:**
   - **Tarjeta de modelo local (`models/embeddinggemma-2/README.md`):**
     - Frontmatter YAML: `license: apache-2.0`.
     - Cabecera: `License: Apache 2.0` con hipervínculo a `https://ai.google.dev/gemma/docs/gemma_4_license`.
     - Términos de gobernanza: Sujeto a la **Gemma Prohibited Use Policy** (`https://ai.google.dev/gemma/prohibited_use_policy`).
     - Metadatos locales de arquitectura ([`models/embeddinggemma-2/config.json`](file:///D:/Key%20Institute/Ciclo%203/ToolLightHouse/ToolLightHouse%20Project/models/embeddinggemma-2/config.json)): `"architectures": ["EmbeddingGemma2Model"]`, `"model_type": "embedding_gemma2"`, `"transformers_version": "5.18.0.dev0"`.
     - Metadatos Sentence-Transformers ([`models/embeddinggemma-2/config_sentence_transformers.json`](file:///D:/Key%20Institute/Ciclo%203/ToolLightHouse/ToolLightHouse%20Project/models/embeddinggemma-2/config_sentence_transformers.json)): `"sentence_transformers": "6.1.0"`, dimensión `768`, similitud `cosine`.

---

### 1.3 Validación Factual de Entorno: GPU y Suites de Pruebas

#### Validación de GPU Local (Comando Real y Salida Factual)
```powershell
rtk .venv\Scripts\python.exe -u probar_gemma.py
```
**Salida exacta:**
```text
GPU: NVIDIA GeForce RTX 3060
Loading weights: 100%|##########| 624/624 [00:00<00:00, 847.59it/s]
Forma: (768,)
Valores válidos: True
Modelo cargado correctamente
```
> [!NOTE] **Delimitación de Hardware GPU**
> El host de desarrollo actual cuenta con una **NVIDIA GeForce RTX 3060**. Las plataformas de destino son **RTX 4060 (8 GB)** y **RTX 5050 (8 GB)**. El modelo ha sido verificado en la GPU RTX 3060 local, pero permanece **físicamente no verificado en el hardware de destino (RTX 4060/5050)**.

#### Diagnóstico Factual de Frontend y Baseline de Timeout
- **Ejecución completa concurrente (`rtk npm --prefix frontend run test`):**
  - Resultado: 9 suites aprobadas (67 tests), 1 fallo por timeout en `src/e2e-dashboard-flow.test.tsx` (`Error: Test timed out in 5000ms (took 6465ms)`).
- **Ejecución aislada:**
  ```powershell
  rtk npm --prefix frontend run test -- src/e2e-dashboard-flow.test.tsx
  ```
  **Resultado:** `Test Files 1 passed (1)`, `Tests 1 passed (1)`, `Duration 3.83s`.
- **Diagnóstico:** El paso aislado confirma que el test puede ejecutarse en 3.83s, lo que sugiere contención de CPU en la suite completa (10 entornos JSDOM simultáneos totalizando 50.65s de creación), pero no prueba de forma concluyente la causa raíz ni garantiza que no existan operaciones asíncronas lentas en el flujo.
- **Acción:** **No se alteran archivos de producto.** Se pospone la corrección a una investigación focalizada en Fase B, evitando timeouts globales arbitrarios.

---

### 1.4 Estado de la Línea Base: Concesión en `app.py`
En el código base actual ([`src/toollighthouse/api/app.py#L386-L395`](file:///D:/Key%20Institute/Ciclo%203/ToolLightHouse/ToolLightHouse%20Project/src/toollighthouse/api/app.py#L386-L395)):
- El backend **ya acepta** una sesión con rol `admin` para endpoints que requieren `role="controller"`.
- Si el lease está vacante (`store.controller_id is None`), la sesión `admin` **ya adquiere el lease de controlador automáticamente**, incrementando `store.lease_generation += 1`.
- Si el lease ya está ocupado por otra sesión, `app.py` retorna `409 Conflict ("another controller session is active")`.
- **Deficiencia real a corregir:** El administrador no puede inspeccionar si otro controlador quedó activo (por ejemplo, tras una recarga incompleta de página o cambio de tablet), y carece de un endpoint explícito de expropiación (`takeover`) para revocar ordenadamente la sesión previa sin reiniciar el backend.

---

## 2. Concesión Unificada de Control Físico (Unified Control Lease)

### 2.1 Especificación Rigurosa de Roles y Prevalencia
El sistema define cuatro roles mutuamente excluyentes:
- `admin`: Facultades para configurar herramientas, galería, calibración, mantenimiento de Arduino y solicitar control físico.
- `controller`: Facultades para operar la inspección y solicitar control físico.
- `reader`: Observador pasivo de resultados y métricas. **Denegación explícita de control físico.**
- `camera_transmitter`: Emisor de video vinculado a una sesión autorizada. **Denegación explícita de control físico.**


### 2.2 Máquina de Estados Normativa de Concesión, Expropiación y Heartbeat

Para evitar condiciones de carrera donde la liberación del cerrojo durante el teardown permita que un takeover concurrente sea sobrescrito a ciegas, el sistema implementa una **máquina de estados normativa** con dos primitivas de sincronización:
1. `transition_mutex` (`asyncio.Lock`): Serializa de manera estricta los procesos de expropiación y conmutación pesada.
2. `state_lock` (`threading.RLock`): Protege exclusivamente las mutaciones atómicas en memoria y validaciones de estado inmediatas.

```python
# src/toollighthouse/api/sessions.py

@dataclass
class PhysicalControlLease:
    holder_session_id: str
    role: str                       # 'admin' | 'controller' exclusivamente
    device_id: str
    acquired_at: float
    lease_expires_at: float
    lease_generation: int

class UnifiedSessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.lease: PhysicalControlLease | None = None
        self.lease_generation: int = 0
        self.transition_state: str = "idle"       # "idle" | "transitioning" | "failed"
        self.current_transition_id: str | None = None
        self.transition_mutex = asyncio.Lock()
        self.state_lock = threading.RLock()

    def acquire_vacant_lease(self, session_id: str) -> PhysicalControlLease:
        """Adquiere el lease ÚNICAMENTE si está vacante o expirado y no hay transición pendiente."""
        with self.state_lock:
            if self.transition_state == "transitioning":
                raise PermissionError("Transición de control en curso; adquisición bloqueada temporalmente.")

            session = self._get_valid_session(session_id)
            if session.role not in {"controller", "admin"}:
                raise PermissionError(f"El rol '{session.role}' no tiene permiso para adquirir el control físico.")

            now = time.monotonic()
            if self.lease is not None and self.lease.lease_expires_at > now:
                if self.lease.holder_session_id == session_id:
                    self.lease.lease_expires_at = now + self.settings.lease_ttl_seconds
                    return self.lease
                raise PermissionError("El control físico está ocupado por otro operador activo.")

            self.lease_generation += 1
            self.lease = PhysicalControlLease(
                holder_session_id=session.session_id,
                role=session.role,
                device_id=session.device_id,
                acquired_at=now,
                lease_expires_at=now + self.settings.lease_ttl_seconds,
                lease_generation=self.lease_generation,
            )
            self.transition_state = "idle"
            return self.lease

    async def takeover_control_lease(
        self,
        admin_session_id: str,
        teardown_callback
    ) -> PhysicalControlLease:
        """
        Expropiación EXPLÍCITA ejecutada con exclusión mutua de transición:
        1. Serializada por transition_mutex (asyncio.Lock).
        2. Bajo state_lock: valida rol 'admin', rechaza si transition_state == 'transitioning',
           asigna transition_id, marca transition_state = 'transitioning', incrementa lease_generation,
           revoca/limpia lease actual, e invalida escenas/fuentes/invitaciones.
        3. Teardown I/O ejecutado FUERA de state_lock.
        4. Re-adquisición de state_lock: revalida transition_id, generación y validez de la sesión admin.
           Si exitoso, asigna nuevo lease. Si falla o es revocada, deja lease = None y transition_state = 'failed'.
        """
        async with self.transition_mutex:
            # --- FASE 1: Validación y bloqueo atómico de transición ---
            with self.state_lock:
                session = self._get_valid_session(admin_session_id)
                if session.role != "admin":
                    raise PermissionError("Únicamente una sesión administrativa puede forzar la toma de control.")
                if self.transition_state == "transitioning":
                    raise PermissionError("Otra transición de control ya se encuentra en curso.")

                transition_id = str(uuid.uuid4())
                self.current_transition_id = transition_id
                self.transition_state = "transitioning"
                self.lease_generation += 1
                captured_generation = self.lease_generation

                # Desasigna de inmediato al titular anterior e invalida trabajo en curso
                self.lease = None

            # --- FASE 2: Teardown I/O y apagado FUERA de state_lock ---
            teardown_ok = False
            try:
                # Callback ejecuta: parada de inferencia, blackout seguro de Arduino (MASK:0)
                # y liberación de fuentes de video anteriores.
                await teardown_callback(new_generation=captured_generation)
                teardown_ok = True
            except Exception:
                teardown_ok = False

            # --- FASE 3: Revalidación y compromiso bajo state_lock ---
            with self.state_lock:
                now = time.monotonic()
                session_still_valid = (
                    admin_session_id in self.sessions
                    and self.sessions[admin_session_id].expires_at > time.time()
                    and self.sessions[admin_session_id].role == "admin"
                )

                if (
                    teardown_ok
                    and self.current_transition_id == transition_id
                    and self.lease_generation == captured_generation
                    and session_still_valid
                ):
                    self.lease = PhysicalControlLease(
                        holder_session_id=admin_session_id,
                        role="admin",
                        device_id=self.sessions[admin_session_id].device_id,
                        acquired_at=now,
                        lease_expires_at=now + self.settings.lease_ttl_seconds,
                        lease_generation=captured_generation,
                    )
                    self.transition_state = "idle"
                    self.current_transition_id = None
                    return self.lease
                else:
                    # Ante fallo o requiriente desautorizado, queda sin lease activo y en fallo
                    self.lease = None
                    self.transition_state = "failed"
                    self.current_transition_id = None
                    raise RuntimeError("Fallo durante la expropiación o sesión de administrador invalidada.")

    def heartbeat(self, session_id: str, ticket: str) -> bool:
        """
        Renovación periódica del lease activo (heartbeat).
        REVALIDA que:
        1. No haya transición pendiente (transition_state != 'transitioning').
        2. La sesión autenticada exista y no esté expirada.
        3. El ticket coincida con seguridad criptográfica (compare_digest).
        4. El solicitante sea el titular actual del lease físico.
        Nunca renueva un rol revocado, inválido o expirado.
        """
        with self.state_lock:
            if self.transition_state == "transitioning":
                return False

            now = time.monotonic()
            session = self.sessions.get(session_id)
            if not session or session.expires_at <= time.time() or session.role not in {"controller", "admin"}:
                return False
            if not secrets.compare_digest(session.ticket, ticket or ""):
                return False
            if self.lease and self.lease.holder_session_id == session_id:
                if self.lease.lease_expires_at > now:
                    self.lease.lease_expires_at = now + self.settings.lease_ttl_seconds
                    session.expires_at = time.time() + self.settings.session_ttl_seconds
                    return True
            return False
```

---

## 3. Registro Central de Fuentes de Cámara (Camera Source Registry)

Se define el subsistema en `src/toollighthouse/camera_registry.py` para gestionar las 5 fuentes del sistema:

### 3.1 Las 5 Fuentes Admitidas y Descriptores
1. `remote_phone`: Transmisión WebRTC P2P desde teléfono móvil (audio desactivado).
2. `laptop_builtin`: Cámara integrada de la laptop servidora (OpenCV índice 0/1).
3. `usb_webcam`: Webcam USB externa conectada a la laptop (OpenCV enumerada).
4. `operator_device`: Cámara local del navegador operador (`getUserMedia` cliente).
5. `manual_upload`: Fotografía estática subida manualmente (JPEG/PNG/WebP).

### 3.2 Descriptor y Modelo de Datos de Fuente
```python
@dataclass
class CameraSourceDescriptor:
    source_id: str                      # ej. "remote_phone", "laptop_builtin", "usb_webcam_1"
    display_name: str                   # Nombre configurable por el usuario
    kind: str                           # "webrtc_remote" | "opencv_builtin" | "opencv_usb" | "browser_local" | "manual_upload"
    connection_type: str                # "webrtc" | "usb" | "builtin" | "browser" | "file"
    status: str                         # "active" | "standby" | "disconnected" | "error" | "needs_calibration"
    current_resolution: dict[str, int]  # {"width": 1920, "height": 1080}
    current_fps: float                  # FPS medido o reportado
    is_active: bool                     # Solo una fuente activa a la vez
    calibration_profile_available: bool # True si existe perfil confirmado para esta fuente y resolución
    last_error: str | None = None
```

### 3.3 Enumeración Segura de Cámaras OpenCV
- **Problema de contención:** Intentar abrir una cámara activa (`isOpened`) para sondearla interrumpe la captura activa de video en Windows (`cv2.CAP_DSHOW`).
- **Enumeración segura:**
  - El escaneo solo sondea índices que **no** coincidan con `current_index` de la cámara activa.
  - Se ejecuta en un hilo de trabajo en segundo plano para no bloquear el bucle de eventos de FastAPI.
  - Identidad estable mejor esfuerzo: Se almacena en la configuración el índice, resolución soportada y un nombre descriptivo asignado por el usuario (`user_label`), permitiendo re-asociar la cámara tras reinicios.

### 3.4 Ciclo de Adquisición OpenCV y Reconexión Acotada
- **Hilo único de lectura:** Un único proceso de adquisición por cámara local activa (`OpenCVCameraService._acquisition_loop`).
- **Desconexión y reconexión acotada:**
  - Si `capture.read()` falla, se reintenta hasta 3 veces con intervalos de 500 ms.
  - Si persiste el fallo, se libera inmediatamente el dispositivo (`capture.release()`), se marca el estado como `error` / `disconnected` y se notifica vía WebSocket.
  - **Siempre libera recursos ante fallo:** En cualquier bloque `finally` o excepción, `capture.release()` es garantizado para no bloquear el dispositivo en Windows.

---

## 4. Perfiles de Calibración Persistidos por Fuente de Imagen

Se reemplaza el esquema de fondo y ROI global único por un registro multi-perfil en `config/app_config.json`:

### 4.1 Esquema Persistido del Perfil de Calibración
```json
{
  "source_id": "remote_phone",
  "resolution": {"width": 1920, "height": 1080},
  "orientation": "normal",
  "crop": null,
  "processing_version": 2,
  "background_filename": "bg_remote_phone_1920x1080.jpg",
  "roi": [180, 120, 1560, 840],
  "calibrated_at": 1728935000.0,
  "status": "ready"
}
```

### 4.2 Migración de Configuración Legacy (Sin Auto-Asociación Silenciosa)
- Si el archivo preexistente contiene `settings.background_path` y `settings.roi`, el backend **no lo auto-asocia silenciosamente a `laptop_builtin`**. Asumir compatibilidad entre sensores sin verificación geométrica induciría errores graves de segmentación.
- Se migra como un perfil no vinculado (`source_id = "unbound_legacy"`) con estado explícito `status = "needs_confirmation"`.
- El operador debe confirmar expresamente en el dashboard a qué dispositivo físico corresponde dicho fondo y ROI antes de que pueda ser activado para inferencia.
- Los archivos de imagen originales en disco se preservan intactos sin sobreescritura.

### 4.3 Transformación Canónica Unificada
- **Principio de consistencia espacial:** Tanto la imagen de fondo vacía capturada como las imágenes de inferencia subsiguientes pasan por la **misma función de normalización y recorte**:
  `frame_normalized = apply_canonical_transform(raw_frame, orientation, crop)`.
- Si el usuario cambia la resolución del sensor o la orientación (de vertical a horizontal), el perfil queda invalidado de inmediato (`status = "incompatible"`), impidiendo inferencias con ROI desfasado hasta recalibrar.

### 4.4 Flujo de Calibración por Fuente
1. Seleccionar la fuente en el dashboard.
2. Solicitar al operador despejar la bandeja y pulsar «Capturar fondo vacío».
3. Si la fuente es local (OpenCV), el fotograma se adquiere directamente de la memoria del servidor. Si es remota (WebRTC), la tablet envía el frame congelado.
4. El operador dibuja el recuadro ROI sobre el lienzo interactivo.
5. El cliente traduce las coordenadas de pantalla a coordenadas absolutas del sensor mediante `mapBoxToViewport` de [`frontend/src/geometry.ts`](file:///D:/Key%20Institute/Ciclo%203/ToolLightHouse/ToolLightHouse%20Project/frontend/src/geometry.ts).
6. El backend valida que el ROI esté completamente contenido dentro de las dimensiones efectivas y persiste el perfil atómicamente.

---

## 5. Inferencia Bounded, Cerca Tupla y Tiempo Monótono

### 5.1 La Tupla de Fencing de Inferencia
Cada fotograma enviado a inferencia y cada comando enviado a Arduino queda sellado por una tupla estricta:
$$\mathcal{T} = (\text{frame\_id}, \text{source\_id}, \text{source\_version}, \text{config\_version}, \text{lease\_generation}, \text{scene\_generation})$$

### 5.2 Medición de Frescura Temporal: Dominios Separados de Navegador y Servidor
> [!CAUTION] **Prohibición de Comparar Relojes de Pared y Desfase Monótono entre Dispositivos**
> El reloj del sistema del teléfono móvil **nunca se compara** con el reloj del servidor para calcular la edad del frame (riesgo de desfases de zona horaria, drift de reloj o falta de sincronización NTP). Asimismo, los relojes monótonos de dispositivos distintos (`performance.now()` en el navegador vs `time.monotonic()` en Python) pertenecen a orígenes independientes y **nunca deben compararse directamente**.
>
> **Corrección normativa de dominios de frescura:**
> 1. `arrival_monotonic = time.monotonic()` en FastAPI mide únicamente el tiempo transcurrido desde que el fotograma ingresó al backend; no mide el retardo acumulado previamente en el navegador o en el transporte WebRTC.
> 2. Por tanto, el navegador operador implementa sus propios controles de frescura mediante `performance.now()`, identificadores de frame y secuencias de captura monotónicas locales.
> 3. En el backend, la expiración por `frame_max_age_seconds` (predeterminado 1.0 s) se evalúa estrictamente sobre `arrival_monotonic`:
> `if (time.monotonic() - arrival_monotonic) > max_age: descartar_frame()`

### 5.3 Inferencia Bounded (One-Flight) y Revalidación de Arduino
- **Worker de una sola tarea en vuelo:** `LatestInferenceWorker` ejecuta como máximo una inferencia en GPU. Si un nuevo fotograma llega mientras la GPU está ocupada, se coloca en un slot de tamaño 1 reemplazando cualquier fotograma anterior en espera (latest-only).
- **Revalidación tardía de hardware:** Antes de transmitir la máscara de LEDs (`serial.send_mask(mask)`), el backend revalida que:
  1. `lease_generation == current_lease_generation`
  2. `scene_generation == current_scene_generation`
  3. `source_version == current_source_version`
  4. El tiempo monótono no haya excedido el deadline.
- Si cualquiera cambió (por ejemplo, el teléfono se desconectó o la tablet cambió de cámara), el resultado se descarta en silencio y **no se tocan las salidas físicas de Arduino**.

### 5.4 Recuperación de Imagen Congelada Real en Servidor
- Cuando el operador presiona «Escanear herramientas» en una cámara local de OpenCV:
  - La laptop toma el frame de resolución nativa directamente del buffer `_latest_frame` en RAM.
  - **Cero transferencia de subida/bajada por la red Wi-Fi.**
  - El servidor devuelve al dashboard la imagen congelada en JPEG (o URL de recurso temporal) junto con las coordenadas de detección, garantizando que los recuadros se dibujen exactamente sobre la imagen procesada por la IA.

---

## 6. Especificación de Endpoints y Contratos de API

Todos los endpoints requieren autenticación mediante encabezados HTTP (`X-Session-Id`, `X-Session-Ticket`), salvo las rutas públicas explícitas (`/health`, `/camera`). Cero tokens en query string.

### 6.1 Gestión de Fuentes y Configuración
- `GET /v1/camera/sources`
  - *Rol:* `reader`
  - *Respuesta (200):* `{"sources": [CameraSourceDescriptor], "active_source_id": "remote_phone", "source_version": 3}`
- `POST /v1/camera/sources/active`
  - *Rol:* `controller` o `admin` con lease
  - *Cuerpo:* `{"source_id": "usb_webcam_1"}`
  - *Respuesta (200):* `{"success": true, "active_source_id": "usb_webcam_1", "source_version": 4, "calibration_status": "ready"}`
  - *Errores:* `404` (fuente no existe), `409` (lease no pertenece al emisor).
- `POST /v1/camera/sources/{source_id}/settings`
  - *Rol:* `admin` Y poseedor del control físico activo (`current control lease`).
  - *Cuerpo:* `{"width": 1920, "height": 1080, "fps": 30.0, "orientation": "normal"}`
  - *Respuesta (200):* `{"success": true, "profile_status": "needs_calibration"}`
  - *Errores:* `403` (rol administrativo requerido), `409` (requiere poseer el lease de control físico activo).

### 6.2 Invitaciones QR y Señalización
- `POST /v1/camera/invite`
  - *Rol:* `controller` o `admin` con lease
  - *Cuerpo:* `{"expires_in_seconds": 180}`
  - *Respuesta (201):* `{"invite_id": "inv_123", "invite_url": "https://192.168.1.50:8000/camera#token=SEC_TOKEN", "expires_at": 1728936000.0}`
- `DELETE /v1/camera/invite/{invite_id}`
  - *Rol:* `controller` o `admin` con lease
  - *Respuesta (200):* `{"cancelled": true}`
- `POST /v1/camera/revoke`
  - *Rol:* `controller` o `admin` con lease
  - *Respuesta (200):* `{"revoked": true, "disconnected_transmitters": 1}`

### 6.3 Señalización WebSocket (`/v1/camera/signal`)
- *Ruta limpia:* `wss://192.168.1.50:8000/v1/camera/signal` (Sin credenciales en query parameters).
- *Autenticación y Ciclo de Credenciales de Transmisor:*
  - El token de invitación (`invite_token`) es de **un solo uso estricto**.
  - Al recibir `auth_transmitter`, el backend consume e invalida atómicamente el `invite_token`, impidiendo que la misma invitación sea reutilizada por otro cliente.
  - El backend emite en la respuesta una credencial de transmisor efímera separada (`transmitter_id`, `transmitter_ticket`), vinculada al `owner_session_id`, `owner_lease_generation` y `source_version`.
  - **Política de reconexión:** Si el teléfono sufre un parpadeo de red, puede reconectarse en una ventana de gracia acotada (30 s) presentando `reconnect_transmitter`. Si el operador cambia de cámara, revoca permisos, o se desconecta la tablet, la credencial queda invalidada de inmediato. La invitación original no puede volver a conectarse.
- *Esquemas Tipados de Autenticación de Señalización:*
  1. **Esquema de Autenticación de Operador (Tablet/Laptop):**
     ```json
     {
       "type": "auth_operator",
       "session_id": "uuid-v4-session-id",
       "ticket": "session-ticket-string"
     }
     ```
  2. **Esquema de Autenticación Inicial de Transmisor (Consumo de Invitación):**
     ```json
     {
       "type": "auth_transmitter",
       "invite_token": "single-use-invite-token"
     }
     ```
  3. **Esquema de Reconexión de Transmisor:**
     ```json
     {
       "type": "reconnect_transmitter",
       "transmitter_id": "trans-123",
       "transmitter_ticket": "short-lived-ticket"
     }
     ```
- *Mensajes de Señalización SDP e ICE:*
  - `offer`: `{"type": "offer", "sdp": "..."}`
  - `answer`: `{"type": "answer", "sdp": "..."}`
  - `candidate`: `{"type": "candidate", "candidate": "...", "sdpMid": "...", "sdpMLineIndex": 0}`
  - `disconnect`: `{"type": "disconnect"}`

### 6.4 Streaming Binario de Preview (`/v1/camera/preview-ws`)
- *Ruta limpia:* `wss://192.168.1.50:8000/v1/camera/preview-ws`.
- *Primer mensaje:* `{"type": "auth", "session_id": "...", "ticket": "..."}`.
- *Flujo posterior:* El servidor emite frames binarios JPEG puros (`bytes`).
- *Parámetros configurables de vista previa:* Los valores iniciales de 640×360 a 15 FPS constituyen valores predeterminados conservadores y configurables en la configuración/UI, no restricciones inmutables; pueden ajustarse según la capacidad real del enlace Wi-Fi y la GPU.

### 6.5 Fallback de Carga de Fotogramas (`POST /v1/camera/frame-upload`)
- *Rol:* Token de transmisor en encabezado `X-Transmitter-Token`.
- *Cuerpo multipart:* `file: capture.jpg` (Máximo 350 KB, 1280x720).
- *Respuesta (200):* `{"accepted": true, "server_monotonic": 12845.67}`.
- *Errores:* `400` (frame excede bytes o resolución), `403` (token inválido/revocado), `429` (tasa superior a 2 FPS).

### 6.6 Sesiones y Expropiación
- `POST /v1/sessions/lease/takeover`
  - *Rol:* `admin`
  - *Respuesta (200):* `{"success": true, "lease_generation": 5, "holder": "admin_session_id"}`
- `POST /v1/sessions/heartbeat`
  - *Encabezados:* `X-Session-Id`, `X-Session-Ticket`
  - *Respuesta (200):* `{"alive": true, "lease_held": true, "expires_in": 15.0}`

---

## 7. Capacidades del Dispositivo Móvil y Wake-Lock

- **Detección de soporte de capacidades:**
  - En el teléfono transmisor (`/camera`), el cliente consulta:
    `const caps = track.getCapabilities ? track.getCapabilities() : {}`
  - Solo si `caps.focusMode` reporta soporte, se ofrece selector de enfoque.
  - Solo si `caps.exposureMode` o `caps.torch` reportan soporte, se muestran los controles correspondientes.
  - **Cero promesas falsas:** Si el navegador (ej. Safari iOS) no expone capacidades de hardware, la UI omite completamente dichos controles sin arrojar errores.
- **Screen Wake-Lock (Mejor Esfuerzo):**
  - Para evitar que la pantalla del teléfono montado en el soporte se apague durante la demostración, se ejecuta:
    ```javascript
    if ('wakeLock' in navigator) {
      try {
        await navigator.wakeLock.request('screen');
      } catch (err) {
        console.warn('Wake Lock no disponible o denegado');
      }
    }
    ```
  - La pérdida o rechazo del wake-lock se maneja de forma transparente sin interrumpir la transmisión de video.

---

## 8. Cerrojo de Transición de Estado Seguro y Manejo de Concurrencia

Para evitar condiciones de carrera, bloqueos del bucle de eventos (`asyncio`) y corrupción de estado en hardware:

```
+----------------------------------------------------------------------------------------------------+
|                                 ARQUITECTURA DE CERROJO SEGURO                                     |
+------------------------------------+---------------------------------------------------------------+
| DENTRO DEL CERROJO (state_lock)    | - Mutación atómica de lease_generation, scene_gen, source_ver. |
| (Operaciones síncronas en memoria) | - Asignación / limpieza de titular del lease y transition_id. |
|                                    | - Asignación de fuente activa y perfil de calibración.        |
+------------------------------------+---------------------------------------------------------------+
| FUERA DEL CERROJO                  | - Escritura/lectura en puerto serie de Arduino (USB).         |
| (Operaciones bloqueantes / I/O)    | - Invocación a model.encode() en GPU.                         |
| (Serializadas por transition_mutex)| - cv2.VideoCapture open/read/release y thread.join().         |
|                                    | - Envíos de red HTTP / WebSocket.                             |
+------------------------------------+---------------------------------------------------------------+
```

- **Manejo de Fallo de Transición:**
  Si durante una conmutación de fuente o expropiación falla la comunicación serial con Arduino:
  1. Se establece `confirmed_mask = None` (ACK de protocolo desconocido/inválido).
  2. Se desactiva la inferencia automática de LEDs.
  3. El sistema entra en estado de advertencia segura en el dashboard (`"arduino_error"`).
  4. **Distinción entre Confirmación de Comando ACK e Iluminación Física:** La respuesta `ACK:N` confirma que el firmware procesó el paquete serie y ejecutó `digitalWrite()` sobre los pines D2–D10 (los cuales residen sobre los puertos PORTD y PORTB del microcontrolador ATmega328P, no exclusivamente `PORTD`). La confirmación lógica de pines no constituye prueba de iluminación óptica real de los LEDs físicos (lo cual requeriría fotosensores o inspección visual del operador). El dashboard reporta con veracidad: *«Comando de protocolo confirmado (ACK)»*, diferenciando el estado lógico del estado óptico no sensado.

---

## 9. Despliegue Same-Origin en Puerto Único, Firewall y Certificados

- **Servidor Único:** FastAPI sirve la API REST (`/v1/*`), los WebSockets (`/v1/ws`, `/v1/camera/*`) y los activos estáticos compilados de la SPA (`frontend/dist`) desde el **mismo puerto 8000**, garantizando mismo origen estricto.
- **Símbolo Válido de Lanzamiento:** `toollighthouse.api:create_app` ejecutado mediante `uvicorn --factory` o `python -m toollighthouse serve`.
- **Firewall de Windows:** Regla de entrada con ámbito restringido al perfil Privado (`-Profile Private`).
- **Elusión de Certificados en Safari iOS:** Safari desactiva totalmente `getUserMedia()` si el certificado no está respaldado por una CA instalada y de confianza plena en *Ajustes de confianza de certificados*.
- **Distinción CA Pública vs Privada:** `rootCA-key.pem` es privada y reside exclusivamente en la laptop servidora; solo `rootCA.pem` (pública) se instala en los dispositivos móviles.
- **Sin Dependencia de Wildcard/mDNS:** Como mDNS (`.local`) falla en múltiples versiones de Android, los certificados emitidos por `mkcert` incluyen la dirección IP LAN numérica fija (SAN: `192.168.1.50`).

---

## 10. Matriz Integral de Auditoría y Aceptación (Secciones 1 a 22)

| Sección / Requisito | Componente / Archivo | Validación en Código | Límite de Evidencia Verificable |
|---|---|---|---|
| **0. Fusión Unificada de Proyectos** | Arquitectura general | Documentada en diseño | Fase A (Documentación pura) |
| **1. Dispositivos A, B y C** | FastAPI, React SPA, `/camera` | Topología definida | Fase A (Diseño) |
| **2. Principio 5 Fuentes de Cámara** | `camera_registry.py` | Modelo de datos y estados | Unit Test / Mock |
| **3.1 WebRTC P2P + Señalización WS** | `webrtc_signaling.py` | First-message auth sin query | Unit Test con Mock WS |
| **3.2 Servicio OpenCV en Backend** | `camera.py` (`OpenCVCameraService`)| Hilo único, captura local | Unit Test con VideoCapture simulado |
| **4. Unified Control Lease** | `sessions.py` (`UnifiedSessionStore`)| Expropiación atómica y roles | Pytest (`test_unified_lease.py`) |
| **5. Vinculación QR y Token Fragment** | `QRModal.tsx`, `/camera#token=` | Limpieza con `replaceState` | Browser Test / Vitest |
| **6. Diseño Visual Independiente** | `styles.css`, `Navigation.tsx` | Tokens morado/gris/Albert Sans| Inspección visual en navegador |
| **7. Selector Central de Cámaras** | `CameraSelector.tsx` | Metadatos y conmutación segura| Vitest / React Testing Library |
| **8. Calibración Per-Source y ROI** | `config.py`, `CalibrationView.tsx` | Schema por fuente y resolución | Pytest / Vitest (`geometry.test.ts`)|
| **9. Inferencia EmbeddingGemma 2** | `embeddings.py`, `classifier.py` | Modos text-only, hybrid, image | Script local en GPU RTX 3060 |
| **10. Fotografía y Cajas Congeladas** | `InspectionPanel.tsx`, `pipeline.py`| Cajas fijadas sobre frame frozen| Vitest / Browser manual |
| **11. Inferencia Continua Regulada** | `LatestInferenceWorker`, 2.0 Hz | `requestVideoFrameCallback` | Vitest / Pytest backpressure |
| **12. Inventario 3×3 y Actividad** | `InventoryGrid.tsx`, `ActivityLog.tsx`| Mapeo real de 9 categorías | Vitest (Mock API) |
| **13. Preservación 5 Secciones Admin** | `App.tsx` (5 tabs existentes) | Inspección, Tools, Calib, etc. | Vitest (`e2e-dashboard-flow.test.tsx`)|
| **14. Arduino Nano y Watchdog** | `firmware/nano.ino`, `serial.py` | `MASK:N`/`ACK:N`, 2500ms watchdog | Pytest con `SimulatedSerial` |
| **15. Despliegue HTTPS LAN y mkcert** | `scripts/setup_certs.ps1` | SANs IP fija, exportación CA | Verificación en red LAN real |
| **16. Monitoreo y Rendimiento** | `metrics.py`, `SystemPanel.tsx` | Métricas p50/p95, FPS, VRAM | Pytest (`test_overlay_metrics.py`) |
| **17. Organización de Componentes** | `frontend/src/`, `toollighthouse/` | Separación de responsabilidades | Validación estática TypeScript/Linter|
| **18. Pruebas Nuevas y Anti-Regresión** | `tests/`, `frontend/src/*.test.tsx`| Suites automatizadas | Pytest y Vitest automáticos |
| **19. Protocolo 20 Casos Dispositivos** | `docs/ACCEPTANCE.md` | Protocolo paso a paso | Prueba manual en hardware real |
| **20. Fases de Implementación A-H** | Plan de fases secuencial | Vertical slice Tablet-Phone-PC | Planificación en diseño |
| **21. Git, PR y No Modificar Ref** | Rama `feat/unified-dashboard-...` | Solo documentación en Fase A | Git status limpio sin commits |
| **22. Criterios de Aceptación Finales** | 20 criterios consolidados | Verificación integral en demo | Checkpoints de fase |

---

## 11. Plan de Fases de Implementación

- **Fase A (Actual):** Auditoría, resolución de deficiencias y diseño técnico formal (Sin cambios en código de producto).
- **Fase B:** Concesión unificada de control físico (`UnifiedSessionStore`) y registro central de fuentes (`CameraSourceRegistry`).
- **Fase C:** Señalización WebSocket de WebRTC nativo (first-message auth) y vista móvil `/camera`.
- **Fase D:** Servicio OpenCV en backend para webcams USB y streaming binario JPEG hacia la tablet.
- **Fase E:** Perfiles de calibración independientes por cámara y persistencia atómica.
- **Fase F:** Inferencia acotada, recuadros sobre imagen congelada y modo continuo regulado (2 Hz).
- **Fase G:** Evolución de estilos en `styles.css` con nuevos tokens visuales, cuadrícula 3×3 real y preservación de las 5 secciones administrativas.
- **Fase H:** Configuración HTTPS LAN con `mkcert`, scripts PowerShell, ejecución de suites de prueba y preparación del Pull Request hacia `main`.
