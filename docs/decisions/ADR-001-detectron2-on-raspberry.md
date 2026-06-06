# ADR-001: Uso de Detectron2 en Raspberry Pi como línea base

## Estado

Aceptada — vigente para la fase de benchmarking inicial.

---

## Contexto

El proyecto utiliza Detectron2 con arquitectura RetinaNet R-50-FPN como detector principal de tomates cherry. El detector fue entrenado con un dataset personalizado y produce un modelo `.pth` que se carga en CPU.

Al iniciar las pruebas en Raspberry Pi 5, se planteó la pregunta: ¿se debe reemplazar Detectron2 por un detector más liviano antes de hacer las primeras pruebas, o conviene medir primero el rendimiento del sistema tal como está?

Detectron2 fue diseñado para entornos con GPU y hardware de escritorio. Su uso en hardware edge (ARM64, CPU solamente) presenta desafíos conocidos:

- Instalación compleja: no hay wheels precompilados para ARM64; debe compilarse desde fuente.
- Alto consumo de CPU durante inferencia.
- Dependencias pesadas (PyTorch, fvcore, pycocotools).

Sin embargo, reemplazar el detector antes de medir implica perder la referencia comparativa con los resultados obtenidos en PC y arriesgar introducir errores de regresión en el pipeline.

---

## Decisión

Se mantiene Detectron2 para la fase de benchmarking inicial en Raspberry Pi 5. No se reemplazará hasta contar con métricas cuantitativas de rendimiento (FPS, CPU, temperatura) que justifiquen la migración.

La instalación se realizó mediante:

```bash
pip install --no-build-isolation 'git+https://github.com/facebookresearch/detectron2.git'
```

Esta forma de instalación compila Detectron2 directamente desde el código fuente en la Raspberry Pi, evitando el problema de ausencia de wheels para ARM64.

---

## Consecuencias

### Positivas

- Permite comparar el rendimiento entre PC (x86-64) y Raspberry Pi (ARM64) con el mismo modelo y pipeline, generando evidencia directamente utilizable en la tesis.
- Evita refactorización prematura antes de tener métricas que la justifiquen (principio benchmark-first, ver ADR-003).
- El pipeline existente no requiere modificaciones para ejecutar en RPi.
- La instalación fue exitosa y el modelo ejecuta correctamente en CPU.

### Negativas

- Proceso de instalación lento (~30-60 minutos de compilación en RPi 5) y no reproducible sin conexión a internet.
- Alto uso de CPU durante inferencia con calentamiento significativo del SoC.
- FPS esperados bajos para el pipeline completo.
- La versión instalada desde GitHub puede diferir entre instalaciones si no se fija el commit.

### Riesgos

- Si la temperatura supera el umbral de throttling del SoC, el rendimiento medido puede no ser representativo del rendimiento nominal. Requiere refrigeración activa obligatoria durante todas las pruebas.
- `requirements-raspberry.txt` aún no documenta las versiones exactas de PyTorch y Detectron2 utilizadas, lo que reduce la reproducibilidad del entorno.

---

## Evidencia

### Validaciones funcionales

- Detectron2 instalado exitosamente con `--no-build-isolation` en Raspberry Pi OS 64-bit.
- Pipeline completo ejecuta sin errores (detección, crops, sanidad, madurez, snapshots, video anotado).
- No se registraron fallos de modelos ni interrupciones durante los experimentos.

### Métricas cuantitativas (experimentos comparativos)

Datos medidos en Raspberry Pi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa, procesando `data/videos/video_02.mp4` (163 frames). Fuente: `outputs/experiments/comparison_summary.csv`.

| Métrica | Valor medido |
|---|---|
| Tiempo de inferencia del detector por frame | 5.32–6.42 s (según estrategia/run) |
| FPS efectivo — `full_detection` (detector en cada frame) | 0.17 FPS (5.82 s promedio/frame) |
| FPS efectivo — estrategias sparse con Scene Gate | 2.01–2.10 FPS (mejora 10–12×) |
| Reducción de invocaciones del detector (Scene Gate) | ~92% (13 de 163 frames) |
| Overhead de Optical Flow por frame propagado | ~0.026 s |
| RAM pico durante pipeline completo | ~2.1 GB de 8 GB disponibles |
| Tracks únicos — `full_detection` | 29 |
| Tracks únicos — estrategias sparse | 16 (~45% de pérdida de cobertura) |

### Observaciones complementarias

- La RAM no constituye un factor limitante; el cuello de botella dominante es la latencia de inferencia del detector en CPU.
- La refrigeración activa es obligatoria para ejecuciones sostenidas. Los valores de temperatura no fueron registrados formalmente en estos experimentos comparativos.
- Existe variación en el tiempo de inferencia entre ejecuciones (~1.1 s), posiblemente atribuible a throttling térmico del SoC o a diferencias en la complejidad visual de los frames seleccionados por el Scene Gate.

### Pendiente

- Los benchmarks formales individuales (`bench_model_load.py`, `bench_single_inference.py`, `bench_full_video.py`) con métricas térmicas detalladas permanecen pendientes de ejecución.

### Fuentes

- `docs/benchmarks/raspberry-baseline.md` — Sección 9: Observaciones y conclusiones.
- `outputs/experiments/comparison_summary.csv` — Datos crudos de los experimentos comparativos.

---

## Alternativas consideradas

| Alternativa | Razón de descarte |
|---|---|
| Reemplazar por YOLOv8n antes del benchmark | Introduce cambios antes de tener línea base; impide comparación directa |
| Usar modelo ONNX exportado | Requiere re-entrenamiento o conversión; riesgo de diferencias en precisión |
| Ejecutar solo en PC y no en RPi | Contradice el objetivo principal del proyecto (edge deployment) |
