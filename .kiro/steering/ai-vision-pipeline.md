# AI Vision Pipeline Steering

## Componentes activos del pipeline

Todos viven en `src/infrastructure/vision/`:

| Módulo | Función |
|---|---|
| `pipeline_orchestrator.py` | Orquesta detección → tracking → análisis por frame |
| `video_inspection_runner.py` | Itera frames del video, aplica scene gate, guarda artefactos |
| `detectron_detector.py` | RetinaNet R-50-FPN vía Detectron2; 1 clase (`cherry_tomato`) |
| `resnet_health_classifier.py` | ResNet-18 fine-tuned; clases: `healthy` / `unhealthy` |
| `maturity_estimator.py` | Segmentación GrabCut + máscara elíptica por defecto |
| `maturity_colorimetry.py` | Colorimetría HSV + CIELab; escala USDA 6 etapas: `green → breaker → turning → pink → light_red → red` |
| `tracker_adapter.py` | SimpleTracker: asociación por IoU (≥0.30) y distancia de centroide (≤120 px) |
| `visual_tracker.py` | Optical Flow Lucas-Kanade; propaga tracks en frames sin detección |
| `capture_gate.py` | Scene Gate: ORB 500 keypoints + histograma HSV 50×60 bins |
| `cropper.py` | Expand (ratio 0.08), clamp a límites del frame, validación de tamaño mínimo |

## Flujo por frame

```
frame → ¿Scene Gate activo? 
  Sí → detector → tracker → deduplication → crop → health → maturity
  No → Optical Flow (propaga tracks previos)
→ anotación → artefactos (opcional)
```

## Políticas operacionales

- `RUN_MATURITY_ONLY_FOR_HEALTHY = True` por defecto — la madurez solo se ejecuta para frutos clasificados como `healthy`
- `DETECTION_SCORE_THRESHOLD = 0.80` — umbral de confianza para aceptar una detección
- `HEALTH_B_THRESHOLD = 0.70` — umbral para clasificar como `unhealthy`
- `MATURITY_MIN_DET_SCORE = 0.80` — score mínimo para ejecutar estimación de madurez
- `MIN_CROP_WIDTH = MIN_CROP_HEIGHT = 20 px` — tamaño mínimo de crop para análisis

## Scene Gate (parámetros actuales)

- Cooldown: ≥18 frames entre detecciones
- Timeout forzado: ≥45 frames sin detección
- ORB: `matches < 35` → escena cambió
- Histograma HSV: `diff > 0.38` → color cambió
- Trigger: `cooldown_ok AND orb_changed AND hist_changed` (o timeout)

## Modelos

| Modelo | Ruta | Arquitectura |
|---|---|---|
| Detector | `models/modelo_d2/model.pth` | RetinaNet R-50-FPN, 1 clase |
| Sanidad | `models/health_model/model.pth` | ResNet-18 fine-tuned, 2 clases |

Ambos modelos se ejecutan en `DEVICE = "cpu"` exclusivamente.

## Riesgos activos

- Detectron2 en ARM64/CPU: latencia alta, calentamiento del SoC (ver ADR-001)
- Carga de modelos en cada request HTTP (sin singleton de modelos)
- GrabCut en crops pequeños: puede fallar o retornar máscara vacía; hay fallback a elipse centrada
- Snapshots + video anotado en RPi aumentan I/O; desactivar si se optimiza para velocidad

## Estrategia de cambios al pipeline

1. No reemplazar ningún componente sin benchmark de línea base (ver ADR-003)
2. Todo cambio de umbral debe registrarse en `docs/benchmarks/` con métricas antes/después
3. Nuevos componentes de visión se agregan en `src/infrastructure/vision/`, nunca en `legacy/`
4. La integración de la AI Camera (modo live) se realiza después del benchmark offline (ver ADR-002)
