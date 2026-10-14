# Desarrollo en PC personal

El nombre del documento conserva la ruta de trabajo prevista en Notion; no exige una marca/modelo de PC. Esta máquina sirve para desarrollar, hacer tests sintéticos y construir React. La demo final se instala por separado en la laptop del compañero.

Para un clone nuevo en Windows:
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

El modo mock no requiere Torch, GPU, pesos ni Arduino. El arranque y clave de emparejamiento están en README. Mantener la clave sólo en entorno local; el teléfono la ingresa para iniciar una sesión. Desde PC abrir localhost; teléfono usa IPv4 de laptop en Wi-Fi. Para cámara web en teléfono se necesita HTTPS confiable; para fotografía se puede usar el selector nativo.

El código original `probar_gemma.py`, pesos locales y `samples/objeto.jpg` se conservaron. El nuevo smoke se ejecuta desde el paquete para aplicar precisión/validación correctas. No hace falta reemplazar el entorno AI existente para probar frontend.

## Trabajo del equipo
| Área | Archivos |
| --- | --- |
| Captura/segmentación | `camera.py`, `segmentation.py`, configuración/calibración |
| Modelo/datos | `embeddings.py`, `gallery.py`, `classifier.py`, datos privados |
| Estado/tracking | `tracking.py`, `state_manager.py`, `pipeline.py` |
| Hardware | `serial_transport.py`, `led_controller.py`, `firmware/nano` |
| App/API | `api/`, `frontend/src/`, tests de integración |

Coordinar contratos antes de editar `pipeline.py`/API. Mantener datos privados separados de código. Para transferir a GitHub revisar `.gitignore`, archivos visibles en `git status`, y no añadir `.venv`, `models`, imágenes privadas, cachés, tokens ni certificados. El Git local no tiene commits/remoto todavía; publicación y destino se deciden después.
