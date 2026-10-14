# Teléfono y laptop en Wi-Fi local

El teléfono sólo captura y muestra resultados. OpenCV, Gemma y serial corren en la laptop. Conectar ambos a la misma Wi-Fi privada; redes de invitados pueden aislar clientes. Desactivar datos móviles durante la prueba si el teléfono intenta cambiar de red. No exponer este prototipo al router público.

## Un solo origen
Compilar React en `frontend/dist` y servirlo desde FastAPI permite abrir `http://IP-DE-LAPTOP:8000/` y usar API/WebSocket del mismo origen. En el PC, `http://127.0.0.1:8000/` sirve para diagnóstico. `localhost` escrito en el teléfono apunta al propio teléfono, no a la laptop.

Para conocer IPv4 en Windows:
```powershell
Get-NetIPConfiguration
```
Usar la IPv4 de la interfaz Wi-Fi activa, no direcciones de adaptadores virtuales. Backend debe escuchar `0.0.0.0`; el teléfono usa la IPv4 concreta. El backend puede arrancar offline si los pesos, inventario textual, galería y build ya están instalados. Fotos de referencia sólo son necesarias para `image-only`/`hybrid`.

## Firewall Windows
En PowerShell **administrador** de la laptop de demostración, crear una regla limitada a red privada y subred local:
```powershell
New-NetFirewallRule -DisplayName 'ToolLighthouse LAN 8000' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private -RemoteAddress LocalSubnet
```
Este comando se documenta; no se ejecuta automáticamente. Para retirarla:
```powershell
Remove-NetFirewallRule -DisplayName 'ToolLighthouse LAN 8000'
```
No deshabilitar el firewall entero. Si se utiliza servidor Vite separado para desarrollo, su puerto también necesita regla privada o usar un proxy local de mismo origen. En la demo preferir el build servido por FastAPI.

## Cámara y HTTPS
Los navegadores permiten `getUserMedia` sólo en contexto seguro; `http://IP-local` en el teléfono normalmente no lo es. El selector de archivo con `accept=image/*` y `capture=environment` permite abrir la cámara nativa donde el navegador lo soporte y es el camino inicial para fotografía en LAN HTTP. También se pueden seleccionar imágenes existentes.

Para video con cámara web, preparar un certificado confiable para la IP/nombre local en laptop y teléfono, y arrancar Uvicorn con `--ssl-keyfile` y `--ssl-certfile`; no basta un certificado que el teléfono rechaza. Configurar URL `https://...` y WebSocket `wss://...`. No almacenar certificados privados en Git. Evitar cámara/IP móvil que cambie perspectiva entre fondo y foto: usar soporte fijo cenital.

## Emparejamiento y exclusión
Crear una clave local fuera de Git y compartirla sólo con los dispositivos del equipo. La clave permite iniciar sesión; la sesión controladora posee un lease exclusivo. Un segundo teléfono puede observar, pero no analizar/controlar la escena mientras otro tiene el lease. Liberarlo al terminar. Los tickets WebSocket deben ser efímeros; no pegar la clave permanente en una URL. No registrar clave ni tokens en capturas/reportes.

En HTTP los datos de sesión no están cifrados: usar una Wi-Fi de confianza para el prototipo y HTTPS confiable para transporte protegido. La autorización básica no sustituye cifrado.

## Secuencia de prueba
1. Abrir salud/status desde laptop, luego teléfono. Comprobar URL, puerto y firewall si falla.
2. Emparejar teléfono y adquirir control; conectar WebSocket y observar estado del sistema.
3. Capturar bandeja vacía y guardar fondo/ROI con la misma resolución y orientación de la escena.
4. Enviar fotos de 1, 2, 3, 5 y 9 objetos separados; revisar cajas y nombres, desconocidos y estado solicitado/ACK.
5. Enviar foto con retiro parcial y con dos instancias iguales; revisar OR de bits.
6. Conectar segundo teléfono: denegar control simultáneo. Liberar/expirar primer control y comprobar cero antes del siguiente.
7. Cortar Wi-Fi/reiniciar backend: verificar errores y reconexión sin aplicar resultados antiguos. Con Nano medir watchdog.

## Video
El estado de video vence por defecto tras 2 segundos sin un resultado fresco, aunque el teléfono mantenga su sesión conectada. Una fotografía conserva su salida hasta 30 segundos o hasta vencer/liberar el control. Los cambios de ACK y apagado se notifican por WebSocket. Estos tiempos son configurables y no son mediciones de rendimiento.

La captura no debe esperar inferencia ni acumular una cola. Mantener sólo último frame pendiente y como máximo un análisis activo; descartar resultados/frame demasiado viejos. Medir p50/p95 de latencia y frecuencia de captura/inferencia por separado. WebRTC puede transportar video mientras el worker extrae frames; la disponibilidad del adaptador y pruebas realizadas se describen en `VALIDATION.md`. No equiparar una preparación de WebRTC con una sesión real desde el teléfono.
