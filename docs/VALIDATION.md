# Resultados de validación

Ejecución del 13 de octubre de 2026, hora de Guatemala. Desarrollo en PC personal; no se usó la laptop final del compañero.

## Entorno observado
Windows x64, Python 3.11.9, Node 24.15.0, npm 11.12.1. Entorno AI existente: NVIDIA GeForce RTX 3060, Torch 2.11.0+cu128, Sentence Transformers 6.1.0, Transformers 5.19.0 y OpenCV 5.0.0. Se conservaron `probar_gemma.py`, pesos locales y `samples/objeto.jpg`. No se descargaron ni reemplazaron los pesos. Se instaló un entorno `.venv-dev` independiente sin Torch para verificar el modo ligero.

## Evidencia de software
| Verificación | Resultado |
| --- | --- |
| `.venv-dev/Scripts/python.exe -m pytest -q --disable-warnings` | **84 passed, 1 skipped**; entorno limpio sin Torch; 105 avisos de deprecación contabilizados |
| Smoke de importación mock sin Torch | Correcto; `find_spec('torch')` ausente |
| `npm test` | **15 pruebas aprobadas** |
| `npm run typecheck` / `npm run build` | Correctos; build React local sin recursos de CDN |
| Compilación Nano `arduino:avr:nano` | Correcta: 4280 bytes flash, 230 bytes SRAM |
| `test-model --device cuda --image samples/objeto.jpg` | Imagen/texto: **768 dimensiones, valores finitos** |
| `run --mode mock --simulate-leds --video .tools/demo.avi --headless --max-frames 10` | **10 frames procesados, 10 capturados**, latest-only |
| `scripts/smoke_photo.py` contra Uvicorn local | 0/1/2/3/5/9 objetos → igual cantidad de cajas, máscara solicitada = ACK simulado; 9 objetos → 511 |
| Exclusión y WebSocket por HTTP real | Segundo controlador rechazado; eventos system/detection/arduino_ack recibidos; sesión liberada al finalizar |
| Dashboard por navegador real local | Emparejamiento, foto, fondo, nueve cajas y ACK simulado; imagen normalizada 900×600 |
| Viewports 320/390/768/914/1280 px | Sin desbordamiento horizontal; columnas de escritorio sin solapamiento |
| Revisión independiente de código | Sin defectos Critical/Important abiertos tras correcciones finales |

El smoke automatizado de GPU se omite por defecto para CI y entornos sin pesos/CUDA; se habilita con `TOOLLIGHTHOUSE_RUN_REAL_SMOKE=1`. El comando CLI de modelo se ejecutó realmente en CUDA en esta PC. Eso prueba carga y embeddings finitos, **no exactitud de clasificación** con las nueve herramientas finales.

Las capturas en `docs/assets/dashboard-simulation-*.png` muestran una imagen sintética de nueve rectángulos y predicciones mock explícitas. Son capturas reales del navegador durante la prueba; no son fotografías ni reconocimiento real de herramientas. Se usó una clave de fixture descartable y servidor limitado a loopback para la verificación; no se guardaron credenciales permanentes.

Los tests usan directorios temporales y no leen/escriben la calibración persistente del desarrollo. Se comprobó que la suite sigue pasando después de guardar un fondo con el smoke HTTP. El visor OpenCV cuenta con HUD de categorías, bits/ACK, conexión y métricas; su prueba con video sintético verifica eventos reales de captura. En headless, FPS de presentación es null; con menos de 20 muestras, los percentiles y tasas insuficientes permanecen null.

## Regresiones cubiertas
Segmentación multiobjeto, máscaras/ROI y dimensiones; asociación global de tracks/movimiento/pérdida; rechazo por umbral/margen/NaN; confirmación temporal sin contar copias de caché; duplicados y máscaras OR; galería invalidada por datos/config/modelo; frames/predicciones viejos; batch y slot pendiente reemplazable; ACK correcto/ausente/incorrecto, desconexión/reconexión y watchdog simulado; tickets obligatorios, control exclusivo y sesiones revocadas; EXIF y límites antes de conversión; inferencias tardías tras liberación; calibración concurrente; TTL de video aun con heartbeat del teléfono; API responsive con serial lento; pérdida/recuperación de ACK por WebSocket; métricas reales, acotadas y sin latencia de hardware inventada.

## Límites reales
- **Sin Nano físico ni circuito:** compilación y transporte falso no validan USB real, reinicio de placa, resistencias, consumo, watchdog físico o luces.
- **Sin cámara/teléfono físico:** la prueba de navegador y viewports no sustituye permisos de cámara del móvil, Wi-Fi, firewall, orientación de cámara real o latencia LAN.
- **Sin herramientas/referencias reales:** inventario, fotos, calibración de umbral/margen, rechazo de distractores y evaluación reservada siguen por completar.
- **Sin RTX 4060/5050 final:** repetir instalación, smoke, VRAM y latencias en esa laptop. La prueba de RTX 3060 no es un benchmark equivalente.
- **Límite de latencia ACK:** en fotografías API se cuenta desde la recepción/decodificación en backend, no desde el obturador del teléfono; CLI usa la lectura OpenCV. La simulación deja esa latencia en null.
- **Video experimental:** transporte multipart latest-only preparado y probado con fixtures; no hubo sesión WebRTC física ni medición real de latencia de teléfono.
- **CUDA nativa no se fuerza a cancelar:** un batch activo puede terminar después de cerrar; generaciones y worker impiden aplicar ese resultado a LEDs. Se cancela trabajo pendiente y no se acumulan inferencias nativas.
- Quedan avisos de deprecación de FastAPI/Starlette y un aviso `act(...)` de test React; las pruebas y builds pasan. No se ocultaron ni se presentan como fallos físicos.

## Estado de entrega
Código y Git local preparados, sin commits, remotos ni push. `.gitignore` excluye pesos, entornos, cachés, fotos privadas, calibración y secretos. La herramienta AGY no pudo ejecutar por un hook global de telemetría mal configurado; el trabajo de implementación/revisión continuó con agentes Codex sin editar esa configuración global.

Para la prueba final seguir `DEPLOY_RTX4060.md`/`DEPLOY_RTX5050.md`, `COMMANDS.md`, `NETWORK.md`, `HARDWARE.md` y la lista `ACCEPTANCE.md`. El servidor de verificación y su sesión se detuvieron; arrancar el servidor con una clave propia para seguir desarrollando.
