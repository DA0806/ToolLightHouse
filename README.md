# ToolLightHouse

Sistema unificado multicámara y pipeline de visión artificial zero-shot para el reconocimiento de hasta nueve categorías de herramientas sobre una bandeja organizadora, señalando sus ubicaciones físicas mediante un arreglo de nueve LEDs controlados por un microcontrolador Arduino Nano (pines D2–D10).

El sistema opera de forma autónoma en una red de área local privada (LAN) sin conexión a Internet ni dependencias de servicios en la nube, distribuyendo responsabilidades entre tres dispositivos principales:

1. **Laptop Servidora (Windows 11 / Linux):** Ejecuta el servidor FastAPI, adquisición de video con OpenCV (DirectShow en Windows), el modelo multimodal **EmbeddingGemma 2** en la GPU NVIDIA dedicada (FP32 o BF16), la comunicación serie USB con Arduino Nano (115200 baudios, watchdog de 2.5 s), el centro de señalización WebRTC local y la concesión unificada de control físico (*Unified Control Lease*).
2. **Tablet Operadora (React 19 + TypeScript):** Centro de administración principal para la demostración con pantalla táctil. Dispone de un diseño responsivo de dos columnas (visor de video oscuro a la izquierda con recuadros SVG de detección en tiempo real; panel de inventario 3×3 y registro de actividad auténtica a la derecha), conmutador central de fuentes de cámara, vinculación por código QR y cinco paneles administrativos (*Inspección*, *Herramientas*, *Calibración*, *Arduino*, *Sistema*).
3. **Teléfono Transmisor (Vista dedicada `/camera`):** Teléfono móvil montado físicamente en un soporte cenital fijo apuntando a las herramientas. Se vincula escaneando un código QR efímero de un solo uso y transmite video en vivo mediante **WebRTC P2P directo** hacia la tablet operadora con `playsInline`, `facingMode: "environment"` y bloqueo de suspensión de pantalla (`navigator.wakeLock`). Si la red local bloquea la conexión P2P, conmuta automáticamente a un **Modo Fallback HTTP** regulado a 2 FPS hacia FastAPI.

---

## Índice de Documentación y Guías Especializadas

Para profundizar en aspectos técnicos y operativos específicos, consulte las guías dedicadas del proyecto:

- 📖 **[Guía Multicámara (docs/MULTICAMERA.md)](docs/MULTICAMERA.md):** Manual de las cinco fuentes canónicas de cámara, flujo de emparejamiento QR/WebRTC, streaming binario de webcams USB y calibración espacial per-source.
- 🔒 **[Despliegue HTTPS en LAN (docs/LAN_HTTPS.md)](docs/LAN_HTTPS.md):** Instrucciones para generar certificados locales con `mkcert`, configurar la confianza de la CA pública en Android e iPad/iOS y abrir el puerto 8000 en el firewall de Windows.
- 💻 **[Despliegue y Operación en Windows (docs/DEPLOYMENT_WINDOWS.md)](docs/DEPLOYMENT_WINDOWS.md):** Guía de instalación, comandos de actualización segura Git sin `reset --hard`, validación del runtime GPU RTX 4060 / 5050 y operación diaria desde el dashboard.
- 🛡️ **[Arquitectura de Seguridad y Fencing (docs/SECURITY.md)](docs/SECURITY.md):** Especificación de roles, exclusividad de control físico, protección contra fugas de tokens, watchdog de hardware y descarte de resultados obsoletos.
- 🧪 **[Matriz de Validación y 20 Casos Físicos (docs/VALIDATION_MULTICAMERA.md)](docs/VALIDATION_MULTICAMERA.md):** Matriz de evidencias de software, delimitación entre mocks y hardware real, y el protocolo de aceptación de 20 casos físicos en banco.
- 🔌 **[Referencia de API REST y WebSockets (docs/API.md)](docs/API.md):** Contrato de endpoints, encabezados de autenticación, esquemas JSON y eventos WebSocket.
- 🎨 **[Procedencia y Créditos de Diseño (frontend/src/PROVENANCE.md)](frontend/src/PROVENANCE.md):** Registro formal de autoría del compañero `adavid` ([`Hopeelex/keytool-tracker`](https://github.com/Hopeelex/keytool-tracker)), licencias SIL OFL-1.1 (Albert Sans) e ISC (Lucide).
- 📐 **[Diseño Técnico Aprobado (docs/superpowers/specs/2026-10-14-unified-dashboard-multicamera-design.md)](docs/superpowers/specs/2026-10-14-unified-dashboard-multicamera-design.md):** Especificación arquitectónica con las correcciones normativas aprobadas por el usuario.
- 🛠️ **[Hardware y Firmware Arduino (docs/HARDWARE.md)](docs/HARDWARE.md):** Asignación de pines D2–D10, resistencias, límites de corriente y protocolo MASK/ACK.
- ⚙️ **[Configuración del Dashboard (docs/DASHBOARD_CONFIGURATION.md)](docs/DASHBOARD_CONFIGURATION.md):** Gestión de versiones draft/active, hot-apply y exportación portable.

---

## 1. Topología del Sistema y Flujo de Datos

```text
       +-------------------------------------------------------------------------+
       |               Red de Área Local Privada (LAN 100% Offline)              |
       +-------------------------------------------------------------------------+
                                            |
             +------------------------------+------------------------------+
             |                                                             |
             v                                                             v
+-----------------------------+                               +-----------------------------+
|  Teléfono Transmisor Cenital|                               |       Tablet Operadora      |
|    (Pantalla SPA /camera)   |                               |      (Dashboard Táctil)     |
|                             |    WebRTC P2P MediaStream     |                             |
| - Cámara trasera fija       |==============================>| - Visor de video en vivo    |
| - Token QR efímero en hash  |   (Directo entre navegadores) | - Cuadrícula 3x3 de estado  |
| - Screen Wake Lock activo   |                               | - Métrica de latencia real  |
| - Fallback JPEG (2 FPS)     |                               | - 5 secciones admin web     |
+-----------------------------+                               +-----------------------------+
             |                                                             |
             | HTTP Fallback Frames                                        | HTTP REST API
             | & Señalización WebRTC WS                                    | & WebSocket de eventos
             v                                                             v
+-------------------------------------------------------------------------------------------+
|                                      Laptop Servidora                                     |
|                                                                                           |
|  [FastAPI 0.115]                                                                          |
|    - UnifiedSessionStore: Control físico exclusivo (Reader / Controller / Admin)          |
|    - Hub de señalización WebRTC local e invitaciones QR de un solo uso (180 s)            |
|    - Registro de 5 fuentes de cámara (remote_phone, builtin, usb_webcam, local, manual)   |
|    - Perfiles de calibración independientes por cámara (coordenadas canónicas 0.0..1.0)   |
|    - OpenCVCameraService: Adquisición DirectShow y streaming binario JPEG preview         |
|                                                                                           |
|  [Pipeline de Visión e IA en GPU]                                                         |
|    - Sustracción de fondo y recorte interactivo de Región de Interés (ROI)                |
|    - LatestInferenceWorker: Cola acotada de tamaño 1 (latest-only, sin retraso acumulado) |
|    - Modelo EmbeddingGemma 2 en GPU NVIDIA (Similitud coseno vs prototipos textuales)    |
|    - Cerca estricta de tupla (source_id, source_ver, lease_gen, scene_gen)                |
|                                                                                           |
|  [Controlador de Hardware]                                                                |
|    - Transporte USB Serial a 115200 baudios, máscara binaria de 9 bits (0..511)           |
|    - Protocolo MASK:N -> ACK:N (confirmación lógica de pines D2..D10)                     |
+-------------------------------------------------------------------------------------------+
                                            |
                                            | USB Serial (COMx)
                                            v
                            +-------------------------------+
                            |      Arduino Nano Clásico     |
                            |   Firmware ATmega328P         |
                            |   Pines D2..D10 -> 9 LEDs     |
                            |   Watchdog de 2500 ms         |
                            +-------------------------------+
```

---

## 2. Las Cinco Fuentes de Cámara Soportadas

El sistema dispone de un selector centralizado que permite alternar la fuente de inspección activa de manera segura y transparente:

1. **`remote_phone` (Teléfono Remoto WebRTC):**
   Transmisión de video directo desde el teléfono del soporte cenital hacia la tablet mediante `RTCPeerConnection` nativo. Incorpora fallback automático a subida JPEG autenticada por HTTPS a 2 FPS (`POST /v1/camera/frame-upload`) si la red Wi-Fi presenta aislamiento de clientes.
2. **`laptop_builtin` (Cámara Integrada de la Laptop):**
   Captura gestionada por OpenCV en el backend de la laptop. Emite vista previa binaria a la tablet vía `WS /v1/camera/preview-ws`. Al pulsar «Escanear herramientas», la inferencia se ejecuta directamente sobre el fotograma en RAM, evitando transferencias innecesarias de píxeles por la red.
3. **`usb_webcam` (Webcam USB Externa):**
   Webcam USB conectada a la laptop, detectada dinámicamente mediante DirectShow (`GET /v1/camera/webcams`). Admite selección de resolución y framerate, proveyendo vista previa remota a la tablet y escaneo local en RAM.
4. **`operator_device` (Cámara de este Dispositivo):**
   Captura local desde el navegador donde está abierto el dashboard (la cámara integrada de la tablet o de la laptop) mediante `navigator.mediaDevices.getUserMedia()`.
5. **`manual_upload` (Fotografía Manual):**
   Permite cargar una imagen estática (JPEG, PNG, WebP) desde el almacenamiento para ejecutar el reconocimiento, útil para diagnósticos o cuando no hay cámaras de video disponibles.

---

## 3. Calibración Independiente por Fuente de Imagen

Dado que cada sensor posee una distancia focal, resolución y ángulo de montaje distintos, ToolLightHouse almacena perfiles de calibración independientes en `config/app_config.json`:

- **Fondo de Bandeja Vacía:** Se captura individualmente para cada cámara (`POST /v1/calibration/profiles/{source_id}/capture`), almacenándose en `data/calibration/`.
- **Región de Interés (ROI):** Se ajusta sobre el lienzo táctil en coordenadas canónicas normalizadas $[0.0, 1.0]$.
- **Consistencia Espacial:** La imagen de fondo y las imágenes de inferencia se someten a la misma función de normalización (`apply_canonical_transform`). Si cambian la resolución o la orientación, el perfil se invalida automáticamente (`needs_confirmation`).
- Para más información, consulte [docs/MULTICAMERA.md](docs/MULTICAMERA.md).

---

## 4. Pipeline de Inteligencia Artificial (EmbeddingGemma 2)

El pipeline de reconocimiento opera íntegramente en la laptop servidora:

$$\text{Captura} \longrightarrow \text{OpenCV} \longrightarrow \text{Sustracción de Fondo / ROI} \longrightarrow \text{Detecciones} \longrightarrow \text{EmbeddingGemma 2} \longrightarrow \text{Máscara OR} \longrightarrow \text{Arduino}$$

### Las Tres Modalidades de Clasificación

| Modalidad | Descripción y Requisitos de Datos |
|---|---|
| **`text-only` (Predeterminada)** | **Zero-shot puro.** No requiere fotografías de referencia de herramientas. Utiliza los nombres y descripciones semánticas de cada herramienta configurados en el inventario para generar prototipos vectoriales de texto y compararlos con las imágenes recortadas. |
| **`hybrid` (Híbrida)** | Combina descripciones textuales con fotografías de referencia opcionales subidas desde el dashboard. Un control deslizante (`text_weight`, 10% a 90%) regula la ponderación entre texto e imágenes. |
| **`image-only` (Avanzada)** | Compara exclusivamente los recortes observados contra los embeddings de las fotografías de referencia almacenadas en `data/references/`. |

> [!IMPORTANT] **Interpretación de Similitudes**
> Los valores de coincidencia reportados por el clasificador (`similarity_top1`, `similarity_top2`, `margin`) son **similitudes coseno** en un espacio de 768 dimensiones, **nunca porcentajes de confianza**. Las detecciones ambiguas o que no superan el margen de separación son rechazadas y no encienden LEDs.

---

## 5. Control de Hardware y Arduino Nano

- **Firmware Oficial:** Ubicado en [`firmware/nano/nano.ino`](file:///firmware/nano/nano.ino). Compatible con Arduino Nano clásico (ATmega328P).
- **Asignación de Pines (D2–D10):**
  - Casillas 1 a 9 del organizador asignadas a los pines D2 a D10 en orden estricto (D2=bit 0, ..., D10=bit 8).
  - Protocolo serie ASCII a 115200 baudios: el host envía `MASK:N` (máscara de 0 a 511) y el microcontrolador responde `ACK:N`.
- **Watchdog de Seguridad:** El microcontrolador apaga automáticamente todos los LEDs tras **2500 ms** sin recibir tramas serie válidas por USB.
- **Veracidad del Contrato ACK:** La respuesta `ACK:N` confirma que el firmware procesó el paquete serie y ejecutó la instrucción lógica `digitalWrite()` sobre los pines D2–D10. No constituye prueba óptica de iluminación física de los diodos (lo que requeriría sensores fotosensibles).
- **Modo Mantenimiento:** Permite encender individualmente cada LED durante un máximo de 3 segundos con apagado automático para pruebas de diagnóstico, bloqueando de forma segura la inferencia concurrente.

---

## 6. Inicio Rápido de Desarrollo y Demostración

### 6.1 Preparación Inicial del Entorno (Una sola vez)

En una ventana de PowerShell en la laptop servidora (con Python 3.11 x64 y Node.js 24):

```powershell
# 1. Crear y activar entorno virtual
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

# 2. Instalar PyTorch con soporte CUDA (consulte https://pytorch.org/get-started/locally/ para la wheel adecuada):
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 3. Instalar paquete con dependencias de desarrollo e IA:
.\.venv\Scripts\python.exe -m pip install -e '.[dev,ai]'

# 4. Compilar frontend de producción:
Push-Location frontend
npm ci
npm run build
Pop-Location

# 5. Generar certificados LAN HTTPS:
.\scripts\setup_certs.ps1

# 6. Descargar modelo y construir galería textual zero-shot:
.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --mode real --device cuda --inventory config/inventory.json --output data/gallery.json
```

### 6.2 Comandos de Ejecución del Servidor

#### Modo Producción (GPU Real + CUDA + HTTPS LAN)
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = "operador123"
$env:TOOLLIGHTHOUSE_ADMIN_KEY = "admin123"

.\.venv\Scripts\python.exe -m toollighthouse serve `
    --mode real `
    --device cuda `
    --ssl-certfile certificates/lan-cert.pem `
    --ssl-keyfile certificates/lan-key.pem `
    --host 0.0.0.0 `
    --port 8000
```

#### Modo Desarrollo y Simulación (Mock + LEDs Simulados)
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = "operador123"
$env:TOOLLIGHTHOUSE_ADMIN_KEY = "admin123"

.\.venv\Scripts\python.exe -m toollighthouse serve `
    --mode mock `
    --simulate-leds `
    --ssl-certfile certificates/lan-cert.pem `
    --ssl-keyfile certificates/lan-key.pem `
    --host 0.0.0.0 `
    --port 8000
```

> [!NOTE] **Comportamiento del Puerto Serie en el Arranque**
> Al iniciar sin el parámetro `--serial`, el backend arranca con serial simulado (`SimulatedSerial()`), emitiendo salidas simuladas en lugar de quedar en un estado desconectado no inicializado. Para enlazar el microcontrolador físico, el operador puede conectar el puerto COM detectado interactivamente desde la pestaña **Arduino** del dashboard, o bien iniciar mediante CLI pasando explícitamente `--serial COMx` junto con un inventario confirmado (`--inventory <archivo> --confirm-inventory`).

---

## 7. Navegación en el Dashboard (Cinco Secciones Administrativas)

El dashboard web accesible desde la tablet o laptop expone cinco paneles especializados:

1. **Inspección:**
   Visor de video en vivo, selector central de cámaras, botón para mostrar el código QR de vinculación, botón «Escanear herramientas», conmutador de reconocimiento continuo, cuadrícula 3×3 de herramientas con indicadores de estado de hardware (`solicitado` / `confirmado ACK`) y panel de actividad reciente.
2. **Herramientas:**
   Administración de las nueve tarjetas de inventario. Permite editar nombres visuales, descripciones semánticas para el modelo zero-shot, intercambiar posiciones de pines D2–D10 sin colisiones y subir fotografías de referencia opcionales.
3. **Calibración:**
   Selección de modalidad (`text-only`, `hybrid`, `image-only`), control deslizante de peso de texto, captura del fondo de bandeja vacía por cámara, dibujo interactivo del recuadro ROI sobre la imagen real y confirmación de calibraciones migradas.
4. **Arduino:**
   Detección y conexión de puertos serie COM, modo de mantenimiento con exclusión de inferencia, botones de prueba individual de LEDs (D2..D10) con apagado automático y botón de apagado de emergencia (`MASK:0`).
5. **Sistema:**
   Auditoría de 7 pasos de preparación, conmutador de modos (`mock`, `preparation`, `real`), diagnóstico de GPU/VRAM, control de versiones de configuración, hot-apply, rollback y exportación/importación de configuración JSON portable.

---

## 8. Evidencia de Pruebas y Límites Físicos

- **Evidencia Actual de Software:**
  - Backend: **202 pruebas aprobadas, 1 prueba omitida** (`test_embeddinggemma_smoke` requiere pesos/GPU local) y verificación con `compileall` aprobada (confirmado independientemente por el líder).
  - Frontend: 14 suites con 87 pruebas aprobadas en Vitest, verificación estática y empaquetado de producción completados. Verificación sintética en navegador Edge (2 páginas) con bucle de medios `canvas.captureStream` completado (`readyState: 4`, invocación única a `getUserMedia`), sin asumir cámaras físicas, GPU ni conectividad Wi-Fi real.
  - El software valida el ciclo de vida de leases, el hub de señalización WebRTC simulado, la lógica de serialización de Arduino y la consistencia matemática de coordenadas canónicas.
- **Delimitación de Banco de Pruebas Físico:**
  Los tests simulados **no sustituyen las pruebas con hardware real**. La validación final de la demostración requiere ejecutar el protocolo de los 20 casos físicos detallado en [docs/VALIDATION_MULTICAMERA.md](docs/VALIDATION_MULTICAMERA.md) en la laptop servidora con GPU RTX 4060/5050, tablet operadora, teléfono montado en soporte cenital y Arduino Nano con las nueve herramientas reales.

---

## 9. Procedencia, Licencias y Créditos

- **Diseño Visual e Inspiración de Emparejamiento:**
  Adaptado del proyecto [`Hopeelex/keytool-tracker`](https://github.com/Hopeelex/keytool-tracker) desarrollado por nuestro compañero de equipo **adavid** (`@Hopeelex`) en el commit `9552053c2`. El autor concedió autorización personal expresa para reutilizar e integrar sus componentes, layout de 2 columnas, tokens de diseño y flujo de vinculación en ToolLightHouse para este proyecto universitario. Consulte los detalles en [`frontend/src/PROVENANCE.md`](frontend/src/PROVENANCE.md).
- **Tipografía Albert Sans:** Distribuida offline mediante `@fontsource/albert-sans` bajo la licencia [SIL Open Font License 1.1 (OFL-1.1)](https://scripts.sil.org/OFL).
- **Iconografía:** Paquete `lucide-react` bajo la [Licencia ISC](https://github.com/lucide-icons/lucide/blob/main/LICENSE).
- **Modelo de IA (EmbeddingGemma 2):** Google EmbeddingGemma 2 bajo los términos exactos de su tarjeta de modelo (`Apache-2.0` con sujeción a la [Gemma Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy)).
