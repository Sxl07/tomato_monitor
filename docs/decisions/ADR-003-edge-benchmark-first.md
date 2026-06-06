# ADR-003: Estrategia benchmark-first para optimización del pipeline edge

## Estado

Aceptada — vigente como principio rector de las decisiones de optimización.

---

## Contexto

El pipeline de Tomato Monitor fue diseñado y validado inicialmente en PC (x86-64 con suficiente CPU y RAM). Al trasladar el sistema a Raspberry Pi 5, surgen preguntas naturales sobre rendimiento:

- ¿Cuántos FPS procesa el pipeline completo en RPi?
- ¿Qué componente consume más tiempo de CPU?
- ¿Es el detector el cuello de botella, o la clasificación de sanidad, o la estimación de madurez?
- ¿Cuánta CPU ahorra el Scene Gate en la práctica?
- ¿Es necesario reemplazar Detectron2 por un modelo más ligero?

Existe la tentación de responder estas preguntas a priori y optimizar el pipeline antes de medirlo. Esta estrategia tiene dos riesgos principales: (1) optimizar el componente incorrecto, y (2) introducir regresiones en precisión sin saber si el sacrificio valió la pena.

La alternativa es medir primero, decidir después.

---

## Decisión

Toda decisión de optimización del pipeline debe estar respaldada por métricas cuantitativas obtenidas en el hardware objetivo (Raspberry Pi 5) con el video de referencia (`data/videos/video_02.mp4`).

El proceso es:

1. Ejecutar el pipeline actual sin modificaciones en RPi y registrar el benchmark de línea base.
2. Identificar el componente dominante (el que consume mayor porcentaje del tiempo de cómputo por frame).
3. Proponer una optimización puntual y medible para ese componente.
4. Implementar, medir y comparar contra la línea base.
5. Aceptar la optimización solo si mejora el FPS sin degradar las métricas de precisión por debajo de los umbrales definidos.

Este ciclo aplica a cada decisión de optimización: detector, resolución de entrada, cuantización de modelos, ajuste de umbrales del Scene Gate, integración del NPU, etc.

---

## Consecuencias

### Positivas

- Evita optimización prematura: se trabaja sobre el componente que realmente importa.
- Genera evidencia cuantitativa directamente utilizable en la tesis (comparativas antes/después).
- Permite tomar decisiones de migración de modelo (Detectron2 → YOLO → ONNX) con justificación técnica objetiva.
- El benchmark de línea base tiene valor académico independiente, como caracterización del sistema en hardware edge.

### Negativas

- Requiere invertir tiempo en la fase de medición antes de ver mejoras de rendimiento.
- El benchmark debe ser reproducible (misma video, mismos parámetros, mismas condiciones térmicas) para que las comparaciones sean válidas.

---

## Criterios de aceptación para optimizaciones

Una optimización es aceptable si cumple todos los criterios siguientes:

| Criterio | Umbral mínimo | Línea base medida (referencia) |
|---|---|---|
| Mejora de FPS | ≥ 10% respecto a línea base en condición equivalente | full_detection: 0.17 FPS; sparse con Scene Gate: hasta 2.10 FPS |
| Precisión de detección (tracks detectados) | No degradar por debajo del umbral operacional definido | full_detection: 29 tracks; sparse: 16 tracks (~45% de pérdida) |
| Uso de RAM | No superar 3.5 GB en RPi 5 con 8 GB | Pico observado: ~2.1 GB |
| Temperatura de operación | No forzar throttling térmico sostenido | Pendiente de medición formal con scripts dedicados |
| Estabilidad | Sin crashes en secuencia de 10+ ejecuciones consecutivas | 4 ejecuciones sin errores registradas |

> **Nota sobre umbrales de precisión:** Los experimentos comparativos proporcionan una referencia parcial de cobertura de detección (29 tracks en detección completa vs. 16 en muestreo sparse). Los umbrales exactos de precisión (mAP formal con ground truth anotado) quedan pendientes de definición, ya que requieren un dataset de evaluación con anotaciones de referencia que no está disponible actualmente. Para decisiones operacionales, se utiliza el conteo de tracks únicos como métrica proxy de cobertura.

---

### Valores de línea base medidos (experimentos comparativos)

Los siguientes valores fueron obtenidos en RPi 5 (8 GB RAM, ARM64, CPU only) con refrigeración activa, procesando `data/videos/video_02.mp4` (163 frames). Fuente: `outputs/experiments/comparison_summary.csv`, documentado en `docs/benchmarks/raspberry-baseline.md` sección 9.

| Métrica | full_detection | sparse_honest | sparse_flow | sparse_flow_candidate |
|---|---|---|---|---|
| FPS efectivo | 0.17 | 2.01 | 1.80 | 2.10 |
| Tiempo promedio por frame (s) | 5.82 | 0.50 | 0.56 | — |
| Tiempo promedio de detección por frame detectado (s) | 5.82 | 6.04 | 6.42 | 5.32 |
| Tiempo promedio por frame omitido (s) | — | 0.019 | 0.048 | — |
| Ejecuciones del detector | 163/163 | 13/163 | 13/163 | — |
| Reducción de ejecuciones por Scene Gate | 0% | ~92% | ~92% | ~92% |
| Tracks únicos detectados | 29 | 16 | 16 | — |
| Ejecuciones de clasificación de sanidad | 28 | 16 | 16 | — |
| Ejecuciones de estimación de madurez | 26 | 13 | 13 | — |
| RAM pico | ~2.1 GB | ~2.1 GB | ~2.1 GB | ~2.1 GB |

**Condiciones del experimento:**

| Parámetro | Valor |
|---|---|
| Dispositivo | Raspberry Pi 5, 8 GB RAM, ARM64 |
| Modo de cómputo | CPU only (sin GPU, sin NPU) |
| Refrigeración | Case oficial con ventilador activo |
| Video de entrada | `data/videos/video_02.mp4` (163 frames) |
| Modelos | Detectron2 RetinaNet R-50-FPN + ResNet-18 |
| Fecha aproximada | Correspondiente a los experimentos comparativos de Spec 001 |

> **Importante:** Estos valores provienen de experimentos comparativos de estrategias, no de los benchmarks formales individuales (`bench_model_load.py`, `bench_single_inference.py`, `bench_full_video.py`), los cuales permanecen pendientes de ejecución en RPi 5. Los datos térmicos formales no fueron registrados en estos experimentos. Los valores presentados constituyen observaciones experimentales reproducibles, pero no mediciones formales completas según la plantilla de benchmark.

---

## Alternativas consideradas

| Alternativa | Razón de descarte |
|---|---|
| Optimizar a priori basándose en el conocimiento del dominio | No garantiza que se trabaje sobre el cuello de botella real en RPi 5 |
| Migrar a YOLO inmediatamente | Introduce cambios de modelo sin línea base; impide evaluar si el cambio fue necesario |
| Aceptar el FPS actual y no optimizar | No genera evidencia de optimización para la tesis; no explora el potencial del hardware |

---

## Referencias

- ADR-001 — Mantenimiento de Detectron2 para benchmarking inicial
- ADR-002 — Decisión sobre integración de AI Camera
- `docs/benchmarks/benchmark-template.md` — plantilla para registro de métricas
- `docs/benchmarks/raspberry-baseline.md` — resultados del benchmark de línea base (parcialmente completado con datos de experimentos comparativos; benchmarks formales individuales pendientes)
- `outputs/experiments/comparison_summary.csv` — datos fuente de los experimentos comparativos de estrategias
