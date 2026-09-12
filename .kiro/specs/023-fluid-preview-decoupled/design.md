# Design Document — Spec 023: Preview fluido desacoplado del pipeline de monitoreo

## Overview

Esta especificación desacopla la **cadencia física de la cámara / preview** (`camera_stream_fps` ≈ 20 FPS) de la **cadencia de grabación** (`recording_target_fps`, propia de cada perfil: EDGE ≈ 5, FULL ≈ 10). El objetivo es una vista previa realmente fluida tanto **antes** como **durante** un monitoreo, sin duplicar/interpolar frames, sin abrir una segunda cámara, y **sin modificar el pipeline de análisis** ni la máquina de estados del monitoreo.

El diseño introduce cuatro piezas nuevas, todas de bajo blast radius, que **componen** las abstracciones existentes:

1. **`camera_stream_fps`** como campo explícito del `ExecutionProfile` (config), con validación.
2. **`RecordingSampler`** (aplicación, `src/application/services/`): decide, por tiempo monotónico, si un frame real debe persistirse. Componente pequeño, puro y testeable (algoritmo phase-preserving, sin drift).
3. **`LivePreviewManager`** (aplicación): gestiona el lifecycle del preview **previo** al monitoreo componiendo `FrameSource`; único runtime propietario del preview físico.
4. **Transporte MJPEG** (`multipart/x-mixed-replace`) en endpoints de streaming, más una validación de **ownership** reutilizable para el preview de monitoreo.

Se modifica de forma **acotada** el `VideoRecordingWorker` (orquestación preview/sampler/métricas) y el `RaspberryCameraFrameSource` para que la cadencia física provenga de `camera_stream_fps` y no de `recording_target_fps`.

> Nomenclatura: `LivePreviewManager` y `RecordingSampler` son nombres conceptuales de diseño. Las clases concretas se definen aquí, pero los requisitos exigen la *responsabilidad/separación*, no un nombre literal.

## Alineación con Requirements

| Requirement | Cubierto por |
|---|---|
| R1 Preview previo fluido | `LivePreviewManager` + endpoint stream previo |
| R2 Preview durante monitoreo | `VideoRecordingWorker.latest_preview_frame` + endpoint stream de monitoreo |
| R3 Desacople de cadencias | `camera_stream_fps` en config + `RecordingSampler` |
| R4 Grabación por perfil | `VideoRecorder(fps=recording_target_fps)` sin cambios de contenedor |
| R5 Pipeline intacto | No se toca `src/infrastructure/vision/` ni `SnapshotAnalysisService`/análisis |
| R6 Único propietario | `_camera_lock` reutilizado; owner único por fase |
| R7 Transición segura + handoff server-side | `LivePreviewManager.suspend_for_handoff()` (stop síncrono acotado + bloqueo de reacquisición) antes de `start_session()` |
| R8 Transporte MJPEG local | `StreamingResponse(multipart/x-mixed-replace)` |
| R9 Lifecycle | Suscriptores + idle stop del manager; cleanup idempotente |
| R10 Concurrencia/backpressure | Buffer latest-frame con lock; sin colas |
| R11 Rendimiento | Codificación JPEG compartida por frame; sin busy loops |
| R12 Térmica | Sin ThermalMonitor nuevo en preview previo |
| R13 Config explícita | `camera_stream_fps` + validación `recording<=stream` |
| R14 Métricas | `RecordingMetrics` extendido + métricas de stream runtime |
| R15 Seguridad/ownership | Dependency `require_monitoring_owner` compartida |
| R16 Preservación | Cambios acotados; sin nuevos estados/signals |
| R17 Tests | Unit por componente + regresión |
| R18 Validación RPi | Métricas diagnósticas + procedimiento |

---

## Arquitectura

### Diagrama de flujo objetivo

```text
Raspberry Pi AI Camera IMX500
          │  (captura física ~camera_stream_fps = 20 FPS, FrameDurationLimits)
          ▼
     RaspberryCameraFrameSource.read()   ← único acceso físico a Picamera2
          │
   ┌──────┴───────────────────────────────────┐
   │ Fase PREVIEW PREVIO         │ Fase MONITOREO (video-first)
   │                             │
   ▼                             ▼
LivePreviewManager          VideoRecordingWorker (orquestador)
  loop background               loop read()
   │                             │
   ├─ latest_preview_frame       ├─ latest_preview_frame  ← ~20 FPS (preview)
   │                             │
   └─ (no graba)                 └─ RecordingSampler.should_write(now)?
                                       │  no → continuar
                                       │  sí → VideoRecorder.write(frame)  ← ~recording_target_fps
                                       ▼
                                  monitoring.mp4  → Pipeline existente (SIN CAMBIOS)
          │                             │
          ▼                             ▼
   GET /api/camera/preview-stream   GET /api/monitoring/{id}/preview-stream
   (MJPEG multipart)                (MJPEG multipart, ownership-checked)
          │                             │
          ▼                             ▼
      <img src=...>  en pantalla táctil DSI 7" (layout existente 800×480)
```

### Capas afectadas (Clean Architecture)

- **Aplicación** (`src/application/`): `RecordingSampler` (política de ejecución pura, `src/application/services/recording_sampler.py`), `LivePreviewManager`, modificación acotada de `VideoRecordingWorker`, reutilización de `CameraService.encode_frame_jpeg`.
- **Infraestructura** (`src/infrastructure/`): `RaspberryCameraFrameSource` (parámetro `camera_stream_fps`), `settings.py` (`camera_stream_fps` en `ExecutionProfile`).
- **Presentación** (`app/`): endpoints de stream MJPEG, dependency de ownership, wiring en `dependencies.py`/`main.py`, `monitoring_setup.html`, `monitoring.js`.

---

## Componentes y decisiones de diseño

### 1. Configuración: `camera_stream_fps` en `ExecutionProfile`

**Cambio:** agregar el campo `camera_stream_fps: float` al dataclass `ExecutionProfile` (frozen). Valores iniciales:

```python
EDGE_PROFILE:  camera_stream_fps=20.0,  recording_target_fps=5.0
FULL_PROFILE:  camera_stream_fps=20.0,  recording_target_fps=10.0
```

**Validación fail-fast** (nueva función `validate_profile_cadences(profile)` en `settings.py`, invocada tras seleccionar `ACTIVE_PROFILE`):

- `camera_stream_fps > 0`
- `recording_target_fps > 0`
- `recording_target_fps <= camera_stream_fps`

**Spec 023 elige explícitamente fail-fast.** Si un perfil constante definido por código viola cualquiera de estas invariantes, `validate_profile_cadences` hace `raise ValueError(...)` claro durante la carga de config/startup. NO se muta el `ExecutionProfile` frozen, NO se corrige silenciosamente, NO se inventan valores y NO se emite warning para continuar. Como los perfiles son constantes del código (no entrada de usuario), un fallo aquí es un bug de configuración que debe detenerse de inmediato. (R13; `requirements.md` permite conceptualmente "rechazar o degradar" — el diseño escoge rechazar.)

**Por qué no reusar `recording_target_fps`:** hoy `agricultural_ui.py` construye el frame source con `fps=int(recording_target_fps)`; esto se cambia a `fps=ACTIVE_PROFILE.camera_stream_fps`. `recording_target_fps` permanece intacto como fps del contenedor MP4 (R4.4, R13.3).

### 2. `RaspberryCameraFrameSource`: cadencia física desde `camera_stream_fps`

El frame source **ya** acepta `fps` y lo traduce a `FrameDurationLimits` vía `_frame_duration_us(fps)` en modo `video`. El único cambio necesario es **quién le pasa qué valor**: se le pasará `camera_stream_fps` (20) en lugar de `recording_target_fps` (5).

- No se añade una API `start()/stop()` a la interfaz `FrameSource` (R17.1). El lifecycle vive en `LivePreviewManager`. El frame source conserva `read()`/`release()`/`capture_single_frame()`/`is_available()`.
- El `_camera_lock` de módulo y `_CAMERA_SETTLE_SECONDS` se conservan sin cambios (R6, R7.3).

**Timeout de adquisición del lock configurable (crítico para el handoff acotado):** hoy `read()` hace `_camera_lock.acquire(timeout=15.0)` hardcodeado. Si el thread del `LivePreviewManager` queda esperando el lock hasta 15 s, `manager.stop(timeout=5)` no podría interrumpirlo → `join` expira → `stop()` False → thread vivo que podría adquirir la cámara inesperadamente. Corrección de bajo blast radius:
- Añadir un parámetro `camera_lock_timeout_seconds: float = 15.0` al `RaspberryCameraFrameSource` (default = comportamiento actual de monitoreo, sin cambios de semántica).
- `read()` usa ese valor en `acquire(timeout=...)` en lugar del literal `15.0`.
- El `LivePreviewManager` construye su frame source con un timeout **corto** (p. ej. `camera_lock_timeout_seconds=1.0`) para que un `read()` que no consigue el lock rápidamente falle y permita que `stop()` termine dentro de su propio timeout. El valor exacto (≤1 s) se ajustará con evidencia; no se congela.
- Esta es la única modificación al frame source además del origen del `fps`; no cambia el flujo de monitoreo (usa el default 15.0).
- **Riesgo abierto (validación física):** que la IMX500 sostenga 20 FPS a 960×720 en modo `video`. Si no lo sostiene, `effective_camera_stream_fps` será menor pero el diseño no se rompe (el sampler y el preview siguen funcionando a la cadencia real). Se documenta como tolerancia en R18.

### 3. `RecordingSampler` (política de ejecución de la aplicación, pura)

Componente pequeño, sin estado de cámara ni cv2, testeable de forma determinista inyectando un reloj. **Ubicación:** `src/application/services/recording_sampler.py`. No es lógica del dominio agrícola sino una **política de ejecución de la aplicación** (muestreo temporal de grabación); se mantiene completamente pura (sin cv2/picamera2/torch/FastAPI, reloj monotónico inyectable).

**Algoritmo phase-preserving, no-burst (evita drift acumulativo):**

```python
# src/application/services/recording_sampler.py
class RecordingSampler:
    """Decide, por tiempo monotónico, si un frame real debe persistirse a recording_fps.

    Preserva la FASE temporal (los slots son k*interval a partir del primer frame)
    y NUNCA emite ráfagas para recuperar intervalos perdidos: tras un hueco, salta
    directamente al siguiente slot futuro. Escribe como máximo un frame por llamada.
    """
    def __init__(self, recording_fps: float, *, clock: Callable[[], float] = time.monotonic):
        if recording_fps <= 0:
            raise ValueError("recording_fps must be > 0")
        self._interval = 1.0 / recording_fps
        self._clock = clock
        self._next_due: float | None = None

    def should_write(self) -> bool:
        now = self._clock()
        if self._next_due is None:                      # primer frame: se escribe
            self._next_due = now + self._interval
            return True
        if now < self._next_due:                        # aún no toca
            return False
        # Toca escribir el frame real actual. Avanzar la fase saltando los
        # intervalos perdidos hacia el PRÓXIMO slot futuro, sin ráfagas.
        skipped = int((now - self._next_due) // self._interval)
        self._next_due = self._next_due + (skipped + 1) * self._interval
        return True
```

**Por qué NO `next_due = now + interval` (corrección de drift):** anclar el próximo slot a `now` acumula el jitter de cada frame aceptado (0.214 → 0.414 → 0.628 …) y produce drift progresivo respecto a la línea temporal objetivo. Anclar a la rejilla `_next_due + (skipped+1)*interval` preserva la fase: los slots siguen siendo múltiplos de `interval` desde el primer frame, y el jitter pequeño no se acumula.

**Propiedades garantizadas:**
- Mantiene la fase temporal (no drift acumulativo significativo).
- Nunca escribe más de un frame por `should_write()`.
- Nunca "recupera" intervalos perdidos mediante ráfagas ni duplica frames (R3.5, R3.6, R11.4).
- Tras un hueco, salta directamente al siguiente slot futuro.

**Selección del "frame más reciente elegible":** el worker llama `should_write()` justo tras `read()`, con el frame recién leído a mano; el frame escrito es el más reciente disponible en ese tick. No hay buffer de candidatos.

**Alternativa descartada:** "guardar cada N-ésimo frame" (`frame_index % 4`). Rechazada por drift con FPS variable (19.2/20.4/18.9).

### 4. `VideoRecordingWorker`: modificación acotada

Cambios permitidos (R16.2), sin tocar estados, signals, START/FINALIZE/ABORT, pausas, thermal pause, cleanup ni registry:

1. **Recibe un `RecordingSampler`** por inyección (construido en `MonitoringService._start_video_first` con `recording_target_fps` del perfil).
2. **Desacopla el buffer de preview de la escritura.** Hoy:
   ```text
   read → open(lazy) → write(frame) → _set_last_frame(frame)   # preview atado a grabación
   ```
   Nuevo:
   ```text
   read frame
     → _set_last_frame(frame)              # SIEMPRE (preview ~camera_stream_fps)
     → if sampler.should_write():
           open(lazy si primer write)      # tamaño desde el primer frame que se escribe
           write(frame)                    # grabación ~recording_target_fps
   ```
   El `open()` perezoso del recorder se mueve para dispararse en el **primer frame que el sampler acepta escribir** (no en el primer frame leído), preservando "abrir con el tamaño del primer frame real escrito".
3. **Métricas:** `RecordingMetrics` gana contadores nuevos de diagnóstico:
   - `camera_frames_produced` (frames leídos de la cámara),
   - `preview_frames_updated` (actualizaciones de `latest_preview_frame`),
   - `camera_capture_elapsed_seconds` (entre primer y último frame recibido, restando pausas vía signals existentes; ver §Observabilidad),
   - `configured_camera_stream_fps`,
   - `effective_camera_stream_fps` = `(camera_frames_produced - 1) / camera_capture_elapsed_seconds` para `N >= 2`, si no `0.0` (ventana de captura activa, **no** la vida total del worker).
   **`frames_written` NO se renombra** (nombre real actual del dataclass, serializado en `pipeline_metrics.json`); `effective_recording_fps` conserva su semántica (no se recalculan).
4. **Secuencia de preview:** cada `read()` incrementa un `preview_sequence`; `get_preview_frame_snapshot()` devuelve `(sequence, frame_copy)` para que el stream de monitoreo detecte novedad sin comparar bytes. `get_last_frame()` se conserva idéntico.

**Contrato preservado:** `get_last_frame()` sigue devolviendo una **copia** thread-safe (tests `test_video_recording_worker.py`, `test_019_task14_ui_preview.py`). El buffer sigue protegido por `_last_frame_lock`, independiente del `_camera_lock`.

**Nota sobre boundary:** el worker sigue en la capa de aplicación y no importa cv2/picamera2. `RecordingSampler` vive en la misma capa de aplicación (política de ejecución pura); su import es intra-capa y no rompe boundaries.

### 5. `LivePreviewManager` (preview previo persistente)

Runtime singleton en `app.state` (como `monitoring_runtime_registry`). **Compone** `FrameSource` en modo persistente (`read()`), sin duplicar acceso a Picamera2 (R1.6, R10.2).

**Construcción del frame source en modo VIDEO (crítico):** el FPS físico solo se aplica cuando `camera_mode="video"` (en `still`, `fps` no impone `FrameDurationLimits`). Por tanto el manager DEBE construir el source explícitamente en modo video:

**Dependencias inyectadas explícitamente (sin imports ocultos application→infraestructura):** el manager recibe `runtime_registry` y `camera_is_locked: Callable[[], bool]` por constructor; el composition root (`app/main.py`) resuelve el callable concreto de infraestructura (`is_camera_locked`). Esto mantiene el manager testeable con `FakeRuntimeRegistry` y un `fake_camera_is_locked`.

```python
LivePreviewManager(
    frame_source_factory=create_frame_source,
    runtime_registry=app.state.monitoring_runtime_registry,
    camera_is_locked=is_camera_locked,           # callable de infraestructura, resuelto en main.py
    camera_stream_fps=ACTIVE_PROFILE.camera_stream_fps,
    camera_wh=(ACTIVE_PROFILE.camera_width, ACTIVE_PROFILE.camera_height),
)
```

Construcción del frame source al arrancar la captura:

```python
self._frame_source = frame_source_factory(
    width=camera_wh[0], height=camera_wh[1],
    fps=camera_stream_fps, camera_mode="video",
    camera_lock_timeout_seconds=1.0,   # corto: permite que stop() sea acotado (§frame source)
)
```

No basta con `fps=20`: sin `camera_mode="video"` la cadencia física no se aplicaría. El timeout corto del lock evita que `read()` bloquee 15 s y rompa el `stop()` acotado.

**Buffer latest-frame con secuencia y notificación por `Condition`:**

```python
STREAM_STOPPED = object()  # sentinela terminal para despertar consumidores

class PreviewBuffer:
    """Buffer latest-frame thread-safe con generación de sesión + secuencia.

    Un solo elemento (el más reciente). El productor publica un JPEG nuevo e
    incrementa sequence; los consumidores esperan un sequence mayor al último
    servido DENTRO de su generación esperada. NO cuenta frames de cámara ni de
    encode (eso lo hace el LivePreviewManager).
    """
    def __init__(self):
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._sequence = 0
        self._generation = 0     # epoch de sesión; cambia en cada reset()
        self._stopped = False

    def current_generation(self) -> int:
        with self._cond:
            return self._generation

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._sequence += 1          # solo cuenta JPEG nuevos publicables
            self._cond.notify_all()

    def stop(self) -> None:
        """Marca terminal y despierta a TODOS los consumidores en wait()."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def reset(self) -> int:
        """Nueva sesión: incrementa generación, limpia frame/sequence, sale de stopped.

        Devuelve la nueva generación. Los consumidores viejos, atados a la
        generación anterior, verán generation mismatch y terminarán.
        """
        with self._cond:
            self._generation += 1
            self._stopped = False
            self._jpeg = None
            self._sequence = 0
            self._cond.notify_all()
            return self._generation

    def wait_for_new(self, expected_generation: int, after_sequence: int, timeout: float):
        """Devuelve (sequence, jpeg) si hay uno más nuevo en la generación esperada;
        STREAM_STOPPED si el buffer fue detenido O si la generación cambió; None si
        expira el timeout sin novedad."""
        def _terminal_or_item():
            if self._stopped or self._generation != expected_generation:
                return STREAM_STOPPED
            if self._sequence > after_sequence and self._jpeg is not None:
                return (self._sequence, self._jpeg)
            return None
        with self._cond:
            item = _terminal_or_item()
            if item is not None:
                return item
            self._cond.wait(timeout)
            return _terminal_or_item()   # reevalúa stop Y generación tras despertar

    def latest(self):
        with self._cond:
            return (self._sequence, self._jpeg) if self._jpeg is not None else None
```

**Despertar de consumidores en stop/suspend (crítico):** al ejecutar `_stop_capture()`/`suspend_for_handoff()`, el manager llama `PreviewBuffer.stop()` → `notify_all()`. Todo generador bloqueado en `wait_for_new` recibe `STREAM_STOPPED`, finaliza y ejecuta `unsubscribe(token)` en su `finally`.

**La generación pertenece a la SESIÓN FÍSICA de captura, no a `enable_preview()` (crítico):** cada transición real `IDLE → STARTING → RUNNING` crea **exactamente una** nueva generación, llamando `PreviewBuffer.reset()` **al arrancar el capture runtime** (no en `enable_preview`/`resume`). La primera sesión = `generation 1`; tras un idle-stop, el siguiente arranque = `generation 2`; etc. Esto cubre el caso: último subscriber se va → idle-stop deja el buffer `stopped` → un nuevo `subscribe()` desde IDLE debe arrancar una nueva sesión que hace `reset()` (sale de `stopped` con nueva generación); si el arranque no reseteara, el buffer quedaría `stopped=True` y el nuevo stream no funcionaría. `enable_preview()`/`resume_after_failed_handoff()` **solo rehabilitan subscriptions** (`_subscriptions_suspended=False`); NO crean generación si no hay una nueva sesión física (evita reset doble y generaciones vacías).

**Anti-carrera con `reset()` — generación de sesión (crítico):** un consumidor viejo despertado por `notify_all()` podría reobtener el lock DESPUÉS de que un nuevo arranque haya hecho `reset()` (`_stopped=False`, `sequence=0`). Con la **generación**, el consumidor guarda su `expected_generation` (recibida al suscribirse) y `wait_for_new` devuelve `STREAM_STOPPED` si `self._generation != expected_generation` **aunque `_stopped` ya sea False**. Propiedad garantizada: **un stream HTTP creado en una sesión de preview NUNCA recibe frames de una sesión posterior.**

**Modelo de estado — dos ejes ortogonales (crítico):** para que la suspensión del handoff sobreviva a `stop()`, se separa el estado de captura del permiso de suscripción:

```python
_capture_state: CaptureState  # IDLE | STARTING | RUNNING | STOPPING | ERROR
_subscriptions_suspended: bool  # True bloquea nuevos subscribe() aunque _capture_state sea IDLE
```

`stop()` cambia solo `_capture_state` (→ IDLE o ERROR); NUNCA toca `_subscriptions_suspended`. Así `suspend_for_handoff()` = poner `_subscriptions_suspended=True` + `stop()`, y tras liberar la cámara el manager queda en `_capture_state=IDLE` pero con `_subscriptions_suspended=True`, por lo que `subscribe()` sigue rechazado hasta una reactivación explícita (§ handoff).

**Serialización del arranque — estado `STARTING` (crítico, cierra Race 1):** dos `subscribe()` simultáneos podrían ver ambos `IDLE` y ambos arrancar el capture runtime. Para evitarlo sin mantener el lock durante `read()`/`frame_source_factory` (violaría la disciplina de locking), se usa el estado `STARTING`:

```text
subscribe A:
  LOCK: si IDLE y can_enable_preview → _capture_state=STARTING; A = startup owner; gen = None-aún   UNLOCK
  A arranca el capture runtime FUERA del lock: PreviewBuffer.reset() → nueva generación; frame source (video); thread
  LOCK: _capture_state=RUNNING; guarda generation; notify (Condition de estado)   UNLOCK
subscribe B (concurrente):
  LOCK: observa STARTING → B NO arranca; espera (Condition de estado) el resultado   UNLOCK
  al pasar a RUNNING → B se asocia y recibe la MISMA generation; si el startup falla (STARTING→ERROR/IDLE), B recibe None
```

Solo un thread ejecuta la inicialización física `IDLE→RUNNING`. Si el startup falla: `STARTING → ERROR` (o `IDLE` seguro según causa), despertando a los subscribers concurrentes (reciben `None`). El `reset()` (nueva generación) ocurre exactamente una vez, dentro del arranque físico del startup owner.

```python
class LivePreviewManager:
    """Único runtime propietario del preview físico PREVIO al monitoreo."""
    # Dos ejes ortogonales (NO son estados de Monitoring):
    #   _capture_state: IDLE | RUNNING | STOPPING | ERROR
    #   _subscriptions_suspended: bool
    def subscribe(self)                    # -> (token, generation) | None si no can_enable_preview/ERROR
    def unsubscribe(self, token) -> None   # decrementa; marca last_active
    def get_latest_preview(self)           # -> (sequence, jpeg) | None
    def wait_for_preview(self, expected_generation, after_sequence, timeout)  # -> (seq, jpeg) | STREAM_STOPPED | None
    def start(self) -> None                # idempotente; adquiere cámara (modo video)
    def _stop_capture(self, *, timeout=5.0) -> bool  # SÍNCRONO acotado; solo _capture_state
    def stop(self, *, timeout=5.0) -> bool           # _stop_capture; NO toca _subscriptions_suspended
    def suspend_for_handoff(self, *, timeout=5.0) -> bool  # suspend + _stop_capture (§7)
    def resume_after_failed_handoff(self) -> None    # solo si cámara libre y sin worker/thread (§7)
    def enable_preview(self) -> None       # SUSPENDED→IDLE seguro al volver a setup (§7)
    def is_running(self) -> bool
    def diagnostics(self) -> dict          # snapshot de métricas (§obs)
```

**Contabilidad de métricas (responsabilidad del manager, no del buffer):** en el loop del manager:
- por cada `frame_source.read()` exitoso → `camera_frames_produced += 1`;
- por cada `encode_frame_jpeg` exitoso → `preview_frames_encoded += 1` y `PreviewBuffer.publish(jpeg)` (que incrementa `sequence`);
- si `read()` tiene éxito pero el encode falla → `camera_frames_produced` sube, `preview_frames_encoded` NO. Son métricas separadas (§métricas).

**Lifecycle y concurrencia (R9, R10):**
- Un `threading.Lock` protege el estado (contador de suscriptores, thread, `_capture_state`, `_subscriptions_suspended`). El `PreviewBuffer` tiene su propio `Condition`.
- **Único owner físico:** a lo sumo un `FrameSource` persistente y un thread de captura. Todos los clientes comparten el mismo JPEG del buffer.
- **Cero suscriptores → idle stop (SIN self-join):** cuando el contador llega a 0 se marca `last_active`. El propio **capture loop** detecta `active_subscribers == 0 and elapsed >= idle_timeout_s` y hace `break` de forma natural; su `finally` llama `_claim_source_for_release()` + `release()` (exactly-once) y deja `_capture_state=IDLE`. El capture thread **nunca** llama `stop()`/`_stop_capture()` sobre sí mismo (eso haría `thread.join()` de sí mismo → `RuntimeError`). `stop()`/`_stop_capture()` son solo para un caller externo. Cerrar la pestaña no deja la cámara tomada (R9.2).
- **Un consumidor que se va no afecta a otros:** `unsubscribe` solo decrementa; mientras haya ≥1 suscriptor la captura sigue (R9.3).
- **JPEG compartido (preview previo):** el loop del manager codifica cada frame a JPEG **una sola vez** y lo publica; todos los clientes leen ese mismo JPEG. Sin cola ni encode por-cliente (R10.3, R11.2). El encode ocurre en el thread del manager, nunca en el event loop (§transporte/encode).
- **Cleanup idempotente:** `stop()` seguro múltiples veces y antes de `start()`. En error de cámara, el loop sale, libera y limpia sin dejar lock/thread huérfano (R9.6).

**Disciplina de locking (anti-deadlock, crítica):** el `threading.Lock` interno del manager se usa SOLO para leer/escribir estado (suscriptores, tokens, referencias de thread/frame source, flags). NUNCA se mantiene adquirido mientras se ejecuta una operación potencialmente larga o que pueda re-tomar el lock: `thread.join()`, `frame_source.read()`, **`frame_source.release()`**, `CameraService.encode_frame_jpeg()`, `Condition.wait()`, `time.sleep()`.

**`release()` exactly-once y FUERA del lock — patrón claim:** tanto `_stop_capture()` (caller externo) como el capture thread (en su `finally`, p. ej. idle-stop o error) compiten por liberar el frame source. Un helper `_claim_source_for_release()` decide, bajo el lock, quién libera; la liberación física ocurre fuera:
```text
_claim_source_for_release():           # helper
    LOCK:
        if not _released and self._frame_source is not None:
            _released = True
            src = self._frame_source
        else:
            src = None
    UNLOCK
    return src

_stop_capture(timeout):
    LOCK: _capture_state=STOPPING; thread_ref=self._thread   UNLOCK
    PreviewBuffer.stop()               # despierta consumidores
    thread_ref.join(timeout)           # FUERA del lock
    src = _claim_source_for_release()
    if src is not None: src.release()  # FUERA del lock, exactamente una vez
    LOCK: _capture_state = IDLE if joined else ERROR   UNLOCK

capture thread finally:
    src = _claim_source_for_release()
    if src is not None: src.release()  # FUERA del lock
```
Solo un camino obtiene el `src` (flag `_released`); `release()` corre siempre fuera del manager lock. Motivo: el capture thread (`finally`) y los generadores HTTP (`unsubscribe`) pueden necesitar el mismo lock; mantenerlo durante `join()`/`release()` provocaría deadlock (Race 5).

**Reutilización vs componente nuevo (R1.6):** se evaluó reutilizar `RaspberryCameraFrameSource` con un loop en la ruta. Rechazado: mezclaría lifecycle de UI en el frame source y no permitiría multi-suscriptor, idle-stop, suspensión de handoff ni notificación por secuencia. `LivePreviewManager` aporta ese lifecycle **componiendo** el frame source existente, sin duplicar acceso físico.

**`get_last_frame()` del worker (compatibilidad):** se conserva sin cambios (contratos/tests existentes). Para el stream de monitoreo con detección de novedad se añade un accessor nuevo `get_preview_frame_snapshot() -> (sequence, frame_copy) | None` en el worker, que expone la misma copia thread-safe más un `sequence` incrementado en cada `read()` (§8). `get_last_frame()` sigue devolviendo solo la copia.

### 6. Contrato síncrono y acotado de `LivePreviewManager.stop()`

El handoff solo es determinista si `stop()` NO retorna hasta que la cámara está realmente libre. `_stop_capture()`/`stop()` operan **solo** sobre `_capture_state` (nunca sobre `_subscriptions_suspended`). Contrato:

```text
_stop_capture(timeout=5.0) -> bool:
    1. _capture_state = STOPPING; señalar stop_event; PreviewBuffer.stop() (despierta consumidores)
    2. thread.join(timeout)                     # espera acotada
    3. si el thread terminó:
         - garantizar frame_source.release() exactamente una vez (flag _released;
           settle _CAMERA_SETTLE_SECONDS incluido dentro de release())
         - _capture_state = IDLE; return True
    4. si el thread NO terminó dentro del timeout:
         - NO forzar; _capture_state = ERROR; return False  (handoff inseguro)
```

- `release()` se ejecuta dentro del thread de captura en su `finally`, o por el propio `stop()` si el thread ya salió; en ambos casos **una sola vez** (flag `_released`). No se añade un segundo `_CAMERA_SETTLE_SECONDS` (R7.3).
- El thread de captura usa el frame source con `camera_lock_timeout_seconds=1.0`, de modo que un `read()` que no consigue el lock falla rápido y el thread termina dentro del `join`.
- Si `_stop_capture()` devuelve `False` (thread no terminó → `_capture_state=ERROR`): el manager permanece en ERROR y `subscribe()` sigue rechazado mientras exista la posibilidad de que el thread use la cámara. **No** se llama `resume_after_failed_handoff()` de inmediato; solo se rehabilita tras verificar que el thread terminó, el frame source fue liberado y el lock está libre (§ rehabilitación).

### Handoff preview → monitoreo (server-side, sin carrera de reacquisición)

**Problema doble:** (a) el navegador puede no cerrar el stream MJPEG a tiempo; (b) aun con `stop()` síncrono, un `GET /preview-stream` que llegue/reintente entre la liberación y la adquisición del worker podría **rearrancar** el preview y volver a tomar la cámara (R7.1, R7.2, R7.4).

**Solución — `suspend_for_handoff()`:** el manager tiene un estado interno `SUSPENDED_FOR_HANDOFF` (lifecycle del preview, **no** de Monitoring). En `agricultural_ui.py::monitoring_start`:

```text
POST /modulos/{id}/monitoreo/iniciar
  → ok = live_preview_manager.suspend_for_handoff(timeout=5.0)
        # 1) marca SUSPENDED (subscribe() devuelve None → NO puede rearrancar la cámara)
        # 2) stop() síncrono y acotado (join + release una sola vez, settle incluido)
        # 3) devuelve True solo si la cámara quedó libre
  → if not ok: abortar inicio con mensaje "cámara ocupada, reintenta"
  → frame_source = create_frame_source(fps=camera_stream_fps, camera_mode="video")
  → try:
        monitoring_service.start_session(...)   # worker adquiere la cámara
    except (fallo antes de adquirir):
        live_preview_manager.resume_after_failed_handoff()  # reactiva preview de forma controlada
        raise / re-render error
```

- **`suspend_for_handoff()` = `_subscriptions_suspended=True` + `_stop_capture()`.** Como `_stop_capture()` solo toca `_capture_state`, la suspensión **sobrevive** al stop: al terminar, `_capture_state=IDLE` pero `_subscriptions_suspended=True`. `subscribe()` sigue devolviendo `None` (endpoint 503), sin reacquisición (cierra la Race 4). Devuelve `True` solo si la cámara quedó libre.

**Coordinación de recursos device-global — reutilizar Spec 020 (crítico).** La condición segura para arrancar/reanudar el preview previo NO se reconstruye a mano; se apoya en la coordinación ya existente del `MonitoringRuntimeRegistry`:

```python
can_enable_preview = (
    not runtime_registry.has_active_capture()      # cubre capture reservation Y active capture
    and not runtime_registry.is_global_analysis_active()  # no arrancar 20 FPS + JPEG junto a heavy analysis
    and not camera_is_locked()                     # red de seguridad de hardware
)
```

`has_active_capture()` es la fuente autoritativa de Spec 020 e incluye la ventana de START donde existe una **capture reservation pero aún no hay worker/thread** — por eso "no worker + no thread" NO basta.

`is_global_analysis_active()` se usa como **preflight guard de rendimiento/coordinación** (no arrancar un preview de 20 FPS + JPEG junto a un análisis pesado que el dispositivo ya considera ocupado). **NO es un claim atómico bidireccional:** entre el chequeo `is_global_analysis_active() == False` y el arranque del preview, un análisis podría tomar el slot global; `can_enable_preview` **no** cierra esa TOCTOU con el heavy analysis. Spec 023 **no** añade una "preview reservation" al `MonitoringRuntimeRegistry` (evita ampliar Spec 020). La exclusión física de la cámara sigue garantizada por `_camera_lock` (el análisis pesado no la usa, pero tampoco abre la cámara). La validación física confirmará que el flujo operacional normal no genera conflicto.

**Esta condición `can_enable_preview` se aplica en:**
- `enable_preview()` (regreso a setup tras monitoreo),
- `resume_after_failed_handoff()` (tras START fallido),
- el arranque desde IDLE dentro de `subscribe()`/`start()` — revalidación: no basta validar al entrar a la pantalla; el GET del stream (que llega después) revalida `can_enable_preview` antes de adquirir la cámara. (Esta revalidación cierra la ventana respecto a capture/camera-lock; respecto al heavy analysis es solo un preflight, no un claim atómico — ver nota abajo.)

No se crea coordinación paralela; se reutiliza la de Spec 020.

**Rehabilitación (SUSPENDED → IDLE), tres caminos server-side, idempotentes:**

1. **START fallido, cleanup seguro (`resume_after_failed_handoff()`):** solo rehabilita si `can_enable_preview` es True. Si el fallo de `start_session()` ocurrió DESPUÉS de que el worker adquiriera parcialmente la cámara (o hay reservation pendiente), `has_active_capture()` seguirá True y NO rehabilita hasta el cleanup. Si `_capture_state=ERROR` (stop no liberó), tampoco. **Solo pone `_subscriptions_suspended=False`; NO llama `PreviewBuffer.reset()` ni crea generación** (el `reset()`/nueva generación ocurre exclusivamente al arrancar la siguiente sesión física IDLE→STARTING→RUNNING).
2. **Monitoreo terminado + regreso a setup (`enable_preview()`):** al volver a `GET /modulos/{id}/monitoreo`, la ruta llama `enable_preview()`, que rehabilita **solo si** `can_enable_preview`. No se añaden estados a `MonitoringState`. La reactivación es explícita y server-side.
3. **Arranque desde IDLE en `subscribe()`/`start()`:** antes de adquirir físicamente la cámara revalida `can_enable_preview`; si es False, `subscribe()` devuelve `None` (endpoint 503) sin arrancar.
4. **Idempotencia:** todas las operaciones son seguras ante invocaciones repetidas.

- **Si el monitoreo arranca:** el preview previo permanece suspendido hasta el camino (2). El preview durante monitoreo lo sirve el worker, no el manager.
- El flujo existente de `start_session()` (verifica `is_camera_locked()` y `CameraStillBusyError`) se conserva como red de seguridad adicional.

### 7. Transporte MJPEG y endpoints

**Endpoints nuevos:**

| Endpoint | Fuente | Ownership |
|---|---|---|
| `GET /api/camera/preview-stream` | `LivePreviewManager.wait_for_preview()` (JPEG ya codificado) | solo auth (no ligado a monitoring) |
| `GET /api/monitoring/{id}/preview-stream` | `registry.get_worker(id).get_preview_frame_snapshot()` → encode fuera del loop | auth + ownership |

**Formato:** `StreamingResponse` con `media_type="multipart/x-mixed-replace; boundary=frame"`. Cada parte:

```text
--frame\r\n
Content-Type: image/jpeg\r\n
Content-Length: <n>\r\n
\r\n
<bytes JPEG>\r\n
```

**Reconciliación del status HTTP (503 ANTES de las cabeceras):** una vez emitidas las cabeceras 200 del `StreamingResponse` no se puede degradar a 503. Por eso el endpoint valida y obtiene el **primer frame** ANTES de construir el `StreamingResponse`:

```text
def GET /api/camera/preview-stream:                    # handler SÍNCRONO
  ver _preview_prev_gen arriba (subscribe → preflight → StreamingResponse), 503 antes de cabeceras.

def GET /api/monitoring/{id}/preview-stream:           # handler SÍNCRONO, Depends(require_monitoring_owner)
  worker = registry.get_worker(id)
  snap = worker.get_preview_frame_snapshot() if worker else None
  if snap is None:                      # sin worker/estado/frame
      return JSONResponse(503, ...)     # NUNCA abre cámara, antes de cabeceras
  return StreamingResponse(gen(worker, snap), media_type="multipart/x-mixed-replace; boundary=frame")
```

**Estrategia única: ROUTE HANDLERS SÍNCRONOS + generadores síncronos (ambos endpoints).** Los handlers son `def` (no `async def`), de modo que FastAPI ejecuta **tanto el preflight** (que incluye `wait_for_preview(..., timeout≈2s)`) **como el generador** en su threadpool. Así ninguna espera bloqueante (`Condition.wait`, `time.sleep`) ni el encode JPEG (CPU-bound) tocan el event loop. No se usa `async def` con llamadas bloqueantes directas ni `await request.is_disconnected()`.

```python
def camera_preview_stream(...):            # SÍNCRONO (threadpool)
    sub = manager.subscribe()
    if sub is None:
        return JSONResponse(503, ...)      # suspended / ERROR / not can_enable_preview
    token, generation = sub
    first = manager.wait_for_preview(generation, after_sequence=0, timeout=2.0)  # preflight en threadpool
    if first is STREAM_STOPPED or first is None:
        manager.unsubscribe(token)
        return JSONResponse(503, ...)
    return StreamingResponse(_preview_prev_gen(manager, token, generation, first),
                             media_type="multipart/x-mixed-replace; boundary=frame")

def _preview_prev_gen(manager, token, generation, first):
    last, first_jpeg = first
    try:
        yield _multipart_part(first_jpeg)  # REUTILIZA el frame del preflight (no se descarta)
        while True:
            item = manager.wait_for_preview(generation, after_sequence=last, timeout=1.0)
            if item is STREAM_STOPPED:
                break                       # stop/suspend o generación cambió → termina
            if item is None:
                continue                    # timeout sin novedad; reevaluar (permite cierre)
            last, jpeg = item
            yield _multipart_part(jpeg)     # JPEG ya codificado por el manager
    finally:
        manager.unsubscribe(token)          # SIEMPRE, ante GeneratorExit/desconexión/stop
```

**Reutilización del primer frame del preflight:** el frame obtenido en el preflight (`first`) se entrega como **primer frame** del generador; no se descarta para esperar otro.

**Detección de frame nuevo por `(generation, sequence)` (R8):** el generador recuerda su `generation` y `last` sequence y solo emite `sequence` mayores de su propia generación. No se comparan bytes/hashes/arrays.
- Preview previo: `manager.wait_for_preview(generation, after_sequence=last, timeout)` usa el `Condition` del `PreviewBuffer` (sin busy-loop); despierta con `STREAM_STOPPED` en stop/suspend o si la generación cambió.
- Preview monitoreo: el worker no tiene `Condition` (una sola sesión de worker por monitoreo, sin `reset` cruzado); el generador síncrono hace `worker.get_preview_frame_snapshot()`, compara `sequence` y, si no hay novedad, duerme un intervalo acotado (`time.sleep(1/camera_stream_fps)`) — sin busy-loop, en el threadpool. No requiere generación porque el worker no reinicia su secuencia dentro de la vida del stream.

**Encoding fuera del event loop (R11):**
- Preview previo: el JPEG ya viene codificado por el thread del manager; el generador solo escribe bytes.
- Preview monitoreo: el worker entrega un **frame BGR crudo** con su `sequence`; el generador (síncrono, en threadpool) codifica con `CameraService.encode_frame_jpeg` — CPU-bound pero fuera del event loop. Se valida que status/log sigan respondiendo con el stream activo.

**Detección de desconexión y cleanup (R9):** con generadores síncronos, la desconexión del cliente hace que Starlette cierre el generador (lanza `GeneratorExit` en el `yield`); el `finally` ejecuta `unsubscribe(token)` siempre. No se usa `request.is_disconnected()`. El `timeout` corto del `wait_for_new` garantiza que el generador reevalúe periódicamente y pueda cerrarse con prontitud aunque no lleguen frames.
- **No abre cámara** durante monitoreo (R2.2, R8.5): solo lee del worker vía registry.

**Endpoint single-frame existente** `GET /api/camera/preview` **se conserva** como fallback/diagnóstico (R8.6).

**Política de encoding (R11):** el JPEG está **compartido** entre clientes en el preview previo (buffer único del manager). En el preview de monitoreo se acepta **encode por cliente** en esta primera versión (caso normal: una sola pantalla local); es correcto con múltiples clientes aunque menos eficiente, no bloquea el event loop, y se valida CPU en Raspberry. No se introduce un segundo background encoder durante monitoreo salvo evidencia de necesidad.

**Preferencia MJPEG confirmada:** no se usa WebSocket ni WebRTC (R8.4). MJPEG sobre HTTP local es simple, consumible por `<img>`, y suficiente para un cliente táctil local.

### 8. Ownership reutilizable (R15)

**Problema:** `/api/monitoring/{id}/preview` hoy solo exige auth; y `_monitoring_owned_by_user` es un helper **privado** de `agricultural_ui.py`. Importarlo desde `monitoring_api.py` acoplaría routers (prohibido, R15.3).

**Solución:** una **dependency FastAPI compartida** en `app/dependencies.py`, apoyada en un método de repositorio ya disponible:

**Reutilización de dependencies existentes (no inventar session helper):** `app/dependencies.py` ya expone `get_monitoring_repository`, `get_module_repository` y `get_greenhouse_repository` (todas request-scoped, comparten la sesión del request). `require_monitoring_owner` se apoya en ellas vía `Depends(...)` — no crea otra forma de administrar sesiones.

```python
# app/dependencies.py
def require_monitoring_owner(
    monitoring_id: int,
    user=Depends(require_current_user_api),
    monitoring_repo=Depends(get_monitoring_repository),
    module_repo=Depends(get_module_repository),
    greenhouse_repo=Depends(get_greenhouse_repository),
):
    """Devuelve el monitoring solo si pertenece al usuario; si no, 404.

    Resuelve Monitoring → Module → Greenhouse reutilizando el método existente
    get_by_id_for_owner (Spec 022). Semántica NOT FOUND para recursos ajenos:
    no distingue 'no existe' de 'de otro usuario' (no filtra existencia).
    """
    monitoring = monitoring_repo.get_by_id(monitoring_id)
    if monitoring is None:
        raise HTTPException(status_code=404, detail="No encontrado")
    module = module_repo.get_by_id(monitoring.module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="No encontrado")
    gh = greenhouse_repo.get_by_id_for_owner(module.greenhouse_id, user.id)
    if gh is None:
        raise HTTPException(status_code=404, detail="No encontrado")
    return monitoring
```

- Se apoya en el método **ya existente** `SqlGreenhouseRepository.get_by_id_for_owner(id, owner_user_id)` (Spec 022), que devuelve None si el greenhouse no pertenece al usuario — misma cadena de ownership que `_monitoring_owned_by_user`, pero en una ubicación compartida (dependency) y reutilizando las dependencies de repositorio existentes, sin acoplar routers ni crear un nuevo mecanismo de sesión.
- Se aplica a `GET /api/monitoring/{id}/preview` (corrige el gap existente) y a `GET /api/monitoring/{id}/preview-stream` (R15.2).
- **Not found (404)** para recursos ajenos evita filtrar la existencia de IDs de otros usuarios (R15.4).
- Los helpers HTML actuales pueden refactorizarse para delegar en esta dependency en una iteración posterior (fuera del alcance mínimo).

### 9. Wiring (`main.py` / `dependencies.py`)

- **`main.py` lifespan (composition root):** crear `app.state.live_preview_manager = LivePreviewManager(frame_source_factory=create_frame_source, runtime_registry=app.state.monitoring_runtime_registry, camera_is_locked=is_camera_locked, camera_stream_fps=ACTIVE_PROFILE.camera_stream_fps, camera_wh=(ACTIVE_PROFILE.camera_width, ACTIVE_PROFILE.camera_height))`, DESPUÉS de crear el `monitoring_runtime_registry`; el callable `is_camera_locked` (infraestructura) se importa aquí, no dentro del manager. En shutdown del lifespan, `live_preview_manager.stop()` (R9.7).
- **`dependencies.py`:** `get_live_preview_manager(request)` devuelve el singleton; `require_monitoring_owner` como se describió.

---

## Frontend

### `monitoring_setup.html` (preview previo)

- Cambiar el `<img id="camera-preview-img">` para apuntar a `src="/api/camera/preview-stream"` (MJPEG). El navegador renderiza el stream continuo en el mismo `<img>`.
- **Semántica de disponibilidad (crítico):** mientras el `LivePreviewManager` está activo, `_camera_lock` está tomado, por lo que `/api/camera/status` reportaría `busy` aunque la cámara la use el preview de **esta misma pantalla**, deshabilitando incorrectamente "Iniciar Monitoreo". Nuevo flujo:
  ```text
  onload del <img src="/preview-stream">  → primer frame recibido → cámara AVAILABLE → habilitar "Iniciar Monitoreo"
  onerror del <img>                        → consultar /api/camera/status como diagnóstico (busy real / not_detected)
  ```
  Es decir, el éxito del propio stream es la señal de disponibilidad; `/api/camera/status` se usa antes de arrancar el stream o como diagnóstico si el stream falla. **No** se cambia globalmente la semántica de `/api/camera/status`.
- El botón "Actualizar cámara" reasigna `src` con `?t=Date.now()` para reconectar.
- Layout táctil existente **800×480 (landscape base + portrait aditivo por media queries)** intacto: esta Spec NO introduce rediseño responsive; solo cambia la fuente de la imagen.

### `monitoring.js` (preview durante monitoreo)

- Reemplazar el fast-loop de polling (`PREVIEW_INTERVAL_MS=200`) por un `<img id="recording-preview-img" src="/api/monitoring/{id}/preview-stream">` asignado cuando el estado entra en `running`, y limpiado (`src=""`) al salir de `running` o en estados terminales (R9.2).
- Se conservan `startMonitoringPolling`/`stopMonitoringPolling`, el slow-loop de status/log 2 s, la auto-redirección a reporte, `visibilitychange` y `beforeunload`.
- El endpoint single-frame `/api/monitoring/{id}/preview` se mantiene como fallback/diagnóstico; el stream es el camino preferido.

> Nota de regresión (obligatoria en tasks): `test_monitoring_preview_loop.py` asume el fast-loop de polling que esta Spec elimina. Debe actualizarse **acorde al nuevo comportamiento** (`<img>`+stream), NO para ocultar regresión, conservando la aserción de que el JS no invoca `capture_single_frame`.

---

## Modelo de datos

Sin cambios de esquema. No se añaden entidades ni columnas SQLite.

## Observabilidad y métricas para el benchmark (R14)

Las métricas son **runtime/diagnóstico** (no se persisten en SQLite). **Camino único y reproducible para R18:**

**Preview previo — `LivePreviewManager.diagnostics()` (API interna obligatoria)** devuelve un dict:
- `camera_frames_produced` (reads exitosos), `preview_frames_encoded` (encodes exitosos), `active_subscribers`,
- `camera_capture_elapsed_seconds`, `effective_camera_stream_fps`,
- `state` (capture_state + subscriptions_suspended).
Se expone vía **`GET /api/camera/preview-diagnostics`** (autenticado, solo diagnóstico local). Este endpoint es el mecanismo único elegido para consultar el preview previo durante R18.

**Monitoreo — `RecordingMetrics`** se sigue capturando por el flujo de finalize con `dataclasses.asdict(worker.recording_metrics)` en `pipeline_metrics.json`. Se **extiende el dataclass** con las métricas nuevas **sin renombrar campos existentes**:
- se conserva `frames_written` con su nombre y semántica actuales (NO se renombra a `recording_frames_written`);
- se añaden `camera_frames_produced`, `preview_frames_updated`, `camera_capture_elapsed_seconds`, `configured_camera_stream_fps`, `effective_camera_stream_fps`.
Si el snapshot/helper de benchmark quiere una clave descriptiva, mapea `"recording_frames_written": metrics.frames_written` **solo en el JSON del helper**, sin tocar el dataclass.

**`preview_frames_served`** (frames realmente escritos al cliente) es opcional; si se instrumenta, vive como contador del generador del stream y se registra en log/diagnóstico. Puede ser menor que `camera_frames_produced` por backpressure/latest-frame (R14.5).

Se añade un **helper/script de benchmark** en `scripts/benchmarks/` que arranca un preview previo y un monitoreo corto, consulta `preview-diagnostics` y lee el `pipeline_metrics.json` resultante, para reproducir R18 sin instrumentación ad-hoc.

**Fórmula de `effective_camera_stream_fps` (basada en intervalos), común a ambos contextos:**
```text
si camera_frames_produced >= 2:
    effective_camera_stream_fps = (camera_frames_produced - 1) / camera_capture_elapsed_seconds
si camera_frames_produced < 2:
    0.0
```
N frames entre el primer y el último contienen N−1 intervalos; usar N−1 evita el sesgo. La semántica de `effective_recording_fps` (basada en `frames_written / recording_duration_seconds`) NO cambia.

**`camera_capture_elapsed_seconds` — difiere por contexto:**
- **Preview previo (`LivePreviewManager`):** NO tiene pausa manual/térmica. Se calcula simplemente `last_successful_frame_time - first_successful_frame_time`. Sus tests NO simulan ni descuentan `pause_event`/`thermal_pause_event` (esos signals no pertenecen a este componente).
- **Monitoreo (`VideoRecordingWorker`):** SÍ tiene `pause_event`/`thermal_pause_event`. Descuenta pausas usando esos signals existentes (no heurística de gaps) **pero solo las que solapan la ventana `[first_frame_time, last_frame_time]`**. Mecanismo con `pending`:
```text
al entrar en pausa (con >=1 frame ya producido): pause_started = monotonic()
al salir de pausa:                                pending_pause_duration += now - pause_started
al llegar EL SIGUIENTE frame exitoso:             accumulated_pause_seconds += pending_pause_duration; pending = 0
```
Así una pausa seguida de un nuevo frame se descuenta (pertenece a un intervalo entre frames); una pausa seguida de finalize/abort **sin** otro frame queda `pending` y NO se descuenta (ocurrió después del último frame). Una pausa antes del primer frame no se contabiliza. `camera_capture_elapsed_seconds = last_frame_time - first_frame_time - accumulated_pause_seconds`, garantizando `>= 0`. No se cambia el comportamiento de la pausa, solo se mide.

---

## Manejo de errores

| Situación | Comportamiento |
|---|---|
| Cámara no disponible al iniciar preview previo | `subscribe()`/primer frame fallan → endpoint responde **503 ANTES de las cabeceras** (nunca 200→503); UI hace `onerror` → `/api/camera/status` diagnóstico. Sin lock/thread huérfano (R1.5, R9.6). |
| Cámara se pierde durante preview previo | Loop captura excepción, `frame_source.release()` en `finally` (una sola vez), contador de suscriptores no bloquea; UI reintenta con el botón. |
| Preview de monitoreo sin worker | 503 en request inicial ANTES de construir el `StreamingResponse` (no abre cámara) (R2.2). |
| Error de encoder JPEG | Frame se descarta; se mantiene el último JPEG válido; no interrumpe la grabación (R11.3). |
| Config de cadencias inválida (perfil de código) | **Fail-fast**: `raise ValueError` claro en carga de config/startup (no mutar el dataclass frozen, no inventar valor, no warning-y-continuar) (R13.4, R13.5). |
| Handoff: `_stop_capture()` no libera dentro del timeout | `_capture_state=ERROR`; `suspend_for_handoff()` devuelve False → **no se inicia el monitoreo**; mensaje "cámara ocupada, reintenta". `subscribe()` sigue rechazado; NO se rehabilita hasta verificar thread terminado + frame source liberado + lock libre (R7.5, §13). |
| `start_session()` falla tras suspender | `resume_after_failed_handoff()` rehabilita SOLO si `can_enable_preview` es True (`not has_active_capture()` and `not is_global_analysis_active()` and `not camera_is_locked()`); si el fallo fue tras adquirir parcialmente, `has_active_capture()` seguirá True y no rehabilita hasta el cleanup. |
| Stream abierto durante stop/suspend | `PreviewBuffer.stop()` → `notify_all()` → consumidores reciben `STREAM_STOPPED`, terminan y `unsubscribe` en `finally` (no esperan al timeout). |
| `read()` no consigue el lock rápido (preview previo) | Timeout corto (`camera_lock_timeout_seconds=1.0`) → `read()` falla → el thread termina → `stop()` acotado funciona. |

Ningún error de preview debe alterar silenciosamente la grabación (R11.4): la grabación tiene prioridad funcional.

---

## Estrategia de pruebas

### Unit — `RecordingSampler` (aplicación, reloj inyectado)
- 20 FPS de entrada simulada → ~5 (EDGE) / ~10 (FULL) escrituras/seg.
- **Simulación larga (30–60 s) con jitter (19.2/20.4/18.9)** → sin drift acumulativo significativo (fase preservada; el nº de escrituras y los timestamps siguen la rejilla objetivo dentro de tolerancia).
- Entrada más lenta que el objetivo → no duplica (no dispara ráfaga tras hueco); tras un hueco salta al siguiente slot futuro.
- Primer frame siempre elegible; como máximo una escritura por `should_write()`; uso de tiempo monotónico (clock inyectado).

### Unit — `VideoRecordingWorker` (fakes, sin cv2)
- Actualiza `latest_preview_frame` para **todos** los frames leídos.
- Escribe solo los frames aceptados por el sampler; mantiene `recording_target_fps` del perfil.
- `open()` perezoso en el primer frame **escrito**.
- `get_last_frame()` devuelve copia; libera recursos en `finally` (contratos existentes preservados).
- Métricas nuevas: `camera_frames_produced`, `effective_camera_stream_fps`.

### Unit — `LivePreviewManager` (frame source fake)
- `start()/stop()` idempotentes; `stop()` antes de `start()`; start/stop repetidos.
- Construye el frame source en modo VIDEO con `camera_stream_fps` y `camera_lock_timeout_seconds` corto.
- Multi-suscriptor: un único frame source, un único thread; cero suscriptores → idle stop libera cámara.
- **`suspend_for_handoff()` sobrevive al stop:** tras liberar, `_capture_state=IDLE` pero `subscribe()` sigue devolviendo None; `enable_preview()`/`resume_after_failed_handoff()` (con cámara libre) rehabilitan; sin ellos, permanece rechazado.
- **`_stop_capture()` timeout → ERROR:** con un fake cuyo `read()` no termina, `stop()` devuelve False y el estado queda ERROR (subscribe rechazado).
- **Coordinación device-global (Spec 020):** con `FakeRuntimeRegistry`, `can_enable_preview` es False si `has_active_capture()` (incluye reservation sin worker) o `is_global_analysis_active()` o `camera_is_locked()`; `subscribe()`/`enable_preview()`/`resume_after_failed_handoff()` respetan esto (revalidan en el arranque desde IDLE — TOCTOU).
- **Inyección explícita:** el manager usa `runtime_registry` y `camera_is_locked` inyectados (fakes), sin imports ocultos.
- **Generación anti-reset:** consumidor A bloqueado en `generation=1` → `stop()` + inmediato `reset()`→`generation=2` → A recibe terminal y JAMÁS frames de `generation=2`.
- **Wake de consumidores:** un consumidor en `wait_for_preview` recibe `STREAM_STOPPED` al llamar `stop()`/`suspend_for_handoff()`.
- **Métricas separadas:** encode fallido tras read exitoso → `camera_frames_produced` sube, `preview_frames_encoded` no; `sequence` solo sube con JPEG publicado.
- **Locking (Race 5):** un fake cuyo `finally`/cleanup intenta re-tomar el lock del manager mientras `_stop_capture()` hace `join()` → sin deadlock, `release()` una sola vez.
- `wait_for_preview` no hace busy-loop (usa Condition).
- Cleanup tras excepción del frame source (sin lock/thread huérfano). Nunca crea una segunda Picamera2 concurrente (fake cuenta adquisiciones).
- **`effective_camera_stream_fps` (preview previo):** con reloj/timestamps controlados usa `(N-1)/elapsed`, SIN descontar pausas (el manager no tiene pause signals).

### Unit — Endpoints de stream (TestClient)
- `Content-Type: multipart/x-mixed-replace` cuando se usa MJPEG.
- **503 ANTES de las cabeceras** cuando no hay frame/worker (nunca 200→503).
- Preview de monitoreo NO abre cámara (mock del registry/worker; `capture_single_frame`/`capture_preview_frame` nunca llamados).
- Emite solo frames con `sequence` nuevo; no acumula backlog; termina ante desconexión simulada.
- **Event loop no bloqueado:** con el stream activo, requests de status/log siguen respondiendo (encode fuera del loop).
- Ownership: usuario A recibe 404 para monitoreo de B; dueño accede; no-auth 401.

### Unit — Carreras de concurrencia (R17.7)
- **Race 1:** dos `subscribe()` simultáneos → un único frame source/Picamera2 (fake cuenta adquisiciones).
- **Race 2:** un cliente `unsubscribe`, otro sigue → cámara NO liberada.
- **Race 3:** último `unsubscribe` → idle timeout → cámara liberada.
- **Race 4:** preview activo + `suspend_for_handoff()` + `subscribe()` durante suspensión → devuelve None (no reacquiere); el monitoreo obtiene la cámara; nunca dos owners.
- **Race 5:** `stop()` durante un `read()` en curso → join acotado, `release()` una sola vez, sin deadlock.
- **Race 6:** monitoreo activo + dos clientes de preview de monitoreo → ninguno abre Picamera2, ambos leen del mismo worker.

### Config
- `camera_stream_fps` presente en EDGE/FULL; validación **fail-fast** ante `recording>stream` o valores `<=0`.

### Frontend
- Semántica de disponibilidad: `onload` del stream → botón habilitado; `onerror` → `/api/camera/status` diagnóstico. `/api/camera/status` no se usa para bloquear un preview propio activo.
- `monitoring.js`: asigna/limpia `src` del `<img>` según estado; no invoca `capture_single_frame`.

### Lifecycle/integración
- pre-preview → start monitoring (handoff síncrono, sin doble settle, sin solape de cámaras).
- monitoring → finalize / abort: preview de monitoreo se detiene; comportamiento existente intacto.
- Error de cámara; refresh/close del navegador.

### Regresión y exclusión de hardware
- Comando oficial: `python -m pytest -q` (usa `addopts = -ra -m "not supabase"` de `pytest.ini`).
- **Los tests de hardware deben excluirse realmente en PC** (un marker NO hace skip por sí solo). Estrategia: (a) preferir que los tests nuevos usen fakes y NO requieran hardware (así corren en PC sin marker), y (b) para los que sí toquen hardware, marcarlos `@pytest.mark.raspberry`/`@pytest.mark.hardware` y **extender `addopts`** a `-m "not supabase and not raspberry and not hardware"` (o añadir un `pytest_collection_modifyitems` que los skipee sin el hardware presente). La decisión concreta se fija en la tarea de wiring de tests.
- Tests existentes se actualizan solo para reflejar el nuevo comportamiento correcto, nunca para ocultar regresiones (R17.8).

---

## Validación en Raspberry Pi (R18)

Procedimiento (documentar en `docs/benchmarks/` siguiendo la plantilla):

- Perfil EDGE, duración 20–30 s, `camera_stream_fps`=20, `recording_target_fps`=5.
- Medir: FPS de preview previo, FPS de preview durante monitoreo, `effective_camera_stream_fps`, `effective_recording_fps`, `recording_frames_written`, duración, CPU, RAM, temperatura (inicial/preview/monitoreo/pico), errores de cámara/libcamera.
- Esperado (20 s): `camera_frames_produced` ≈ 400, `recording_frames_written` ≈ 100. FULL: ≈ 200 escritos.
- Propiedad clave: `effective_camera_stream_fps` ≈ objetivo y preview perceptiblemente fluido; no se exige `preview_frames_served == camera_frames_produced` (descarte intencional).
- Registrar commit, fecha, dispositivo y configuración.

---

## Alternativas descartadas (para trazabilidad / posible ADR)

1. **Subir `recording_target_fps` a 20.** Rechazado: cambia tamaño del video, nº de frames, análisis y métricas (guardrail explícito del prompt).
2. **Dual-stream Picamera2 `main + lores`.** Descartado en la primera implementación por complejidad/blast radius; solo se considera si la validación física demuestra que el JPEG a 20 FPS sobre el stream `main` es excesivo. Documentado como optimización futura.
3. **WebSocket/WebRTC.** Rechazado: MJPEG local es más simple y suficiente (R8.4).
4. **Muestreo por índice de frame (`% N`) y anclaje `now+interval`.** Rechazados: el primero deriva con FPS variable; el segundo acumula jitter (drift). Se usa rejilla phase-preserving con tiempo monotónico.
5. **Reutilizar `RaspberryCameraFrameSource` como manager de UI.** Rechazado: mezclaría responsabilidades; se compone en `LivePreviewManager`.
6. **Detección de frame nuevo por hash/comparación de bytes.** Rechazada: cara y frágil; se usa un `sequence` monotónico + `Condition`.
7. **Encode JPEG en el event loop async.** Rechazado: CPU-bound bloquearía FastAPI; se usa generador síncrono (threadpool de Starlette) o `to_thread`.
8. **`RecordingSampler` en `src/domain/`.** Rechazado: es política de ejecución de la aplicación, no regla del dominio agrícola; vive en `src/application/services/`.
9. **Async generators con `await request.is_disconnected()`.** Rechazado por inconsistencia con espera bloqueante/encode CPU-bound; se usan **generadores síncronos** (threadpool de Starlette) con `try/finally`+`unsubscribe`.
10. **Estado único del manager (un solo enum).** Rechazado: no permitía que la suspensión de handoff sobreviviera a `stop()`. Se usan dos ejes ortogonales (`_capture_state` + `_subscriptions_suspended`).
11. **Fallback silencioso de config (`max(...)` + warning).** Rechazado: Spec 023 usa **fail-fast** (`raise`).
12. **Renombrar `RecordingMetrics.frames_written`.** Rechazado: rompería `pipeline_metrics.json`/tests/benchmarks históricos; se conserva el nombre y solo se extiende el dataclass.

> Recomendación: registrar un ADR (`docs/decisions/ADR-004-preview-decoupled-cadence.md`) con la decisión de desacople de cadencias y la elección MJPEG, conforme a `documentation-standards`.
