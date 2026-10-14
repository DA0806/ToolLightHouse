# Referencias, calibración y evaluación

Las nueve categorías finales aún no están confirmadas. El inventario de ejemplo se usa para probar estructura/LEDs; no representa nombres reales. Completar nombre canónico, nombre visible, descripciones visuales, ubicación física, índice LED y pin. Nueve asignaciones únicas deben validarse antes de serial real.

Organizar imágenes privadas en `data/references/<class_id>/`, `data/calibration/<class_id>/` y `data/evaluation/<class_id>/`. Nunca reutilizar fotos de referencia como evaluación. Para cada herramienta fotografiar distintos ángulos/orientaciones e iluminación dentro de la bandeja fija, guardar recortes RGB compatibles con el pipeline y evitar fondos dominantes. Incluir distractores/objetos ajenos en evaluación como `unknown`.

El baseline usa imagen-imagen. Evaluar por separado `text_weight=0` y una mezcla textual configurada, conservando el conjunto de evaluación. Los scores coseno son similitudes, no probabilidades calibradas ni porcentajes de precisión. Ajustar umbral de similitud y margen top1/top2 con el conjunto de calibración; reportar falsos LEDs en unknown, recall de segmentación y exactitud por recorte además de métricas de latencia.

El cache debe cambiar cuando cambia cualquier imagen de referencia, descripción, configuración/preprocesamiento, dimensión, modelo o peso textual. No commitear embeddings ni fotos personales. Registrar para cada ejecución un manifiesto local de datos/versiones y parámetros. Si cambian cámara, resolución o perspectiva, repetir fondo/ROI y evaluación.

Objetos separados: probar 0/1/2/3/5/9 y dos de la misma clase, retiro parcial, retorno, movimientos, sombra/mano y distractores. Si contornos se fusionan, mejorar fondo/luz/separación primero. YOLO ligero entrenado/evaluado es Plan B opcional, sin dependencia obligatoria en este MVP.
