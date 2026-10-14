# Dashboard móvil ToolLighthouse

La interfaz principal es un instrumento de laboratorio: capturar una escena y entender qué objetos se identificaron y cuáles luces se confirmaron. Usar React web; no existe app previa. El elemento principal es la imagen de la bandeja anotada; evitar paneles decorativos y cifras ficticias.

## Tokens
| Rol | Valor |
| --- | --- |
| Fondo | `#eef3f8` |
| Superficie | `#ffffff` |
| Tinta | `#172b46` |
| Secundario | `#52647a` |
| Acción / confirmado | `#17636c` |
| Pendiente / aviso | `#92610d` |
| Error | `#b42335` |
| Borde | `#d5dfe9` |

Tipografía local del sistema: Segoe UI en Windows, sans-serif del dispositivo en teléfono. Sin Google Fonts ni recursos externos para preservar demo offline. Cuerpo 16 px; instrucciones 14 px; títulos de sección 20–24 px. Controles mínimo 44 px, foco visible, estados con texto/icono además de color. Espaciado de 4/8/12/16/24/32 px y radios según función: controles 10 px, visor 16 px. Animación breve sólo al responder acciones; respetar reduced-motion.

## Disposición
En móvil: cabecera/estado de conexión → imagen/acción capturar → resultados → mapa de nueve ubicaciones → conexión y sesión en panel plegable. En escritorio: visor ancho a izquierda y resultados/LEDs a derecha. Alinear textos a izquierda; los indicadores representan bits/ubicaciones reales del inventario, no estadísticas inventadas.

El visor debe compartir transformación de imagen y overlay: SVG `viewBox` con dimensiones normalizadas y `preserveAspectRatio` equivalente al object-fit, sin porcentajes aproximados. Etiquetas legibles sin ocultar objetos. Unknown se presenta como «Sin identificar»; similitud como número decimal, sin porcentaje de confianza.

La simulación se indica persistentemente. Separar «solicitado» de «confirmado» para luces. Cámara/upload y reconexión fallida deben orientar acciones concretas. Mostrar modo continuo como experimental y explicar requerimiento HTTPS donde aplique. No mantener una escena previa cuando otra sesión controla el equipo.

## Revisión del diseño
La vista central de bandeja y mapa de ubicaciones responde al problema concreto del laboratorio. Se descartan gráficas genéricas y tarjetas de métricas con valores de muestra. La jerarquía deja captura y resultado antes de parámetros técnicos; la URL y clave de emparejamiento aparecen sólo al conectar.
