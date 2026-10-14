# Laptop del compañero: RTX 5050

Esta guía también sirve para RTX 4060. La PC de desarrollo y la laptop final son hosts independientes: cada una instala su entorno. No copiar `.venv` entre computadoras ni subir pesos/fotos/secretos a GitHub.

## Instalación del código
En Windows, instalar Python 3.11 x64, Node.js 24, Git y un driver NVIDIA compatible. Para una instalación nueva clonar el repositorio del equipo y abrir PowerShell en la raíz. Si ya existe una instalación, conservar su entorno y usar `git pull --ff-only` antes de actualizar dependencias.

```powershell
git clone https://github.com/DA0806/ToolLightHouse.git
cd ToolLightHouse
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -e '.[dev,ai]' sentence-transformers==6.1.0 transformers==5.19.0 huggingface_hub==1.33.0
Push-Location frontend
npm ci
npm test
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m toollighthouse doctor
```

Los comandos anteriores reproducen las versiones de IA instaladas en desarrollo, incluido el par Torch/Torchvision CUDA. El extra `ai` declara explícitamente `torch`, `torchvision`, `sentence-transformers`, `transformers` y `huggingface_hub`; mock usa sólo `dev`. Si esas wheels no soportan el driver/GPU final, seleccionar un par Torch/Torchvision CUDA compatible según [PyTorch](https://pytorch.org/get-started/locally/) y repetir las pruebas. Una instalación del paquete no demuestra soporte CUDA de la RTX 5050/4060 del compañero.

Comprobar antes de inferir:
```powershell
.\.venv\Scripts\python.exe -c "import torch; print('Torch', torch.__version__); print('CUDA runtime', torch.version.cuda); print('CUDA disponible', torch.cuda.is_available()); print('GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else None); print('Arquitecturas wheel', torch.cuda.get_arch_list())"
```

Si CUDA no está disponible o aparece `no kernel image`, corregir driver/wheel; no declarar funcionando GPU porque el paquete se instaló. CPU FP32 puede servir para un smoke opcional, pero no se promete video a baja latencia.

## Modelo y datos
Descargar el modelo mediante el comando explícito o transferir su carpeta completa a `models/embeddinggemma-2`. Preparar todo antes de una demo sin Internet. El [modelo oficial](https://huggingface.co/google/embeddinggemma-2) y [la guía Google](https://ai.google.dev/gemma/docs/embeddinggemma/multimodal-embeddinggemma-with-sentence-transformers) definen la API multimodal. Usar BF16 en hardware compatible o FP32; nunca FP16.

```powershell
.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2
.\.venv\Scripts\python.exe -m toollighthouse test-model --device cuda --model-path models/embeddinggemma-2
if (!(Test-Path config/inventory.json)) { Copy-Item config/inventory.example.json config/inventory.json }
if (!(Test-Path config/settings.json)) { Copy-Item config/settings.example.json config/settings.json }
notepad config/inventory.json
```

Completar los nueve nombres y listas de descripciones visuales; conservar los nueve `class_id`, LEDs y pines. La plantilla no conoce cuáles son las herramientas reales del equipo. En `config/settings.json` cambiar `inventory_path` a `inventory.json` y conservar `gallery_mode: "text-only"`. No se necesitan carpetas ni fotos de referencia para construir la galería textual y no se entrena el modelo.

```powershell
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --config config/settings.json --mode real --device cuda --inventory config/inventory.json --output data/gallery.json
$env:TOOLLIGHTHOUSE_PAIRING_KEY = Read-Host 'Clave local para emparejar el teléfono'
.\.venv\Scripts\python.exe -m toollighthouse serve --config config/settings.json --mode mock --inventory config/inventory.json --simulate-leds --host 0.0.0.0 --port 8000
```

Abrir `http://IP-DE-LAPTOP:8000/` en el teléfono en la misma Wi-Fi, emparejar y guardar una fotografía de la bandeja vacía como fondo con la cámara fija final. Liberar control y detener mock con Ctrl+C. El archivo de fondo guardado permite iniciar el modo real; el fondo es para segmentación, no una referencia de clasificación.

```powershell
.\.venv\Scripts\python.exe -m toollighthouse serve --config config/settings.json --mode real --device cuda --inventory config/inventory.json --gallery data/gallery.json --simulate-leds --host 0.0.0.0 --port 8000
```

Volver a emparejar y analizar fotos de herramientas. Las imágenes capturadas se comparan con embeddings de sus textos. Si cambian dimensiones/orientación/cámara, volver a calibrar el fondo antes de analizar.

Para futuras pruebas `image-only`/`hybrid`, sí preparar fotos de referencia separadas de calibración/evaluación y seleccionar la misma `--gallery-mode` al construir y ejecutar. Cambios de texto, modalidad, modelo y parámetros de galería invalidan el caché; construirlo en la laptop final. `docs/COMMANDS.md` tiene ejemplos de las tres modalidades.

Calibrar `classifier_profiles` por modalidad usando fotos reales etiquetadas en un conjunto de calibración y comprobar precisión/falsas aceptaciones en evaluación reservada. Las similitudes coseno no son porcentajes de confianza. Ni descargar el modelo ni generar la galería textual demuestran exactitud de las nueve herramientas; seguir `docs/DATA.md`.

## Antes de conectar Nano
Primero ejecutar mock y fotografía desde el teléfono siguiendo README y `NETWORK.md`. Después ejecutar modelo real con LEDs simulados. Sólo tras probar galería, rechazo y umbrales, cargar `firmware/nano/nano.ino`, verificar circuito según `HARDWARE.md` y seleccionar COM explícito. No conectar hardware con inventario de ejemplo.

Ejecutar pruebas `ACCEPTANCE.md`; registrar tiempos y VRAM. Una RTX 5050 no garantiza exactitud, cámara, iluminación ni circuito. El reporte de esta PC aparece en `VALIDATION.md`; repetir sus pruebas en la laptop final.

## Recuperación
- Modelo ausente/incompatible: revisar archivos completos y versiones de Sentence Transformers/Transformers. El modo mock no necesita descargarlo.
- Foto rechazada por dimensiones/fondo: repetir fondo/ROI con misma cámara/orientación/resolución.
- COM ocupado: cerrar Arduino Serial Monitor y otros programas. Verificar cable USB de datos y puerto después de reconectar.
- ACK faltante: revisar firmware/115200/newline, reinicio inicial y máscara. No asumir luces aplicadas.
- Teléfono desconectado: revisar IPv4/Wi-Fi/firewall/URL; la sesión debe recuperar o liberar control de forma explícita.
- Latencia alta: reducir frecuencia de inferencia/lote, revisar VRAM y tamaño de recortes; conservar latest-only y rechazo de frames atrasados.
