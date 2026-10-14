# Laptop del compañero: RTX 5050

Esta guía también sirve para RTX 4060. La PC de desarrollo y la laptop final son hosts independientes: cada una instala su entorno. No copiar `.venv` entre computadoras ni subir pesos/fotos/secretos a GitHub.

## Instalación del código
En Windows, instalar Python 3.11 x64, Node.js 24, Git y un driver NVIDIA compatible. Clonar el repositorio que el equipo publique y abrir PowerShell en la raíz. No se creó un repositorio remoto ni se hizo push durante esta implementación.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -e '.[dev,ai]'
Push-Location frontend
npm ci
npm test
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m toollighthouse doctor
```

Seleccionar una wheel CUDA soportada por esa GPU según [PyTorch](https://pytorch.org/get-started/locally/). El entorno de desarrollo inspeccionado usa Torch 2.11.0+cu128; eso no prueba que la RTX 5050 del compañero tenga su driver/runtime configurado. No usar wheels antiguas que carezcan de kernels para la arquitectura de la GPU.

Comprobar antes de inferir:
```powershell
.\.venv\Scripts\python.exe -c "import torch; print('Torch', torch.__version__); print('CUDA runtime', torch.version.cuda); print('CUDA disponible', torch.cuda.is_available()); print('GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else None); print('Arquitecturas wheel', torch.cuda.get_arch_list())"
```

Si CUDA no está disponible o aparece `no kernel image`, corregir driver/wheel; no declarar funcionando GPU porque el paquete se instaló. CPU FP32 puede servir para un smoke opcional, pero no se promete video a baja latencia.

## Modelo y datos
Descargar el modelo mediante el comando explícito documentado en README o transferir su carpeta completa a `models/embeddinggemma-2`. Preparar todo antes de una demo sin Internet. El [modelo oficial](https://huggingface.co/google/embeddinggemma-2) y [la guía Google](https://ai.google.dev/gemma/docs/embeddinggemma/multimodal-embeddinggemma-with-sentence-transformers) definen la API multimodal. Usar BF16 en hardware compatible o FP32; nunca FP16.

Copiar por vía local segura las referencias reales separadas de calibración/evaluación. Completar inventario real y guardar ROI/fondo desde la cámara fija final. Construir caché de galería en esta laptop. Cambios de referencias/modelo/config invalidan caché.

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
