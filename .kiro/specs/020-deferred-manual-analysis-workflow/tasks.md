# Implementation Plan: 020 — Deferred Manual Analysis Workflow

## Overview

Este plan convierte el diseño de la feature 020 en una serie de tareas de codificación incrementales. Cada tarea construye sobre las anteriores y termina integrando el cambio en el flujo real. El objetivo es **separar la finalización de la grabación del inicio del análisis** en el flujo video-first (Spec 019), introduciendo el estado no terminal, persistido y recuperable `ready_for_analysis`, con inicio manual del análisis protegido por un preflight técnico y una confirmación de fuente de energía por intento.

**Restricciones transversales de la feature:**
- Lenguaje de implementación: **Python** (el diseño ya especifica Python; no aplica selección de lenguaje).
- Cambio quirúrgico y aditivo: NO se modifica el pipeline de visión, RetinaNet, umbrales, Scene Gate, Optical Flow, modelo de salud ni algoritmo de madurez (Requirements 20.1–20.6).
- Sin nuevas columnas ni migraciones de base de datos: `status String(20)` acomoda `"ready_for_analysis"` y `video_path` ya existe (Requirement 2.2).
- El flujo capture-first legacy (`video_first_enabled=False`) permanece sin cambios observables (Requirement 15.1).
- `DEVICE = "cpu"`, sin nuevas dependencias, sin lenguaje de robot/motores/autonomía (Requirements 20.6, 8.3, 17.6).

## Tasks

- [ ] 1. Añadir el estado `ready_for_analysis` a la FSM del dominio
  - [ ] 1.1 Extender `MonitoringState`/`MonitoringStatus` con `READY_FOR_ANALYSIS`
    - En `src/domain/value_objects/monitoring_status.py`: añadir el valor de enum `READY_FOR_ANALYSIS = "ready_for_analysis"`.
    - Añadir a `VALID_TRANSITIONS`: `running → ready_for_analysis`, `ready_for_analysis → {analyzing, error, aborted}`.
    - Conservar la transición existente `running → analyzing` (usada por capture-first legacy y por reprocess vía reset controlado).
    - NO modificar `TERMINAL_STATES` (`ready_for_analysis` debe quedar como NO terminal; `is_terminal()` debe devolver `False`).
    - Sin dependencias de infraestructura en el dominio.
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 14.4_
  - [ ] 1.2 Escribir tests unitarios de la FSM
    - En `tests/domain/test_monitoring_status.py`: `running→ready_for_analysis` válida; `ready_for_analysis→{analyzing, error, aborted}` válidas; toda otra transición desde `ready_for_analysis` lanza `InvalidTransitionError`; `ready_for_analysis` no es terminal; `completed`/`aborted`/`error` siguen siendo terminales; `completed→analyzing` y `error→analyzing` NO son aristas de la FSM.
    - _Requirements: 1.3, 1.5, 1.6, 1.7, 1.8, 14.4, 15.3_
  - [ ] 1.3 Escribir property test de la FSM (Property 1)
    - **Property 1: `ready_for_analysis` es no terminal y solo transiciona a analyzing/error/aborted**
    - Nuevo archivo `tests/properties/test_ready_for_analysis_fsm_properties.py`, etiquetado `Feature: 020-deferred-manual-analysis-workflow, Property 1`, mínimo 100 iteraciones.
    - Para todo estado `ready_for_analysis`: transiciones permitidas exactamente `{analyzing, error, aborted}`, `is_terminal()` falso, y cualquier transición fuera del conjunto lanza `InvalidTransitionError`.
    - **Validates: Requirements 1.3, 1.5, 1.6, 1.7, 1.8**

- [ ] 2. Compatibilidad de persistencia y round-trip de `ready_for_analysis`
  - [ ] 2.1 Confirmar compatibilidad de esquema e incluir `ready_for_analysis` en el Active_Status_Set
    - Verificar en `src/infrastructure/persistence/models/` que `MonitoringModel.status` es `String(20)` (acomoda `"ready_for_analysis"`, 18 chars) y que `video_path` ya existe (`String(500)`, nullable) — sin nuevas columnas ni migración.
    - En `src/application/services/monitoring_service.py`: añadir `ready_for_analysis` a `_ACTIVE_STATUSES` (Active_Status_Set unificado). Este conjunto es usado por la regla de una sesión activa por módulo, la reconciliación y el preflight; el cambio debe ser consistente en todos esos usos.
    - Asegurar que `get_active()` (o equivalente) incluye monitoreos en `ready_for_analysis`.
    - _Requirements: 2.1, 2.2, 9.3, 10.5_
  - [ ] 2.2 Escribir tests de round-trip de persistencia
    - En `tests/infrastructure/` (nuevo archivo `test_monitoring_repository_ready_for_analysis.py`): persistir `ready_for_analysis`, releer desde SQLite y verificar valor idéntico; simular reinicio (nueva sesión/engine sobre el mismo archivo) y verificar recuperación; verificar que `get_active()` incluye `ready_for_analysis`.
    - _Requirements: 2.1, 2.3, 2.4, 10.2, 10.5_
  - [ ] 2.3 Escribir property test de round-trip de persistencia (Property 3)
    - **Property 3: La persistencia de `ready_for_analysis` es un round-trip fiel**
    - Nuevo archivo `tests/properties/test_ready_for_analysis_persistence_properties.py`, etiquetado `Feature: 020-deferred-manual-analysis-workflow, Property 3`, mínimo 100 iteraciones.
    - Para todo monitoreo con estado persistido `ready_for_analysis`, leerlo desde SQLite (incluso tras reinicio simulado) devuelve exactamente `ready_for_analysis` sin recálculo.
    - **Validates: Requirements 2.3, 2.4, 10.2**

- [ ] 3. Exclusión atómica de análisis en el runtime registry
  - [ ] 3.1 Extender `MonitoringRuntimeRegistry` con reclamos de análisis
    - En `src/application/services/monitoring_runtime_registry.py`: añadir el conjunto separado `_analysis_claims` y los métodos `claim_analysis(id)` / `release_analysis(id)` / `is_analysis_claimed(id)`, adquiridos atómicamente bajo el `RLock` existente y **distintos** de `_finalization_claims`.
    - `claim_analysis` concede `True` a una sola solicitud concurrente; `release_analysis` idempotente.
    - Añadir el reclamo global ligero de dispositivo (`_analysis_global_lock` thread-safe) que permita como máximo un análisis en curso en toda la RPi (decisión adoptada en el diseño), con su método de adquisición/liberación.
    - _Requirements: 5.3, 9.1, 9.4, 9.5, 16.3, 16.4_
  - [ ] 3.2 Escribir tests unitarios del registry
    - En `tests/unit/test_monitoring_runtime_registry.py`: `claim_analysis` concede a una sola de N solicitudes concurrentes; `release_analysis` idempotente; independencia respecto de `_finalization_claims`; reclamo global permite un único análisis simultáneo y se libera correctamente.
    - _Requirements: 9.1, 9.4, 9.5, 16.3, 16.4_
  - [ ] 3.3 Escribir property test de concurrencia del reclamo de análisis (Property 2)
    - **Property 2: Idempotencia del inicio manual bajo concurrencia (exactamente un análisis)**
    - Nuevo archivo `tests/properties/test_analysis_claim_concurrency_properties.py`, etiquetado `Feature: 020-deferred-manual-analysis-workflow, Property 2`, mínimo 100 iteraciones.
    - Para todo número de solicitudes concurrentes de `claim_analysis` sobre el mismo monitoreo, exactamente una obtiene el reclamo; las demás se rechazan sin alterar el reclamo concedido.
    - **Validates: Requirements 9.1, 9.2, 16.2, 16.3, 16.4**

- [ ] 3B. Coordinación device-global de captura/análisis (vía `MonitoringRuntimeRegistry`)
  - [ ] 3B.1 Implementar la coordinación captura/análisis a nivel de dispositivo
    - Realizada mediante el `MonitoringRuntimeRegistry` **existente** (sin un segundo coordinador). Extender `src/application/services/monitoring_runtime_registry.py` con: (a) un reclamo/lock de análisis global de dispositivo, ligero y thread-safe, **adicional** a los `_analysis_claims` por-monitoreo, adquirido bajo el `RLock` existente; (b) una consulta thread-safe **global** de captura activa `has_active_capture()` (o nombre coherente con el código) que detecta una captura activa con independencia del módulo y es testeable fuera de la Raspberry Pi. (Si Task 3 ya añade el reclamo global ligero, esta tarea añade `has_active_capture()` y los cross-checks; no duplicar el lock.)
    - El método por módulo `has_live_worker_for_module` NO es suficiente como fuente global de captura activa; `is_camera_locked()` se conserva solo como safety net de hardware, no como fuente única de la coordinación device-global.
    - En `src/application/services/monitoring_service.py`: `start_deferred_analysis` debe **rechazar** si `has_active_capture()` indica una captura activa en el dispositivo (independiente del módulo), conservando `ready_for_analysis` sin retener reclamo ni lanzar hilo.
    - En `src/application/services/monitoring_service.py`: `start_session` (video-first y capture-first legacy) debe **rechazar** el inicio de una nueva captura si hay un análisis pesado activo (reclamo global de dispositivo tomado). Este rechazo al iniciar la sesión es la ÚNICA restricción nueva sobre el capture-first legacy: una sesión capture-first ya admitida conserva su FSM, lógica interna, análisis y artefactos sin cambios (Requirement 15.1).
    - Liberar el reclamo global en el `finally` del hilo de análisis ante cualquier desenlace (`completed`/`aborted`/`error`).
    - Múltiples módulos pueden coexistir en `ready_for_analysis` (no bloqueante); los nuevos recorridos NO se bloquean únicamente por videos pendientes en `ready_for_analysis` — solo un análisis pesado en curso bloquea nuevas capturas.
    - _Requirements: 15.1, 21.1, 21.2, 21.3, 21.7, 9.1, 9.4, 9.5, 16.3, 16.4_
  - [ ] 3B.2 Escribir tests de la coordinación device-global
    - En nuevo archivo `tests/unit/test_device_analysis_coordination.py`: `has_active_capture()` detecta captura activa con independencia del módulo (testeable fuera de RPi, sin depender de `is_camera_locked`); análisis activo + `start_session` (video-first y capture-first legacy) → rechazado; captura activa + `start_deferred_analysis` → rechazado (estado intacto, sin reclamo retenido); monitoreo en `ready_for_analysis` sin runtime + captura de otro módulo → permitido; dos monitoreos en `ready_for_analysis` → solo uno puede analizar a la vez (reclamo global de dispositivo); al terminar el análisis → la siguiente operación (captura o análisis) queda permitida; una sesión capture-first ya admitida no cambia su comportamiento.
    - _Requirements: 15.1, 21.1, 21.2, 21.3, 21.7, 9.4, 9.5, 16.3, 16.4_

- [ ] 4. Finalización video-first hacia `ready_for_analysis` (sin arranque automático)
  - [ ] 4.1 Modificar `_finalize_video_first` para transicionar a `ready_for_analysis`
    - En `src/application/services/monitoring_service.py`, dentro de `_finalize_video_first(monitoring_id, worker)`: tras validar el video (existe, >0 bytes, abre, ≥1 frame vía `VideoRecorder.validate()`), promover atómicamente `monitoring.recording.mp4 → monitoring.mp4` (`os.replace`), persistir `video_path` como ruta relativa, y transicionar `running → ready_for_analysis`.
    - **Detenerse tras la transición:** NO lanzar hilo de análisis, NO registrar hilo en el registry; hacer `remove_runtime` del worker de grabación y `release_finalization`.
    - Asegurar que la cámara fue liberada por `VideoRecordingWorker` antes de alcanzar `ready_for_analysis`; si la liberación falla, impedir la transición y marcar `error`.
    - Idempotencia: si el monitoreo ya está en `ready_for_analysis`, no repetir promoción ni transición (no-op de éxito).
    - Casos de error: 0 frames capturados → `error` (no `ready_for_analysis`); validación inválida o fallo de `os.replace` → `error` conservando `monitoring.recording.mp4` para diagnóstico.
    - Preservar el flujo capture-first legacy (`video_first_enabled=False`) sin cambios: `running→analyzing` automático vía `_start_capture_first` + `_run_analysis`.
    - _Requirements: 1.1, 1.2, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 15.1, 16.1_
  - [ ] 4.2 Escribir tests unitarios de finalización video-first
    - En `tests/unit/test_monitoring_service_video_first.py` (y/o `..._integration.py`): finalize video-first → `ready_for_analysis` sin lanzar hilo de análisis (verificar cero registros de análisis en el registry); cámara liberada antes de la transición; fallo de liberación de cámara impide la transición → `error`; idempotencia (segunda finalización = no-op de éxito, sin segunda transición ni segunda promoción); 0 frames → `error`; validación inválida → `error` con temporal conservado; fallo de `os.replace` → `error`.
    - Verificar explícitamente que el flujo capture-first legacy no cambió.
    - _Requirements: 1.1, 1.2, 3.3, 3.4, 4.1, 4.4, 5.1, 5.2, 5.5, 15.1, 16.1_

- [ ] 5. Preflight técnico y sonda opcional de subtensión
  - [ ] 5.1 Implementar `UndervoltagePresenceProbe`
    - Nuevo archivo `src/infrastructure/monitoring/undervoltage_probe.py`: capacidad OPCIONAL sobre `vcgencmd get_throttled`. Interpretar la máscara de bits: **bit 0 (`0x1`)** = subtensión actual → `PRESENT`; **bit 16 (`0x10000`)** = flag histórico → NO se interpreta como fallo actual. Estado tri-valuado `PRESENT` / `ABSENT` / `UNAVAILABLE` (fuera de RPi, `vcgencmd` ausente o error de parseo → `UNAVAILABLE`).
    - Sin lenguaje de robot/motores; sin nuevas dependencias.
    - _Requirements: 7.6, 7.7, 19.4, 19.5_
  - [ ] 5.2 Implementar `AnalysisPreflight`
    - Nuevo archivo `src/application/services/analysis_preflight.py`: helper/servicio de aplicación puro y testeable que ejecuta verificaciones **en orden**, deteniéndose en el primer fallo con mensaje accionable en español: (1) `status == "ready_for_analysis"`; (2) `video_path` no nulo; (3) ruta segura vía `validate_safe_path` de `src/infrastructure/security/path_sanitizer.py`; (4) archivo existe y tamaño > 0; (5) abre y lee ≥1 frame reutilizando `VideoRecorder.validate()` u `OpenCvVideoReader` (sin cargar RetinaNet ni abrir cámara); (6) sin análisis concurrente para este monitoreo, sin conflicto de sesión de módulo por Active_Status_Set (excluyendo el propio `monitoring_id`), sin reclamo global de análisis tomado y sin captura activa en el dispositivo según la consulta global `has_active_capture()` (independiente del módulo; `is_camera_locked()` solo como safety net); (7) temperatura ≤ umbral seguro del perfil activo leyendo vía el mecanismo de `ThermalMonitor` (si la lectura no está disponible, no bloquear y registrar "verificación térmica no disponible"); (8) subtensión: si `UndervoltagePresenceProbe` está disponible y reporta `PRESENT`, rechazar; si no está disponible, no bloquear y registrar la condición.
    - El preflight nunca afirma conexión a una fuente específica u oficial.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 9.3, 11.1, 11.2, 11.3, 19.3, 19.4, 19.5_
  - [ ] 5.3 Escribir tests unitarios de la sonda de subtensión
    - Nuevo archivo `tests/unit/test_undervoltage_probe.py`: bit 0 activo → `PRESENT`; bit 16 activo sin bit 0 → `ABSENT`; fuera de RPi / error de parseo → `UNAVAILABLE`; nunca `PRESENT` ante lectura ausente.
    - _Requirements: 7.6, 7.7, 19.4, 19.5_
  - [ ] 5.4 Escribir tests unitarios del preflight
    - Nuevo archivo `tests/unit/test_analysis_preflight.py`: cada verificación en orden; ruta insegura rechazada; `video_path` nulo / archivo inexistente / no legible rechazados con los mensajes correctos; temperatura no disponible no bloquea; temperatura por encima del umbral bloquea; subtensión ausente/no disponible no bloquea; subtensión presente bloquea; conflicto de sesión de módulo excluye el propio `monitoring_id`.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.8, 9.3, 11.1, 11.2, 11.3_
  - [ ] 5.5 Escribir property test de la sonda de subtensión (Property 4)
    - **Property 4: Semántica de bits de la sonda de subtensión**
    - Nuevo archivo `tests/properties/test_undervoltage_probe_properties.py`, etiquetado `Feature: 020-deferred-manual-analysis-workflow, Property 4`, mínimo 100 iteraciones.
    - Para toda máscara de `get_throttled`, la sonda reporta `PRESENT` sii el bit 0 está activo, ignorando el bit 16; lectura ausente/errónea → `UNAVAILABLE`, nunca `PRESENT`.
    - **Validates: Requirements 7.6, 7.7, 19.4, 19.5**

- [ ] 6. Inicio manual del análisis (aplicación + ruta HTML)
  - [ ] 6.1 Implementar `start_deferred_analysis` en `MonitoringService`
    - En `src/application/services/monitoring_service.py`: nuevo método `start_deferred_analysis(monitoring_id, power_source_confirmed, db_session)`. Flujo: si `power_source_confirmed` ausente → rechazo conservando `ready_for_analysis`; ejecutar `AnalysisPreflight.run(monitoring)`, si falla → rechazo conservando `ready_for_analysis` con mensaje accionable; `claim_analysis(id)` (y reclamo global), si ya reclamado → "El análisis ya está en curso"; **solo tras adquirir el reclamo**, escribir metadata de inicio (`manual_deferred_analysis=true`, `deferred_analysis_started_at`, temperatura del preflight); `update_status(id, "analyzing")`, registrar el hilo y lanzar `_run_video_analysis(id, video_path)` **reutilizado sin cambios de comportamiento de inferencia**; si el lanzamiento falla → `release_analysis` + rollback a `ready_for_analysis` sin descartar el video. Liberar el reclamo de análisis en el `finally` del hilo (cualquier desenlace: completed/aborted/error).
    - Rechazo por estado ≠ `ready_for_analysis` conservando el estado actual sin modificar campos.
    - Idempotencia: solicitud sobre monitoreo ya en `analyzing` → no lanzar segundo hilo, devolver "análisis ya en curso".
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.2, 9.1, 9.2, 9.4, 9.5, 11.1, 11.2, 11.3, 11.4, 11.5, 12.1, 12.2, 12.3, 12.4, 16.2, 16.3, 16.4, 19.1, 19.2, 20.1, 20.2, 20.3_
  - [ ] 6.2 Añadir la ruta HTML `POST /monitoreos/{id}/iniciar-analisis`
    - En `app/routes/agricultural_ui.py`: nuevo handler con `Depends(require_current_user_html)` que recibe la confirmación de energía como **campo de formulario opcional con default false** (`power_source_confirmed: bool = Form(False)`) para que FastAPI NO devuelva 422 antes de nuestra lógica; la ausencia o el valor `false` debe llegar a `start_deferred_analysis`, conservar `ready_for_analysis`, no crear hilo, no retener exclusión atómica y mostrar un error controlado en español (redirect `?error=`). El handler llama a `MonitoringService.start_deferred_analysis(...)` y en éxito redirige a `/monitoreos/{id}/ejecucion` (patrón 303 como `finalizar-captura`), traduciendo excepciones a redirects con `?error=` accionable en español. La confirmación no se persiste ni se guarda en sesión; cada reintento requiere re-confirmar.
    - _Requirements: 6.1, 6.3, 6.4, 8.1, 8.2, 8.4, 12.2, 12.3, 19.1, 19.2_
  - [ ] 6.3 Escribir tests unitarios de `start_deferred_analysis`
    - En nuevo archivo `tests/unit/test_start_deferred_analysis.py`: confirmación ausente → rechazo (estado intacto, sin reclamo retenido); preflight falla → rechazo (estado intacto); éxito → `analyzing` + hilo registrado + `_run_video_analysis` invocado; fallo de lanzamiento → rollback a `ready_for_analysis` + `release_analysis`; doble inicio concurrente → un solo hilo; estado ≠ `ready_for_analysis` → rechazo; video ausente/corrupto → mensajes exactos conservando `ready_for_analysis`.
    - _Requirements: 6.1, 6.3, 6.4, 6.6, 8.2, 9.2, 11.1, 11.2, 11.3, 12.1, 12.4, 16.2, 16.3_
  - [ ] 6.4 Escribir tests de la ruta HTML
    - En nuevo archivo `tests/unit/test_iniciar_analisis_route.py`: el POST SIN el campo `power_source_confirmed` NO produce 422 y en su lugar arroja el error controlado en español (redirect con `?error=`) conservando `ready_for_analysis`; con confirmación `true` y preflight OK redirige a ejecución (303); mensajes en español.
    - _Requirements: 8.1, 8.2, 8.4, 12.2, 12.3_
  - [ ] 6.5 Escribir property test de preservación en preflight (Property 5)
    - **Property 5: Preflight preserva el monitoreo ante cualquier rechazo**
    - Nuevo archivo `tests/properties/test_preflight_preservation_properties.py`, etiquetado `Feature: 020-deferred-manual-analysis-workflow, Property 5`, mínimo 100 iteraciones.
    - Para todo monitoreo en `ready_for_analysis` y toda combinación con al menos una verificación fallida, `start_deferred_analysis` deja el monitoreo en `ready_for_analysis` con `video_path` y datos intactos y sin reclamo de análisis retenido.
    - **Validates: Requirements 6.4, 7.8, 11.4, 11.5, 12.1, 12.4**

- [ ] 7. Recuperación tras reinicio (excepción para `ready_for_analysis`)
  - [ ] 7.1 Exceptuar `ready_for_analysis` de la reconciliación de arranque
    - En `src/application/services/monitoring_service.py`: en `reconcile_orphaned_sessions_on_startup()` y `_reconcile_orphaned_sessions(module_id)`, tratar un monitoreo en `ready_for_analysis` sin hilo en memoria como huérfano **esperado** y NO transicionarlo a `error`. Conservar el comportamiento actual: `analyzing` sin runtime → `error`; `initializing/running/paused/finishing` huérfanos → `error`.
    - Verificar que `recover_abrupt_recordings()` y `reprocess_monitoring` permanecen sin cambios (documentar su interacción); no auto-iniciar análisis en recuperación.
    - Tras reinicio, iniciar análisis sobre un monitoreo recuperado en `ready_for_analysis` ejecuta el mismo flujo (preflight + confirmación + lanzamiento); si `monitoring.mp4` ya no existe/legible, conservar `ready_for_analysis` y presentar el error de video ausente/corrupto.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 15.3, 15.4_
  - [ ] 7.2 Escribir tests de recuperación al arranque
    - En nuevo archivo `tests/unit/test_startup_recovery_ready_for_analysis.py`: `ready_for_analysis` exento de reconciliación (permanece); `analyzing` sin runtime → `error`; otros activos huérfanos → `error`; reinicio conserva `video_path`; video ausente tras reinicio → error al intentar iniciar, estado conservado.
    - _Requirements: 10.1, 10.2, 10.3, 10.6_

- [ ] 8. Trazabilidad durable de métricas (merge incremental no destructivo)
  - [ ] 8.1 Implementar merge incremental en el report writer
    - En `src/infrastructure/persistence/local/snapshot_analysis_report_writer.py`: asegurar que la escritura de métricas es **leer-fusionar-escribir** (o escribir solo claves nuevas) sin truncar claves previas. Orden de merge: (1) métricas de captura/grabación en `finalize`; (2) al entrar en `ready_for_analysis` (finalize) escribir **solo** `capture_completed_at` (UTC durable) — NO escribir `manual_deferred_analysis` ni `deferred_analysis_started_at` en finalize; (3) metadata de inicio manual (`manual_deferred_analysis=true`, `deferred_analysis_started_at` UTC y temperatura del preflight en °C o nulo) **solo** cuando el inicio manual es aceptado (preflight OK + confirmación + reclamo atómico adquirido), después del reclamo; (4) métricas de análisis de `VideoAnalysisService` al finalizar.
    - En caso de rollback a `ready_for_analysis` (el hilo no logró arrancar), NO dejar metadata que afirme falsamente que el análisis inició (`manual_deferred_analysis`/`deferred_analysis_started_at` no deben quedar escritos).
    - Sin nueva columna en base de datos: las marcas durables van en el archivo de métricas bajo `outputs/monitorings/{id}/`, escritas antes del análisis para sobrevivir a reinicio. `completed_at` sin cambios; no reutilizar `completed_at`.
    - Si la escritura del archivo de métricas falla, preservar la transición de estado (`error`/`completed`) sin revertirla y registrar el indicador de fallo.
    - _Requirements: 13.3, 13.6, 18.1, 18.2, 18.3, 18.4, 18.5_
  - [ ] 8.2 Escribir tests del merge durable de métricas
    - En nuevo archivo `tests/unit/test_durable_metrics_merge.py`: al entrar en `ready_for_analysis` está presente `capture_completed_at` SIN `manual_deferred_analysis` ni `deferred_analysis_started_at`; `manual_deferred_analysis=true` y `deferred_analysis_started_at` aparecen solo tras un inicio aceptado; un lanzamiento fallido con rollback a `ready_for_analysis` no deja metadata falsa de inicio; temperatura nula cuando no disponible; el merge permanece no destructivo (no sobrescribe claves previas); fallo de escritura no revierte el estado.
    - _Requirements: 13.3, 13.6, 18.1, 18.2, 18.3, 18.4, 18.5_

- [ ] 9. UI de `ready_for_analysis` e inicio manual con confirmación de energía
  - [ ] 9.1 Reconocer `ready_for_analysis` en los endpoints de estado
    - En `app/routes/monitoring.py` y `app/routes/monitoring_api.py`: asegurar que el endpoint de estado reconoce y expone `ready_for_analysis` sin error (Requirement 2.3: valor idéntico al persistido).
    - _Requirements: 2.3, 14.5_
  - [ ] 9.2 Añadir el bloque UI `ready_for_analysis` y el diálogo de energía
    - En `app/templates/agricultural/monitoring_execution.html`: nuevo bloque `#status-ready_for_analysis` (patrón de los bloques `#status-*` existentes) con indicador en español "Captura finalizada — análisis pendiente" (prohibido "pausado"), texto legible y contraste conforme a reglas de UI; botón primario único "Iniciar análisis" (área táctil ≥ mínimos del proyecto); diálogo de confirmación de energía (patrón `finalize-dialog`) que al confirmar envía el formulario con `power_source_confirmed=true` y al cancelar mantiene `ready_for_analysis` con la acción disponible; en `analyzing` se muestra el progreso y se oculta "Iniciar análisis". Sin lenguaje de robot/autonomía/chasis/motores.
    - _Requirements: 8.1, 8.3, 8.4, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_
  - [ ] 9.3 Reconocer `ready_for_analysis` en el JS de polling y etiqueta del dashboard
    - En `app/static/js/monitoring.js`: añadir `ready_for_analysis` al mapa de bloques `#status-*` para que el polling muestre el bloque correcto.
    - En `app/templates/agricultural/dashboard.html`: añadir `ready_for_analysis` solo a la lista de etiquetas de estado (sin rediseño del dashboard).
    - _Requirements: 17.1, 17.5, 15.5_
  - [ ] 9.4 Escribir tests de UI/plantilla
    - En nuevo archivo `tests/unit/test_ready_for_analysis_ui.py`: el bloque `ready_for_analysis` no usa "pausado" y expone "Iniciar análisis" habilitado; `analyzing` oculta la acción; el endpoint de estado devuelve `ready_for_analysis`; la etiqueta del dashboard existe.
    - _Requirements: 17.1, 17.2, 17.5, 17.6_

- [ ] 10. Checkpoint — Verificar tests dirigidos
  - Ejecutar los tests de los componentes anteriores; asegurar que todos pasan; preguntar al usuario si surgen dudas.
  - _Requirements: 15.2, 15.7_

- [ ] 11. Manejo de errores durante y al completar el análisis (verificación de integración)
  - [ ] 11.1 Verificar transiciones terminales del análisis diferido
    - Confirmar (sin modificar la lógica de inferencia de `VideoAnalysisService`) que: fallo fatal durante análisis → `analyzing → error` conservando `monitoring.mp4`; fallo no fatal de un frame → descarta ese frame y continúa; éxito total → `analyzing → completed` con métricas agregadas persistidas (mecanismo Spec 019) y `completed_at` registrado; fallo de persistencia de métricas agregadas en la transición a `completed` → `error` conservando resultados; `pipeline_metrics.json` escrito en cualquier desenlace.
    - Ajustar únicamente el cableado de estado (no el pipeline) si algún caso no se cumple.
    - _Requirements: 13.1, 13.2, 13.4, 13.5, 14.1, 14.2, 14.3, 14.6, 20.4, 20.5, 20.7_
  - [ ] 11.2 Escribir tests de integración de desenlaces del análisis
    - En nuevo archivo `tests/unit/test_deferred_analysis_outcomes.py` (con dobles del pipeline): error fatal → `error` con video intacto y reclamo liberado; éxito → `completed` con métricas y `completed_at`; fallo de métricas agregadas → `error`; verificar que el reclamo de análisis se libera en todos los desenlaces.
    - _Requirements: 13.1, 13.2, 14.1, 14.2, 14.3, 14.6, 9.4_

- [ ] 12. Checkpoint final — Suite completa, regresión y validación en Raspberry
  - [ ] 12.1 Ejecutar tests dirigidos y suite completa con comparación de línea base
    - Ejecutar los tests de esta feature y luego la suite completa `python -m pytest -q`.
    - Comparar contra la línea base previa a Spec 020: cero **nuevas** regresiones y cero nuevos errores de recolección/ejecución. El fallo conocido `tests/unit/test_sync_routes.py::TestSyncLocalTrigger::test_post_with_pending_records_triggers_export` queda FUERA DE ALCANCE — puede permanecer y NO debe intentarse corregir.
    - _Requirements: 15.2, 15.5, 15.6, 15.7_
  - [ ] 12.2 Verificar diff sin cambios en el pipeline de visión
    - Revisar el diff completo de la feature para confirmar que NO hay cambios en RetinaNet, umbrales, exposición, resolución de grabación, Scene Gate, Optical Flow, modelo de salud, algoritmo de madurez ni scheduler disperso; y que no se introdujeron dependencias nuevas ni lenguaje de robot/motores/autonomía.
    - _Requirements: 20.1, 20.2, 20.3, 20.6_
  - [ ] 12.3 Checklist de validación E2E en Raspberry Pi 5 (PENDIENTE — manual en hardware, `@pytest.mark.raspberry`/`@pytest.mark.hardware`)
    - **Checklist manual PENDIENTE de ejecución en el dispositivo físico** (NO es una prueba ya completada). Escribir/dejar documentado el checklist para ejecutarlo en la RPi 5, con los pasos E2E: grabar → finalizar → verificar estado `ready_for_analysis` → apagar/reiniciar → reabrir app → estado sigue `ready_for_analysis` → confirmar fuente de energía → iniciar análisis → `analyzing` → `completed`. Registrar temperatura de inicio, pico y fin.
    - Verificar explícitamente que RetinaNet NO se inicia inmediatamente tras finalizar la captura (cero hilos de análisis en `ready_for_analysis`).
    - _Requirements: 1.1, 1.2, 5.1, 5.2, 10.1, 10.4, 18.4_
  - [ ] 12.4 Checkpoint final
    - Asegurar que todos los tests dirigidos pasan y que no hay nuevas regresiones; preguntar al usuario si surgen dudas.
    - _Requirements: 15.2, 15.7_

## Notes

- Todas las pruebas definidas para la Spec 020 (unitarias, property-based e integración) son **obligatorias** y forman parte de la implementación; deben ejecutarse, no omitirse.
- Cada tarea referencia los números de requisito que satisface para trazabilidad.
- Los property tests están etiquetados `Feature: 020-deferred-manual-analysis-workflow, Property N` y corren con mínimo 100 iteraciones (Hypothesis).
- No se crean nuevas columnas, tablas ni migraciones para `ready_for_analysis`; las marcas durables van al archivo de métricas por monitoreo.
- `ready_for_analysis` es un estado activo/no terminal; el flujo capture-first legacy permanece sin cambios y el reprocesamiento de Spec 019 permanece fuera de la FSM y del nuevo flujo `ready_for_analysis`.
- El reclamo de análisis es distinto del reclamo de finalización; `AnalysisPreflight` no afirma conexión a una fuente específica.
- `UndervoltagePresenceProbe` es opcional: bit 0 bloqueante, bit 16 no bloqueante, lectura no disponible no bloqueante.
- El pipeline de visión (RetinaNet, umbrales, Scene Gate, Optical Flow, salud, madurez) queda fuera de alcance; el umbral EDGE 0.60 permanece sin cambios.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1", "3.1", "5.3"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "3.3", "3B.1"] },
    { "id": 3, "tasks": ["3B.2", "4.1"] },
    { "id": 4, "tasks": ["4.2", "5.2", "8.1"] },
    { "id": 5, "tasks": ["5.4", "5.5", "6.1", "8.2"] },
    { "id": 6, "tasks": ["6.2", "6.3", "6.5", "7.1"] },
    { "id": 7, "tasks": ["6.4", "7.2", "9.1", "11.1"] },
    { "id": 8, "tasks": ["9.2", "11.2"] },
    { "id": 9, "tasks": ["9.3"] },
    { "id": 10, "tasks": ["9.4"] },
    { "id": 11, "tasks": ["12.1"] },
    { "id": 12, "tasks": ["12.2", "12.3"] }
  ]
}
```
