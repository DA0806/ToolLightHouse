# Texto, referencias opcionales, calibración y evaluación

Las nueve categorías finales aún no están confirmadas. El inventario de ejemplo es una plantilla de estructura/LEDs; no representa nombres reales. Completar `name`, `descriptions` (lista de frases visuales), ubicación física, índice LED y pin. Los nueve `class_id` del inventario son la fuente de verdad para todas las modalidades. Nueve asignaciones únicas deben validarse antes de serial real.

## Galería predeterminada: text-only

No se entrena el modelo y no se necesitan fotos de referencia. Cada frase combina el nombre con una descripción independiente; si no hay descripciones se usa el nombre. Por ejemplo, una clase de destornillador plano podría tener `descriptions: ["Herramienta alargada con mango y varilla metálica terminada en una punta plana", "Destornillador de punta recta, sin mordazas ni cabeza de martillo"]`. Escribir rasgos observables, sin depender de ubicación LED o propiedades que no se ven en la foto. Evitar frases idénticas entre clases.

Se normalizan los embeddings de las frases, se promedian por clase y se normaliza el prototipo resultante. El recorte BGR se convierte a imagen RGB para el encoder de visión. Ambos encoders de EmbeddingGemma 2 comparten el espacio de embeddings; se calcula el coseno imagen-texto. `text-only` describe los datos de la galería, no la entrada: la entrada continúa siendo una imagen capturada en el teléfono o por OpenCV.

## Modalidades opcionales

`image-only` requiere fotos en `data/references/<class_id>/` para cada una de las nueve clases, promedia embeddings normalizados de imágenes y normaliza cada prototipo. `hybrid` exige texto y fotos, y normaliza la combinación `(1 - text_weight) * imagen + text_weight * texto`; el peso predeterminado es 0.5 y debe estar entre 0 y 1, sin incluir los extremos. Seleccionar `--gallery-mode` tanto en `build-gallery` como en `serve`, `run` y `evaluate`. `text-only` no consulta las carpetas de fotos, aunque existan.

Las fotos opcionales de referencia deben incluir distintos ángulos/orientaciones e iluminación dentro de la bandeja fija, ser recortes compatibles con el pipeline y evitar fondos dominantes. Nunca reutilizarlas para evaluación.

## Calibración de rechazo y medición de precisión

El fondo/ROI para segmentar sigue siendo obligatorio en modo real. Guardar una foto de bandeja vacía desde la misma cámara/orientación/resolución final. Esta foto de fondo no es una referencia de clasificación.

Separar recortes reales etiquetados en `data/calibration/<class_id>/` y `data/evaluation/<class_id>/`; añadir objetos ajenos en la carpeta `unknown` de ambos conjuntos. La calibración ajusta los límites y la evaluación reservada mide el resultado final. No modificar descripciones, pesos ni límites después de mirar la evaluación reservada sin separar un nuevo conjunto.

Los scores coseno son similitudes en [-1, 1], no probabilidades ni porcentajes de confianza. Una predicción pasa sólo si top1 supera el umbral y la diferencia top1-top2 supera el margen; el margen posible está entre 0 y 2. Cada modalidad puede tener distribuciones distintas: ajustar sus límites por separado en `classifier_profiles` de `config/settings.json`. Los campos globales `classifier_threshold` y `classifier_margin` sirven de respaldo para perfiles incompletos. Todos los valores de ejemplo (.72/.08) son provisionales, no recomendaciones de precisión.

1. Construir la galería con los nombres/descripciones definitivos y la modalidad elegida.
2. Ejecutar `evaluate --references data/calibration --output data/calibration-report.json` con la misma configuración/modalidad/inventario/galería. El reporte incluye top1, top2, margen, validación y resultado por muestra.
3. Variar `--classifier-threshold` y `--classifier-margin` y repetir sólo sobre calibración. Bajar el umbral acepta más conocidos y puede aumentar falsos LEDs; aumentar margen rechaza clases parecidas. Elegir límites que equilibren rechazo de desconocidos y recall conocido para la demo.
4. Guardar los valores elegidos en el perfil de esa modalidad y evaluar una sola vez sobre `data/evaluation`. Informar exactitud conocida, rechazos, tasa de falsa aceptación de desconocidos y matriz de confusión por clase, con cantidades de muestras y versión de configuración.
5. Probar además fotos de bandeja completas y medir recall de segmentación, herramientas confundidas y falsos LEDs. `evaluate` opera sobre recortes etiquetados y no mide por sí solo segmentación, tracking o circuito físico.

Ejemplo de búsqueda de límites (valores exploratorios, no validados):

```powershell
.\.venv\Scripts\python.exe -m toollighthouse evaluate --mode real --device cuda --gallery-mode text-only --inventory config/inventory.json --gallery data/gallery.json --references data/calibration --classifier-threshold 0.55 --classifier-margin 0.04 --output data/calibration-report.json
```

Tener nueve prototipos textuales y embeddings finitos prueba el flujo técnico, no que reconozca bien las nueve herramientas. No se ha realizado una evaluación zero-shot de ese inventario con fotografías reales.

## Caché y datos privados

Reconstruir la galería cuando cambien IDs, nombres/descripciones, modalidad, modelo local, receta de preprocesamiento/ensemble, peso híbrido o imágenes de referencia activas (incluidas altas/bajas). La carga vuelve a calcular la receta actual y rechaza cachés antiguos o incompatibles. En `text-only`, las fotos no forman parte de la receta. Ajustar sólo umbral/margen no requiere volver a generar embeddings.

No commitear embeddings ni fotos personales. Registrar un manifiesto local de datos/versiones y parámetros por ejecución. Si cambian cámara, resolución o perspectiva, repetir fondo/ROI y evaluación.

Objetos separados: probar 0/1/2/3/5/9 y dos de la misma clase, retiro parcial, retorno, movimientos, sombra/mano y distractores. Si contornos se fusionan, mejorar fondo/luz/separación primero. YOLO ligero entrenado/evaluado es Plan B opcional, sin dependencia obligatoria en este MVP.
