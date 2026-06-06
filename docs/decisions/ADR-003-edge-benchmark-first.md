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

| Criterio | Umbral mínimo |
|---|---|
| Mejora de FPS | ≥ 10% respecto a línea base en condición equivalente |
| Precisión de detección (mAP) | No degradar por debajo del umbral operacional definido |
| Uso de RAM | No superar 3.5 GB en RPi 5 con 8 GB |
| Temperatura de operación | No forzar throttling térmico sostenido |
| Estabilidad | Sin crashes en secuencia de 10+ ejecuciones consecutivas |

Los umbrales exactos de precisión se definirán al completar el benchmark de línea base.

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
- `docs/benchmarks/raspberry-baseline.md` — resultados del benchmark de línea base (pendiente)
