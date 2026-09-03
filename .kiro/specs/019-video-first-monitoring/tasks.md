# Implementation Plan: 019 — Video-First Monitoring

## Overview

Este plan implementa el rediseño **video-first** de forma incremental, dejando el sistema
testeable y en verde (`python -m pytest -q`) tras cada tarea. La estrategia:

1. Extraer primero la lógica pura de decisión de detección (`decide_run_detector`) sin cambiar
   comportamiento, y hacer que el runner legacy la consuma (sin regresión).
2. Construir de abajo hacia arriba: infraestructura (`VideoRecorder`, `VideoReaderPort` +
   `OpenCvVideoReader`) → aplicación (`VideoRecordingWorker`, `VideoAnalysisService`) →
   persistencia (columna + migración idempotente) → configuración por perfil.
3. Integrar en `MonitoringService` (grabación + finalización atómica + análisis diferido),
   añadir reprocesamiento controlado (Alternativa B) con guard de sincronización estricto,
   recuperación tras terminación abrupta, adaptar UI/rutas y seguridad de rutas.
4. Cerrar con regresión completa, boundaries/imports y documentación (ADR opcional).

Restricciones respetadas en todo el plan:

- `CaptureWorker` y `SnapshotAnalysisService` se **preservan sin cambios** (regresión). Ninguna
  tarea modifica su comportamiento.
- `ExportService` permanece **sin cambios**: la inclusión de `monitoring.mp4` en el ZIP está
  **fuera de alcance** (posible spec futura). **No hay task de exportación.**
- Detector se mantiene en Detectron2/RetinaNet; salud ResNet-18; madurez HSV+CIELab. **No YOLO.**
- No se modifican los modelos ni sus umbrales.
- Dominio libre de SQLAlchemy/OpenCV/PyTorch/Detectron2. **La capa de aplicación no importa
  `cv2`** (lectura de video confinada a `OpenCvVideoReader` tras el `VideoReaderPort`).
- Rutas sin lógica de negocio. `DEVICE = "cpu"`; sin dependencias nuevas; sin robótica/GPIO.
- Cambios pequeños, revisables y reversibles; build en verde entre tareas.
- Código e identificadores en inglés; documentación de tareas en español.

**Convención de tests (OBLIGATORIA):** el **conjunto de tests núcleo de la Spec 019 es
OBLIGATORIO**, no opcional; NO puede omitirse para un MVP. Solo pueden marcarse con `*`
(opcionales) los property-based adicionales de Hypothesis **cuando ya existe un test
determinista equivalente** que cubre el mismo invariante, más la documentación/ADR extra no
requerida para el funcionamiento. Todos los demás tests (unitarios de `decide_run_detector`,
equivalencia del runner legacy, `VideoRecorder`, `VideoReaderPort`/`OpenCvVideoReader`,
`VideoRecordingWorker`, migración de `video_path`, perfiles/config, `VideoAnalysisService`,
integración start→recording→finalize→analyzing→completed, integridad de video, reprocesamiento
incluido reproceso-bloqueado-si-sincronizado, recuperación tras terminación abrupta, preview
durante grabación, path traversal, regresión/imports/boundaries) son **obligatorios** y se
implementan.

---

## Tasks

- [ ] 1. Extraer la función pura `decide_run_detector` (sin cambio de comportamiento)
  - [ ] 1.1 Crear `src/infrastructure/vision/detector_decision.py`
    - Definir `@dataclass(frozen=True) DetectorDecision(run_detector: bool, reason: str)`.
    - Implementar `decide_run_detector(*, frame_idx, frames_since_last_detection, last_detection_frame, current_frame, enable_sparse_detection, use_scene_gate, min_frames_between_detections, max_frames_without_detection, force_detect_on_first_frame, scene_gate_fn)` como extracción 1:1 del bloque `run_detector`/`detector_reason` de `video_inspection_runner.run_video_inspection`.
    - Respetar el orden de precedencia: `full_detection` → `first_frame` → `max_gap_force` → (`scene_gate`/`scene_gate_blocked` | `min_gap_ready`) → `cooldown`.
    - Módulo sin dependencias pesadas (no importar torch/detectron2/cv2 a nivel de módulo); `np` solo para type hints.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 16.2_

  - [ ] 1.2 Escribir tests unitarios de `decide_run_detector` (OBLIGATORIO)
    - Tabla de casos que cubra cada `reason`: `first_frame`, `max_gap_force`, `scene_gate`, `scene_gate_blocked`, `min_gap_ready`, `cooldown`, `full_detection`.
    - Verificar precedencia entre ramas, frontera `min_gap <= max_gap`, determinismo (misma entrada → misma salida) y pureza (sin cámara, sin inferencia, sin disco).
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [ ]* 1.3 Escribir property test de correctitud/totalidad de la decisión (opcional — ya cubierto por 1.2)
    - **Property 9: Correctitud y totalidad de la decisión de detección**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 16.3**
    - Hypothesis: para toda combinación válida (`frame_idx`, `gap`, `min_gap <= max_gap`, flags, gate inyectado) retorna exactamente una razón del conjunto cerrado y nunca `cooldown` cuando `gap >= max_gap`.

  - [ ] 1.4 Refactorizar `video_inspection_runner` para llamar a `decide_run_detector`
    - Sustituir el bloque embebido por la llamada a la función pura, inyectando `should_run_detector_by_scene_change` como `scene_gate_fn`.
    - Mantener el **comportamiento observable sin cambios** (runner legacy = herramienta de benchmark/diagnóstico, fuera del flujo de producción).
    - _Requirements: 16.1, 16.2, 16.3_

  - [ ] 1.5 Test de equivalencia de comportamiento del runner legacy (OBLIGATORIO)
    - Verificar que la refactorización no altera las decisiones observables del runner sobre una secuencia fija de frames sintéticos (mismas decisiones que antes del refactor).
    - _Requirements: 16.3_

- [ ] 2. Implementar `VideoReaderPort` (aplicación, sin cv2) y `OpenCvVideoReader` (infra, un único `cv2.VideoCapture`)
  - [ ] 2.1 Crear el puerto `src/application/interfaces/video_reader_port.py` (SIN cv2)
    - Definir `@dataclass(frozen=True) VideoMetadata(fps: float, total_frames: int, width: int, height: int)`.
    - Definir `class VideoReaderPort(ABC)` con `open()`, `is_available() -> bool`, `metadata() -> VideoMetadata`, `read() -> tuple[bool, np.ndarray | None]`, `release()`.
    - No importar `cv2` a nivel de módulo (solo `np` para type hints).
    - _Requirements: 4.1, 4.2, 15.3_

  - [ ] 2.2 Crear el adaptador `src/infrastructure/camera/opencv_video_reader.py` con **un único `cv2.VideoCapture`**
    - `class OpenCvVideoReader(VideoReaderPort)`: **poseer exactamente UN `cv2.VideoCapture`** usado para `open`, `is_available`, `metadata`, `read` y `release` (todos operan sobre ese mismo handle durante todo el ciclo de vida).
    - Leer los metadatos (`cv2.CAP_PROP_FPS` / `CAP_PROP_FRAME_COUNT` / `CAP_PROP_FRAME_WIDTH` / `CAP_PROP_FRAME_HEIGHT`) del **mismo** `VideoCapture`; **NO abrir un segundo `cv2.VideoCapture` solo para metadatos.**
    - **NO reutilizar `VideoFileFrameSource`** internamente ni acceder a su estado privado `_cap`: la reutilización de `VideoFileFrameSource` **NO es obligatoria** y no debe hacerse si acopla a estado privado o abre dos handles del mismo archivo. `VideoFileFrameSource` permanece **intacto** para sus consumidores existentes.
    - Reportar no-disponibilidad (`is_available()` falso) si el archivo no abre (video corrupto), sin propagar excepción cruda de infraestructura.
    - Sanear `video_path` con `path_sanitizer` antes de abrir (ver tarea 13).
    - _Requirements: 4.1, 4.2, 4.3, 15.3_

  - [ ] 2.3 Escribir tests de `VideoReaderPort` / `OpenCvVideoReader` (OBLIGATORIO)
    - Con video sintético corto: `metadata()` reporta fps/total_frames/width/height plausibles; `read()` itera frames; `release()` idempotente.
    - **Asserar el comportamiento de captura única:** `open`/`is_available`/`metadata`/`read`/`release` operan sobre el **mismo** `cv2.VideoCapture`; **no** se instancia un segundo `cv2.VideoCapture` (verificable, p. ej., contando construcciones vía mock de `cv2.VideoCapture`); no se accede al `_cap` de `VideoFileFrameSource`.
    - Video inexistente/corrupto → `is_available()` falso sin excepción cruda.
    - _Requirements: 4.1, 4.2, 4.3, 15.3_

- [ ] 3. Implementar `VideoRecorder` (infraestructura, wrap de `cv2.VideoWriter`)
  - [ ] 3.1 Crear `src/infrastructure/camera/video_recorder.py`
    - Constructor: `VideoRecorder(output_path, fps=configured_recording_fps, codec_candidates=("mp4v", "avc1"))`. **`fps` (= `configured_recording_fps`) se fija en el constructor; el `frame_size` NO va en el constructor.**
    - `open(frame_size)` con fallback de códec (`mp4v` → `avc1`, configurable); primero que abra gana. **El `frame_size` proviene EXCLUSIVAMENTE del primer frame válido** (`frame.shape`); escribir ese primer frame y registrar `recorded_width`/`recorded_height` realmente grabados.
    - Escribir en el archivo temporal `monitoring.recording.mp4` (sin downscale, sin selección de frames).
    - `write(frame_bgr)`: **tras `open`, toda frame debe coincidir con `frame_size`; si una frame llega con dimensiones distintas → error de grabación EXPLÍCITO, sin resize silencioso.**
    - `close()` idempotente con `release()` del writer en `finally`.
    - `validate()`: archivo existe, `size > 0`, y se puede abrir y leer ≥1 frame.
    - Exponer `frames_written` y `codec_used`; el `cv2.VideoWriter` se abre con `configured_recording_fps` de modo que `container_fps == configured_recording_fps`.
    - Manejar "ningún códec abre" con excepción clara y log del códec intentado.
    - Sanear el `output_path` con `path_sanitizer` (ver tarea 13).
    - _Requirements: 1.3, 1.9, 1.10, 1.11, 13.2, 13.6_

  - [ ] 3.2 Escribir tests unitarios de `VideoRecorder` (mock de `cv2.VideoWriter`) (OBLIGATORIO)
    - **`fps` en el constructor** (`configured_recording_fps`); `open(frame_size)` usa la dimensión del **primer frame válido**; el primer frame se escribe; `recorded_width/height` reflejan `frame.shape`.
    - **Dimensión distinta tras `open` → error de grabación explícito (sin resize silencioso).**
    - Fallback de códec (`mp4v` falla → `avc1` abre); `open()` que falla con todos los candidatos → excepción clara.
    - `close()` idempotente (doble llamada no falla); `validate()` sobre archivo vacío vs válido; escribe a `monitoring.recording.mp4`.
    - Verificar `frames_written`, `codec_used` y `container_fps == configured_recording_fps`.
    - _Requirements: 1.3, 1.9, 1.10, 1.11, 13.2, 13.6_

- [ ] 4. Implementar `VideoRecordingWorker` (aplicación, daemon, camera owner)
  - [ ] 4.1 Crear `src/application/services/video_recording_worker.py`
    - Definir `@dataclass RecordingMetrics(recording_duration_seconds, frames_written, configured_recording_fps, container_fps, effective_recording_fps, deviation_between_configured_and_effective_fps, recorded_width, recorded_height, codec_used, peak_temperature_c, exit_reason)`.
      - `container_fps = configured_recording_fps` (fps con que se abrió el `VideoWriter`; **no** se modifica tras grabar).
      - `effective_recording_fps = frames_written / recording_duration_seconds` (medido de forma independiente).
      - `deviation_between_configured_and_effective_fps = configured_recording_fps - effective_recording_fps` (diagnóstico; **el video NUNCA se reescribe** por esta desviación: sin remux, sin two-pass, sin re-encode).
    - Constructor recibe `configured_recording_fps` (NO `target_fps`); el `VideoRecorder` recibe ese fps en su construcción.
    - `run()`: adquirir cámara **una sola vez** vía `FrameSource` (único propietario), abrir el `VideoRecorder` con el primer frame válido, y **escribir CADA frame entregado** por el `FrameSource`.
    - **La cadencia la controla explícitamente la cámara/frame source; el worker NO aplica throttle: sin `sleep()` para limitar FPS, sin doble-throttle que descarte frames.** No ejecuta inferencia, Scene Gate, selección de snapshots ni downscale adicional durante la grabación.
    - Eventos `finalize_event`, `abort_event`, `pause_event`, `thermal_pause_event` (pausa cooperativa, mismo patrón que `CaptureWorker`).
    - `release_resources()` idempotente en `finally`: `recorder.close()` (valida) + `frame_source.release()` (libera `_camera_lock`). **Nunca** salir de `run()` sin liberar la cámara (liberación garantizada por cualquier causa de salida).
    - `get_last_frame()` **thread-safe**: retorna una **copia** del último frame leído (nunca un `ndarray` mutable concurrentemente por el bucle de grabación) para el preview UI.
    - Fijar `exit_reason` (`finalize` | `abort` | `error` | `frame_source_exhausted`) y registrar todas las métricas de `RecordingMetrics` (incluida `deviation_between_configured_and_effective_fps`).
    - _Requirements: 1.5, 1.6, 1.7, 1.8, 1.12, 1.19, 7.1, 7.4, 13.5_

  - [ ] 4.2 Introducir la ruta de configuración VIDEO de Picamera2 con FPS objetivo (solo video-first)
    - Añadir a `RaspberryCameraFrameSource` una ruta de **configuración VIDEO de Picamera2** que fije el FPS objetivo explícitamente vía los controles soportados (`FrameRate` o `FrameDurationLimits`), usada **ÚNICAMENTE** para la grabación video-first. **Cambio más pequeño posible.**
    - **NO alterar** la ruta de *still-configuration* usada por el preview y el capture-first (`_start_camera`/`capture_single_frame` permanecen con `create_still_configuration`), y **NO crear una segunda instancia de `Picamera2`**.
    - Cablear esta ruta desde el arranque del `VideoRecordingWorker` (video-first) sin tocar el flujo capture-first.
    - _Requirements: 1.6, 14.1_

  - [ ] 4.3 Escribir tests unitarios de `VideoRecordingWorker` (FrameSource fake) (OBLIGATORIO)
    - Liberación de recursos en `finally` incluso ante excepción; `exit_reason` correcto por cada salida; **nunca** se retorna sin liberar la cámara.
    - Respuesta a `finalize_event` y `abort_event`; `get_last_frame()` retorna una **copia** del último frame grabado (mutar el retorno no afecta el buffer interno).
    - **Verificar que NO hay throttle: el worker no llama `sleep()` para limitar FPS y escribe cada frame entregado por el FrameSource** (no descarta frames); no ejecuta inferencia ni Scene Gate durante la grabación.
    - Verificar métricas de `RecordingMetrics`: `frames_written`, `recording_duration_seconds`, `configured_recording_fps`, `container_fps == configured_recording_fps`, `effective_recording_fps` y `deviation_between_configured_and_effective_fps`.
    - _Requirements: 1.5, 1.6, 1.7, 1.8, 1.12, 1.19, 7.1, 7.4, 13.5_

  - [ ]* 4.4 Escribir property test de exclusividad de cámara (opcional — ya cubierto por 4.3)
    - **Property 6: Exclusividad de cámara y preview thread-safe**
    - **Validates: Requirements 1.2, 1.12, 7.2, 7.3, 7.4**
    - Verificar que tras `run()` (por finalize/abort/error) el `_camera_lock` siempre queda liberado y no se abre una segunda fuente en paralelo para preview.

  - [ ]* 4.5 Verificación de la config VIDEO de Picamera2 en hardware (marcada, NO en la suite núcleo)
    - Marcar con `@pytest.mark.hardware` / `@pytest.mark.raspberry`: la config VIDEO fija el FPS objetivo en cámara IMX500/RPi sin romper preview/capture-first ni crear una segunda `Picamera2`.
    - **No requerido en la suite núcleo** (que corre sin cámara/GPIO/RPi).
    - _Requirements: 1.6, 14.1_

- [ ] 5. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Persistencia: columna `video_path` (ORM + dominio + repo + migración idempotente)
  - [ ] 6.1 Añadir `video_path` nullable al modelo ORM `MonitoringModel`
    - `video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)`.
    - _Requirements: 10.1, 10.3_

  - [ ] 6.2 Añadir `video_path: Optional[str] = None` a la entidad de dominio `Monitoring`
    - Mantener el dominio **libre de SQLAlchemy**; solo el campo opcional en la dataclass.
    - _Requirements: 10.5, 15.2_

  - [ ] 6.3 Mapear `video_path` en `SqlMonitoringRepository` (ambos sentidos)
    - Mapear `video_path` en lectura (model → domain) y escritura (domain → model).
    - Persistir siempre como ruta **relativa** desde la raíz del proyecto.
    - _Requirements: 10.1, 10.2_

  - [ ] 6.4 Añadir migración idempotente `ALTER TABLE` en `_migrate_add_columns` (cableada en `init_db()`)
    - En `src/infrastructure/persistence/database.py`, dentro de `_migrate_add_columns`, verificar la ausencia de la columna vía `PRAGMA table_info(monitorings)` y ejecutar `ALTER TABLE monitorings ADD COLUMN video_path VARCHAR(500)` **solo cuando esté ausente**.
    - Cablear la migración desde `DatabaseManager.init_db()`. Dejar explícito que `Base.metadata.create_all()` por sí solo **NO** añade la columna a una tabla `monitorings` ya existente; por eso se requiere el `ALTER TABLE` idempotente.
    - Cubrir los tres casos: instalación nueva (create_all crea la tabla con la columna, migración no-op), BD Raspberry existente sin columna (ALTER la crea preservando filas), reinicios posteriores (no-op).
    - _Requirements: 10.6, 10.7, 10.9, 10.10_

  - [ ] 6.5 Escribir test OBLIGATORIO de migración de `video_path`
    - Partir de una BD existente **sin** `video_path` (simular BD Raspberry previa con filas) → ejecutar `init_db()`/migración → verificar que la columna `video_path` fue **creada** (PRAGMA table_info) **y** que las filas existentes se preservan intactas (mismo conteo y contenido).
    - Verificar **idempotencia**: un segundo `init_db()` no falla ni duplica la columna (no-op).
    - Round-trip de repositorio: guardar/leer `Monitoring` con y sin `video_path` (default `None`), persistiendo ruta relativa.
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10_

- [ ] 7. Configuración de muestreo disperso y grabación por perfil (`settings.py`)
  - [ ] 7.1 Añadir campos de video-first a `ExecutionProfile` para `edge` y `full`
    - Campos: `video_first_enabled`, `recording_target_fps` (→ `configured_recording_fps` del worker/recorder), `video_codec_candidates`, `sparse_min_frames_between_detections`, `sparse_max_frames_without_detection`, `sparse_use_scene_gate`, `sparse_enable_flow_propagation`, `save_annotated_video`.
    - Baseline documentado (no definitivo, configurable): `min≈5`, `max≈12`, `use_scene_gate=True`, `enable_flow_propagation=True`, `save_annotated_video=False`. Gaps entendidos como **frames** (equivalencia temporal depende del fps).
    - `edge` puede usar valores **más conservadores** que el baseline para preservar recall. No hardcodear valores como constantes definitivas. Documentar que el fps de grabación de la Raspberry no está garantizado y debe medirse.
    - **NO** convertir gaps de frames a intervalos de tiempo ni introducir muestreo basado en segundos (fuera de alcance — ver Notes).
    - _Requirements: 6.1, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8_

  - [ ] 7.2 Escribir tests/smoke de perfiles/config (OBLIGATORIO)
    - Verificar que ambos perfiles exponen los campos, que `save_annotated_video` es `False` por defecto en producción, y que `edge` es más conservador que `full`.
    - Prueba de importación del módulo de settings.
    - _Requirements: 6.1, 11.1, 11.2, 11.4, 11.5_

- [ ] 8. Implementar `VideoAnalysisService` (aplicación, inferencia diferida sobre mp4, sin cv2)
  - [ ] 8.1 Crear `src/application/services/video_analysis_service.py` — esqueleto e I/O vía puerto
    - Definir `@dataclass VideoAnalysisConfig(...)` y `VideoAnalysisProgress`/`VideoAnalysisResult`.
    - Constructor con `video_reader: VideoReaderPort` **inyectado** y factories inyectadas (`components_factory`, `process_frame_fn`, `annotation_renderer`, `thermal_monitor`, `report_writer`, `profile_name`) → **importable sin Detectron2 y sin `cv2`** (lazy import; el módulo NO importa `cv2` ni instancia `cv2.VideoCapture`).
    - `run()`: `video_reader.open()`, `video_reader.metadata()` (fps, total_frames, size); video corrupto/ilegible (`is_available()` falso) → `error` conservando el archivo.
    - _Requirements: 4.1, 4.2, 13.4, 14.4, 15.3_

  - [ ] 8.2 Implementar el bucle de análisis por frame con un único `components`
    - Mantener **un solo objeto `components`** (de `components_factory()`) durante todo el video; `components.tracker` (SimpleTracker) provee el tracking cross-frame de RetinaNet. **NO** instanciar un `SimpleTracker` adicional.
    - Usar `OpticalFlowVisualTracker` **solo** en los frames donde el detector no corre (propagación).
    - Por frame: `decide_run_detector(...)` con la config del análisis; contar razones.
    - Si corre detector: `process_frame(frame, components, name)` (reusado sin cambios), `last_detection_frame`, `gap=0`. Si no: `gap += 1` y propagación por flujo cuando esté habilitada.
    - Ejecutar salud (ResNet-18) y madurez (HSV+CIELab) según políticas existentes cuando el score lo justifique.
    - _Requirements: 4.4, 4.5, 4.6, 4.7_

  - [ ] 8.3 Semántica scheduled/successful/failed y snapshots derivados
    - En **todo** frame `detector_scheduled` (`run_detector = true`): **persistir el `Snapshot` derivado (DB + JPEG raw en `outputs/monitorings/{id}/snapshots/raw/`) ANTES de llamar a `process_frame()`**; registrar `frame_index` = índice de frame del video.
    - Contabilizar `detector_scheduled_frames`. `process_frame()` **puede lanzar excepción incluso dentro de RetinaNet**; en éxito → `analysis_successful_frames`; si `process_frame()` o una persistencia recuperable relacionada falla → `analysis_failed_frames`, **conservar el snapshot ya persistido** y continuar con el frame siguiente (sin distinguir si el fallo fue en RetinaNet, ResNet o madurez). Se cumple el invariante `analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames`.
    - Generar crops en `crops/snapshot_XXXXXX/`; acumular `best_by_track` y persistir **≤1** `DetectionInspectionResult` por track (mejor área).
    - _Requirements: 4.4, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 13.8_

  - [ ] 8.4 Métricas de ejecución, video anotado OFF y `MonitoringMetrics`
    - Calcular y persistir `MonitoringMetrics` al completar.
    - Escribir `pipeline_metrics.json` en **todos** los desenlaces con: `detector_scheduled_frames`, `analysis_successful_frames`, `analysis_failed_frames` (cumpliendo `successful + failed == scheduled`), `source_video_fps`, `configured_recording_fps`, `container_fps`, `effective_recording_fps`, `deviation_between_configured_and_effective_fps`, `min_frames_between_detections`, `max_frames_without_detection`, min/max gaps, `frames_written`, `recording_duration_seconds`, conteo por causa, tracks únicos, detecciones, tiempos, la **nota de equivalencia temporal aproximada** de los gaps (sin conversión frame-gap→tiempo) y la **config usada**.
    - `save_annotated_video=False` por defecto: no escribir video anotado; si `True`, escribirlo sin modificar el video original.
    - Pausa térmica cooperativa (mismo patrón que `SnapshotAnalysisService`).
    - _Requirements: 6.2, 6.3, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 13.8_

  - [ ] 8.5 Escribir tests unitarios de `VideoAnalysisService` (fakes + `VideoReaderPort` fake) (OBLIGATORIO)
    - Con `components_factory`/`process_frame_fn` fakes y `VideoReaderPort` fake (sin `cv2`): snapshots persistidos == frames `detector_scheduled` (incluso si `process_frame()` falla); ≤1 resultado por track; `pipeline_metrics.json` con config y conteos `detector_scheduled_frames`/`analysis_successful_frames`/`analysis_failed_frames` (`successful + failed == scheduled`); video anotado no escrito por defecto.
    - Verificar que se mantiene un único `components` (tracking cross-frame vía `components.tracker`) y que no se instancia un `SimpleTracker` adicional.
    - Video corrupto (`is_available()` falso) → `error` con archivo conservado.
    - Fallo de `process_frame()` en un subconjunto de frames (incluso dentro de RetinaNet) → se contabiliza en `analysis_failed_frames`, snapshot conservado, análisis continúa (no aborta).
    - _Requirements: 4.1, 4.2, 4.4, 5.1, 5.4, 5.6, 5.7, 6.2, 12.1, 12.3, 13.4, 13.8_

  - [ ]* 8.6 Escribir property tests de snapshots derivados y un resultado por track (opcional — ya cubierto por 8.5)
    - **Property 3: Snapshots derivados de frames con detector programado** — snapshots persistidos == `detector_scheduled_frames`; `analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames`. **Validates: Requirements 3.7, 4.3, 5.1, 5.2, 5.3, 5.4**
    - **Property 4: Un resultado por track** — ≤1 `DetectionInspectionResult` por track. **Validates: Requirements 5.7**
    - Hypothesis: para secuencias generadas de decisiones y fallos arbitrarios de `process_frame()`.

  - [ ]* 8.7 Escribir property test de tolerancia a fallos por frame (opcional — ya cubierto por 8.5)
    - **Property 11: Tolerancia a fallos por frame (semántica scheduled/successful/failed)**
    - **Validates: Requirements 13.8**
    - Hypothesis: con un subconjunto arbitrario de frames cuyo `process_frame()` falla (incluida RetinaNet), el análisis continúa, `snapshots == detector_scheduled_frames`, `analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames` y no aborta.

- [ ] 9. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Integrar grabación y finalización atómica en `MonitoringService`
  - [ ] 10.1 `start_session`: arrancar `VideoRecordingWorker` en modo video-first
    - Si `video_first_enabled`, registrar y lanzar `VideoRecordingWorker` (estado `RUNNING = recording`) en lugar de `CaptureWorker`; conservar reconciliación de huérfanas, enforcement de 1 sesión activa y check del camera lock. Cablear la ruta de configuración VIDEO de Picamera2 (tarea 4.2) para la grabación video-first.
    - Chequear espacio en disco antes de iniciar; si insuficiente, bloquear con mensaje "Espacio en disco bajo ({space} MB)." y no arrancar.
    - Cámara no disponible al iniciar → `error` con mensaje "La cámara no está disponible. Verifica la conexión."
    - `CaptureWorker` permanece disponible y sin cambios para regresión/benchmark.
    - _Requirements: 1.1, 8.1, 13.1, 13.3_

  - [ ] 10.2 `finalize_capture`: finalización ATÓMICA del video, persistir `video_path`, lanzar análisis
    - Señalar `finalize_event`, esperar al hilo; el worker en su `finally` (shutdown controlado) **detiene captura → cierra writer → libera cámara**.
    - **Validar** el archivo temporal `monitoring.recording.mp4` (existe, `size > 0`, el reader abre y lee ≥1 frame).
    - Si es **válido**: `os.replace(monitoring.recording.mp4 → monitoring.mp4)` (rename atómico en el mismo FS) + persistir `video_path` (ruta relativa); escribir métricas de grabación.
    - Si es **inválido/ilegible**: NO renombrar; conservar el temp/fallido para diagnóstico cuando sea seguro; transicionar a `error`.
    - El análisis **solo** inicia tras existir un `monitoring.mp4` final y validado. Transicionar `running → analyzing` y lanzar `VideoAnalysisService` (daemon, sesión DB propia, `OpenCvVideoReader(final_path)` inyectado) con la config sparse del perfil activo.
    - `analyzing → completed` solo al terminar el análisis con éxito (nunca `completed` durante procesamiento). Un fallo de análisis posterior **nunca** modifica/elimina un `monitoring.mp4` validado.
    - _Requirements: 1.13, 1.14, 1.15, 1.16, 2.2, 2.3, 8.2, 8.3, 8.4, 13.4, 13.10_

  - [ ] 10.3 Escribir tests de integración `start → recording → finalize → analyzing → completed` (OBLIGATORIO)
    - Usar `VideoFileFrameSource` (sin cámara, off-Raspberry) tomando un video de `data/videos` como fuente para grabar y luego analizar.
    - Verificar persistencia de `video_path`, rename atómico a `monitoring.mp4`, transición a `analyzing` y luego `completed`, y que el video permanece intacto.
    - _Requirements: 1.14, 2.2, 8.2, 8.4, 14.3_

  - [ ] 10.4 Escribir test OBLIGATORIO de integridad del video
    - Verificar que un `monitoring.mp4` válido nunca se elimina ni corrompe: simular fallo fatal de análisis → estado `error` y el archivo permanece intacto (mismo tamaño/legible).
    - Archivo temporal inválido en finalize → `error`, sin `monitoring.mp4`, temp conservado.
    - _Requirements: 1.14, 1.15, 1.16, 13.4, 13.10_

  - [ ]* 10.5 Escribir property tests de integridad de video e invariante de estado (opcional — ya cubierto por 10.3/10.4)
    - **Property 1: Integridad del video** — **Validates: Requirements 1.9, 1.10, 6.3, 8.5, 8.6, 9.7, 13.4, 13.5, 13.6, 13.7, 13.10**
    - **Property 5: Invariante de estado** — **Validates: Requirements 8.3, 8.4, 4.7, 9.6**

- [ ] 11. Recuperación tras terminación abrupta y reprocesamiento controlado (Alternativa B)
  - [ ] 11.1 Implementar la rutina "Recuperación tras terminación abrupta" (junto a reconciliación de huérfanas)
    - En el reinicio de la app (junto a `_reconcile_orphaned_sessions` de `MonitoringService`): si existe un `monitoring.recording.mp4` remanente de una terminación abrupta (`finally` no corrió), **conservarlo** (nunca borrarlo ciegamente).
    - **Validarlo de forma segura** con el mismo criterio de `validate()` (existe, `size > 0`, el reader abre y lee ≥1 frame).
    - Si **valida**: promover a `monitoring.mp4` mediante la rutina de recuperación (rename atómico `os.replace` + persistir `video_path` relativo), quedando reprocesable.
    - Si **no valida**: conservarlo para diagnóstico; **nunca** marcarlo automáticamente como `monitoring.mp4` válido sin pasar validación.
    - La sesión sigue marcándose `error` por la reconciliación (para desbloquear el módulo); el video temporal se conserva y la rutina decide si es promocionable.
    - _Requirements: 1.17, 1.18, 13.11, 9.7_

  - [ ] 11.2 Escribir test OBLIGATORIO de recuperación tras terminación abrupta
    - Temp `monitoring.recording.mp4` **válido** remanente → tras la rutina se **promueve** a `monitoring.mp4` (rename atómico) y se persiste `video_path`.
    - Temp **inválido/corrupto** remanente → se **conserva**, **NO** se promueve, `video_path` no se marca como válido, y la sesión queda `error`.
    - Reprocesabilidad tras la promoción (el video promovido es reprocesable).
    - _Requirements: 1.17, 1.18, 13.11, 9.1_

  - [ ] 11.3 Añadir `reset_for_reprocess` al repositorio de `Monitoring`
    - Método explícito y auditado que reinicia el estado a `analyzing` **sin** ejecutar una transición directa desde un estado terminal (no se añade arista `completed → analyzing` a la FSM).
    - Documentar que es un reinicio de análisis, no una transición de negocio normal.
    - _Requirements: 9.6_

  - [ ] 11.4 Implementar `reprocess_monitoring(monitoring_id, config)` con guard de sync ESTRICTO
    - Validar precondiciones: estado terminal `completed`/`error`, `video_path` no nulo y archivo existente en disco (ruta saneada); si no se cumplen, rechazar sin modificar el monitoreo.
    - Rechazar si otro monitoreo del módulo está activo o si ya hay un reproceso en curso (claim de exclusividad vía `MonitoringRuntimeRegistry`).
    - **Guard de sincronización ESTRICTO:** bloquear el reproceso destructivo si **CUALQUIERA** de estas entidades tiene `remote_sync_status == "synced"` (una sola basta): el **Monitoring**, **ALGÚN** `Snapshot` relacionado, **ALGÚN** `DetectionInspectionResult` relacionado, o el `MonitoringMetrics` relacionado. Al bloquear, mensaje claro y **sin alterar** ningún dato local ni remoto. **No** añadir DELETE remoto ni tombstones.
    - Si NINGUNA entidad está sincronizada: limpiar resultados previos (snapshots, inspection results, metrics, artefactos derivados); `reset_for_reprocess`; lanzar `VideoAnalysisService` con la config indicada sobre el **mismo** mp4 (sin modificar/eliminar el original), `OpenCvVideoReader(video_path)` inyectado (ruta saneada, ver tarea 13). Liberar el claim en `finally`.
    - _Requirements: 2.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 13.10_

  - [ ] 11.5 Adaptar reconciliación de sesiones huérfanas para preservar reprocesabilidad
    - `_reconcile_orphaned_sessions`: sesión activa sin hilo vivo → `error` (desbloquea el módulo) **conservando** `video_path` y el video para reproceso.
    - _Requirements: 9.7, 13.9, 13.10_

  - [ ] 11.6 Escribir tests OBLIGATORIOS de reprocesamiento y guard de sync ESTRICTO
    - Reprocesamiento **permitido** solo cuando NINGUNA entidad está `synced`: limpia resultados y re-corre con config distinta.
    - Reprocesamiento **BLOQUEADO** cuando **CUALQUIER** entidad relacionada está `synced`: **probar cada tipo de entidad por separado** (solo el Monitoring `synced`; solo un Snapshot `synced`; solo un DetectionInspectionResult `synced`; solo el MonitoringMetrics `synced`) → cada caso rechaza con mensaje claro y **nada se borra** (local ni remoto).
    - Fallo de análisis mantiene el video intacto y reintento OK con config distinta.
    - Casos de rechazo por precondición: no terminal, `video_path` nulo, archivo ausente, sesión activa o reproceso en curso.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.8, 13.10_

  - [ ]* 11.7 Escribir property test de reprocesabilidad con guard de sync (opcional — ya cubierto por 11.6)
    - **Property 2: Reprocesabilidad con guard de sincronización estricto**
    - **Validates: Requirements 2.5, 9.2, 9.3, 9.4, 9.7**

- [ ] 12. Seguridad de rutas: sanear `video_path` contra path traversal
  - [ ] 12.1 Aplicar `path_sanitizer` en todo acceso a `video_path`
    - Validar/sanear la ruta antes de abrir, leer o borrar el archivo en `VideoRecorder`, `OpenCvVideoReader`/`VideoAnalysisService` y `MonitoringService.reprocess_monitoring`/recuperación.
    - Persistir siempre rutas relativas; rechazar rutas con secuencias de traversal.
    - _Requirements: 10.2, 10.4, 15.3_

  - [ ] 12.2 Escribir test OBLIGATORIO de seguridad de rutas (incluye intentos de traversal)
    - Casos con `../` y rutas absolutas → saneadas o rechazadas antes de acceder al archivo; se persiste ruta relativa.
    - Cubrir los tres puntos de acceso: `VideoRecorder` (escritura), `OpenCvVideoReader`/`VideoAnalysisService` (lectura), `MonitoringService.reprocess_monitoring` (lectura/reproceso).
    - **Property 10: Seguridad de rutas** — **Validates: Requirements 10.2, 10.4**
    - _Requirements: 10.2, 10.4, 15.3_

- [ ] 13. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. UI/rutas: grabación sin selección de archivo y preview thread-safe
  - [ ] 14.1 Adaptar `app/routes/agricultural_ui.py` — acción de inicio sin selección de archivo
    - La acción de inicio graba directamente (modo video-first) **sin** ofrecer selección manual de archivos desde `data/videos`; delegar en `MonitoringService` (rutas sin lógica de negocio).
    - _Requirements: 8.1, 14.5, 15.1_

  - [ ] 14.2 Adaptar templates de ejecución y preview
    - Reflejar únicamente **Recording → Processing → Completed** según el estado del monitoreo.
    - Preview durante grabación: obtener el worker vía `MonitoringRuntimeRegistry.get_worker()` (thread-safe) y mostrar la **copia** del último frame de `VideoRecordingWorker.get_last_frame()` sin abrir la cámara en paralelo ni instanciar una segunda `Picamera2`.
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2_

  - [ ] 14.3 Escribir tests OBLIGATORIOS de rutas y preview durante grabación
    - Verificar que la acción de inicio no expone selección de archivos.
    - Verificar que el preview usa el worker del registry y `get_last_frame()` (copia) sin fuente de cámara paralela y sin `capture_single_frame()`.
    - _Requirements: 7.2, 7.3, 7.4, 14.5_

- [ ] 15. Regresión completa, boundaries/imports y documentación
  - [ ] 15.1 Verificar no regresión y boundaries/imports arquitectónicas (OBLIGATORIO)
    - Ejecutar `python -m pytest -q` (la suite completa no debe requerir cámara/GPIO/RPi).
    - `test_imports.py`: `video_analysis_service` importable **sin** Detectron2 **y sin `cv2`** (usa `VideoReaderPort`); `video_reader_port` (application interface) importable **sin `cv2`**.
    - `test_architecture_boundaries.py`: `video_analysis_service`, `video_recording_worker` y `video_reader_port` (application) **no** importan `cv2`/`torch`/`detectron2` a nivel de módulo; `cv2` confinado a `OpenCvVideoReader`/`VideoRecorder`/`VideoFileFrameSource` (infra); dominio libre de SQLAlchemy.
    - Marcar pruebas que requieran cámara IMX500/RPi con `@pytest.mark.hardware`/`@pytest.mark.raspberry`.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 15.2, 15.3, 15.4_

  - [ ] 15.2 Escribir test OBLIGATORIO de no regresión de capture-first
    - Confirmar que `CaptureWorker` y `SnapshotAnalysisService` permanecen invariantes y sus tests existentes pasan (sin cambios de comportamiento).
    - **Property 8: No regresión** — **Validates: Requirements 14.1, 14.4**
    - _Requirements: 14.1, 14.4_

  - [ ]* 15.3 Documentar ADR capture-first → video-first y plan de benchmark (opcional — no requerido para funcionar)
    - Crear `docs/decisions/ADR-NNN-video-first-monitoring.md` (estado, contexto, decisión, consecuencias +/-, evidencia, fecha, relación con spec 019).
    - Registrar el plan de comparación benchmark `full_detection` vs `sparse` (métricas antes/después) en `docs/benchmarks/` según ADR-003.
    - _Requirements: 12.7_

- [ ] 16. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- **El conjunto de tests núcleo de la Spec 019 es OBLIGATORIO, no opcional; no puede omitirse para
  un MVP.** Corrige cualquier indicación previa en contrario: los tests de `decide_run_detector`,
  equivalencia del runner legacy, `VideoReaderPort`/`OpenCvVideoReader`, `VideoRecorder`,
  `VideoRecordingWorker`, migración de `video_path`, perfiles/config, `VideoAnalysisService`,
  integración start→recording→finalize→analyzing→completed, integridad de video, reprocesamiento
  (incluido reproceso-bloqueado-si-sincronizado), recuperación tras terminación abrupta, preview
  durante grabación, path traversal y regresión/imports/boundaries son **obligatorios** y se
  implementan.
- Solo permanecen opcionales (`*`) los property-based de Hypothesis **cuando ya existe un test
  determinista equivalente** (1.3, 4.4, 8.6, 8.7, 10.5, 11.7) y el ADR/documentación extra (15.3),
  por no ser necesarios para el funcionamiento del sistema. La verificación en hardware de la
  config VIDEO de Picamera2 (4.5) está marcada `@pytest.mark.hardware`/`@pytest.mark.raspberry` y
  **no** forma parte de la suite núcleo que corre sin hardware.
- Cada tarea referencia requisitos específicos para trazabilidad y termina con la suite en verde.
- **No se modifican** `CaptureWorker`, `SnapshotAnalysisService` ni `ExportService`. La
  **exportación del video completo (`monitoring.mp4`) está fuera de alcance** de la Spec 019 y
  podría abordarse en una spec futura; `ExportService` permanece intacto. **No hay task de
  exportación.**
- **`OpenCvVideoReader` posee exactamente UN `cv2.VideoCapture`** para `open`/`is_available`/
  `metadata`/`read`/`release`; **no** abre un segundo `cv2.VideoCapture` para metadatos ni accede
  al `_cap` privado de `VideoFileFrameSource`, que permanece **intacto** para sus consumidores.
- **`VideoRecorder` recibe `configured_recording_fps` en el constructor y el `frame_size` solo en
  `open()`** (descubierto del primer frame válido). Tras `open`, una frame con dimensión distinta
  es un **error de grabación explícito** (sin resize silencioso).
- **La cadencia la controla la cámara/frame source; el `VideoRecordingWorker` no aplica throttle
  (sin `sleep()` de FPS) y escribe cada frame entregado.** `container_fps == configured_recording_fps`;
  `effective_recording_fps = frames_written / recording_duration_seconds`;
  `deviation_between_configured_and_effective_fps` es solo diagnóstico y **el video nunca se
  reescribe** (sin remux/two-pass/re-encode).
- **Config VIDEO de Picamera2:** video-first fija el FPS objetivo (`FrameRate`/`FrameDurationLimits`)
  por una ruta de video **usada solo para grabación video-first**, **sin** alterar la ruta
  still-configuration de preview/capture-first ni crear una segunda `Picamera2`.
- **Métricas de análisis renombradas:** `detector_scheduled_frames`, `analysis_successful_frames`,
  `analysis_failed_frames`, con invariante `analysis_successful_frames + analysis_failed_frames ==
  detector_scheduled_frames`. El `Snapshot` crudo se persiste **ANTES** de `process_frame()`; un
  fallo de `process_frame()` (incluso dentro de RetinaNet) cuenta como `analysis_failed_frames`,
  conserva el snapshot y continúa.
- **Muestreo temporal FUERA DE ALCANCE (diferido a una spec futura):** ninguna task añade muestreo
  basado en segundos ni conversión frame-gap→intervalo-de-tiempo. Los gaps `min`/`max` permanecen
  **en frames** y configurables; las métricas solo registran `source_video_fps`, los gaps en frames
  y su **equivalencia temporal aproximada** como orientación documentada (Requisitos 11.7, 11.8).
- **Reproceso con guard de sync ESTRICTO:** basta **UNA** entidad relacionada (Monitoring, ALGÚN
  Snapshot, ALGÚN DetectionInspectionResult o el MonitoringMetrics) con `remote_sync_status ==
  "synced"` para **bloquear** el reproceso destructivo. No se añaden DELETE remotos ni tombstones.
- **Recuperación tras terminación abrupta:** al reiniciar, un `monitoring.recording.mp4` remanente
  se conserva y se valida de forma segura; se promueve a `monitoring.mp4` (rename atómico + persistir
  `video_path`) **solo si valida**; si es inválido se conserva para diagnóstico y **nunca** se marca
  válido sin validación; la sesión queda `error` por reconciliación.
- El video original (`monitoring.mp4`) nunca se elimina ni corrompe por un fallo de análisis
  posterior; la finalización a `monitoring.mp4` es atómica y solo tras validación.
- `Base.metadata.create_all()` por sí solo **no** añade la columna `video_path` a una tabla
  `monitorings` existente; por eso la migración usa `ALTER TABLE ... ADD COLUMN` idempotente
  (verificada con `PRAGMA table_info`) cableada en `init_db()`.
- El Requirement 16 corresponde al **runner legacy** como herramienta de diagnóstico (no a
  exportación); las sub-tareas 1.4/1.5 lo referencian.
- Se mantiene un único `components.tracker` (SimpleTracker) para el tracking cross-frame; no se
  instancia un SimpleTracker adicional.
- No se migra a YOLO; ResNet-18 y madurez HSV+CIELab se mantienen; no se modifican los modelos.
- Todo cambio respeta Clean Architecture, `DEVICE = "cpu"`, sin dependencias nuevas y sin
  robótica/GPIO.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "6.1", "6.2", "7.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2", "3.2", "6.3", "6.4", "7.2"] },
    { "id": 2, "tasks": ["1.5", "2.3", "4.1", "6.5"] },
    { "id": 3, "tasks": ["4.2", "4.3", "4.4", "4.5", "8.1"] },
    { "id": 4, "tasks": ["8.2"] },
    { "id": 5, "tasks": ["8.3", "8.4"] },
    { "id": 6, "tasks": ["8.5", "8.6", "8.7", "10.1", "11.3"] },
    { "id": 7, "tasks": ["10.2"] },
    { "id": 8, "tasks": ["11.1"] },
    { "id": 9, "tasks": ["10.3", "10.4", "10.5", "11.2"] },
    { "id": 10, "tasks": ["11.4"] },
    { "id": 11, "tasks": ["11.5", "12.1"] },
    { "id": 12, "tasks": ["11.6", "11.7", "12.2", "14.1"] },
    { "id": 13, "tasks": ["14.2"] },
    { "id": 14, "tasks": ["14.3", "15.1"] },
    { "id": 15, "tasks": ["15.2", "15.3"] }
  ]
}
```
