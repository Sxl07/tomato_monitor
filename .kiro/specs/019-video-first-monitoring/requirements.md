# Requirements Document: 019 — Video-First Monitoring

## Introduction

Esta feature reemplaza la estrategia de adquisición y procesamiento del monitoreo, pasando de **capture-first** (selección de snapshots en vivo, con descarte irreversible de frames no seleccionados) a **video-first** (grabación del video completo como fuente primaria re-procesable). Al conservar el video original, la inferencia se ejecuta de forma diferida (offline) después de la caminata, y el mismo `monitoring.mp4` puede reprocesarse con una configuración distinta sin volver al invernadero.

**Dos problemas distintos que NO deben confundirse.** La baja cobertura observada en campo (por ejemplo, del orden de ~4 de ~30 tomates en una corrida capture-first) puede provenir de dos causas diferentes:

- **(A) Problema de adquisición:** frames útiles se descartan **antes** de que RetinaNet los vea. La selección en vivo (Scene Gate + cooldown) elimina de forma irreversible los frames intermedios; el detector nunca tuvo la oportunidad de procesarlos.
- **(B) Problema de detector:** RetinaNet recibe un buen frame pero **no detecta** el tomate (falso negativo del modelo), por oclusión, escala pequeña, iluminación o umbral de score.

**Spec 019 resuelve principalmente (A)** al conservar el video completo como fuente re-procesable: ningún frame se pierde antes de la inferencia. Además, **habilita diagnosticar y reprocesar (B)**, porque el mismo video puede reanalizarse con configuraciones distintas (por ejemplo, detección densa cuadro por cuadro o umbrales alternativos) sin volver al invernadero. **No se afirma que video-first garantice, por sí solo, una mejora de recall a un valor específico** (por ejemplo, subir de ~4/30). El detector y sus umbrales permanecen sin cambios. Lo que aporta esta feature es la capacidad de **medir sobre el video completo cuál de los dos problemas domina**: si al procesar el video íntegro con detección densa la cobertura sube, el cuello de botella era de adquisición (A); si sigue baja aun con detección densa, el cuello de botella es del detector (B) y deberá abordarse en una spec posterior de modelo.

El sistema separa de forma estricta dos fases: **Captura (grabación)**, donde el operario recorre el módulo con el dispositivo y se graba video sin inferencia pesada, y **Análisis (inferencia diferida)**, donde sobre el video ya cerrado se aplica selección dispersa de frames (Scene Gate + Optical Flow + RetinaNet solo cuando se justifica), tracking, salud y madurez.

El detector se mantiene en Detectron2/RetinaNet R-50-FPN, la salud en ResNet-18 y la madurez en HSV+CIELab (escala USDA de 6 etapas); no se migra a YOLO. Los snapshots pasan a ser artefactos derivados del video (frames donde efectivamente se programó el detector). En producción no se genera video anotado (desactivado por defecto); el archivo importante es el video original.

El orden de prioridades declarado es: (1) integridad del video, (2) cobertura/recall de observaciones, (3) precisión/recall, (4) estabilidad, (5) rendimiento. No se sacrifica recall por FPS: el procesamiento puede tardar tras la caminata. El diseño reutiliza el pipeline de visión existente sin modificarlo y preserva los componentes capture-first actuales intactos para no romper la regresión ni el modo benchmark/diagnóstico.

**Fuera de alcance (exportación del video).** Spec 019 no modifica la exportación ZIP: `Export_Service` permanece sin cambios y funcional. El video se conserva localmente y es reprocesable; la inclusión del `monitoring.mp4` completo en el paquete de exportación queda como posible spec futura y no forma parte de este alcance.

## Glossary

- **Video_First_System**: conjunto de servicios de aplicación e infraestructura que implementan la grabación de video como fuente primaria y el análisis diferido sobre el archivo grabado.
- **Video_Recording_Worker**: servicio de aplicación (daemon) propietario exclusivo de la cámara durante la grabación; consume frames del `FrameSource` y los escribe en el `Video_Recorder`. No ejecuta inferencia.
- **Video_Recorder**: componente de infraestructura que envuelve `cv2.VideoWriter`, gestiona selección de códec, apertura con la dimensión del primer frame real, escritura sin pérdida, cierre limpio, validación del archivo resultante y finalización atómica (`monitoring.recording.mp4` → `monitoring.mp4`).
- **Video_Analysis_Service**: servicio de aplicación (daemon) que lee el `monitoring.mp4` de forma diferida a través del `VideoReaderPort`, aplica la decisión de detección dispersa, tracking, salud y madurez, y persiste resultados derivados. No importa `cv2`.
- **VideoReaderPort**: puerto de aplicación (interfaz, `src/application/interfaces/`) que entrega frames y metadatos del video (`fps`, `total_frames`, `width`, `height`); no importa `cv2`.
- **OpenCvVideoReader**: adaptador de infraestructura que implementa `VideoReaderPort` usando OpenCV; única capa que importa `cv2` para la lectura de video en el análisis.
- **Monitoring_Service**: servicio de aplicación que orquesta el ciclo de vida del monitoreo (start, finalize, reprocess) y las transiciones de estado.
- **Detector_Decision**: función pura `decide_run_detector` que determina si el detector corre en un frame y la razón (`first_frame`, `max_gap_force`, `scene_gate`, `scene_gate_blocked`, `min_gap_ready`, `cooldown`, `full_detection`).
- **detector_scheduled_frames**: frames en los que `decide_run_detector` retornó `run_detector = true` (el detector fue programado para correr en ese frame).
- **analysis_successful_frames**: frames `detector_scheduled` en los que `process_frame()` completó con éxito.
- **analysis_failed_frames**: frames `detector_scheduled` en los que `process_frame()` o una persistencia recuperable relacionada lanzó excepción (la excepción puede ocurrir incluso dentro de RetinaNet en `process_frame()`; no se distingue aquí si el fallo fue en RetinaNet, ResNet o madurez — el error/log por frame lo registra cuando está disponible).
- **monitoring.recording.mp4**: archivo temporal de grabación en curso; se renombra atómicamente a `monitoring.mp4` solo tras validarse.
- **Monitoring_Status_FSM**: máquina de estados del monitoreo con estados `initializing`, `running`, `paused`, `finishing`, `analyzing`, `completed`, `aborted`, `error`; `completed`, `aborted` y `error` son terminales.
- **Recording**: fase de grabación de video; mapea al estado `running`.
- **Processing**: fase de inferencia diferida; mapea al estado `analyzing`.
- **Snapshot**: frame del video persistido (DB + JPEG) exclusivamente para frames donde el detector fue programado (`detector_scheduled_frames`); artefacto derivado; se persiste ANTES de `process_frame()`, de modo que se conserva aunque el análisis falle.
- **Detection_Inspection_Result**: resultado de inspección persistido por track (el de mayor área).
- **Monitoring_Metrics**: métricas agregadas del monitoreo persistidas en base de datos.
- **Pipeline_Metrics_File**: archivo `pipeline_metrics.json` con métricas de ejecución y la configuración utilizada.
- **Execution_Profile**: perfil de ejecución (`edge`/`full`) que parametriza el muestreo disperso y la grabación.
- **Camera_Lock**: candado global (`_camera_lock`) de `RaspberryCameraFrameSource` que garantiza un único propietario de cámara.
- **Path_Sanitizer**: componente que valida y sanea rutas contra ataques de path traversal.
- **Capture_Worker**: componente capture-first preexistente que se preserva sin cambios.
- **Snapshot_Analysis_Service**: componente capture-first preexistente que se preserva sin cambios.
- **Video_Inspection_Runner**: runner legacy de inspección de video, conservado solo como herramienta de benchmark/diagnóstico.
- **Export_Service**: servicio que genera paquetes ZIP de exportación de datos e imágenes; permanece sin cambios en Spec 019 (la inclusión del video completo está fuera de alcance).
- **operario**: farmer autenticado que transporta el dispositivo por el invernadero.

## Requirements

### Requirement 1: Grabación de video como fuente primaria

**User Story:** Como operario, quiero que al iniciar un monitoreo el dispositivo grabe el recorrido completo en video, para conservar toda la evidencia visual sin descartar frames intermedios.

#### Acceptance Criteria

1. WHEN el operario inicia un monitoreo en modo video-first, THE Monitoring_Service SHALL arrancar el Video_Recording_Worker en estado `running` y adquirir la cámara vía el Camera_Lock.
2. WHILE un monitoreo está en estado `running`, THE Video_Recording_Worker SHALL escribir cada frame entregado por el FrameSource en el Video_Recorder a la resolución entregada, sin ejecutar inferencia de detección, salud ni madurez, sin Scene Gate, sin selección de snapshots y sin downscale adicional.
3. WHEN el Video_Recording_Worker recibe el primer frame válido del FrameSource, THE Video_Recorder SHALL abrirse mediante `open(frame_size)` usando las dimensiones de ese primer frame (`frame.shape`) como única fuente del `frame_size`, escribir ese primer frame y registrar el ancho y alto realmente grabados.
4. THE Video_Recording_Worker SHALL almacenar en `monitoring.mp4` exactamente la resolución entregada por el FrameSource, sin reescalado.
5. THE Video_First_System SHALL asignar el control de la cadencia de captura de forma explícita a la cámara/frame source, y THE Video_Recording_Worker SHALL abstenerse de aplicar throttle adicional (sin `sleep()` para limitar FPS), escribiendo cada frame entregado por el FrameSource sin introducir doble-throttle que descarte información de frames.
6. THE Video_First_System SHALL configurar el fps objetivo de la grabación video-first de forma explícita mediante una configuración de video de Picamera2 (`FrameRate`/`FrameDurationLimits`) como el cambio más pequeño posible, sin alterar la ruta de still-configuration usada por el preview y el capture-first ni crear una segunda instancia de `Picamera2`.
7. WHEN el Video_Recorder se abre, THE Video_Recorder SHALL abrir el `cv2.VideoWriter` con `configured_recording_fps`, de modo que `container_fps == configured_recording_fps`.
8. THE Video_First_System SHALL medir `effective_recording_fps` de forma independiente como `frames_written / recording_duration_seconds`, sin modificar el `container_fps` tras la grabación y sin realizar remux, two-pass ni re-encode para "corregir" el fps.
9. THE Video_Recorder SHALL recibir el `fps` (= `configured_recording_fps`) en su construcción y el `frame_size` únicamente en `open()`, descubriéndolo exclusivamente del primer frame válido entregado por el FrameSource.
10. IF, tras `open()`, aparece un frame cuyas dimensiones difieren del `frame_size` con el que se abrió el Video_Recorder, THEN THE Video_Recorder SHALL tratarlo como un error de grabación explícito, sin realizar resize silencioso.
11. WHILE un monitoreo está en estado `running`, THE Video_Recording_Worker SHALL escribir en el archivo temporal `monitoring.recording.mp4`.
12. WHEN el operario finaliza la captura, THE Video_Recording_Worker SHALL detener la grabación, cerrar el Video_Recorder y liberar la cámara.
13. WHEN el Video_Recording_Worker cierra el Video_Recorder, THE Video_Recorder SHALL validar que el archivo temporal existe, tiene tamaño mayor que cero y puede abrirse y leerse al menos un frame.
14. WHEN, en una terminación controlada (finalización normal, aborto cooperativo o excepción del worker cuyo bloque `finally` sí se ejecuta), el archivo temporal resulta validado, THE Monitoring_Service SHALL renombrar atómicamente `monitoring.recording.mp4` a `monitoring.mp4` y persistir su ruta relativa en el campo `video_path` del monitoreo.
15. IF en una terminación controlada el archivo temporal resulta inválido, THEN THE Monitoring_Service SHALL abstenerse de persistir un video válido, conservar el archivo temporal o fallido para diagnóstico cuando sea seguro, y transicionar el monitoreo al estado `error`.
16. WHEN el monitoreo alcanza `completed` con un `monitoring.mp4` validado, THE Video_First_System SHALL garantizar que un fallo de análisis posterior nunca modifica ni elimina ese `monitoring.mp4` validado.
17. IF ocurre una terminación abrupta (corte de energía, `kill -9`, crash del proceso antes de que corra `finally`), THEN THE Video_First_System SHALL abstenerse de garantizar que el contenedor MP4 quedó correctamente finalizado.
18. WHEN la aplicación reinicia y existe un `monitoring.recording.mp4` remanente de una terminación abrupta, THE Video_First_System SHALL conservarlo, validarlo de forma segura con el mismo criterio de validación y promoverlo a `monitoring.mp4` mediante la rutina de recuperación únicamente si resulta válido, conservándolo para diagnóstico si resulta inválido y sin marcarlo automáticamente como `monitoring.mp4` válido sin pasar la validación.
19. WHEN el Video_Recording_Worker termina su ejecución por cualquier causa, THE Video_Recording_Worker SHALL liberar el Camera_Lock antes de retornar.

### Requirement 2: Separación estricta entre captura y análisis

**User Story:** Como equipo de tesis, quiero que la grabación y el análisis sean fases independientes, para poder reprocesar el mismo video con distinta configuración sin volver al campo.

#### Acceptance Criteria

1. THE Video_Recording_Worker SHALL producir el archivo de video sin ejecutar inferencia pesada durante la fase de captura.
2. WHEN existe un `monitoring.mp4` final y validado, THE Monitoring_Service SHALL lanzar el Video_Analysis_Service sobre ese archivo en estado `analyzing`.
3. THE Monitoring_Service SHALL abstenerse de iniciar el análisis diferido antes de que exista un `monitoring.mp4` final y validado.
4. THE Video_Analysis_Service SHALL leer el `monitoring.mp4` de forma diferida como su única fuente de frames.
5. WHERE un monitoreo terminal conserva un `video_path` no nulo con archivo existente, THE Monitoring_Service SHALL permitir reprocesar el mismo video con una configuración distinta sin requerir una nueva grabación.

### Requirement 3: Decisión de ejecución del detector (muestreo disperso)

**User Story:** Como equipo de tesis, quiero una lógica clara que decida cuándo corre el detector sobre cada frame, para equilibrar cobertura y costo de procesamiento de forma reproducible.

#### Acceptance Criteria

1. IF el muestreo disperso está deshabilitado, THEN THE Detector_Decision SHALL retornar `run_detector = true` con razón `full_detection`.
2. WHEN el frame es el primero y la detección forzada del primer frame está activa, THE Detector_Decision SHALL retornar `run_detector = true` con razón `first_frame`.
3. WHEN el número de frames sin detección alcanza o supera el máximo configurado, THE Detector_Decision SHALL retornar `run_detector = true` con razón `max_gap_force`.
4. WHILE el número de frames sin detección alcanza o supera el mínimo configurado y el Scene Gate está activo con frame de referencia disponible, THE Detector_Decision SHALL retornar `run_detector = true` con razón `scene_gate` cuando el Scene Gate detecta cambio, y `run_detector = false` con razón `scene_gate_blocked` cuando no detecta cambio.
5. WHILE el número de frames sin detección alcanza o supera el mínimo configurado y el Scene Gate está inactivo o no hay frame de referencia, THE Detector_Decision SHALL retornar `run_detector = true` con razón `min_gap_ready`.
6. IF el número de frames sin detección es menor que el mínimo configurado, THEN THE Detector_Decision SHALL retornar `run_detector = false` con razón `cooldown`.
7. THE Detector_Decision SHALL retornar exactamente una razón perteneciente al conjunto cerrado {`first_frame`, `max_gap_force`, `scene_gate`, `scene_gate_blocked`, `min_gap_ready`, `cooldown`, `full_detection`}.
8. THE Detector_Decision SHALL operar como función pura sin abrir la cámara, sin ejecutar inferencia y sin escribir en disco.

### Requirement 4: Análisis diferido sobre el video

**User Story:** Como operario, quiero que el análisis se ejecute sobre el video grabado aplicando detección, tracking, salud y madurez, para obtener métricas agrícolas completas del recorrido.

#### Acceptance Criteria

1. WHEN el Video_Analysis_Service inicia, THE Video_Analysis_Service SHALL abrir el video y leer sus metadatos (`fps`, `total_frames`, `width`, `height`) a través del VideoReaderPort inyectado.
2. THE Video_Analysis_Service SHALL abstenerse de importar `cv2`, delegando toda lectura de video y metadatos en el VideoReaderPort cuya implementación OpenCV reside en la capa de infraestructura.
3. THE OpenCvVideoReader SHALL poseer exactamente un `cv2.VideoCapture` usado para `open`, `is_available`, `metadata`, `read` y `release`, sin abrir un segundo `cv2.VideoCapture` ni acceder al estado privado de otra frame source, mientras VideoFileFrameSource permanece intacto para sus consumidores existentes.
4. WHEN el detector debe correr en un frame según la Detector_Decision, THE Video_Analysis_Service SHALL ejecutar el pipeline de detección Detectron2/RetinaNet R-50-FPN sobre ese frame.
5. THE Video_Analysis_Service SHALL reutilizar un único objeto de componentes de pipeline durante todo el video, de modo que `components.tracker` (SimpleTracker) provea el tracking cross-frame de las detecciones de RetinaNet, sin instanciar un SimpleTracker adicional.
6. WHERE la propagación por Optical Flow está habilitada y el detector no corre en un frame, THE Video_Analysis_Service SHALL propagar los tracks existentes mediante el OpticalFlowVisualTracker exclusivamente en los frames donde el detector no corre.
7. WHERE un track resulta de una detección con score suficiente, THE Video_Analysis_Service SHALL ejecutar la clasificación de salud con ResNet-18 y la estimación de madurez con HSV+CIELab cuando corresponda según las políticas existentes.
8. WHEN el análisis completa el recorrido del video con éxito, THE Monitoring_Service SHALL transicionar el monitoreo al estado `completed`.

### Requirement 5: Snapshots como artefactos derivados

**User Story:** Como equipo de tesis, quiero que los snapshots se generen solo a partir del video para frames analizados por el detector, para mantener trazabilidad entre snapshots y detecciones reales.

#### Acceptance Criteria

1. WHEN la Detector_Decision retorna `run_detector = true` para un frame (`detector_scheduled_frames`), THE Video_Analysis_Service SHALL persistir el Snapshot derivado (frame crudo en base de datos y archivo JPEG) antes de ejecutar `process_frame()` sobre ese frame.
2. IF el detector no se programa en un frame durante el análisis, THEN THE Video_Analysis_Service SHALL abstenerse de persistir un Snapshot para ese frame.
3. THE Video_Analysis_Service SHALL registrar el índice de frame del video en el campo `frame_index` de cada Snapshot persistido.
4. THE Video_Analysis_Service SHALL persistir un número de Snapshots igual a la cantidad de frames `detector_scheduled_frames`, con independencia de que `process_frame()` de algún frame falle, dado que el Snapshot crudo se persiste antes de `process_frame()`.
5. THE Video_Analysis_Service SHALL contabilizar cada frame `detector_scheduled_frames` como `analysis_successful_frames` cuando `process_frame()` completa con éxito, o como `analysis_failed_frames` cuando `process_frame()` o una persistencia recuperable relacionada lanza excepción, cumpliéndose `analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames`.
6. IF `process_frame()` o una persistencia recuperable relacionada falla en un frame programado (la excepción puede ocurrir incluso dentro de RetinaNet), THEN THE Video_Analysis_Service SHALL registrar el frame como `analysis_failed_frames`, conservar el Snapshot ya persistido y continuar con el frame siguiente, sin necesidad de distinguir si el fallo fue en RetinaNet, ResNet o madurez.
7. THE Video_Analysis_Service SHALL persistir a lo sumo un Detection_Inspection_Result por track, correspondiente a la detección de mayor área observada para ese track.

### Requirement 6: Video anotado desactivado en producción

**User Story:** Como equipo de tesis, quiero que el video anotado esté desactivado por defecto, para priorizar la conservación del video original y reducir carga de I/O y CPU.

#### Acceptance Criteria

1. THE Execution_Profile SHALL definir la generación de video anotado como desactivada por defecto en producción.
2. IF la generación de video anotado está desactivada, THEN THE Video_Analysis_Service SHALL abstenerse de escribir video anotado durante el análisis.
3. WHERE la generación de video anotado está habilitada, THE Video_Analysis_Service SHALL escribir el video anotado sin eliminar ni modificar el video original.

### Requirement 7: Vista previa durante la grabación

**User Story:** Como operario, quiero ver una vista previa mientras grabo, para confirmar que el dispositivo está capturando el módulo correctamente.

#### Acceptance Criteria

1. WHILE un monitoreo está en estado `running`, THE Video_Recording_Worker SHALL exponer una copia del último frame leído para la vista previa de la interfaz.
2. WHILE la grabación está activa, THE Video_First_System SHALL abstenerse de abrir la cámara en paralelo, de llamar a `capture_single_frame()` y de crear una segunda instancia de `Picamera2` para generar la vista previa.
3. WHEN la interfaz solicita la vista previa durante la grabación, THE Video_First_System SHALL obtener el Video_Recording_Worker mediante `MonitoringRuntimeRegistry.get_worker()` de forma thread-safe y mostrar la copia del último frame devuelta por `get_last_frame()`.
4. WHEN `get_last_frame()` retorna un frame, THE Video_Recording_Worker SHALL entregar una copia del último frame leído, sin exponer un `ndarray` que pueda ser mutado concurrentemente por el bucle de grabación.

### Requirement 8: Ciclo de vida y estados del monitoreo

**User Story:** Como operario, quiero que el estado del monitoreo refleje con precisión la fase actual, para no ver un monitoreo como terminado mientras aún se procesa.

#### Acceptance Criteria

1. WHEN la grabación está en curso, THE Monitoring_Status_FSM SHALL representar el monitoreo en estado `running`.
2. WHEN el análisis diferido está en curso, THE Monitoring_Status_FSM SHALL representar el monitoreo en estado `analyzing`.
3. WHILE el análisis diferido está en curso, THE Monitoring_Service SHALL abstenerse de exponer el monitoreo en estado `completed`.
4. WHEN el análisis diferido finaliza con éxito, THE Monitoring_Service SHALL transicionar el monitoreo al estado `completed`.
5. WHEN el operario cancela explícitamente la grabación, THE Monitoring_Service SHALL transicionar el monitoreo al estado `aborted` conservando el video parcial si existe.
6. IF ocurre un fallo del sistema durante grabación o análisis, THEN THE Monitoring_Service SHALL transicionar el monitoreo al estado `error` conservando el video grabado.

### Requirement 9: Reprocesamiento controlado

**User Story:** Como operario, quiero reprocesar un monitoreo terminado con una configuración distinta, para mejorar la cobertura sin repetir el recorrido físico.

#### Acceptance Criteria

1. WHEN el operario solicita reprocesar un monitoreo, THE Monitoring_Service SHALL validar que el monitoreo está en estado terminal `completed` o `error`, que `video_path` es no nulo y que el archivo existe en disco.
2. IF las precondiciones de reprocesamiento no se cumplen, THEN THE Monitoring_Service SHALL rechazar el reprocesamiento sin modificar el monitoreo.
3. WHILE el Monitoring, TODOS sus Snapshots relacionados, TODOS sus Detection_Inspection_Results relacionados y su Monitoring_Metrics relacionado tienen `remote_sync_status` distinto de `synced`, THE Monitoring_Service SHALL permitir el reprocesamiento destructivo.
4. IF el Monitoring, ALGÚN Snapshot relacionado, ALGÚN Detection_Inspection_Result relacionado o el Monitoring_Metrics relacionado tienen `remote_sync_status` igual a `synced` (una sola entidad sincronizada basta), THEN THE Monitoring_Service SHALL bloquear el reprocesamiento con un mensaje claro y abstenerse de alterar cualquier dato local o remoto.
5. WHEN el reprocesamiento inicia y no está bloqueado por sincronización, THE Monitoring_Service SHALL limpiar los resultados previos (snapshots, resultados de inspección, métricas y artefactos derivados) del monitoreo.
6. WHEN el reprocesamiento inicia, THE Monitoring_Service SHALL reinicializar el estado del monitoreo a `analyzing` mediante un reset controlado que no ejecuta una transición directa desde un estado terminal.
7. WHEN el reprocesamiento se ejecuta, THE Video_Analysis_Service SHALL procesar el mismo video con la configuración indicada sin modificar ni eliminar el video original.
8. IF otro monitoreo del mismo módulo está activo o un reprocesamiento ya está en curso, THEN THE Monitoring_Service SHALL rechazar el reprocesamiento.

> **Nota de alcance:** Spec 019 no añade eliminación remota (DELETE) ni *tombstones* en el proveedor de sincronización (Spec 017 tampoco propaga el DELETE local a Supabase). La regla mínima segura es **estricta**: reprocesar libremente solo mientras NINGUNA de las entidades relevantes (Monitoring, Snapshots, Detection_Inspection_Results, Monitoring_Metrics) esté sincronizada; **una sola** entidad con `remote_sync_status == "synced"` basta para bloquear el reproceso destructivo. La propagación de eliminaciones o re-sync tras reproceso queda fuera de alcance.

### Requirement 10: Persistencia y artefactos

**User Story:** Como equipo de tesis, quiero que los artefactos se organicen de forma trazable y con rutas seguras, para mantener la integridad de la evidencia y su reprocesamiento.

#### Acceptance Criteria

1. THE Video_First_System SHALL almacenar el video original en `outputs/monitorings/{id}/video/monitoring.mp4`.
2. THE Video_First_System SHALL persistir el campo `video_path` del monitoreo como ruta relativa desde la raíz del proyecto.
3. THE persistencia SHALL definir el campo `video_path` del monitoreo como nullable, permaneciendo nulo hasta que exista un video grabado.
4. WHEN se recibe una ruta de video para abrir, leer o borrar, THE Video_First_System SHALL validar y sanear la ruta contra path traversal mediante el Path_Sanitizer antes de acceder al archivo.
5. THE domain layer SHALL representar el campo `video_path` del monitoreo sin dependencias de SQLAlchemy.
6. WHEN `DatabaseManager.init_db()` se ejecuta y la columna `video_path` está ausente en la tabla `monitorings`, THE Video_First_System SHALL ejecutar una migración idempotente `ALTER TABLE monitorings ADD COLUMN video_path VARCHAR(500)` verificando la ausencia de la columna mediante `PRAGMA table_info(monitorings)`.
7. WHEN `DatabaseManager.init_db()` se ejecuta y la columna `video_path` ya existe, THE Video_First_System SHALL abstenerse de repetir la migración de la columna (operación no-op).
8. WHEN la migración de `video_path` se ejecuta sobre una base de datos existente sin la columna, THE Video_First_System SHALL preservar intactas todas las filas existentes (mismo conteo y contenido) tras crear la columna.
9. THE Video_First_System SHALL soportar la creación de la columna `video_path` en instalación nueva, en base de datos Raspberry existente sin la columna y en reinicios posteriores.
10. THE Video_First_System SHALL abstenerse de depender de `Base.metadata.create_all()` como mecanismo para añadir la columna `video_path` a una tabla `monitorings` existente, dado que `create_all()` por sí solo no añade columnas a tablas ya creadas.

### Requirement 11: Configuración de muestreo por perfil

**User Story:** Como equipo de tesis, quiero parametrizar el muestreo disperso por perfil de ejecución, para ajustar cobertura y rendimiento según el hardware sin recompilar valores fijos.

#### Acceptance Criteria

1. THE Execution_Profile SHALL exponer parámetros configurables de muestreo disperso, incluyendo el mínimo y el máximo de frames entre detecciones, el uso de Scene Gate y la propagación por Optical Flow.
2. THE Execution_Profile SHALL usar como baseline documentado los valores `sparse_min_frames_between_detections ≈ 5`, `sparse_max_frames_without_detection ≈ 12`, `sparse_use_scene_gate = true` y `sparse_enable_flow_propagation = true`, entendidos como gaps expresados en frames.
3. THE Execution_Profile SHALL documentar que los gaps `min ≈ 5` y `max ≈ 12` son gaps basados en frames cuya equivalencia temporal depende del fps (por ejemplo, sobre `data/videos/video_02.mp4` medido a 30 fps, 165 frames y 720×1280, corresponden aproximadamente a 0.17 s y 0.40 s).
4. THE Video_First_System SHALL abstenerse de fijar los valores de muestreo como constantes definitivas no configurables.
5. WHERE el perfil activo es `edge`, THE Execution_Profile SHALL admitir valores de muestreo más conservadores que el baseline para preservar recall aceptando mayor tiempo de procesamiento.
6. THE Execution_Profile SHALL documentar que el fps de grabación de la Raspberry no está garantizado por la configuración de la cámara y debe medirse en lugar de asumirse.
7. THE Video_First_System SHALL abstenerse de convertir automáticamente los gaps de frames a intervalos de tiempo y de realizar muestreo basado en segundos en Spec 019, difiriendo dicha conversión a una spec futura y manteniendo los gaps `min ≈ 5` / `max ≈ 12` (legacy) solo como referencia.
8. THE Pipeline_Metrics_File SHALL registrar `source_video_fps`, los gaps de muestreo en frames y la equivalencia temporal aproximada de esos gaps como orientación documentada, sin adoptar una conversión frame-gap→intervalo de tiempo.
9. WHERE se considere independizar el muestreo del fps, THE Execution_Profile SHALL documentar como alternativa abierta y no adoptada la derivación de los gaps a partir de intervalos de tiempo multiplicados por el fps real.

### Requirement 12: Métricas de ejecución por monitoreo

**User Story:** Como equipo de tesis, quiero registrar métricas detalladas de cada análisis junto con su configuración, para comparar configuraciones sobre el mismo video de forma reproducible.

#### Acceptance Criteria

1. WHEN el análisis de un monitoreo termina por cualquier desenlace, THE Video_Analysis_Service SHALL escribir el Pipeline_Metrics_File con las métricas del análisis.
2. THE Pipeline_Metrics_File SHALL incluir duración del video, `source_video_fps`, total de frames, frames procesados, ejecuciones del detector, frames omitidos, ratio de ejecución del detector, conteo por causa de ejecución, tracks únicos, detecciones, tiempo de procesamiento y tiempo promedio del detector.
3. THE Pipeline_Metrics_File SHALL registrar `detector_scheduled_frames`, `analysis_successful_frames` y `analysis_failed_frames`, cumpliéndose `analysis_successful_frames + analysis_failed_frames == detector_scheduled_frames`.
4. THE Pipeline_Metrics_File SHALL registrar `configured_recording_fps`, `container_fps` (= `configured_recording_fps`), `effective_recording_fps` (= `frames_written / recording_duration_seconds`), `deviation_between_configured_and_effective_fps` (diagnóstico = `configured_recording_fps - effective_recording_fps`; el video nunca se reescribe), `source_video_fps`, `min_frames_between_detections`, `max_frames_without_detection`, `frames_written` y `recording_duration_seconds`.
5. THE Pipeline_Metrics_File SHALL registrar la equivalencia temporal aproximada de los gaps de muestreo (gap en frames dividido por el fps aplicable) como orientación documentada.
6. THE Pipeline_Metrics_File SHALL registrar la configuración de análisis utilizada.
7. WHERE dos análisis del mismo video se ejecutan con configuraciones distintas, THE Pipeline_Metrics_File SHALL permitir comparar sus métricas mediante la configuración registrada.

### Requirement 13: Manejo de errores e integridad del video

**User Story:** Como operario, quiero que el sistema maneje fallos sin perder el video grabado y con mensajes accionables, para no perder evidencia ni tiempo de recorrido.

#### Acceptance Criteria

1. IF la cámara no está disponible al iniciar, THEN THE Monitoring_Service SHALL transicionar a `error` y presentar el mensaje "La cámara no está disponible. Verifica la conexión."
2. IF el Video_Recorder no logra abrir con ningún códec candidato, THEN THE Monitoring_Service SHALL transicionar a `error` y registrar el códec en el log.
3. IF el espacio en disco disponible es insuficiente al iniciar, THEN THE Monitoring_Service SHALL bloquear el inicio de la grabación y presentar el mensaje "Espacio en disco bajo ({space} MB)."
4. IF el archivo temporal `monitoring.recording.mp4` resulta corrupto o ilegible al validarse, THEN THE Monitoring_Service SHALL transicionar a `error`, abstenerse de renombrarlo a `monitoring.mp4` y conservar el archivo temporal para inspección.
5. IF ocurre una interrupción inesperada durante la grabación cuyo bloque `finally` sí se ejecuta (terminación controlada), THEN THE Video_Recording_Worker SHALL cerrar el Video_Recorder y validar el archivo temporal, y THE Monitoring_Service SHALL renombrar atómicamente el archivo a `monitoring.mp4` y persistir `video_path` cuando el video parcial resulte válido.
6. IF el cierre del encoder falla, THEN THE Video_Recorder SHALL validar el archivo resultante para decidir si es utilizable, conservando el video en disco.
7. IF el análisis diferido falla de forma fatal, THEN THE Monitoring_Service SHALL transicionar de `analyzing` a `error` manteniendo el video intacto.
8. IF `process_frame()` o una persistencia recuperable relacionada falla en un frame programado (la excepción puede ocurrir incluso dentro de RetinaNet), THEN THE Video_Analysis_Service SHALL contabilizar el frame en `analysis_failed_frames`, conservar el Snapshot ya persistido y continuar con el frame siguiente sin abortar el análisis.
9. WHEN la aplicación reinicia con una sesión activa sin hilo vivo, THE Monitoring_Service SHALL marcar la sesión como `error` conservando el video para su reprocesamiento.
10. WHEN un monitoreo alcanza `completed` con un `monitoring.mp4` validado, THE Video_First_System SHALL garantizar que ese `monitoring.mp4` validado nunca se modifica, elimina ni corrompe como consecuencia de un fallo de análisis posterior.
11. IF ocurre una terminación abrupta (corte de energía, `kill -9`, crash antes de que corra `finally`), THEN THE Video_First_System SHALL abstenerse de garantizar la finalización correcta del contenedor MP4 y, en el reinicio, conservar el `monitoring.recording.mp4` remanente, validarlo de forma segura y promoverlo a `monitoring.mp4` únicamente si resulta válido, conservándolo para diagnóstico si resulta inválido y sin marcarlo automáticamente como válido sin pasar validación.

### Requirement 14: No regresión de capacidades existentes

**User Story:** Como equipo de tesis, quiero que la introducción de video-first no rompa las capacidades existentes, para preservar la estabilidad de la plataforma validada.

#### Acceptance Criteria

1. THE Video_First_System SHALL preservar sin cambios el Capture_Worker y el Snapshot_Analysis_Service, manteniendo el paso de sus tests existentes.
2. THE Video_First_System SHALL mantener operativas la autenticación, el dashboard, los invernaderos, los módulos, el historial de monitoreos, los reportes, la bitácora agrícola, la exportación ZIP, la sincronización manual, la sincronización Supabase, la persistencia SQLite, la interfaz kiosk/táctil y la vista previa de cámara.
3. THE Video_First_System SHALL operar fuera de la Raspberry Pi sin requerir hardware específico para la suite de pruebas.
4. THE Video_Analysis_Service SHALL ser importable sin Detectron2 instalado mediante factories inyectadas.
5. WHEN se ejecuta el flujo de producción, THE Video_First_System SHALL abstenerse de ofrecer selección manual de archivos de video desde `data/videos`.

### Requirement 15: Arquitectura limpia y restricciones de dominio

**User Story:** Como equipo de tesis, quiero que la implementación respete la arquitectura limpia y las restricciones del proyecto, para mantener el código mantenible y trazable.

#### Acceptance Criteria

1. THE app/routes layer SHALL delegar la lógica de negocio en servicios de aplicación sin contener lógica de infraestructura.
2. THE domain layer SHALL permanecer libre de importaciones de SQLAlchemy, OpenCV, PyTorch y Detectron2.
3. THE application layer SHALL permanecer libre de importaciones de `cv2`, confinando la lectura de video con OpenCV al adaptador OpenCvVideoReader de infraestructura detrás del VideoReaderPort, adaptador que posee un único `cv2.VideoCapture` y no accede al estado privado de otra frame source.
4. THE Video_First_System SHALL abstenerse de introducir locomoción, GPIO, motores o navegación autónoma.
5. THE Video_First_System SHALL usar `DEVICE = "cpu"` en toda inferencia sin introducir dependencias nuevas.

### Requirement 16: Runner legacy como herramienta de diagnóstico

**User Story:** Como equipo de tesis, quiero conservar el runner de inspección de video como herramienta de benchmark, para mantener referencia de diagnóstico sin duplicar la lógica de decisión.

#### Acceptance Criteria

1. THE Video_Inspection_Runner SHALL permanecer disponible únicamente como herramienta de benchmark/diagnóstico, fuera del flujo de producción.
2. THE Video_Inspection_Runner SHALL invocar la función Detector_Decision para decidir la ejecución del detector.
3. WHEN el Video_Inspection_Runner se refactoriza para usar la Detector_Decision, THE Video_Inspection_Runner SHALL mantener su comportamiento observable sin cambios.
