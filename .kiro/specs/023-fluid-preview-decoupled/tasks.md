# Implementation Plan — Spec 023: Preview fluido desacoplado del pipeline de monitoreo

## Overview

Este plan implementa de forma incremental el desacople de la cadencia física de cámara / preview (`camera_stream_fps` ≈ 20 FPS) respecto de la cadencia de grabación (`recording_target_fps`, propia de cada perfil: EDGE ≈ 5, FULL ≈ 10), sin modificar el pipeline de análisis ni la máquina de estados del monitoreo.

El orden sigue el diseño: config → cadencia física del frame source → `RecordingSampler` (aplicación, pura) → modificación acotada del `VideoRecordingWorker` → `LivePreviewManager` → ownership compartido → endpoints MJPEG → handoff → wiring → frontend → observabilidad/benchmark → regresión → validación física.

Convenciones del proyecto aplicadas:
- Código, comentarios y docstrings en inglés; documentación de spec en español.
- Pruebas de lógica pura/aplicación sin hardware (fakes/in-memory, reloj inyectado, SQLite in-memory, TestClient).
- Cambios pequeños, revisables y reversibles. No refactorizaciones fuera de alcance.
- Solo pruebas dirigidas. Esta spec NO requiere property-based (Hypothesis).
- **Exclusión de hardware:** un marker `@pytest.mark.raspberry`/`@pytest.mark.hardware` NO hace skip por sí solo (ver Task 12.0). Los tests nuevos deben usar fakes y correr en PC sin hardware; los que toquen hardware se excluyen del comando oficial.
- Sub-tareas marcadas con `*` son opcionales y NO se implementan automáticamente.
- En checkpoints intermedios se ejecutan **solo** las pruebas dirigidas del área modificada; la suite completa se ejecuta **a lo sumo una vez, al final**.
- NO se toca `src/infrastructure/vision/`, `SnapshotAnalysisService`, ni la lógica/frecuencia del análisis diferido (Requirement 5).
- Layout táctil existente **800×480 (landscape base + portrait aditivo por media queries)**; esta Spec NO introduce rediseño responsive.

## Tasks

- [x] 1. Configuración: `camera_stream_fps` explícito y validación fail-fast
  - [x] 1.1 Añadir `camera_stream_fps` al `ExecutionProfile` y a ambos perfiles
    - En `src/infrastructure/config/settings.py`, agregar el campo `camera_stream_fps: float` al dataclass `ExecutionProfile` (frozen).
    - Fijar `camera_stream_fps=20.0` en `EDGE_PROFILE` y `FULL_PROFILE`; conservar `recording_target_fps` (5.0 EDGE / 10.0 FULL) SIN cambios.
    - Documentar en el docstring del campo que `camera_stream_fps` es la cadencia física/preview y NO la de grabación.
    - _Requirements: 3.1, 4.2, 13.1, 13.2, 13.3_

  - [x] 1.2 Validación fail-fast de cadencias por perfil
    - Crear `validate_profile_cadences(profile)` en `settings.py`, invocada tras seleccionar `ACTIVE_PROFILE`, que exija `camera_stream_fps > 0`, `recording_target_fps > 0` y `recording_target_fps <= camera_stream_fps`.
    - Ante configuración inválida de un perfil (constante de código): `raise ValueError` claro durante la carga de config/startup. NO mutar el dataclass frozen, NO corregir con `max(...)`, NO inventar valores, NO warning-y-continuar.
    - _Requirements: 13.4, 13.5, 13.6_

  - [x] 1.3 Unit tests de config (obligatorio)
    - `camera_stream_fps` presente en EDGE (20/5) y FULL (20/10); `recording_target_fps` inalterado por perfil.
    - `validate_profile_cadences`: acepta configs válidas; `raise` ante `recording > stream` y ante valores `<= 0`.
    - _Requirements: 13.1, 13.2, 13.4, 13.5_
  - _Checkpoint: `python -m pytest tests/unit -k "profile or settings or cadence" -q`_

- [x] 2. Cadencia física del frame source desde `camera_stream_fps` (modo VIDEO)
  - [x] 2.1 Alimentar el frame source de monitoreo con `camera_stream_fps` y añadir timeout de lock configurable
    - En `app/routes/agricultural_ui.py::monitoring_start`, cambiar la construcción video-first a `create_frame_source(width=..., height=..., fps=ACTIVE_PROFILE.camera_stream_fps, camera_mode="video")`.
    - En `RaspberryCameraFrameSource`, añadir el parámetro `camera_lock_timeout_seconds: float = 15.0` (default = comportamiento actual de monitoreo) y usarlo en `read()` en lugar del literal `_camera_lock.acquire(timeout=15.0)`. Propagarlo por `create_frame_source`.
    - No modificar la rama capture-first (legacy) ni la interfaz `FrameSource`. No añadir `start()/stop()` a `FrameSource`. Conservar `_camera_lock`, `_CAMERA_SETTLE_SECONDS`, `_frame_duration_us` y la semántica de monitoreo (default 15.0).
    - _Requirements: 3.2, 6.1, 6.3, 7.3, 17.1_

  - [x] 2.2 Unit test de cadencia física en modo VIDEO y timeout de lock (obligatorio)
    - Verificar que `RaspberryCameraFrameSource(camera_mode="video", fps=20)` traduce a `FrameDurationLimits` coherente con `_frame_duration_us(20)`; que el modo `still` NO impone cadencia; que la validación `fps > 0` se preserva; y que `read()` usa `camera_lock_timeout_seconds` (default 15.0; corto cuando se configura). Test puro de configuración, sin abrir cámara (no requiere hardware).
    - _Requirements: 3.2, 4.2, 7.3, 17.1_
  - _Checkpoint: `python -m pytest tests/unit -k "frame_source or raspberry_camera" -q`_

- [x] 3. `RecordingSampler` — política de ejecución pura (aplicación)
  - [x] 3.1 Implementar `RecordingSampler`
    - Crear `src/application/services/recording_sampler.py` con `RecordingSampler(recording_fps, *, clock=time.monotonic)` y `should_write() -> bool`.
    - Algoritmo **phase-preserving, no-burst**: primer frame elegible; si `now < next_due` no escribe; si toca, avanza `next_due` saltando los intervalos perdidos hacia el próximo slot futuro (`next_due + (skipped+1)*interval`). NO usar `now + interval` (produce drift). Máximo una escritura por llamada.
    - Sin imports de cv2/picamera2/torch/FastAPI (lógica pura); validación `recording_fps > 0`.
    - _Requirements: 3.3, 3.4, 3.5, 3.6_

  - [x] 3.2 Unit tests del sampler con reloj inyectado (obligatorio)
    - Entrada ~20 FPS → ~5 escrituras/seg (EDGE) y ~10/seg (FULL).
    - **Simulación larga (30–60 s) con jitter (19.2/20.4/18.9 FPS) → sin drift acumulativo significativo** (fase preservada respecto a la rejilla objetivo, dentro de tolerancia documentada).
    - Entrada más lenta que el objetivo no duplica ni dispara ráfaga tras hueco; salta al siguiente slot futuro; uso de tiempo monotónico (clock inyectado); primer frame elegible.
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 17.3_
  - _Checkpoint: `python -m pytest tests/application -k "recording_sampler" -q` (o `tests/unit` según ubicación de tests)._

- [x] 4. Modificación acotada del `VideoRecordingWorker`
  - [x] 4.1 Inyectar el sampler, desacoplar el buffer de preview y añadir `preview_sequence`
    - En `src/application/services/video_recording_worker.py`, aceptar un `recording_sampler` inyectado.
    - En el loop: tras `read()`, llamar SIEMPRE `_set_last_frame(frame)` e incrementar un `preview_sequence` (preview ~camera_stream_fps); luego, solo si `recording_sampler.should_write()`, abrir el recorder de forma perezosa en el primer frame **escrito** y `video_recorder.write(frame)`.
    - Añadir `get_preview_frame_snapshot() -> (sequence, frame_copy) | None` (copia thread-safe + sequence). Conservar `get_last_frame()` idéntico (contrato/tests existentes).
    - NO modificar: estados, signals, semántica START/FINALIZE/ABORT, pausa manual/térmica, `release_resources()`/cleanup en `finally`, ni el registry.
    - _Requirements: 2.3, 2.4, 2.6, 3.7, 4.1, 8 (sequence), 16.2_

  - [x] 4.2 Extender `RecordingMetrics` con métricas de diagnóstico y ventana de captura
    - Añadir campos NUEVOS: `camera_frames_produced`, `preview_frames_updated`, `camera_capture_elapsed_seconds`, `accumulated_pause_seconds`, `configured_camera_stream_fps`, `effective_camera_stream_fps`.
    - `effective_camera_stream_fps` = `(camera_frames_produced - 1) / camera_capture_elapsed_seconds` para `N >= 2`, si no `0.0`.
    - `camera_capture_elapsed_seconds` = `last_frame_time - first_frame_time - accumulated_pause_seconds`; `accumulated_pause_seconds` se acumula al entrar/salir del loop cooperativo de pausa (`pause_event`/`thermal_pause_event`), NO por heurística de gaps. No cambiar el comportamiento de la pausa, solo medirla.
    - **NO renombrar `frames_written`** (nombre real actual, serializado en `pipeline_metrics.json`). Preservar `configured_recording_fps`, `container_fps`, `effective_recording_fps` y su semántica (NO recalcular).
    - _Requirements: 14.1, 14.2, 14.3_

  - [x] 4.3 Construir el sampler y pasar `camera_stream_fps` en `MonitoringService._start_video_first`
    - En `src/application/services/monitoring_service.py::_start_video_first`, instanciar `RecordingSampler(recording_fps=ACTIVE_PROFILE.recording_target_fps)` e inyectarlo al `VideoRecordingWorker`; pasar `configured_camera_stream_fps=ACTIVE_PROFILE.camera_stream_fps`.
    - No alterar `_start_capture_first`, reservas del registry, thermal monitor, ni el flujo finalize/abort.
    - _Requirements: 4.1, 16.2, 16.4_

  - [x] 4.4 Unit tests del worker (obligatorio, con fakes)
    - Actualiza `latest_preview_frame` e incrementa `preview_sequence` para TODOS los frames leídos; escribe solo los aceptados por el sampler; `open()` perezoso en el primer frame escrito; mantiene el `recording_target_fps` del perfil; libera recursos en `finally`; `get_last_frame()` devuelve copia; `get_preview_frame_snapshot()` devuelve `(sequence, copia)`.
    - Métricas: `frames_written` conserva nombre y valor; `effective_recording_fps` sin cambios; `effective_camera_stream_fps` = `(N-1)/elapsed`.
    - `camera_capture_elapsed_seconds` solo descuenta pausas que solapan `[first_frame, last_frame]` (mecanismo `pending` que se consolida al llegar el siguiente frame). Tests: (a) pause+resume+nuevo frame → pausa descontada; (b) pause + finalize/abort SIN nuevo frame → pausa NO descontada; (c) pause antes del primer frame → no afecta; en todos `camera_capture_elapsed_seconds >= 0`.
    - No importar cv2/picamera2 (boundary preservado).
    - _Requirements: 2.3, 2.4, 2.6, 3.7, 4.1, 14.1, 14.2, 14.3, 16.2, 17.4_
  - _Checkpoint: `python -m pytest tests/unit -k "video_recording_worker or monitoring_service_video_first" -q`_

- [x] 5. `LivePreviewManager` — preview previo persistente
  - [x] 5.1 Implementar `PreviewBuffer` (latest-frame con generación de sesión + `sequence` + `Condition` + sentinela)
    - Buffer thread-safe de un solo elemento con `_generation` (epoch de sesión) y `_sequence`. `publish(jpeg)` incrementa `sequence` (solo JPEG publicables) y `notify_all()`; `stop()` marca terminal y `notify_all()`; `reset()` **incrementa `_generation`**, limpia frame/sequence, sale de stopped, `notify_all()` y devuelve la nueva generación; `current_generation()`; `wait_for_new(expected_generation, after_sequence, timeout)` usa `threading.Condition` y devuelve `(sequence, jpeg)` | `STREAM_STOPPED` (si `_stopped` **o** `_generation != expected_generation`) | `None`; `latest()`.
    - `reset()` lo invoca el **arranque de una sesión física de captura** (IDLE→RUNNING), no `enable_preview()`. Propiedad: un consumidor de una generación NUNCA recibe frames de una generación posterior aunque `reset()` haya limpiado `_stopped`. El `PreviewBuffer` NO cuenta frames de cámara/encode. Sin busy-loop.
    - _Requirements: 8, 9.1, 10.2, 10.3, 11.1_

  - [x] 5.2 Implementar `LivePreviewManager` (dos ejes de estado + coordinación Spec 020 + inyección explícita)
    - **Dependencias inyectadas por constructor:** `frame_source_factory`, `runtime_registry`, `camera_is_locked: Callable[[], bool]`, `camera_stream_fps`, `camera_wh`. No importar infraestructura directamente; el callable `is_camera_locked` se resuelve en `main.py` (Task 9.1).
    - Estado en **dos ejes ortogonales** (NO de Monitoring): `_capture_state` (IDLE|STARTING|RUNNING|STOPPING|ERROR) y `_subscriptions_suspended: bool`.
    - **Coordinación device-global (Spec 020):** `can_enable_preview = not runtime_registry.has_active_capture() and not runtime_registry.is_global_analysis_active() and not camera_is_locked()`. Se evalúa al arrancar desde IDLE dentro de `subscribe()`/`start()` (revalidación de capture/camera-lock), y en `enable_preview()`/`resume_after_failed_handoff()`. `is_global_analysis_active()` es **preflight de rendimiento, NO claim atómico** (documentar que no cierra TOCTOU con el heavy analysis; la exclusión física la da `_camera_lock`). NO reconstruir coordinación paralela ni añadir una preview-reservation a Spec 020.
    - **Serialización del arranque con estado `STARTING` (Race 1):** el primer `subscribe()` desde IDLE que cumpla `can_enable_preview` pasa a `STARTING` bajo el lock y se vuelve startup owner; arranca el capture runtime FUERA del lock; al pasar a `RUNNING` guarda la generación. Un `subscribe()` concurrente que ve `STARTING` NO arranca: espera (Condition de estado) y se asocia a la MISMA generación al pasar a RUNNING, o recibe `None` si el startup falla (`STARTING→ERROR/IDLE`). Solo un thread ejecuta `IDLE→RUNNING`.
    - **Generación por sesión física:** el arranque del capture runtime llama `PreviewBuffer.reset()` (nueva generación) exactamente una vez. `enable_preview()`/`resume_after_failed_handoff()` NO crean generación (solo rehabilitan subscriptions).
    - Construir el frame source **explícitamente en modo VIDEO** y con timeout de lock corto: `frame_source_factory(..., fps=camera_stream_fps, camera_mode="video", camera_lock_timeout_seconds=1.0)`.
    - Thread daemon: por cada `read()` exitoso `camera_frames_produced += 1`; codifica JPEG **una sola vez** y, por cada encode exitoso, `preview_frames_encoded += 1` + `PreviewBuffer.publish(jpeg)`. Métricas separadas (encode fallido no incrementa `preview_frames_encoded`).
    - Multi-suscriptor: `subscribe()` devuelve `None` si `_subscriptions_suspended`, `_capture_state==ERROR` o `not can_enable_preview`; si no, `(token, generation)`; `unsubscribe(token)` decrementa y marca `last_active`; **cero suscriptores + `idle_timeout_s` → el propio capture loop hace `break` (sin self-join)** y su `finally` libera.
    - `get_latest_preview()`, `wait_for_preview(expected_generation, after_sequence, timeout)` (propaga `STREAM_STOPPED`), `is_running()`, `diagnostics()`.
    - No importar cv2/picamera2 directamente; delega en `FrameSource`/`CameraService`. Cleanup idempotente; error de cámara sale del loop y libera sin lock/thread huérfano.
    - _Requirements: 1.1, 1.2, 1.4, 1.6, 6.4, 9.1, 9.2, 9.3, 9.6, 10.1, 10.2, 10.3, 11.1, 11.2, 14.2, 14.3_

  - [x] 5.3 Contrato síncrono `_stop_capture()` (disciplina de locking) + `suspend_for_handoff()`/`resume_after_failed_handoff()`/`enable_preview()`
    - **Disciplina de locking (anti-deadlock):** el lock interno se usa SOLO para leer/escribir estado; NUNCA se mantiene durante `thread.join()`, `read()`, `release()`, `encode_frame_jpeg()`, `Condition.wait()`, `time.sleep()`.
    - **`release()` exactly-once y FUERA del lock (patrón claim):** helper `_claim_source_for_release()` que, bajo el lock, devuelve el frame source y marca `_released=True` solo si nadie lo reclamó; la llamada física `src.release()` ocurre FUERA del lock. Lo usan tanto `_stop_capture()` (caller externo) como el capture thread en su `finally` (idle-stop/error). Solo un camino libera.
    - `_stop_capture(timeout=5.0) -> bool`: `LOCK`(STOPPING + copiar thread_ref)`UNLOCK` → `PreviewBuffer.stop()` → `thread_ref.join(timeout)` FUERA del lock → `src=_claim_source_for_release()`; `if src: src.release()` FUERA del lock (settle incluido, sin segundo `_CAMERA_SETTLE_SECONDS`) → `LOCK`(estado final)`UNLOCK`. Éxito → `_capture_state=IDLE`, `True`; timeout → `_capture_state=ERROR`, `False`. `stop()` = `_stop_capture()` y **NO** toca `_subscriptions_suspended`. **`stop()`/`_stop_capture()` son solo para caller externo; el capture thread NUNCA se hace self-join.**
    - `suspend_for_handoff(timeout) -> bool`: `_subscriptions_suspended=True` + `_stop_capture()`. La suspensión sobrevive al stop.
    - `resume_after_failed_handoff()` y `enable_preview()`: **SOLO rehabilitan subscriptions** (`_subscriptions_suspended=False`) **si `can_enable_preview`** (Spec 020: `has_active_capture()` y `is_global_analysis_active()` False, y `camera_is_locked()` False); NO crean generación ni llaman `PreviewBuffer.reset()` (eso ocurre solo al arrancar una nueva sesión física IDLE→STARTING→RUNNING); idempotentes.
    - _Requirements: 6.1, 7.1, 7.2, 7.3, 7.4, 9.3, 9.7_

  - [x] 5.4 Unit tests del manager (obligatorio, fakes: `FakeRuntimeRegistry`, `fake_camera_is_locked`, frame source fake)
    - `start()/stop()` idempotentes; `stop()` antes de `start()`; start/stop repetidos; `_stop_capture()` síncrono devuelve tras liberar; **timeout → `_capture_state=ERROR`, False y subscribe rechazado**.
    - Construye el frame source en modo VIDEO con `camera_stream_fps` y `camera_lock_timeout_seconds` corto; usa `runtime_registry`/`camera_is_locked` inyectados.
    - **Coordinación Spec 020:** `subscribe()`/`enable_preview()`/`resume_after_failed_handoff()` devuelven/permanecen bloqueados si `has_active_capture()` (incl. reservation sin worker), `is_global_analysis_active()` o `camera_is_locked()`; revalidación en arranque desde IDLE. Test que documenta que `is_global_analysis_active()` es **preflight, no claim atómico** (no cierra TOCTOU con heavy analysis).
    - **Nueva generación tras idle-stop (Race/lifecycle):** `subscribe` → generation 1 → unsubscribe último → idle-stop (capture loop `break`, release, IDLE) → `subscribe` de nuevo → generation 2 → el nuevo stream funciona (buffer no queda `stopped`).
    - **Race 1 (STARTING):** dos `subscribe()` exactamente simultáneos desde IDLE → `frame_source_factory` llamada UNA vez → un solo capture thread → ambos reciben la MISMA generation; si el startup falla, ambos reciben None.
    - **Idle timeout sin self-join:** último `unsubscribe` → idle timeout → capture loop termina naturalmente (`break`) → `release()` una sola vez → `_capture_state=IDLE` → sin `RuntimeError`/deadlock.
    - Multi-suscriptor con un único frame source/thread; `get_latest_preview()` None antes del primer frame; `wait_for_preview` no hace busy-loop (Condition).
    - **`suspend_for_handoff()` sobrevive al stop:** `_capture_state=IDLE` pero `subscribe()` sigue None hasta rehabilitación con `can_enable_preview`.
    - **Generación anti-reset:** consumidor en `generation=1` + `stop()` + arranque `reset()`→`generation=2` → recibe `STREAM_STOPPED` y jamás frames de `generation=2`.
    - **Wake:** consumidor en `wait_for_preview` recibe `STREAM_STOPPED` al `stop()`/`suspend_for_handoff()`.
    - **Locking / release fuera del lock (Race 5):** fake cuyo cleanup intenta re-tomar el lock del manager mientras `_stop_capture()` hace `join()` → sin deadlock; `_claim_source_for_release()` garantiza `release()` una sola vez y siempre FUERA del lock.
    - **Métricas separadas:** encode fallido tras read exitoso → `camera_frames_produced` sube, `preview_frames_encoded` no; `sequence` solo sube con publish.
    - **`effective_camera_stream_fps` (preview previo)** = `(N-1)/elapsed`, SIN descontar pausas (el manager no tiene pause signals; los tests NO simulan `pause_event`/`thermal_pause_event`).
    - Cleanup tras excepción del frame source; nunca crea una segunda instancia concurrente (fake cuenta adquisiciones).
    - _Requirements: 1.1, 1.4, 6.4, 7.1, 7.3, 9.2, 9.3, 9.6, 10.2, 14.2, 14.3, 17.2, 17.7_
  - _Checkpoint: `python -m pytest tests/unit -k "live_preview_manager or preview_buffer" -q`_

- [x] 6. Ownership reutilizable para endpoints de preview
  - [x] 6.1 Dependency `require_monitoring_owner` compartida (reutiliza dependencies existentes)
    - En `app/dependencies.py`, agregar `require_monitoring_owner(monitoring_id, user=Depends(require_current_user_api), monitoring_repo=Depends(get_monitoring_repository), module_repo=Depends(get_module_repository), greenhouse_repo=Depends(get_greenhouse_repository))`.
    - Resolver `Monitoring → Module → Greenhouse` con `greenhouse_repo.get_by_id_for_owner(greenhouse_id, user.id)` (Spec 022). NO crear otro mecanismo de sesión. `monitoring_api.py` NO importa `_monitoring_owned_by_user` de `agricultural_ui.py`.
    - Semántica **404 not found** para recursos inexistentes o ajenos (no filtra existencia).
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 6.2 Aplicar ownership al endpoint single-frame existente de monitoreo
    - Cambiar `GET /api/monitoring/{monitoring_id}/preview` en `app/routes/monitoring_api.py` para usar `Depends(require_monitoring_owner)` (corrige el gap actual de solo-auth), sin cambiar su comportamiento de servir `get_last_frame()` ni abrir cámara.
    - _Requirements: 2.2, 15.2, 15.5_

  - [x] 6.3 Unit tests de ownership (obligatorio, TestClient)
    - Usuario dueño accede; usuario ajeno recibe 404; usuario no autenticado 401; recurso inexistente → 404 (misma respuesta que ajeno).
    - _Requirements: 15.1, 15.2, 15.4, 17.6_
  - _Checkpoint: `python -m pytest tests/unit -k "monitoring_preview or ownership or preview_owner" -q`_

- [x] 7. Endpoints de stream MJPEG (503 antes de cabeceras; encode fuera del event loop)
  - [x] 7.1 Endpoint de stream del preview previo (ROUTE HANDLER + generador SÍNCRONOS)
    - Añadir `GET /api/camera/preview-stream` como handler **`def` síncrono** (no `async def`), para que FastAPI ejecute preflight y generador en threadpool. Preflight: `subscribe()` → si None (suspendido/ERROR/`not can_enable_preview`), 503; `(token, generation)`; `wait_for_preview(generation, after=0, timeout~2s)` → si None/`STREAM_STOPPED`, `unsubscribe` + 503; solo entonces `StreamingResponse` con generador síncrono.
    - El generador **reutiliza el frame del preflight como primer frame** (no lo descarta); luego emite solo `sequence` nuevos de su `generation` (usa `wait_for_preview`, que devuelve `STREAM_STOPPED` en stop/suspend o cambio de generación → termina); `unsubscribe(token)` en `finally` (ante `GeneratorExit`/desconexión/stop). NO usar `await request.is_disconnected()`. El JPEG ya viene codificado por el manager (no encode en el loop).
    - _Requirements: 1.1, 1.2, 1.3, 8, 8.1, 8.3, 8.5, 9.1, 10.3, 10.4, 13 (503 antes de cabeceras)_

  - [x] 7.2 Endpoint de stream del preview de monitoreo (ROUTE HANDLER + generador SÍNCRONOS)
    - Añadir `GET /api/monitoring/{monitoring_id}/preview-stream` como handler **`def` síncrono** con `Depends(require_monitoring_owner)`. Validar worker/estado y obtener el primer `get_preview_frame_snapshot()` ANTES de construir la respuesta; si no hay worker/frame → 503 (nunca abre cámara). Ese primer snapshot se usa como primer frame del generador.
    - **Generador síncrono** (threadpool) que lee `worker.get_preview_frame_snapshot()`, emite solo cuando `sequence` es nuevo, duerme `time.sleep(1/camera_stream_fps)` si no hay novedad (sin busy-loop), y codifica JPEG con `CameraService.encode_frame_jpeg` **fuera del event loop**. Encode por cliente aceptado en esta versión (una pantalla local). Termina ante `GeneratorExit`.
    - _Requirements: 2.1, 2.2, 2.5, 8, 8.1, 8.3, 8.5, 10.3, 10.4, 11.3, 15.2_

  - [x] 7.3 Conservar el endpoint single-frame `GET /api/camera/preview`
    - Mantener el endpoint existente como fallback/diagnóstico (no eliminarlo). Sin cambios funcionales.
    - _Requirements: 8.6_

  - [x] 7.4 Unit tests de endpoints de stream (obligatorio, TestClient con fakes)
    - `Content-Type: multipart/x-mixed-replace` en ambos streams; **503 antes de cabeceras** sin frame/worker (nunca 200→503).
    - El stream de monitoreo NO llama `capture_single_frame`/`capture_preview_frame` ni abre cámara (mock del registry/worker); ambos emiten solo `sequence` nuevos; no acumulan backlog; terminan ante desconexión simulada (`finally`/`unsubscribe` en el previo).
    - **Wake:** con un stream previo abierto, `manager.stop()`/`suspend_for_handoff()` hace que el generador termine (recibe `STREAM_STOPPED`) sin esperar timeouts largos.
    - **Event loop no bloqueado:** con el stream de monitoreo activo, un request a `/monitoring/{id}/status` (o `/api/.../log`) responde (espera y encode fuera del loop, generador síncrono).
    - Ownership del stream de monitoreo (dueño 200 / ajeno 404 / no-auth 401).
    - _Requirements: 2.2, 8.1, 8.3, 8.5, 9.1, 10.3, 10.4, 11.3, 15.2, 17.6, 17.7_
  - _Checkpoint: `python -m pytest tests/unit -k "preview_stream or mjpeg" -q`_

- [x] 8. Handoff server-side preview → monitoreo (sin reacquisición)
  - [x] 8.1 Suspender el preview previo antes de adquirir la cámara de monitoreo
    - En `agricultural_ui.py::monitoring_start`, ANTES de `create_frame_source(...)` y `start_session(...)`, llamar `ok = live_preview_manager.suspend_for_handoff(timeout=5.0)`.
    - Si `ok is False` → abortar inicio con mensaje "cámara ocupada, reintenta" (no iniciar monitoreo). Si `start_session()` falla, llamar `resume_after_failed_handoff()` (que solo rehabilita si `can_enable_preview`; si el fallo fue tras adquisición parcial, `has_active_capture()` seguirá True y no rehabilita hasta el cleanup). NO añadir un segundo `_CAMERA_SETTLE_SECONDS`. Preservar `CameraStillBusyError`/`ActiveSessionError` como red de seguridad.
    - _Requirements: 6.1, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 8.2 Rehabilitar el preview al volver a la pantalla de setup
    - En `agricultural_ui.py`, en el handler GET de la pantalla de preparación del monitoreo (`/modulos/{id}/monitoreo`), llamar `live_preview_manager.enable_preview()` (idempotente; solo rehabilita si `can_enable_preview`). Esto cierra el lifecycle SUSPENDED→IDLE tras un monitoreo terminado sin añadir estados a `MonitoringState`.
    - _Requirements: 7.1, 9.3_

  - [x] 8.3 Tests de handoff y carreras de concurrencia (obligatorio, fakes)
    - Handoff invoca `suspend_for_handoff()` antes de construir el frame source de monitoreo; no hay solape de instancias de cámara; no hay segundo settle redundante; `enable_preview()` al volver a setup rehabilita solo con cámara libre.
    - **Race 1** dos `subscribe()` simultáneos → un único frame source/Picamera2.
    - **Race 2** un cliente `unsubscribe` con otro activo → cámara no liberada.
    - **Race 3** último `unsubscribe` → idle timeout → cámara liberada.
    - **Race 4** preview activo + `suspend_for_handoff()` + `subscribe()` durante suspensión → devuelve None; el monitoreo obtiene la cámara; nunca dos owners.
    - **Race 5** `stop()` durante un `read()` en curso → join acotado, `release()` una sola vez, sin deadlock.
    - **Race 6** monitoreo activo + dos clientes de preview de monitoreo → ninguno abre Picamera2; ambos leen del worker.
    - _Requirements: 6.1, 6.4, 7.1, 7.2, 7.3, 7.4, 9.2, 9.3, 10.1, 17.7_
  - _Checkpoint: `python -m pytest tests/unit -k "handoff or monitoring_start or preview_race" -q`_

- [x] 9. Wiring de aplicación (`main.py` / `dependencies.py`)
  - [x] 9.1 Crear el singleton `LivePreviewManager` en el lifespan (composition root)
    - En `app/main.py::lifespan`, crear `app.state.live_preview_manager = LivePreviewManager(frame_source_factory=create_frame_source, runtime_registry=app.state.monitoring_runtime_registry, camera_is_locked=is_camera_locked, camera_stream_fps=ACTIVE_PROFILE.camera_stream_fps, camera_wh=(ACTIVE_PROFILE.camera_width, ACTIVE_PROFILE.camera_height))`. El callable `is_camera_locked` se importa aquí (composition root), no dentro del manager. Crear el manager DESPUÉS del `monitoring_runtime_registry`.
    - En el shutdown del lifespan, `app.state.live_preview_manager.stop()` (idempotente, best-effort).
    - _Requirements: 9.7, 10.2_

  - [x] 9.2 Dependency `get_live_preview_manager`
    - En `app/dependencies.py`, añadir `get_live_preview_manager(request)` que devuelva el singleton de `app.state`.
    - _Requirements: 10.1, 10.2_
  - _Checkpoint: `python -m pytest tests/unit -k "imports or lifespan or dependencies" -q` y `python -c "import app.main"`._

- [x] 10. Frontend
  - [x] 10.1 Migrar `monitoring_setup.html` al stream de preview previo con semántica de disponibilidad correcta
    - Apuntar `<img id="camera-preview-img">` a `src="/api/camera/preview-stream"`. El botón "Actualizar cámara" reasigna `src` con `?t=Date.now()`.
    - Nueva semántica de disponibilidad: `onload` del stream → cámara AVAILABLE → habilitar "Iniciar Monitoreo"; `onerror` → consultar `/api/camera/status` solo como diagnóstico. NO usar `/api/camera/status` para bloquear un preview propio activo. No cambiar globalmente la semántica de `/api/camera/status`.
    - Mantener el layout táctil existente 800×480 (sin rediseño).
    - _Requirements: 1.1, 1.2, 8.3, 9.1, 12 (status semantics)_

  - [x] 10.2 Usar el stream de preview durante monitoreo en `monitoring.js`
    - Al entrar a estado `running`, asignar `<img id="recording-preview-img" src="/api/monitoring/{id}/preview-stream">`; limpiar `src` al salir de `running` o en estados terminales.
    - Retirar el fast-loop de polling (`PREVIEW_INTERVAL_MS`) manteniendo el slow-loop de status/log (2 s), la auto-redirección a reporte, y los listeners `visibilitychange`/`beforeunload`. No llamar `capture_single_frame`/`capture_preview_frame`.
    - _Requirements: 2.1, 2.5, 8.3, 9.1, 9.2_

  - [x] 10.3 Actualizar tests de frontend existentes al nuevo comportamiento (obligatorio)
    - Actualizar `tests/unit/test_monitoring_preview_loop.py` y afines para reflejar el uso de `<img>`+stream en lugar del fast-loop de polling. Actualizar SOLO para el nuevo comportamiento correcto, nunca para ocultar una regresión. Conservar la aserción de que el JS no invoca `capture_single_frame`. Cubrir la semántica de disponibilidad (`onload`/`onerror`).
    - _Requirements: 12, 17.8_
  - _Checkpoint: `python -m pytest tests/unit -k "preview_loop or ui_preview or monitoring_setup" -q`_

- [x] 11. Observabilidad y benchmark
  - [x] 11.1 Exponer diagnósticos de preview (camino único obligatorio)
    - `LivePreviewManager.diagnostics()` (API interna obligatoria) devuelve `camera_frames_produced`, `preview_frames_encoded`, `active_subscribers`, `camera_capture_elapsed_seconds`, `effective_camera_stream_fps`, `state` (capture_state + subscriptions_suspended).
    - Exponer vía **`GET /api/camera/preview-diagnostics`** (autenticado, diagnóstico local) — mecanismo único para consultar el preview previo en R18.
    - Confirmar que `RecordingMetrics` (Task 4.2) se sigue capturando por el flujo de finalize con `dataclasses.asdict(worker.recording_metrics)` en `pipeline_metrics.json`, con `frames_written` intacto; si el helper de benchmark quiere una clave descriptiva, mapear `"recording_frames_written": metrics.frames_written` SOLO en el JSON del helper.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [x] 11.2 Helper de recolección de benchmark (obligatorio, solo lectura/recolección)
    - Crear un helper en `scripts/benchmarks/` que **únicamente recolecte evidencia** durante el procedimiento MANUAL desde la UI real: consultar `GET /api/camera/preview-diagnostics`, leer el `pipeline_metrics.json` generado, y consultar temperatura/CPU/RAM; resumir/exportar los resultados. El helper **NO** crea/inicia/finaliza monitorings ni duplica lógica de aplicación (el usuario opera Tomato Monitor normalmente). Añadir un documento en `docs/benchmarks/` (basado en `benchmark-template.md`) con las métricas y tolerancias.
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

  - [ ]* 11.3 ADR del desacople de cadencias y transporte MJPEG (opcional, no bloqueante)
    - Crear `docs/decisions/ADR-004-preview-decoupled-cadence.md` (estado, contexto, decisión, consecuencias +/-, evidencia pendiente de RPi, fecha, relación con Spec 023) conforme a `documentation-standards`. No debe retrasar implementación ni validación.
    - _Requirements: 3.6, 8.3, 8.4_

- [x] 12. Regresión final
  - [x] 12.0 Asegurar exclusión real de tests de hardware en PC
    - Inspeccionar `pytest.ini` (actual: `addopts = -ra -m "not supabase"`). Garantizar que los tests nuevos NO requieren hardware (usan fakes) y por tanto corren en PC. Para cualquier test que sí toque hardware, marcarlo `@pytest.mark.raspberry`/`@pytest.mark.hardware` Y extender `addopts` a `-m "not supabase and not raspberry and not hardware"` (o añadir `pytest_collection_modifyitems` que los skipee sin hardware). Verificar que el comando oficial no intenta acceder a cámara/GPIO.
    - _Requirements: 17.8_

  - [x] 12.1 Ejecutar la suite completa
    - `python -m pytest -q`. La suite debe pasar en PC sin cámara/GPIO/Raspberry. No modificar tests existentes para ocultar regresiones.
    - _Requirements: 5.1, 5.2, 5.3, 16.1, 16.2, 16.3, 16.4, 17.8_

- [ ] 13. Validación física en Raspberry Pi (manual, obligatoria para cerrar la Spec)
  - [ ] 13.1 Ejecutar el procedimiento de validación en RPi 5 (manual)
    - Perfil EDGE, 20–30 s, `camera_stream_fps`=20, `recording_target_fps`=5. Verificar: preview previo y de monitoreo fluidos; `effective_camera_stream_fps` ≈ objetivo; `camera_frames_produced` ≈ 400 y `recording_frames_written` ≈ 100 en 20 s (≈ 200 en FULL); sin errores `Camera in Running state`/allocator/`camera already acquired`; cámara liberada al terminar; CPU/RAM/temperatura dentro de valores operacionales; status/log responden con el stream activo. Documentar en `docs/benchmarks/`.
    - **La Spec NO se marca completa hasta ejecutar esta validación física junto con el usuario.**
    - _Requirements: 4.2, 6.1, 6.2, 7.4, 11.3, 12.1, 12.3, 14.3, 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_
