# ToolLighthouse

Prototipo local para reconocer múltiples herramientas sobre una bandeja y señalar sus ubicaciones con nueve LEDs de Arduino Nano. El teléfono captura fotografías y muestra cajas/resultados en un dashboard React; la laptop ejecuta OpenCV, tracking y EmbeddingGemma 2 y controla USB serial. No se ejecutan modelos en el teléfono.

El desarrollo se realiza en esta PC personal. El destino final es la laptop del compañero con RTX 4060 o RTX 5050. Cada equipo instala su propio entorno; la demo completa funciona en una sola laptop, sin Internet una vez preparados build, dependencias, modelo y galería. El reconocimiento predeterminado es zero-shot imagen → texto: utiliza nombres y descripciones del inventario, sin entrenamiento ni fotografías de referencia. Los nombres reales y ubicaciones de las nueve herramientas siguen pendientes: el inventario incluido es una plantilla.

## Inicio de desarrollo

Desde PowerShell en la raíz, Python 3.11 y Node.js 24:
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Push-Location frontend
npm ci
npm test
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m pytest -q
```

Si ya tienes `.venv`, conservarlo: instalar sólo las dependencias faltantes. Mock no carga Torch, pesos ni GPU. Crear una clave de emparejamiento local y escribir la misma en el teléfono; no guardarla en Git:
```powershell
$env:TOOLLIGHTHOUSE_PAIRING_KEY = Read-Host 'Clave local para emparejar el teléfono'
.\.venv\Scripts\python.exe -m toollighthouse serve --mode mock --simulate-leds --host 0.0.0.0 --port 8000
```

En Linux/macOS, el mismo modo de desarrollo se prepara con `python3 -m venv .venv`, `source .venv/bin/activate`, `python -m pip install -e '.[dev]'` y los mismos comandos npm dentro de `frontend`. Usar `python -m toollighthouse serve --mode mock --simulate-leds` con `TOOLLIGHTHOUSE_PAIRING_KEY` definido en el entorno. Esos hosts no se probaron durante esta ejecución; el firmware y paquete usan el mismo contrato. CUDA requiere una GPU/instalación compatible; en macOS usar CPU para el modelo o mantener mock.

Abrir [dashboard local](http://127.0.0.1:8000/) en PC. Desde el teléfono usar `http://IP-DE-LAPTOP:8000/`, con ambos en la misma Wi-Fi privada. La interfaz solicita URL, clave y rol; un solo controlador opera la escena. Los demás pueden elegir lector. El selector **Elegir foto** permite cámara nativa donde el navegador lo soporte; cámara web/video requiere HTTPS confiable o localhost. Ver `docs/NETWORK.md` para firewall y HTTPS.

1. Emparejar como controlador.
2. En modo real, capturar bandeja vacía y guardar fondo con la misma resolución/orientación; mantener cámara fija cenital.
3. Elegir una fotografía de herramientas separadas y analizarla.
4. Revisar cajas, IDs, categorías/rechazos, ubicaciones y similitud coseno; no es porcentaje de confianza.
5. Revisar estado solicitado y ACK confirmado. El modo mock y las luces simuladas aparecen identificados.
6. Liberar control al terminar. Una escena o sesión vencida no debe reactivar LEDs mediante una inferencia anterior. Por defecto la foto conserva luces hasta 30 segundos y el video vence tras 2 segundos sin resultados frescos; estos tiempos son configurables.

## Modos

| Modo | Visión / modelo | Arduino |
| --- | --- | --- |
| Mock | Predicciones sintéticas identificadas para desarrollo/tests | Simulado |
| Real con `--simulate-leds` | Cámara/foto y Gemma real en laptop | Simulado |
| Real con `--serial COM5` | Cámara/foto y Gemma real | Nano físico; inventario definitivo confirmado |

La simulación de LEDs no sustituye el modelo por un mock. Los modelos se cargan desde `models/embeddinggemma-2`; sólo el comando explícito de descarga utiliza red. Audio se desactiva, imágenes/texto comparten embeddings y la precisión es BF16 compatible o FP32.

## Galería textual: IA real sin fotos de referencia

Los comandos de desarrollo anteriores instalan `.[dev]` para mock. Para IA real instalar también Torch/Torchvision CUDA y `.[dev,ai]` siguiendo `docs/DEPLOY_RTX5050.md` (también RTX 4060). Esa guía incluye descarga del modelo y smoke CUDA.

Copiar `config/inventory.example.json` a `config/inventory.json` y completar los nueve nombres y las listas `descriptions` con rasgos visibles: forma, partes, materiales y diferencias frente a otras clases. Conservar los nueve `class_id`, índices LED y pines. La plantilla no identifica las herramientas reales del equipo.

```powershell
.\.venv\Scripts\python.exe -m toollighthouse download-model --model-path models/embeddinggemma-2
.\.venv\Scripts\python.exe -m toollighthouse test-model --device cuda
.\.venv\Scripts\python.exe -m toollighthouse build-gallery --mode real --device cuda --inventory config/inventory.json --output data/gallery.json
$env:TOOLLIGHTHOUSE_PAIRING_KEY = Read-Host 'Clave local para emparejar el teléfono'
.\.venv\Scripts\python.exe -m toollighthouse serve --mode mock --inventory config/inventory.json --simulate-leds --host 0.0.0.0 --port 8000
```

En una instalación nueva, abrir el dashboard desde el teléfono y guardar una foto de la bandeja vacía como fondo, con la misma cámara fija/resolución/orientación que se usará después. Liberar el control y detener el servidor mock con Ctrl+C. El modo real exige ese fondo persistido para segmentar; no es una foto de referencia de una herramienta. Iniciar entonces el modelo real:

```powershell
.\.venv\Scripts\python.exe -m toollighthouse serve --mode real --device cuda --inventory config/inventory.json --gallery data/gallery.json --simulate-leds --host 0.0.0.0 --port 8000
```

`build-gallery` usa `text-only` por defecto y no busca carpetas de fotos. Genera embeddings de cada nombre/descripción, promedia sus vectores normalizados por categoría y vuelve a normalizar. Cada recorte de imagen se compara con esos nueve prototipos mediante similitud coseno.

| `--gallery-mode` | Datos para construir la galería |
| --- | --- |
| `text-only` (predeterminado) | Nombres y una o varias descripciones por clase; sin fotos |
| `image-only` | Fotos en `data/references/<class_id>/` para las nueve clases |
| `hybrid` | Texto y fotos de las nueve clases; `--text-weight` controla la mezcla |

Usar la misma modalidad al construir, evaluar y ejecutar la galería. Los cambios de nombres/descripciones, modo, modelo, mezcla o referencias activas invalidan el caché y requieren `build-gallery` otra vez. `classifier_profiles` en la configuración permite calibrar umbral y margen por modalidad; los valores incluidos son iniciales, sin calibrar. Las similitudes no son porcentajes de confianza. Ver `docs/DATA.md` para calibración y evaluación con fotografías reales separadas; no se ha demostrado la precisión zero-shot de las nueve herramientas.

## Arquitectura y módulos
```text
Teléfono React → multipart foto / frames → FastAPI local
                                           ↓
Captura OpenCV → fondo/ROI → cajas/recortes → tracking
                                           ↓
        nombres/descripciones → galería ↔ EmbeddingGemma 2
                                           ↓
                       rechazo / estabilización por objeto
                                           ↓
                     inventario → máscara OR → MASK:N / ACK:N
                                           ↓
                             Nano D2–D10 → nueve LEDs
Resultados / imagen normalizada / eventos WebSocket → React
```

`src/toollighthouse` separa cámara, segmentación, tracking, embeddings, galería, clasificación, estado, métricas, serial, overlays, pipeline y API. FastAPI y CLI usan el mismo pipeline. Captura e inferencia están desacopladas; no acumular frames viejos. Dos instancias de una categoría producen dos detecciones y un solo LED. Unknown/ambiguous no encienden un nuevo LED.

`firmware/nano/nano.ino` implementa mensajes de máscara completa de 0–511 a 115200 baudios, ACK, validación y watchdog de 2500 ms. Una resistencia por LED discreto; cargas potentes necesitan driver/fuente. Leer `docs/HARDWARE.md` antes de conectar. No usar D0/D1 para luces.

## Laptop de demostración y datos
Seguir `docs/DEPLOY_RTX4060.md` o `docs/DEPLOY_RTX5050.md`. Instalar Torch/Torchvision CUDA compatible en esa laptop, verificar GPU/driver y smoke real. Completar nombres/descripciones y construir la galería textual. Las fotos de referencia se reservan para `image-only`/`hybrid`; las fotografías reales de calibración y evaluación sirven para medir y ajustar el reconocimiento en cualquier modalidad. Ver `docs/DATA.md` y `docs/ACCEPTANCE.md`.

La configuración valida nueve categorías y nueve asignaciones únicas. El inventario de ejemplo no habilita operación física definitiva. El fondo calibrado es necesario para segmentar; nombres/descripciones y un modelo operativo permiten ejecutar zero-shot, pero su exactitud se debe medir con las herramientas reales.

## Comandos y contrato API
Ejecutar `python -m toollighthouse --help` para las opciones vigentes. Los comandos cubren `doctor`, `test-camera`, `calibrate`, `download-model`, `build-gallery`, `test-model`, `capture-reference`, `evaluate`, `test-leds`, `run` y `serve`. Los ejemplos verificados se detallan en `docs/COMMANDS.md`. Las rutas/sesiones/multipart/WebSocket y el formato de cajas están en `docs/API.md`.

Para desarrollo React con recarga:
```powershell
Push-Location frontend
npm run dev -- --host 0.0.0.0
Pop-Location
```
Mantener backend encendido; preferir build servido por FastAPI para prueba móvil de un solo origen.

## Validación y transferencia
`docs/VALIDATION.md` registra comandos/resultados y límites de este entorno. CI verifica Python/React y compila firmware; no valida hardware, red móvil ni precisión. El modo continuo actual es experimental; WebRTC no se declara probado.

El repositorio del equipo es [DA0806/ToolLightHouse](https://github.com/DA0806/ToolLightHouse). Para preparar la laptop del compañero:

```powershell
git clone https://github.com/DA0806/ToolLightHouse.git
cd ToolLightHouse
```

Seguir después la guía de instalación de su GPU. `.gitignore` excluye entornos, pesos, datos privados, cachés, claves y certificados; cada laptop prepara esos recursos localmente. El script original `probar_gemma.py`, pesos y muestra existentes se conservaron en la PC de desarrollo.
