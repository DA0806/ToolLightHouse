# Arquitectura de Seguridad, Exclusividad y Protección de Hardware (ToolLightHouse)

Este documento especifica el modelo de amenazas, las garantías de exclusividad de control físico, el aislamiento de privilegios y las protecciones frente a resultados obsoletos implementadas en ToolLightHouse.

---

## 1. Modelo de Amenazas y Límites de Confianza

El sistema opera en una red de área local privada (LAN) bajo las siguientes premisas de aislamiento:

1. **Operación 100% Fuera de Nube (Zero-Cloud Boundary):**
   - Ningún componente transmite datos, imágenes, vectores o telemetría a servidores externos en Internet.
   - No se emplean servicios públicos de señalización STUN/TURN, redes CDN de terceros ni generadores de códigos QR basados en la nube.
   - Toda la señalización WebRTC, el streaming de video y la inferencia de IA residen dentro del dominio de difusión de la red Wi-Fi local.
2. **Aislamiento de Dispositivos:**
   - **Laptop Servidora:** Es el único proceso con acceso físico al puerto serie USB (Arduino Nano), a la GPU NVIDIA (EmbeddingGemma 2) y al sistema de archivos local.
   - **Tablet Operadora:** Actúa como cliente web autenticado. Sus acciones están delimitadas por el rol de sesión asignado.
   - **Teléfono Transmisor:** Es un cliente con privilegios mínimos, cuyo único propósito es capturar video y transmitirlo al operador.

---

## 2. Segregación Estricta de Roles y Mínimo Privilegio

El sistema define cuatro roles mutuamente excluyentes gestionados por `UnifiedSessionStore`:

| Rol | Concesión de Control Físico | Permisos Permitidos | Permisos Denegados |
|---|---|---|---|
| `admin` | Sí (adquisición o expropiación explícita) | Configurar herramientas, editar inventario, calibrar fondos/ROI, conmutar modos de sistema, pruebas de Arduino en mantenimiento y forzar expropiación de control. | Ninguno dentro del sistema. |
| `controller` | Sí (exclusivo, un titular a la vez) | Seleccionar fuentes de cámara, generar invitaciones QR, capturar fotos, activar inferencia continua y transmitir comandos a Arduino. | Modificar configuración persistente, editar inventario o entrar a mantenimiento de Arduino. |
| `reader` | No (denegación absoluta) | Consultar telemetría, ver estado del sistema, observar inventario y recibir eventos de detección vía WebSocket. | Modificar hardware, cambiar fuentes, subir imágenes o alterar configuración. |
| `camera_transmitter` | No (denegación absoluta) | Negociar WebRTC P2P en `/v1/camera/signal` y subir fotogramas en modo fallback (`/v1/camera/frame-upload`). | Acceso a cualquier endpoint administrativo, de inspección, calibración o inventario (`403 Forbidden`). |

> [!IMPORTANT] **Aislamiento del Teléfono Transmisor**
> El teléfono móvil **nunca recibe la clave de administrador ni la clave de emparejamiento**. Sus credenciales se derivan exclusivamente del consumo atómico de una invitación temporal de cámara. Cualquier intento del transmisor de interactuar con endpoints de control físico es rechazado con código `403 Forbidden`.

---

## 3. Concesión Unificada de Control Físico (Unified Control Lease)

Para garantizar que dos operadores no puedan encender LEDs contradictorios ni alterar la escena simultáneamente:

### 3.1 Invariante de Exclusividad
- **Solo un dispositivo** puede ostentar el lease de control físico activo (`PhysicalControlLease`) en cualquier instante.
- Si un segundo operador intenta emparejarse como `controller` mientras existe un titular activo, el backend retorna `409 Conflict ("another controller session is active")`.

### 3.2 Renovación Periódica (Heartbeat)
- El titular del lease debe enviar un latido periódico (`POST /v1/sessions/heartbeat`) cada 5 a 10 segundos.
- La expiración del lease (`lease_ttl_seconds`, por defecto **30 segundos**) revoca automáticamente la concesión y apaga las salidas físicas.

### 3.3 Expropiación Administrativa Explícita (Takeover)
Cuando el administrador legítimo necesita recuperar el control (por ejemplo, si la tablet del operador perdió batería o quedó bloqueada):

1. El administrador invoca `POST /v1/sessions/lease/takeover`.
2. La operación se serializa estrictamente mediante `transition_mutex` (`asyncio.Lock`) para evitar condiciones de carrera.
3. Se incrementa atómicamente la generación de control:
   $$\text{lease\_generation} \leftarrow \text{lease\_generation} + 1$$
4. Se desasigna de inmediato al titular anterior y se revocan todas las invitaciones de cámara pendientes.
5. Se ejecuta el apagado seguro de hardware transmitiendo `MASK:0` a Arduino Nano.
6. Se asigna el nuevo lease a la sesión administrativa y el sistema vuelve al estado de reposo seguro.

---

## 4. Vinculación QR Segura y Protección contra Fugas de Tokens

La experiencia de emparejamiento del teléfono móvil mediante código QR implementa salvaguardas criptográficas:

1. **Invitaciones Efímeras de Un Solo Uso:**
   - Cada invitación (`POST /v1/camera/invite`) genera un token criptográfico pseudoaleatorio de 32 bytes (`secrets.token_urlsafe(32)`).
   - Su validez expira a los **180 segundos** (configurable entre 30 y 600 segundos).
2. **Protección de Fragmento de URL (`#token=...`):**
   - El token se embebe en el fragmento de hash de la URL (`#token=...`), nunca como parámetro de consulta (`?token=...`).
   - Los fragmentos de hash **nunca se envían al servidor en peticiones HTTP** ni se registran en los archivos de registro (*access logs*) del proxy o servidor web.
3. **Depuración Inmediata de Historial:**
   - Tan pronto como la página `/camera` carga en el teléfono, el script ejecuta:
     ```javascript
     window.history.replaceState(null, '', window.location.pathname);
     ```
   - Esto elimina el secreto de la barra de navegación y del historial del navegador, evitando que quede expuesto si el usuario revisa su historial o comparte el enlace.
4. **Consumo Atómico:**
   - Al autenticarse en el WebSocket de señalización, el backend marca la invitación como `used = True` de forma atómica.
   - Cualquier intento posterior de reutilizar el mismo token de invitación es rechazado de inmediato.

---

## 5. Seguridad de Hardware y Watchdog de Arduino Nano

ToolLightHouse implementa múltiples capas de protección para evitar que salidas físicas permanezcan encendidas indebidamente:

### 5.1 Watchdog en Firmware (2500 ms)
- El firmware del Arduino Nano (`firmware/nano/nano.ino`) cuenta con un temporizador de vigilancia de **2500 ms**.
- Si el microcontrolador no recibe un comando válido con salto de línea dentro de esa ventana, **apaga automáticamente todos los pines de salida (D2–D10)**.
- Esta salvaguarda protege estrictamente contra caídas de la laptop, congelamiento del backend Python o desconexiones físicas del cable USB serie. (Nota de delimitación: el watchdog de firmware no responde directamente a eventos de red Wi-Fi; ante una desconexión Wi-Fi entre la tablet o teléfono y la laptop, es el backend en el host quien detecta la expiración del lease o timeout de video y envía `MASK:0` por el enlace serie).

### 5.2 Apagado Seguro Automático (`MASK:0`)
El backend transmite de inmediato la máscara de apagado (`MASK:0`) ante cualquiera de los siguientes eventos:
- Expiración del lease de control físico por falta de heartbeat.
- Conmutación activa de fuente de cámara (ej. de teléfono a webcam USB).
- Desconexión del teléfono transmisor.
- Entrada en modo de mantenimiento de Arduino.
- Cierre ordenado del servidor FastAPI (`shutdown event`).

### 5.3 Contrato de Confirmación ACK
- **`MASK:N` $\rightarrow$ `ACK:N`:** El firmware procesa la máscara binaria y ejecuta `digitalWrite()` sobre los pines D2–D10.
- La respuesta `ACK:N` confirma únicamente que el comando lógico fue recibido y aplicado a nivel de microcontrolador.
- **Transparencia en el Dashboard:** El sistema no asume ni promete iluminación física de un LED en ausencia de confirmación serie (`confirmed_mask = None`). Si un LED se desconecta físicamente o se daña su resistencia, el software reporta *«Comando de protocolo confirmado (ACK)»*, evitando falsas conclusiones sobre la iluminación óptica real.

---

## 6. Cerca de Generación y Descarte de Resultados Obsoletos (Stale Fencing)

En sistemas de visión con procesamiento en GPU y transporte de video por red, pueden ocurrir retrasos asíncronos donde una inferencia finaliza después de que el usuario cambió de escena o soltó el control.

Para mitigar esta vulnerabilidad, ToolLightHouse implementa la **Tupla de Cerca**:
$$\mathcal{T} = (\text{frame\_id}, \text{source\_id}, \text{source\_version}, \text{config\_version}, \text{lease\_generation}, \text{scene\_generation})$$

### Reglas de Descarte:
1. Al capturar o recibir un fotograma, el sistema fija la tupla $\mathcal{T}$ vigente.
2. La inferencia se ejecuta en GPU de forma asíncrona.
3. Al obtener las detecciones y antes de calcular o enviar la máscara a Arduino:
   - El worker revalida que `lease_generation`, `scene_generation` y `source_version` coincidan exactamente con el estado activo.
   - Si el operador conmutó de cámara, cerró la sesión o pulsó parada de emergencia durante la inferencia, **el resultado se descarta en silencio y las salidas físicas permanecen intactas**.

---

## 7. Gestión Segura de Certificados Criptográficos

- La clave privada de la CA raíz (`rootCA-key.pem`) permanece exclusivamente en la laptop servidora y **nunca debe comitearse a Git ni compartirse**.
- El certificado público `rootCA.pem` se distribuye únicamente a los dispositivos móviles de prueba autorizados.
- El directorio `certificates/` está explícitamente incorporado en `.gitignore` para evitar fugas accidentales de claves TLS en repositorios remotos.
