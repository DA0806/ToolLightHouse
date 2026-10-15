# ToolLightHouse: Protocolo de Operaciones y Demostración en Vivo

Guía práctica, concisa y ejecutable para la puesta en marcha de ToolLightHouse en Windows con arquitectura multidispositivo (Laptop Servidora, Tablet Dashboard y Teléfono Cámara Remota).

---

## Secuencia Exacta de 19 Acciones Operativas

### 1. Sincronización Segura de Git
Verifique que el árbol de trabajo no tenga modificaciones no confirmadas. Si hay cambios locales sin guardar, deténgase y resuelva; nunca ejecute `git reset --hard` ni `git stash` automáticamente:
```powershell
git status --porcelain
```
Si la salida es vacía (árbol limpio), cambie a la rama oficial del dashboard unificado y actualice con avance rápido exclusivo (*fast-forward only*):
```powershell
git switch feat/unified-dashboard-multicamera
git fetch origin
git pull --ff-only origin feat/unified-dashboard-multicamera
```

### 2. Verificación de Entorno Virtual y Aceleración CUDA
Ejecute el diagnóstico no destructivo de ToolLightHouse:
```powershell
.\scripts\preflight.ps1
```
Si hay varias interfaces, una zona portátil sin gateway o el diagnóstico marca una dirección ambigua, fuerce la IP que tablet y teléfono realmente alcanzan: `.\scripts\preflight.ps1 -LanIP <IP>`.
Confirme que el reporte indique `[PASS] Python Venv` y `[PASS] CUDA / GPU` con el nombre de la tarjeta gráfica NVIDIA detectada.

### 3. Compilación de Activos del Frontend
Tras sincronizar cambios del repositorio o si los archivos de `frontend/src` fueron modificados, compile los activos de React para evitar bundles desactualizados:
```powershell
cd frontend
npm ci
npm run build
cd ..
```
Verifique la existencia del artefacto principal:
```powershell
Test-Path "frontend\dist\index.html"
```

### 4. Detección y Presencia del Modelo de IA Local
Compruebe la presencia local de los pesos de `google/embeddinggemma-2`:
```powershell
Test-Path "models\embeddinggemma-2\model.safetensors"
```
*(Si no estuviera presente, descargue los pesos con: `.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2`)*.

### 5. Emisión y Confianza del Certificado HTTPS LAN
1. **Confianza Local en Windows (Decisión Manual):** Si es la primera vez que ejecuta mkcert en la laptop, instale el certificado raíz en el almacén de Windows ejecutando manualmente una vez en terminal con privilegios de Administrador:
   ```powershell
   mkcert -install
   ```
2. **Generación de Certificados de Red:** Genere los certificados locales vinculados a su IP LAN:
   ```powershell
   .\scripts\setup_certs.ps1
   ```
   *Si la laptop cambió de red Wi-Fi o asignación de IP:* Regenere explícitamente indicando la IP deseada:
   ```powershell
   .\scripts\setup_certs.ps1 -AdditionalIP <IP-LAN-ACTIVA> -Force
   ```
> [!CRITICAL] **Regla Estricta de Claves Criptográficas**
> Transfiera a la tablet y al teléfono celular **ÚNICAMENTE el certificado público `rootCA.pem`** (ubicado en la ruta mostrada por `mkcert -CAROOT`).
> **JAMÁS copie la clave privada `rootCA-key.pem` ni `lan-key.pem` a los dispositivos móviles.**
> - **Android:** Ajustes -> Seguridad -> Cifrado y credenciales -> Instalar un certificado -> Certificado de CA.
> - **iOS / iPadOS:** Ajustes -> Perfil descargado -> Instalar. Luego en Ajustes -> General -> Información -> Ajustes de confianza de certificados -> Activar confianza total para mkcert root.

### 6. Inicio del Servidor FastAPI con TLS y Modo Seguro
Inicie el backend utilizando el lanzador interactivo seguro:
```powershell
.\scripts\start-demo.ps1
```
Si utiliza una interfaz específica o zona Wi-Fi móvil: `.\scripts\start-demo.ps1 -LanIP <IP>`.
El script validará semánticamente `config/app_config.json` antes de pedir credenciales, solicitará interactivamente la clave de operador y la clave de administración mediante `Read-Host -AsSecureString` (en memoria solamente, sin eco en pantalla ni almacenamiento en historial), verificará criptográficamente el certificado TLS y la coincidencia exacta de la IP en el SAN, comprobará que el puerto 8000 esté libre y sin instancias en conflicto, y arrancará en modo `real` seguro con serial simulado (`safe_startup`).

### 7. Apertura y Autenticación de Administrador en la Tablet
Desde el navegador de la tablet conectada a la misma red Wi-Fi, abra la URL indicada por el lanzador:
```
https://<IP-LAN-LAPTOP>:8000/
```
En la sección **Sesión y backend**:
1. En el desplegable `Rol de sesión`, elija **`Administrador · configuración, calibración y hardware`** *(el rol `controller` no tiene permisos para calibrar, editar inventario ni controlar Arduino)*.
2. Ingrese la `Clave de administrador`.
3. Pulse el botón **«Verificar y conectar»**.
4. Pulse el botón **«Tomar Control Físico (Admin Takeover)»** para asumir el control exclusivo del sistema.

### 8. Generación del Código QR de Emparejamiento
En el dashboard de la tablet:
1. En el selector `Fuente de Video`, elija **Teléfono móvil (WebRTC / LAN) (remote_phone)**.
2. Pulse el botón **«Vincular Celular (QR)»**.
3. Se abrirá el modal con el código QR conteniendo un token efímero de un solo uso en el fragmento hash (`#token=...`).
*(Nota: El token es de un solo uso y expira en 180 s. Si el teléfono se recarga o pierde la sesión, cierre el modal y pulse nuevamente «Vincular Celular (QR)» para obtener una nueva invitación).*

### 9. Conexión y Transmisión de Video desde el Teléfono
1. Con la cámara o navegador del teléfono móvil, escanee el código QR.
2. El teléfono abrirá `https://<IP-LAN-LAPTOP>:8000/camera#token=...`.
3. El frontend consumirá el token de forma atómica y solicitará permiso para acceder a la cámara (`getUserMedia`).
4. Conceda permiso. La señalización WebSocket (`/v1/camera/signal`) se conectará y el flujo WebRTC transmitirá video en vivo hacia la tablet.

### 10. Enumeración y Preparación de Hardware Arduino
1. Conecte el Arduino Nano al puerto USB de la laptop mediante cable.
2. En la tablet, diríjase a la pestaña **Arduino** (sección *Control de Iluminación y Hardware Arduino*).
3. Pulse el botón **«Actualizar puertos»** para enumerar los puertos COM detectados por el sistema.
4. En el desplegable `Puerto Serie`, seleccione el puerto correspondiente (ej. `COM3`).
5. Deje seleccionado el puerto; la conexión se realiza después de confirmar el inventario en el paso 11. (Verifique únicamente los indicadores `Conectado / Desconectado / Simulado` y `Confirmación ACK`; no asuma estados de watchdog inexistentes).

### 11. Confirmación y Guardado del Inventario de 9 Herramientas
1. Diríjase a la pestaña **Herramientas**.
2. Para cada una de las 9 herramientas (`tool_1` a `tool_9`), revise y configure:
   - `Nombre visible (UI)` (ej. *Destornillador Phillips*)
   - `Ubicación física` (ej. *Casilla A1*)
   - `Nombre para IA (Clasificación)` (ej. *phillips screwdriver*)
   - `LED asignado (Intercambio automático)` (verifique la correlación digital D2 a D10 para índices 0 a 8)
   - Rasgos visuales descriptivos: escriba en `Nuevo rasgo visible (presiona Enter o Añadir)` y pulse **«Añadir»** (ej. *mango amarillo, punta metálica en cruz*).
3. En la barra de acciones inferior, pulse **«Confirmar inventario real»** (marca el inventario como confirmado).
4. Pulse **«Guardar borrador»** y seguidamente **«Guardar y aplicar»** (guarda y persiste en `config/app_config.json`).
5. Regrese a la pestaña **Arduino** y pulse **«Conectar Arduino»**. Compruebe que el indicador reporte estado `Conectado` con el microcontrolador activo.

### 12. Calibración Óptica del Fondo Vacío
1. Asegúrese de que la bandeja de trabajo esté completamente **vacía** y bajo la iluminación habitual de la sala.
2. En la tablet, diríjase a la pestaña **Calibración** (sección *Reconocimiento IA y Bandeja Vacía*).
3. En **«Perfiles de Calibración por Cámara (Multicámara)»**, pulse la pastilla **`remote_phone`** y confirme que indique la fuente seleccionada.
4. En la tarjeta `1. Imagen de Fondo (Bandeja Vacía)`, pulse **«📷 Tomar foto de bandeja vacía (Cámara)»** (o si cuenta con un archivo previo, «📁 Seleccionar archivo de fondo»).
5. El sistema guardará la imagen de referencia para la sustracción de fondo y segmentación de contornos.

### 13. Delimitación de la Región de Interés (ROI)
1. En la misma pestaña de Calibración, ubíquese en la tarjeta `2. Delimitación de Región de Interés (ROI)`.
2. Delimite la superficie útil de la bandeja sobre el visor SVG interactivo arrastrando el recuadro, o ingrese numéricamente las coordenadas exactas en los campos:
   - `X`, `Y`, `Ancho`, `Alto`.
3. Pulse el botón **«Guardar ROI»** (persiste la delimitación en `config/roi_remote_phone.json`).

### 14. Configuración del Clasificador Textual Zero-Shot
1. En la tarjeta `3. Configuración del Clasificador y Galería`, expanda el desplegable **«⌄ Configuración avanzada de clasificación»**.
2. Seleccione la opción de radio:
   **`Solo texto: Usa nombres y descripciones del inventario (zero-shot)`**.
   *(Permite clasificar con el modelo `embeddinggemma-2` basándose directamente en los nombres y rasgos visuales redactados en el inventario, sin requerir fotos de entrenamiento)*.
3. Pulse el botón **«Guardar parámetros avanzados»**.
4. Vaya a la pestaña **Sistema** y, en **«Galería de Embeddings»**, pulse **«Preparar IA»**. Espere el progreso y confirme que el estado indique la galería compilada y sincronizada.
5. Si el modo aún aparece como preparación, revise los requisitos mostrados por el asistente; no fuerce el modo real ni cambie umbrales para fabricar un resultado positivo.

### 15. Salida del Modo Mantenimiento, Activación Real y Retorno a Inspección
1. Vaya a la pestaña **Arduino** y confirme que el panel muestre el estado de mantenimiento actual.
2. Si el estado indica mantenimiento activo, sólo dentro del panel **«Modo de Mantenimiento y Pruebas Individuales»** pulse **«Salir de Modo Mantenimiento»** para habilitar la inferencia en vivo. Esta acción no se realiza en la telemetría superior.
3. Vaya a la pestaña **Sistema**. Cuando los requisitos estén completos y el botón esté habilitado, pulse **«Modo Real (IA Gemma + OpenCV)»**. Si está deshabilitado, siga la lista **«Requisitos pendientes para activar el Modo Real»** y vuelva a intentarlo después de resolverla; no fuerce el cambio.
4. En la barra superior de navegación, pulse **«← Volver a Inspección»** para acceder a la pantalla operativa principal.

### 16. Prueba de Identificación de Una Herramienta
1. Coloque una única herramienta física (ej. Alicate) en su casilla asignada dentro de la bandeja.
2. En la tablet, pulse el botón **«Escanear Cámara Remota»** (o si usa webcam: *«Escanear Cámara Web»*; si usa archivo: *«Analizar foto»*).
3. Confirme en pantalla que se dibuje el recuadro delimitador (*bounding box*), se muestre el nombre del inventario, la ubicación de la casilla y la similitud; este valor no es una probabilidad de confianza.

### 17. Prueba de Identificación Multiobjeto Concurrente
1. Coloque simultáneamente tres o cuatro herramientas distintas en diferentes casillas de la bandeja.
2. Pulse el botón **«Escanear Cámara Remota»** (o active el interruptor **«Continuo 2 FPS»** para escaneo continuo).
3. Verifique que el algoritmo de seguimiento espacial identifique y etiquete individualmente cada herramienta sin interferencias entre casillas.

### 18. Observación de LEDs y Confirmación de Firmware
1. Observe directamente la bandeja de trabajo: deben iluminarse los LEDs correspondientes a las casillas de las herramientas detectadas.
2. En el panel `Salida física` del dashboard, compruebe que se refleje la máscara de bits activa y el estado `Confirmación ACK` (`ACK verificado` con código `ACK:N`).
*(Nota técnica: La respuesta ACK confirma que el microcontrolador Arduino aplicó la orden digital en los pines D2 a D10; la verificación de que la luz LED emite efectivamente debe ser observada físicamente por el operador).*

### 19. Resiliencia Operativa y Alternativas de Cámara ante Contingencias
1. Si durante la demostración la red local sufre congestión o caídas de paquetes WebRTC, el cliente móvil en el teléfono conmuta automáticamente a subida de fotogramas vía HTTP JPEG (`POST /v1/camera/frame-upload`).
2. Si requiere cambiar de fuente de video durante la sesión:
   - Use el desplegable `Fuente de Video` para alternar a **Cámara web integrada (laptop_builtin)**, **Cámara web externa USB (usb_webcam)** o **Carga manual de imagen (manual_upload)**.
3. Si el teléfono se recarga o se desconecta, el token efímero original quedó consumido: pulse nuevamente **«Vincular Celular (QR)»** en la tablet para generar una nueva invitación.
> [!WARNING] **Prácticas Prohibidas durante la Demostración**
> - **JAMÁS bloquee deliberadamente puertos UDP o el cortafuegos durante la presentación.**
> - **NO altere ni reduzca los umbrales de clasificación para forzar falsos positivos.** Conserve los parámetros nominales validados en la calibración.

---

## Diagnóstico y Matriz de Red (Verificaciones de Conectividad)

Si experimenta problemas de comunicación entre la Laptop, Tablet o Teléfono, ejecute este diagnóstico ordenado por capas:

### Capa 1: Identificación y Perfil de Interfaz en Windows
Identifique qué dirección IPv4 está asignada a la interfaz activa y su perfil de red:
```powershell
Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*", "Ethernet*" | Where-Object { $_.IPAddress -notmatch '^169\.254\.' } | Select-Object IPAddress, InterfaceAlias
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory
```
- **Perfil de Red (`NetworkCategory`):** Si la interfaz figura como `Public`, Windows Defender puede restringir el tráfico entrante. Cambie el perfil a **Privada** en la configuración de red de Windows.
- **Regla de Firewall Entrante (Puerto 8000):** La existencia de una regla pasiva permite el tráfico en el host, pero no sustituye la accesibilidad en el conmutador Wi-Fi:
  ```powershell
  # Crear regla explícita en PowerShell Administrador (si fuera necesario):
  New-NetFirewallRule -DisplayName "ToolLightHouse LAN (HTTPS)" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet
  ```
- **Parámetro `-LanIP` y `-AdditionalIP`:** Si su equipo tiene múltiples adaptadores activos o genera una zona con cobertura inalámbrica móvil (Mobile Hotspot), genere el certificado indicando esa IP:
  ```powershell
  .\scripts\setup_certs.ps1 -AdditionalIP 192.168.137.1 -Force
  .\scripts\start-demo.ps1 -LanIP 192.168.137.1
  ```

### Capa 2: Alcance entre Dispositivos y Aislamiento Wi-Fi (AP Isolation)
- **Prueba de Conectividad Real:** Los teléfonos y tablets no tienen puertos TCP abiertos y suelen ignorar el comando *ping* (ICMP). La prueba definitiva de alcance es **abrir `https://<IP-LAN-LAPTOP>:8000/` directamente desde el navegador de la tablet y del teléfono**.
- **Aislamiento de Clientes (Client Isolation):** En redes institucionales, los routers Wi-Fi frecuentemente bloquean la comunicación directa entre dispositivos inalámbricos.
- **Solución Recomendada:** Utilice un router Wi-Fi dedicado sin Internet o active en Windows la **Zona con cobertura inalámbrica móvil (Mobile Hotspot)** y conecte la tablet y el teléfono a esa red.

### Capa 3: Señalización WebSocket y WebRTC ICE
- **Señalización:** Opera a través de WebSocket seguro en `wss://<IP-LAN-LAPTOP>:8000/v1/camera/signal`. Si el navegador móvil rechaza el certificado HTTPS, el WebSocket se cerrará de inmediato.
- **Candidatos ICE:** En redes locales sin salida a Internet exterior, WebRTC negocia exclusivamente candidatos locales tipo `host`. Si la conexión P2P no concluye en 7 segundos, el frontend del teléfono conmuta automáticamente a subida de fotogramas HTTP.

---

## Si Algo Falla (Resolución Rápida en Vivo)

| Síntoma | Causa Frecuente | Solución Inmediata |
|---|---|---|
| **La tablet no abre `https://<IP>:8000/` (Timeout)** | Perfil de red en `Public` o Client Isolation en el router Wi-Fi. | Verifique `Get-NetConnectionProfile`. Cambie a red Privada. Si persiste, active el Mobile Hotspot de la laptop y use `-LanIP`. |
| **El celular muestra advertencia de certificado no seguro** | No se instaló `rootCA.pem` o cambió la IP LAN. | Verifique que la IP de la barra coincida con el certificado. Ejecute `.\scripts\setup_certs.ps1 -AdditionalIP <IP> -Force` e instale `rootCA.pem` en el celular. |
| **El celular no pide permiso de cámara** | La página no se abrió mediante HTTPS o la pestaña está en segundo plano. | Asegúrese de navegar a través de `https://` (no `http://`). En iOS Safari, toque la pantalla para autorizar medios. |
| **El código QR da error "Token inválido o expirado"** | El token ya fue consumido por otra pestaña o superó los 180 s. | Cierre el modal en la tablet y pulse de nuevo «Vincular Celular (QR)» para generar una nueva invitación. |
| **Error al iniciar: "Port 8000 occupied" o "Duplicate instance"** | Quedó un proceso previo de Python o Uvicorn en segundo plano. | Identifique el proceso con `Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'"` y ciérrelo antes de reiniciar. |
| **Arduino no conecta o no figura en la lista de puertos** | Cable USB desconectado o inventario no confirmado. | Reconecte el cable USB. Confirme el inventario de 9 herramientas en la pestaña «Herramientas» y pulse «Conectar Arduino». |
| **Detecciones inestables o fondo no segmentado** | Iluminación ambiental modificada tras la calibración. | Retire las herramientas y pulse «📷 Tomar foto de bandeja vacía (Cámara)» nuevamente con la luz actual de la sala. |
