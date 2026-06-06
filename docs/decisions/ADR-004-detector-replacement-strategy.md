# ADR-004: Estrategia de reemplazo del detector para operación en tiempo real

## Estado

Propuesta — pendiente de implementación. Requiere validación experimental antes de ejecutar cualquier cambio.

---

## Contexto

El benchmark de línea base (Spec 001) demostró cuantitativamente que Detectron2 RetinaNet R-50-FPN **no es viable para operación en tiempo real** en Raspberry Pi 5 con CPU solamente:

| Métrica | Valor medido | Requisito mínimo estimado |
|---|---|---|
| FPS — detección en cada frame | 0.17 FPS | ≥5 FPS (mínimo operacional) |
| FPS — con Scene Gate activo (best case) | 2.10 FPS | ≥5 FPS (mínimo operacional) |
| Tiempo de inferencia por frame | 5.32–6.42 s | ≤1 s (operacionalmente aceptable) |
| Tiempo real requerido (estándar video) | — | ≥15–30 FPS |

El cuello de botella dominante es la latencia de inferencia del detector en CPU. La RAM (~2.1 GB de 8 GB) no es un factor limitante. El Scene Gate reduce las invocaciones en ~92%, pero el sistema sigue operando a ~2 FPS en el mejor caso.

Sin embargo, el flujo operacional del sistema evoluciona (Spec 007) desde procesamiento de video frame-a-frame hacia un modelo de **captura de snapshots por detección de cambios + inferencia por imagen individual**. En este flujo:

- No se requiere FPS de video en tiempo real (≥15–30 FPS).
- Se requiere que la inferencia sobre un snapshot individual sea lo suficientemente rápida para no bloquear el flujo de captura del robot (~1–3 s aceptable por snapshot en contexto agrícola).
- El criterio operacional se relaja significativamente respecto al procesamiento de video frame-a-frame.

Esto redefine la pregunta: no es "¿puede Detectron2 procesar video en tiempo real?" (claramente no), sino "¿puede Detectron2 inferir un snapshot individual en un tiempo aceptable para el flujo agrícola?"

Con 5.32–6.42 s por inferencia, la respuesta sigue siendo que el tiempo es **elevado pero potencialmente tolerable** en un flujo donde el robot recorre un módulo y captura 10–30 snapshots por monitoreo. Un monitoreo completo tardaría 1–3 minutos adicionales de inferencia, lo cual podría ser aceptable operacionalmente pero no óptimo.

---

## Decisión propuesta

Evaluar tres alternativas de forma incremental, midiendo cada una contra la línea base establecida:

### Alternativa 1 — Modelo liviano en CPU (YOLO / MobileNet-SSD)

Reemplazar Detectron2 por un detector más eficiente en CPU:

- **YOLOv8n / YOLOv8s** (Ultralytics): optimizado para edge, soporte ONNX, comunidad activa.
- **MobileNet-SSD v2**: arquitectura diseñada para dispositivos móviles.
- **EfficientDet-Lite**: variante ligera de EfficientDet.

**Requisitos para evaluación:**
- Re-entrenar o fine-tune con el dataset de tomates cherry existente.
- Medir FPS, precisión (usando tracks como métrica proxy), y latencia por frame.
- Comparar contra línea base: ≥5× mejora en FPS sin degradar precisión por debajo del umbral operacional.

### Alternativa 2 — Cuantización del modelo actual

Aplicar cuantización INT8 al modelo RetinaNet existente:

- Exportar a ONNX y cuantizar con ONNX Runtime.
- Alternativamente, usar PyTorch quantization (post-training quantization).

**Requisitos para evaluación:**
- Medir degradación de precisión vs. ganancia de velocidad.
- Viable solo si la ganancia es ≥2× sin pérdida significativa de detecciones.

### Alternativa 3 — Inferencia en NPU de la AI Camera (IMX500)

Delegar la detección al NPU del IMX500 (13 TOPS):

- Requiere exportar el modelo a formato compatible con el NPU.
- Ver ADR-002 (Estrategia B) para contexto.

**Requisitos para evaluación:**
- Confirmar que la arquitectura del detector (o una alternativa) es compatible con las restricciones del NPU.
- Medir latencia de inferencia en el NPU.
- Evaluar si el pipeline downstream (sanidad, madurez) puede funcionar con los outputs del NPU.

---

## Orden de evaluación recomendado

| Prioridad | Alternativa | Justificación |
|---|---|---|
| 1 | Modelo liviano en CPU (YOLOv8n) | Menor riesgo, mayor comunidad, resultados predecibles |
| 2 | Cuantización del modelo actual | Bajo esfuerzo si la ganancia es suficiente |
| 3 | Inferencia en NPU (IMX500) | Mayor potencial pero mayor complejidad y riesgo |

La evaluación sigue el principio benchmark-first (ADR-003): cada alternativa se mide contra la línea base antes de aceptarse.

---

## Consecuencias

### Positivas

- Proporciona un camino claro hacia tiempos de inferencia operacionalmente aceptables.
- Mantiene el principio de decisión basada en evidencia.
- Permite evaluar alternativas de forma incremental sin comprometer el sistema funcional.
- Genera evidencia comparativa directamente utilizable en la tesis.

### Negativas

- Cualquier reemplazo de modelo requiere re-entrenamiento o fine-tuning con el dataset existente.
- La migración de Detectron2 implica adaptar `src/infrastructure/vision/detectron_detector.py` y potencialmente el formato de salida del detector.
- El tiempo de evaluación de cada alternativa puede ser significativo (1–2 semanas por alternativa).
- El dataset de entrenamiento original puede requerir adaptación de formato (COCO → YOLO, por ejemplo).

---

## Criterios de aceptación para el reemplazo

Una alternativa es aceptable si cumple:

| Criterio | Umbral |
|---|---|
| Latencia por frame | ≤2 s (inferencia sobre un snapshot individual) |
| Precisión de detección | ≥80% de los tracks detectados por Detectron2 en el video de referencia |
| RAM pico | ≤3.5 GB |
| Estabilidad térmica | Sin throttling sostenido durante un monitoreo completo (~30 snapshots) |
| Compatibilidad | ARM64, CPU only (alternativa NPU exceptuada) |
| Instalación | Reproducible con instrucciones documentadas |

---

## Alternativas descartadas

| Alternativa | Razón de descarte |
|---|---|
| Mantener Detectron2 sin cambios para el flujo live | 5–6 s por snapshot es tolerable pero subóptimo; el agricultor espera menos |
| Usar detector en la nube vía API | Contradice el requisito offline-first del proyecto |
| Reducir resolución de entrada drásticamente | Puede degradar la detección de frutos pequeños; requiere medición primero |
| Abandonar detección automática | Contradice el objetivo principal de la tesis |

---

## Restricciones de implementación

- **No implementar hasta completar Spec 001** (benchmarks formales pendientes).
- **No implementar sin spec dedicada** (Spec 003 — Edge Pipeline Optimization).
- **El pipeline actual se mantiene funcional** durante toda la evaluación.
- **Detectron2 se conserva como referencia** para comparaciones posteriores.
- **Toda medición sigue el protocolo de ADR-003**: mismo video, mismas condiciones térmicas, mismos umbrales de evaluación.

---

## Evidencia de soporte

- `docs/benchmarks/raspberry-baseline.md` — Sección 9.3: "Detectron2 RetinaNet en RPi 5 CPU no es viable para uso en tiempo real."
- `outputs/experiments/comparison_summary.csv` — FPS efectivo medido: 0.17 (full), 2.10 (sparse best).
- ADR-001 — Detectron2 mantenido para benchmarking; evidencia ahora disponible.
- ADR-003 — Criterios de aceptación para optimizaciones.

---

## Relación con specs

| Spec | Relación |
|---|---|
| 001 — Benchmark línea base | Provee la evidencia que motiva esta decisión |
| 003 — Optimización edge | Implementará la alternativa seleccionada |
| 007 — Flujo de monitoreo live | Define el contexto operacional (snapshots, no video) |

---

## Fecha

Propuesta redactada al concluir la fase de análisis de resultados de Spec 001.
