# Manual de Despliegue, Actualización y Operación en Windows (RTX 4060 / RTX 5050)

Este documento detalla el procedimiento de instalación inicial, actualización segura mediante ramas Git, validación del entorno de GPU NVIDIA y operación diaria de ToolLightHouse en la laptop servidora (Windows 11).

---

## 1. Dispositivos Objetivo y Especificaciones de Hardware

El software está diseñado para ejecutarse de forma autónoma en una laptop servidora con GPU NVIDIA dedicada:

| Equipo | GPU | VRAM | Memoria RAM del Sistema | Rol en la Arquitectura |
|---|---|---|---|---|
| **Principal** | NVIDIA GeForce RTX 4060 | 8 GB | — | Servidor central (FastAPI + OpenCV + Gemma 2 + USB Arduino) |
| **Alternativo** | NVIDIA GeForce RTX 5050 | 8 GB | — | Servidor central alternativo de pruebas |
| **Desarrollo inicial** | NVIDIA GeForce RTX 3060 | — | — | GPU observada en máquina de desarrollo; otros componentes de host no verificados |

> [!NOTE] **Delimitación de Validación de Hardware**
> El modelo y los scripts de inferencia fueron validados empíricamente en la GPU NVIDIA GeForce RTX 3060 observada en la máquina de desarrollo. El comportamiento, asignación efectiva de memoria y tiempos de respuesta en las GPUs de destino (RTX 4060 y RTX 5050) deben verificarse mediante pruebas reales en cada equipo; no se asumen versiones de runtime ni capacidades medidas sin prueba en banco.

---

## 2. Separación entre Preparación Inicial y Operación Cotidiana

Es fundamental distinguir dos momentos en el ciclo de vida del sistema:

1. **Preparación Inicial (Una sola vez):**
   - Instalación de Python 3.11, PyTorch con soporte CUDA compatible y dependencias del proyecto.
   - Descarga de los pesos del modelo `google/embeddinggemma-2` al almacenamiento local.
   - Construcción inicial de la galería zero-shot `text-only` a partir del inventario textual.
   - Compilación del frontend React (`npm run build`).
   - Generación de certificados LAN HTTPS con `mkcert` y regla de firewall de subred privada.
   - Grabación del firmware `nano.ino` en el Arduino Nano mediante Arduino IDE.

2. **Operación Cotidiana (Desde el Dashboard Web):**
   - El operador no requiere editar archivos JSON manualmente ni recompilar código en cada sesión ordinaria.
   - Inicia el servidor con el comando de arranque estándar.
   - Abre el dashboard desde la tablet (o la laptop).
   - Desde la interfaz web realiza las tareas operativas:
     - Selección y conmutación de fuentes de cámara (*Inspección* / selector).
     - Vinculación del teléfono mediante código QR (*Inspección* / *Emparejar*).
     - Conexión al puerto COM de Arduino Nano (*Arduino* / *Conectar*).
     - Calibración de fondo y ROI (*Calibración*).
     - Administración de herramientas y descripciones (*Herramientas*).
     - Diagnósticos y auditoría del sistema (*Sistema*).

---

## 3. Guía Paso a Paso de Instalación Inicial (PowerShell)

Ejecute los siguientes pasos en una ventana de PowerShell en la raíz del proyecto.

### 3.1 Entorno Virtual y Dependencias de IA

```powershell
# 1. Crear entorno virtual con Python 3.11 (64-bit)
py -3.11 -m venv .venv

# 2. Actualizar herramientas de empaquetado
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

# 3. Instalar PyTorch con soporte CUDA
# Consulte https://pytorch.org/get-started/locally/ para seleccionar la versión adecuada para el driver NVIDIA
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 4. Instalar ToolLightHouse con extras de desarrollo e inteligencia artificial
.\.venv\Scripts\python.exe -m pip install -e '.[dev,ai]'
```

### 3.2 Compilación del Frontend (React + Vite)

El backend de FastAPI sirve los archivos compilados de la SPA desde `frontend/dist/`:

```powershell
Push-Location frontend
npm ci
npm run build
Pop-Location
```

### 3.3 Descarga de Pesos y Preparación de Galería

```powershell
# 1. Descargar pesos de EmbeddingGemma 2 a models/
.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2

# 2. Verificar que la GPU cargue los tensores correctamente
.\.venv\Scripts\python.exe -m toollighthouse test-model --device cuda

# 3. Asegurar que exista una configuración e inventario base
if (-not (Test-Path "config\inventory.json")) {
    Copy-Item "config\inventory.example.json" "config\inventory.json"
}

# 4. Construir la galería zero-shot predeterminada (text-only)
.\.venv\Scripts\python.exe -m toollighthouse build-gallery `
    --mode real `
    --device cuda `
    --inventory config/inventory.json `
    --output data/gallery.json
```

### 3.4 Certificados LAN HTTPS y Firewall

```powershell
# 1. Generar certificados con mkcert
.\scripts\setup_certs.ps1

# 2. En una consola de PowerShell con permisos de Administrador, abrir el puerto 8000 para la subred local:
# New-NetFirewallRule -DisplayName "ToolLightHouse LAN (HTTPS)" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

### 3.5 Auditoría de Diagnóstico Inicial

Verifique el estado del sistema ejecutando:

```powershell
.\.venv\Scripts\python.exe -m toollighthouse doctor --check-cameras
```

El informe JSON confirmará la disponibilidad de CUDA, la presencia de los pesos del modelo, las librerías instaladas, los puertos COM detectados y las cámaras web operativas.

---

## 4. Procedimiento de Actualización Segura de la Rama Git

Para actualizar la laptop de demostración cuando otros miembros del equipo publiquen mejoras en la rama de trabajo:

> [!CAUTION] **Regla de Oro: Prohibido `git reset --hard`**
> No ejecute `git reset --hard` en la laptop de pruebas. Podría eliminar calibraciones locales, inventarios confirmados o fondos de bandeja capturados durante los ensayos.

### Secuencia de Comandos PowerShell para Actualización

```powershell
# 1. Inspeccionar cambios locales no comiteados y verificar estado
git status

# 2. Realizar copia de seguridad de la configuración persistida y calibraciones locales
if (Test-Path "config\app_config.json") {
    Copy-Item "config\app_config.json" "config\app_config.backup.json" -Force
    Write-Host "[+] Copia de seguridad de app_config.json creada." -ForegroundColor Green
}

# 3. Descargar las últimas confirmaciones del repositorio remoto
git fetch origin

# 4. Cambiar a la rama de trabajo antes del pull para evitar merges accidentales sobre main
git checkout feat/unified-dashboard-multicamera

# 5. Avanzar la rama de trabajo de forma rápida y limpia (fast-forward strictly)
git pull --ff-only origin feat/unified-dashboard-multicamera

# 6. Si hubo cambios en el frontend, reconstruir la SPA:
Push-Location frontend
npm ci
npm run build
Pop-Location

# 7. Si hubo cambios en dependencias de Python, actualizar el paquete:
.\.venv\Scripts\python.exe -m pip install -e '.[dev,ai]'

# 8. Ejecutar el asistente doctor para revalidar el entorno:
.\.venv\Scripts\python.exe -m toollighthouse doctor
```

---

## 5. Validación del Runtime de GPU en la Máquina de Destino

Para verificar que PyTorch reconozca la GPU dedicada en la laptop de destino:

```powershell
.\.venv\Scripts\python.exe -c "import torch; print('CUDA disponible:', torch.cuda.is_available()); print('Dispositivo 0:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU'); print('Soporte BF16:', torch.cuda.is_bf16_supported())"
```

Verifique que `CUDA disponible: True` y que se reporte el nombre del dispositivo asignado. La memoria disponible y la compatibilidad con formatos de precisión (BF16/FP32) dependen del runtime instalado y deben verificarse mediante el smoke test real en cada equipo.

---

## 6. Puesta en Marcha y Operación del Sistema

### 6.1 Inicio del Servidor

En la laptop servidora, abra PowerShell en la raíz del proyecto e inicie el backend con soporte SSL/TLS:

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

> [!NOTE] **Comportamiento del Puerto Serie en el Arranque**
> Al iniciar sin el parámetro `--serial`, el backend arranca con serial simulado (`SimulatedSerial()`), emitiendo salidas simuladas en lugar de quedar en un estado desconectado no inicializado. Para enlazar el microcontrolador físico, el operador puede conectar el puerto COM detectado interactivamente desde la pestaña **Arduino** del dashboard, o bien iniciar mediante CLI pasando explícitamente `--serial COMx` junto con un inventario confirmado (`--inventory <archivo> --confirm-inventory`).

### 6.2 Acceso al Dashboard

1. **Desde la Tablet Operadora (Recomendado):**
   Navegue a `https://<IP-LAN-LAPTOP>:8000/`.
   Ingrese la clave de emparejamiento (`operador123` para rol `controller`, o `admin123` para rol `admin`).
2. **Desde la Propia Laptop:**
   Abra en su navegador `https://127.0.0.1:8000/` o `https://localhost:8000/`.

### 6.3 Conexión de Arduino Nano desde la Interfaz

1. Conecte el Arduino Nano a un puerto USB de la laptop.
2. En la tablet, vaya a la sección **Arduino**.
3. Pulse **«Actualizar puertos»**. El sistema detectará los puertos COM disponibles.
4. Seleccione el puerto correspondiente y pulse **«Conectar»**.
5. Verifique que el estado informe conexión exitosa y que el watchdog muestre respuesta `ACK` activa.
