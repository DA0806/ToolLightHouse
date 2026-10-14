# Pruebas de aceptación en laptop final

Esta lista se ejecuta después de la instalación. Registrar fecha, commit transferido, GPU/driver, versión Torch/CUDA, modelo local, cámara/resolución, inventario y parámetros. No dar por completado un ítem sólo porque existe código.

| Caso | Resultado que comprobar |
| --- | --- |
| Sin herramientas | Cero cajas y máscara 0 confirmada |
| 1 / 2 / 3 / 5 / 9 objetos | Una caja e ID por objeto separado, categorías correctas o rechazo explícito |
| Dos de igual categoría | Dos detecciones, un solo bit; retirar una conserva el bit |
| Retiro parcial | Se apaga sólo ubicación ausente, quedan otras presentes |
| Objeto ajeno | Unknown/ambiguous sin nuevos LEDs |
| Movimiento | ID/etiqueta no se transfieren al objeto vecino |
| Ocultación breve y timeout | Track recuperado según configuración; desaparecido vence y se apaga |
| Cambio de luz/sombra/mano | Documentar falsos contornos/rechazos; no ocultar fallos |
| Retrato/paisaje/EXIF | Imagen mostrada y cajas coinciden tras normalizar |
| Fondo distinto o resolución distinta | Rechazo claro y nueva calibración |
| Dos teléfonos | Un controlador; segundo observa sin modificar Nano |
| Liberar durante análisis | Inferencia anterior no vuelve a activar LEDs |
| Wi-Fi perdido | Error/reconexión visible y apagado de estado vencido |
| USB desconectado | ACK no inventado, recuperación/sincronización cero |
| Backend detenido | Watchdog Nano apaga en plazo medido |
| GPU ocupada | Frontend responsive, frames atrasados descartados |
| Sin Internet | App/build, modelo/galería textual y USB funcionan localmente; referencias sólo image/hybrid |
| Galería sólo textual | Nueve IDs del inventario y varios textos por clase, sin carpetas de referencias |
| Caché incompatible | Cambio de nombres/descripciones, modo, modelo o referencias activas exige reconstrucción |

## Métricas
Separar FPS captura, presentación e inferencia. Cronometrar segmentación, embeddings/clasificación, end-to-end y captura→ACK sólo cuando exista ACK físico. Reportar VRAM real. p50/p95 requieren suficientes muestras, con cantidad y unidad; dejar null/no disponible si aún no hay datos. No sustituir un benchmark físico por timestamps sintéticos.

## Aprobación de demo
La demo física se acepta sólo después de completar inventario real, datos de evaluación separados, calibración del umbral/margen, prueba eléctrica, ACK físico, desconexión/reconexión y fotografía móvil desde Wi-Fi. El reporte `VALIDATION.md` distingue verificaciones de software ya realizadas de estas pruebas pendientes.

Evaluar `text-only`, `image-only` y `hybrid` por separado cuando se comparen mejoras. Registrar los límites del perfil activo, exactitud por clase, tasa de falsa aceptación de `unknown`, cantidad de muestras y matriz de confusión. Ninguna prueba sintética ni smoke de embeddings finitos demuestra que zero-shot reconoce las nueve herramientas reales. `DATA.md` describe calibración sin reutilizar el conjunto de evaluación.
