# Reporte del frontend

La carpeta `frontend/` contiene un dashboard React web con TypeScript y Vite. Se diseñó para usarse primero desde un teléfono, pero conserva una disposición de dos columnas en escritorio.

## Flujo implementado

- La URL del backend, el ID del teléfono y la clave de emparejamiento se capturan desde el panel de conexión. La URL y el ID se guardan localmente; la clave no se persiste. El valor inicial usa el mismo origen cuando el build lo sirve FastAPI y cambia `localhost:5173/4173` a `localhost:8000` durante desarrollo; Vite también deja `/v1` y `/health` proxificados.
- `POST /v1/sessions` adquiere el rol solicitado y `POST /v1/sessions/{session_id}/ws-ticket` obtiene un ticket efímero de un solo uso para el WebSocket. Cada reconexión solicita un ticket nuevo; la clave permanente nunca se coloca en la URL WS. Las fotos usan `multipart/form-data` en `POST /v1/analyze/photo` y envían `X-Session-Id` y `X-Session-Ticket`.
- El selector permite `controller` o `reader`; un lector puede ver estado, inventario y eventos sin adquirir control del Nano. El controlador puede guardar la foto actual como fondo con `POST /v1/calibration/background`.
- El selector de archivos usa `capture="environment"`. Cuando el navegador ofrece `createImageBitmap`, la foto se dibuja en un canvas con orientación EXIF aplicada antes del envío. Si no está disponible, se conserva la imagen original y se informa cualquier error de lectura.
- El overlay es un SVG con el mismo `viewBox` de las dimensiones normalizadas de la respuesta. El modo completa usa `xMidYMid meet`; el modo recortado usa `xMidYMid slice`. Las cajas se dibujan desde `detections[].bbox` sin detecciones sintéticas. Si el backend entrega `image.preview_url` o `image.preview_data_url`, se usa esa imagen normalizada; si no, se usa la captura local y se avisa cuando sus dimensiones no coinciden. En ese caso se ocultan las cajas para no presentar una posición falsa.
- La sesión abre WebSocket para eventos de detección, sistema y ACK. Mientras está activa, el cliente valida el estado cada dos segundos para conservar el lease y detectar expiración. La interfaz diferencia máscara solicitada y máscara confirmada en las nueve ubicaciones; si no hay ACK o conexión física, muestra `Sin confirmación` en lugar de inventar apagados. El inventario usa `led_index` para nombrar las posiciones. La simulación se muestra cuando el backend devuelve `simulated` o el Arduino simulado.
- Las ubicaciones vacías, `unknown` o `unconfirmed` se presentan como `Ubicación por confirmar`. Cuando el backend marca el inventario como ejemplo, el dashboard usa nombres genéricos (`Ubicación 1`…`Ubicación 9`) y lo indica explícitamente, sin presentar esas posiciones como una calibración real. Los ACK se aceptan tanto en el objeto `arduino` como en el nivel raíz del evento para tolerar ambas formas documentadas.
- El visor y el panel de resultados usan columnas con `minmax(0, ...)`, el visor conserva relación 4:3 sin imponer un ancho mínimo y el SVG calcula la fuente de las etiquetas según el tamaño renderizado. El mapa de luces usa tipografía legible y dos columnas en móvil.
- La sección de trazabilidad expone sólo las métricas que entrega el backend: captura, presentación, frecuencia de inferencia, p50/p95 con cantidad de muestras, VRAM, captura→ACK y etapas opcionales. Los valores nulos permanecen como `Sin muestras suficientes`, `No disponible` o `No disponible en simulación` según corresponda; no se presentan porcentajes de confianza ni latencias físicas inventadas.
- El modo continuo es experimental: requiere cámara y una sesión de controlador, usa `/v1/stream/frame` y retiene como máximo un frame pendiente. La respuesta se asocia a la imagen enviada y una generación de ejecución impide que una respuesta tardía actualice la escena después de detener/reiniciar. Cerrar sesión detiene cámara, reconexiones y frames pendientes.

## Verificación

Desde `frontend/`:

```powershell
npm install
npm test
npm run typecheck
npm run build
```

`src/geometry.test.ts` verifica contain, cover con recorte y rotación de 90 grados. `src/api.test.ts` verifica multipart, headers, inventario, calibración, URL y ticket WS efímero. `src/App.test.tsx` verifica la cámara bajo `StrictMode`, rol seleccionado, permiso tardío, reinicio de cámara, liberación, reconexión con ticket nuevo, overlay con una caja normalizada y renderizado de métricas reales con nulos. El navegador debe validar además permisos de cámara y el flujo HTTP/WS contra la laptop.

## Límites actuales

- La cámara requiere `https://` o `http://localhost`; el selector de archivo funciona como fallback.
- El backend devuelve un `preview_data_url` normalizado cuando está disponible. Si falta, el frontend usa la captura local sólo cuando las dimensiones coinciden; si no, oculta las cajas.
- Los WebSocket de navegador no permiten headers personalizados. El cliente obtiene primero un ticket efímero por `POST /v1/sessions/{session_id}/ws-ticket` y envía sólo ese ticket junto con `session_id` como query parameters.
- WebRTC no se declara operativo: el transporte continuo actual es multipart con backpressure limitado por el cliente.
- No se validaron cámara física, red Wi-Fi, firewall, GPU ni ACK de un Nano en este equipo.
