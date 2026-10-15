# Configuración HTTPS en Red Local (LAN) y Certificados Confiables

Este manual explica cómo configurar y desplegar ToolLightHouse en una red de área local privada (LAN) con HTTPS confiable, permitiendo el acceso seguro a la cámara (`getUserMedia`), WebRTC y Screen Wake Lock en tablets y teléfonos móviles sin recurrir a servicios públicos de nube.

---

## 1. Justificación de HTTPS en Red Local

Los navegadores web modernos (Chrome en Android, Safari en iOS/iPadOS y navegadores de escritorio) imponen la política de **Contextos Seguros** (*Secure Contexts*):
- Las API `navigator.mediaDevices.getUserMedia()`, `RTCPeerConnection` y `navigator.wakeLock` están **estrictamente deshabilitadas** sobre conexiones HTTP ordinarias que no sean `localhost` (como `http://192.168.1.50:8000/`).
- Para que la tablet operadora y el teléfono transmisor puedan capturar y transmitir video sobre la Wi-Fi local, el servidor debe exponer un certificado SSL/TLS confiable.
- No basta con utilizar certificados autofirmados ignorando advertencias: navegadores como Safari iOS bloquean completamente el acceso al hardware si la autoridad de certificación (CA) no está explícitamente instalada en los ajustes de confianza del sistema operativo.

---

## 2. Generación de Certificados con `mkcert`

Para evitar depender de autoridades públicas (como Let's Encrypt, que exigen nombres de dominio públicos y conexión a Internet), utilizamos `mkcert`, una herramienta que crea una Autoridad Certificadora (CA) raíz local y emite certificados con Nombres Alternativos del Sujeto (SANs) válidos para la IP de la laptop en la red local.

### 2.1 Requisitos Previos e Instalación de `mkcert` en Windows

En la laptop servidora (Windows 11):
```powershell
# Opción A: Mediante winget (recomendado)
winget install FiloSottile.mkcert

# Opción B: Mediante Chocolatey
choco install mkcert
```

### 2.2 Instalación de la CA en el Almacén de Confianza de Windows (Manual)

Para que los navegadores de la laptop confíen en el certificado emitido, ejecute manualmente **una única vez** en una consola de PowerShell con privilegios de Administrador:

```powershell
mkcert -install
```
> [!NOTE]
> Este comando instala la CA local en el almacén de certificados raíz de Windows. El asistente de configuración de ToolLightHouse documenta esta acción pero **no modifica el almacén del sistema de forma automática**.

### 2.3 Ejecución del Asistente `scripts/setup_certs.ps1`

Desde la raíz del proyecto en PowerShell:

```powershell
.\scripts\setup_certs.ps1
```

El script realiza automáticamente las siguientes acciones:
1. Crea el directorio seguro `certificates/` (cubierto en `.gitignore`).
2. Detecta la dirección IPv4 activa de su interfaz Wi-Fi/Ethernet principal (filtrando adaptadores virtuales de WSL, Tailscale, ZeroTier y direcciones de loopback).
3. Emite un certificado con los SANs necesarios: `localhost`, `127.0.0.1`, `::1` y la IPv4 local detectada (por ejemplo, `192.168.1.50`).
4. Genera dos archivos:
   - `certificates/lan-cert.pem`: Certificado público del servidor.
   - `certificates/lan-key.pem`: Clave privada del servidor.

Si necesita agregar una dirección IP adicional o un nombre de host manual:
```powershell
.\scripts\setup_certs.ps1 -AdditionalIP 192.168.1.120 -Hostnames @("laptop-servidora.local")
```

---

## 3. Seguridad de las Claves y Límites de Confianza

> [!CAUTION] **Distinción Crítica entre CA Pública y Clave Privada**
> Para consultar la ubicación de la CA raíz creada por `mkcert`, ejecute:
> ```powershell
> mkcert -CAROOT
> ```
> Ese directorio contiene dos archivos con propósitos diametralmente distintos:
> 1. **`rootCA.pem` (CERTIFICADO PÚBLICO):** Es el único archivo que debe copiarse a los teléfonos y tablets autorizados para habilitar la confianza SSL.
> 2. **`rootCA-key.pem` (CLAVE PRIVADA):** **JAMÁS debe compartirse, transferirse a dispositivos móviles ni comitearse a Git.** Quien posea esa clave podría emitir certificados de confianza para cualquier sitio en los dispositivos que hayan instalado su CA.

Asegúrese de que el archivo `.gitignore` contenga la regla:
```text
certificates/
```

---

## 4. Instalación de la CA Pública en Dispositivos Móviles

Para que el teléfono y la tablet naveguen a `https://<IP-LAN>:8000/` sin advertencias de seguridad y con acceso completo a la cámara, instale `rootCA.pem` en cada dispositivo:

### 4.1 En Dispositivos Android (Teléfono / Tablet)

1. **Transferir `rootCA.pem`:** Conecte el dispositivo mediante cable USB o transfiera el archivo a través de almacenamiento local compartido.
2. **Instalar Certificado:**
   - Abra **Ajustes** $\rightarrow$ **Seguridad y privacidad** $\rightarrow$ **Ajustes avanzados** $\rightarrow$ **Cifrado y credenciales** (la ruta exacta puede variar según el fabricante).
   - Seleccione **Instalar un certificado** $\rightarrow$ **Certificado de CA**.
   - Si el sistema muestra una advertencia de seguridad, seleccione **Instalar de todos modos**.
   - Busque y seleccione el archivo `rootCA.pem` transferido.
3. **Verificación:** Abra Chrome en Android y navegue a `https://<IP-LAN>:8000/`. El candado de seguridad debe aparecer cerrado sin alertas.

### 4.2 En Dispositivos Apple (iPad / iPhone)

1. **Transferir `rootCA.pem`:** Envíe el archivo mediante AirDrop o descárguelo desde la red local.
2. **Instalar Perfil:**
   - Al recibir el archivo, el sistema notifica *«Perfil descargado»*.
   - Vaya a **Ajustes** $\rightarrow$ **Perfil descargado** (en la parte superior) y pulse **Instalar** (ingrese su código de desbloqueo).
3. **Paso Crítico de Confianza Plena:**
   - Vaya a **Ajustes** $\rightarrow$ **General** $\rightarrow$ **Información** $\rightarrow$ **Ajustes de confianza de certificados** (al final de la pantalla).
   - En la sección *«Confiar plenamente en los certificados raíz»*, busque el certificado emitido por `mkcert` y **active el interruptor**.
   - Confirme en el cuadro de diálogo. Sin este paso, Safari rechazará la cámara aunque el perfil esté instalado.

---

## 5. Regla del Firewall de Windows (Puerto 8000)

Para permitir que los dispositivos de la subred local se conecten al puerto 8000 de la laptop servidora, ejecute el siguiente comando en PowerShell con privilegios de **Administrador**:

```powershell
New-NetFirewallRule -DisplayName "ToolLightHouse LAN (HTTPS)" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

> [!IMPORTANT]
> Este comando limita la apertura exclusivamente a redes marcadas como **Privadas** y restringe el tráfico a la **subred local** (`LocalSubnet`), impidiendo la exposición del puerto a redes públicas o routers externos.

Para retirar la regla posteriormente:
```powershell
Remove-NetFirewallRule -DisplayName "ToolLightHouse LAN (HTTPS)"
```

---

## 6. Advertencia sobre Direccionamiento IP y mDNS (`.local`)

- **Limitación de Android con mDNS:** Aunque Windows y Apple soportan mDNS (`.local`) de forma nativa mediante Bonjour/Avahi, muchas versiones y capas de personalización de Android **no resuelven nombres `.local`** de manera consistente sin servicios adicionales.
- **Práctica Recomendada:** Utilice siempre la **dirección IPv4 numérica fija** de la laptop en la red Wi-Fi (por ejemplo, `https://192.168.1.50:8000/`).
- Para consultar la IP actual en Windows:
  ```powershell
  Get-NetIPConfiguration
  ```

---

## 7. Ejecución del Servidor con TLS

Una vez emitidos los certificados en `certificates/`, inicie el backend utilizando el comando oficial del CLI:

### Modo Producción (GPU Real + CUDA + Arduino Serial)
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = "operador123"
$env:TOOLLIGHTHOUSE_ADMIN_KEY = "admin123"

python -m toollighthouse serve `
    --mode real `
    --device cuda `
    --ssl-certfile certificates/lan-cert.pem `
    --ssl-keyfile certificates/lan-key.pem `
    --host 0.0.0.0 `
    --port 8000
```

### Modo Simulación / Desarrollo (Mock + LEDs Simulados)
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = "operador123"
$env:TOOLLIGHTHOUSE_ADMIN_KEY = "admin123"

python -m toollighthouse serve `
    --mode mock `
    --simulate-leds `
    --ssl-certfile certificates/lan-cert.pem `
    --ssl-keyfile certificates/lan-key.pem `
    --host 0.0.0.0 `
    --port 8000
```

### Alternativa Directa con Uvicorn Factory
Si prefiere invocar el cargador ASGI directamente:
```powershell
python -m uvicorn toollighthouse.api:create_app --factory `
    --host 0.0.0.0 `
    --port 8000 `
    --ssl-certfile certificates/lan-cert.pem `
    --ssl-keyfile certificates/lan-key.pem
```
*(Nota: la invocación directa vía factory carga las opciones efectivas desde las variables de entorno y el archivo de configuración persistido `config/app_config.json`).*

---

## 8. Matriz de Resolución de Problemas

| Problema | Causa | Solución |
|---|---|---|
| Advertencia de certificado no confiable (`NET::ERR_CERT_AUTHORITY_INVALID`) | La CA pública `rootCA.pem` no se instaló en el dispositivo móvil, o falta el paso de confianza plena en iOS. | Instale `rootCA.pem` siguiendo la sección 4. En iOS, active el interruptor en *Ajustes de confianza de certificados*. |
| El navegador rechaza la cámara sin pedir permisos | Conexión iniciada vía HTTP en lugar de HTTPS. | Verifique que la URL comience con `https://` y no `http://`. |
| La tablet no carga la página (`ERR_CONNECTION_TIMED_OUT`) | El firewall de Windows bloquea el puerto 8000 o la red está configurada como Pública. | Ejecute el comando de firewall de la sección 5 y asegúrese de que el perfil de red de Windows sea «Privado». |
| WebSockets no conectan (`wss://`) | El certificado no coincide con la IP ingresada en la barra de direcciones. | Regenere los certificados ejecutando `.\scripts\setup_certs.ps1 -AdditionalIP <IP_ACTUAL>`. |
