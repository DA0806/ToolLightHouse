# Laptop RTX 4060

Seguir `DEPLOY_RTX5050.md`: código y configuración son iguales, y toda la demo debe poder ejecutarse en una sola laptop. La RTX 4060 de 8 GB/24 GB RAM indicada en el prompt es un destino válido; Notion describe la RTX 5050 de 8 GB/16 GB RAM como primer equipo de pruebas.

Crear `.venv` propia, instalar wheel CUDA compatible, verificar `doctor`, ejecutar smoke EmbeddingGemma, preparar referencias/inventario/fondo y luego probar teléfono/Nano. Seleccionar BF16 si Torch confirma soporte; de lo contrario FP32. No forzar FP16 ni comparar benchmarks de las dos GPUs sin medirlas.

Los pasos de red, firmware, calibración y aceptación son idénticos. No se requiere que la PC personal ni otra laptop permanezcan encendidas durante la demo final.
