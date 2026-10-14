# Datos fuera de Git

La galería predeterminada `text-only` se genera desde nombres/descripciones del inventario, sin fotografías ni carpeta `references`. Preparar localmente el fondo de bandeja en `calibration/` y la galería generada. Para medir precisión, separar recortes reales etiquetados en `calibration/<class_id>/` y `evaluation/<class_id>/`, incluyendo `unknown`. `references/<class_id>/` es opcional y se usa sólo en `image-only`/`hybrid`.

No se distribuyen fotos personales, fondo, embeddings ni datos de evaluación en el repositorio. Ver `docs/DATA.md` para separación de conjuntos y `docs/COMMANDS.md` para construir/calibrar/evaluar.

El inventario de ejemplo y la configuración de software sí se encuentran en `config/`. Los nueve nombres/ubicaciones definitivos deben completarse con el equipo antes de conectar LEDs físicos.
