# ADR-002: Estrategia de integración de la Raspberry Pi AI Camera

## Estado

Propuesta — pendiente de implementación (posterior al benchmark de línea base).

---

## Contexto

El hardware del proyecto incluye una Raspberry Pi AI Camera, que combina un sensor de imagen Sony IMX500 con un NPU (Neural Processing Unit) integrado directamente en el módulo de cámara. Esta cámara permite ejecutar modelos de inferencia en el propio sensor, con una capacidad declarada de hasta 13 TOPS (tera-operations per second) en el NPU.

Actualmente, el pipeline de Tomato Monitor procesa video offline cargado desde archivo (`data/videos/video_02.mp4`) utilizando `cv2.VideoCapture(index)`. La integración de la AI Camera implicaría cambiar la fuente de entrada a un stream en tiempo real, con la posibilidad opcional de delegar parte de la inferencia al NPU del sensor.

Se presentan dos estrategias principales:

**Estrategia A — Camera como fuente de frames solamente:**
Usar la AI Camera como fuente de video (picamera2 o GStreamer) pero mantener toda la inferencia en la CPU de la RPi 5. El pipeline permanece sin cambios; solo se modifica la fuente de entrada.

**Estrategia B — Camera con inferencia en NPU:**
Exportar el detector (o un modelo más liviano) al formato que acepta el NPU del IMX500, delegar la detección al sensor y recibir solo los bounding boxes ya procesados. El resto del pipeline (sanidad, madurez, tracking) continúa en CPU.

---

## Decisión

**Fase 1 (inmediata):** Implementar Estrategia A. Integrar la AI Camera únicamente como fuente de frames usando `picamera2`, sin modificar el pipeline de inferencia. Esto desbloquea el modo de captura en tiempo real con el menor riesgo de regresión.

**Fase 2 (condicional):** Evaluar Estrategia B si los resultados del benchmark de línea base (ver `docs/benchmarks/raspberry-baseline.md`) muestran que el detector es el componente dominante en tiempo de cómputo y que el FPS resultante no es aceptable para uso en tiempo real. La decisión de migrar al NPU requiere evidencia cuantitativa antes de invertir en la conversión del modelo.

---

## Consecuencias

### Positivas (Estrategia A)

- Mínimo riesgo: el pipeline de inferencia no cambia.
- Permite validar la integración de la cámara de forma aislada.
- Genera datos de rendimiento del modo live que se pueden comparar con el modo offline.
- Aprovecha el driver oficial `picamera2` que tiene soporte activo en RPi OS.

### Negativas (Estrategia A)

- No aprovecha el NPU del IMX500; la carga en CPU permanece igual que con el video offline.
- El modo streaming requiere adaptar `video_inspection_runner.py` para no depender de `cv2.VideoCapture` con archivo.

### Riesgos (Estrategia B)

- Convertir el modelo de detección al formato del IMX500 requiere re-entrenamiento o exportación ONNX con posible pérdida de precisión.
- El NPU del IMX500 tiene restricciones de arquitectura (no todos los operadores están soportados).
- El tiempo de desarrollo puede exceder el alcance de la tesis si la conversión resulta problemática.

---

## Alternativas consideradas

| Alternativa | Razón de descarte |
|---|---|
| Usar cámara USB genérica en lugar de AI Camera | No aprovecha el hardware disponible; contradice el objetivo de evaluar el hardware del proyecto |
| Implementar directamente Estrategia B | Requiere evidencia de que el NPU es necesario; benchmark-first (ADR-003) establece este criterio |
| OpenCV VideoCapture con índice de cámara | Compatible, pero `picamera2` ofrece control de exposición, ganancia y resolución específico para el módulo IMX500 |

---

## Referencias

- [picamera2 documentation](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [IMX500 AI Camera — Raspberry Pi](https://www.raspberrypi.com/documentation/accessories/ai-camera.html)
- ADR-001 — Uso de Detectron2 como detector principal
- ADR-003 — Estrategia benchmark-first
