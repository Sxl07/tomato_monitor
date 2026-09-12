# Benchmark — Spec 023: Preview fluido desacoplado

## Objetivo

Demostrar en Raspberry Pi 5 que la captura/preview opera a ~20 FPS mientras la
grabación conserva la cadencia del perfil (EDGE ≈ 5 FPS), sin alterar el pipeline
de análisis. La **validación física manual es la fuente de verdad**; las métricas
de diagnóstico son evidencia de apoyo.

## Configuración esperada

- `camera_stream_fps` (físico/preview) = **20** (EDGE y FULL).
- `recording_target_fps` (video): EDGE = **5**, FULL = **10**.
- Perfil de referencia para este benchmark: **EDGE**.

## Requisitos previos

- Raspberry Pi 5 con refrigeración activa (obligatoria para inferencia sostenida).
- Raspberry Pi AI Camera conectada y validada a nivel de hardware.
- Aplicación Tomato Monitor en ejecución local.
- Sesión autenticada (para consultar `/api/camera/preview-diagnostics`).

## Procedimiento manual

1. Abrir en la UI la pantalla de preparación de monitoreo de un módulo
   (`/modulos/{id}/monitoreo/nuevo`). Confirmar que el preview se ve **fluido**.
2. Observar el preview unos segundos (sin iniciar monitoreo).
3. Recolectar diagnósticos del preview previo:
   ```bash
   # exporta la cookie de sesión sin imprimirla
   export PREVIEW_BENCHMARK_COOKIE="session=<token>"
   python scripts/benchmarks/collect_preview_benchmark.py \
       --base-url http://localhost:8000 \
       --output docs/benchmarks/spec023-run-preview.json
   ```
4. Iniciar el monitoreo desde la UI (botón "Iniciar Monitoreo"). Confirmar que la
   transición NO produce errores de cámara/libcamera.
5. Recorrer el módulo con el dispositivo durante **20–30 s**.
6. Finalizar la captura desde la UI.
7. Esperar a que se genere `pipeline_metrics.json`
   (`outputs/monitorings/{id}/pipeline_metrics.json`).
8. Ejecutar el colector apuntando al `pipeline_metrics.json`:
   ```bash
   python scripts/benchmarks/collect_preview_benchmark.py \
       --base-url http://localhost:8000 \
       --pipeline-metrics outputs/monitorings/{id}/pipeline_metrics.json \
       --output docs/benchmarks/spec023-run-monitoring.json
   ```
9. Registrar los resultados en la tabla de abajo.

## Métricas a registrar

Del preview previo (`/api/camera/preview-diagnostics`):

| Métrica | Valor |
|---|---|
| `camera_frames_produced` | |
| `preview_frames_encoded` | |
| `effective_camera_stream_fps` | |
| `active_subscribers` | |

De la grabación (`pipeline_metrics.json`, sección `capture`):

| Métrica | Valor |
|---|---|
| `configured_camera_stream_fps` | (esperado 20) |
| `camera_frames_produced` | |
| `effective_camera_stream_fps` | |
| `configured_recording_fps` | (EDGE 5) |
| `frames_written` (recording_frames_written) | |
| `effective_recording_fps` | |
| `recording_duration_seconds` | |
| `camera_capture_elapsed_seconds` | |
| `accumulated_pause_seconds` | |

Sistema y entorno:

| Métrica | Valor |
|---|---|
| CPU (%) | |
| RAM usada (MB) | |
| Temperatura inicial (°C) | |
| Temperatura durante preview (°C) | |
| Temperatura durante monitoreo (°C) | |
| Temperatura pico (°C) | |
| Errores cámara/libcamera | |
| Commit / fecha / dispositivo | |

## Tolerancias orientativas (ventana de 20 s, EDGE)

- `camera_frames_produced` ≈ **400** (no exigir exactitud matemática).
- `frames_written` ≈ **100** (no ~400).
- `effective_camera_stream_fps` ≈ objetivo (20), aceptando FPS reales variables
  (p. ej. 18.9–20.4).
- `effective_recording_fps` ≈ `recording_target_fps` del perfil.

> Nota: la cámara puede entregar FPS variables; el preview puede descartar frames
> antiguos intencionalmente. **No** se exige `preview_frames_encoded == camera_frames_produced`.
> La validación física en Raspberry Pi es la que decide la aceptación final.

## Resultados

_(Completar tras la ejecución física manual.)_
