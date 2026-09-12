# Requirements Document

## Introduction

Esta especificación define una **vista previa (preview) visual fluida de la cámara** para Tomato Monitor, tanto **antes de iniciar un monitoreo** (pantalla de preparación) como **durante un monitoreo activo**, ejecutándose completamente de forma local sobre una Raspberry Pi 5 con una Raspberry Pi AI Camera IMX500.

El sistema actual opera bajo una arquitectura **video-first** (Spec 019/020): al iniciar un monitoreo, un `VideoRecordingWorker` es el propietario exclusivo de la cámara vía `RaspberryCameraFrameSource` y escribe **todos** los frames leídos a un `VideoRecorder` (`monitoring.mp4`). Finalizada la captura, el video se analiza de manera diferida. El preview durante el monitoreo obtiene una copia thread-safe del último frame del worker (`get_last_frame()`), y el preview previo se obtiene mediante `capture_single_frame()` (abrir–capturar–cerrar Picamera2 por cada frame).

El problema central es que la **cadencia física de la cámara** y la **cadencia de grabación** están hoy **acopladas**: el frame source de monitoreo se construye con `fps = recording_target_fps` (5 FPS en el perfil `edge`) y `camera_mode="video"`, lo que fija físicamente `FrameDurationLimits` a 5 FPS. Como el worker escribe cada frame recibido y actualiza el buffer de preview solo después de escribir, el preview queda limitado a ~5 FPS. Además, el preview previo abre y cierra Picamera2 por cada imagen, lo cual es inadecuado para un flujo continuo.

El objetivo de esta especificación es **desacoplar explícitamente la cadencia visual/captura (~20 FPS) de la cadencia de grabación (propia de cada perfil: EDGE ≈ 5 FPS, FULL ≈ 10 FPS)**, manteniendo un único propietario físico de la cámara, sin duplicar ni interpolar frames, y **sin modificar el pipeline de inferencia ni el análisis diferido**. El video almacenado debe seguir conteniendo la cadencia de grabación actual de su perfil (esta especificación NO unifica ni cambia `recording_target_fps` entre perfiles), y el pipeline existente debe procesarlo sin cambios.

Esta especificación se limita a: adquisición de cámara, preview, muestreo temporal para grabación, transporte del preview hacia la UI, lifecycle/concurrencia del preview, seguridad/ownership de los endpoints de preview y métricas de diagnóstico para validar la nueva separación de cadencias.

## Glossary

- **Camera_Stream_FPS (cadencia de captura/stream)**: Frecuencia física aproximada a la que la cámara produce frames y a la que se alimenta el preview visual. Objetivo inicial: **20 FPS**, común a todos los perfiles. Es un concepto **distinto** de `Recording_FPS` y NO depende del perfil de grabación.
- **Recording_FPS (cadencia de grabación)**: Frecuencia aproximada a la que los frames se persisten en `monitoring.mp4`. Corresponde al actual `recording_target_fps` y es **propia de cada Execution_Profile**: EDGE ≈ 5 FPS, FULL ≈ 10 FPS. Esta especificación NO la unifica entre perfiles.
- **Frame_Source**: Implementación de `FrameSource` que posee físicamente la cámara y encapsula su acceso/configuración. En Raspberry es `RaspberryCameraFrameSource` (Picamera2); en PC de desarrollo es `OpenCvFrameSource`. Es el único responsable del acceso físico a la cámara.
- **Camera_Lock**: Cerrojo (`threading.Lock`) a nivel de módulo (`_camera_lock`) que garantiza que un único componente interactúe con Picamera2/libcamera a la vez (principio *single camera owner*). Incluye el helper `is_camera_locked()` y el tiempo de asentamiento `_CAMERA_SETTLE_SECONDS`, aplicado hoy dentro de `RaspberryCameraFrameSource.release()`.
- **Live_Preview_Manager (Gestor de preview persistente)**: Nombre **conceptual** del componente de lifecycle responsable del preview **previo al monitoreo**: gestiona el ciclo de vida del preview (start/stop idempotente, background capture, buffer del último frame) **componiendo y reutilizando** las abstracciones de cámara existentes (`Frame_Source` / `RaspberryCameraFrameSource`), sin duplicar la lógica de acceso físico a Picamera2. La clase concreta (nombre y ubicación) se selecciona en `design.md`; no se congela aquí.
- **Recording_Sampler (muestreador de grabación)**: Nombre **conceptual** de un componente pequeño y desacoplado cuya única responsabilidad es decidir, mediante **tiempo monotónico**, si un frame real recibido debe persistirse según `Recording_FPS`, sin duplicar ni interpolar. No embebe esta lógica dentro del worker. La clase concreta se selecciona en `design.md`; no se congela aquí.
- **Latest_Preview_Frame (buffer de preview)**: Buffer thread-safe de **un único frame** (el más reciente) usado para servir el preview. No es una cola; descarta frames antiguos (política *latest-frame*).
- **Preview_Transport (transporte de preview)**: Mecanismo por el cual los frames llegan al navegador. La preferencia arquitectónica es un stream HTTP local tipo **MJPEG** (`multipart/x-mixed-replace`), consumible por un `<img>`. El transporte definitivo se decide en `design.md`.
- **Video_Recording_Worker**: Worker de la fase de grabación (Spec 019) que consume el `Frame_Source` y escribe al `VideoRecorder`. Único consumidor/propietario físico durante el monitoreo.
- **Monitoring_Runtime_Registry**: Registro compartido (persistente entre requests) de workers y threads de monitoreo activos.
- **Analysis_Pipeline (pipeline de análisis)**: Conjunto de componentes de inferencia diferida (RetinaNet/Detectron2, ResNet-18 de sanidad, colorimetría de madurez, Scene Gate, ORB, histogramas HSV, Optical Flow, tracking, deduplicación, thresholds, reglas y frecuencia del análisis del video). Fuera del alcance de modificación.
- **Ownership (propiedad de monitoreo)**: Relación por la cual un monitoreo pertenece a un usuario a través de la cadena `Monitoring → Module → Greenhouse → owner_user_id` (Spec 022). Se verifica localmente con los helpers `_monitoring_owned_by_user`.
- **Execution_Profile**: Perfil de configuración (`edge`/`full`) que define parámetros de cámara, grabación y térmicos. Es un dataclass inmutable seleccionado por variable de entorno.

## Requirements

### Requirement 1: Preview fluido previo al monitoreo

**User Story:** Como operario en la pantalla de preparación del monitoreo, quiero ver una vista previa realmente fluida de la cámara, para confirmar el encuadre antes de iniciar la captura.

#### Acceptance Criteria

1. WHEN un operario autenticado carga la pantalla de preparación del monitoreo con la cámara disponible, THE Live_Preview_Manager SHALL abrir y configurar la cámara una única vez y comenzar una captura continua a una cadencia objetivo de `Camera_Stream_FPS`.
2. WHILE el preview previo está activo, THE Live_Preview_Manager SHALL entregar a la UI frames reales provenientes de la cámara de forma continua, aproximándose a `Camera_Stream_FPS`.
3. THE Preview_Transport SHALL entregar únicamente frames reales capturados por la cámara y SHALL NOT duplicar, repetir ni interpolar frames para simular una cadencia mayor.
4. WHILE el preview previo está activo, THE Live_Preview_Manager SHALL NOT abrir ni cerrar Picamera2 por cada frame individual.
5. IF la cámara no está disponible cuando se solicita el preview previo, THEN THE System SHALL informar el estado (no disponible u ocupada) sin dejar el `Camera_Lock` retenido ni threads huérfanos, y SHALL mantener el mensaje actual orientado al operario.
6. WHERE ya exista una abstracción reutilizable adecuada para el preview persistente, THE Design SHALL reutilizarla en lugar de crear un componente nuevo, documentando la decisión.

### Requirement 2: Preview fluido durante el monitoreo

**User Story:** Como operario durante un monitoreo activo, quiero ver una vista previa fluida de lo que la cámara está capturando, para verificar que estoy recorriendo correctamente el módulo.

#### Acceptance Criteria

1. WHILE un monitoreo está en estado `running`, THE Video_Recording_Worker SHALL seguir siendo el único consumidor/propietario físico de la cámara.
2. WHILE un monitoreo está en estado `running`, THE Preview_Transport SHALL servir frames desde memoria (buffer en proceso) y SHALL NOT abrir una segunda cámara, construir una segunda instancia de Picamera2 ni re-adquirir el `Camera_Lock`.
3. WHEN la cámara produce frames a ~`Camera_Stream_FPS` durante el monitoreo, THE Latest_Preview_Frame SHALL actualizarse a la cadencia del stream de cámara y NOT únicamente a la cadencia de grabación.
4. WHEN se recibe un frame de la cámara durante el monitoreo, THE Video_Recording_Worker SHALL actualizar el `Latest_Preview_Frame` de forma independiente de la decisión de persistir dicho frame en el `VideoRecorder`.
5. THE UI de ejecución SHALL poder visualizar el preview de manera fluida sin requerir una segunda fuente de cámara.
6. THE Latest_Preview_Frame SHALL exponerse mediante una copia thread-safe y SHALL NOT compartir memoria mutable con el buffer interno del worker.

### Requirement 3: Desacople de cadencias captura vs grabación

**User Story:** Como equipo de desarrollo, quiero que la cadencia física de la cámara sea independiente de la cadencia de grabación, para lograr preview fluido sin alterar el video ni el análisis.

#### Acceptance Criteria

1. THE System SHALL tratar `Camera_Stream_FPS` (cadencia física/preview, común a todos los perfiles) y `Recording_FPS` (cadencia de video, propia de cada Execution_Profile) como dos parámetros semánticamente separados.
2. WHEN se inicia un monitoreo, THE Frame_Source SHALL configurar la cadencia física de la cámara a partir de `Camera_Stream_FPS` y NOT a partir de `Recording_FPS`.
3. THE Recording_Sampler SHALL ser un componente pequeño y desacoplado cuya única responsabilidad sea decidir, mediante tiempo monotónico, si un frame real recibido debe persistirse; THE lógica de muestreo temporal SHALL NOT embeberse como lógica compleja dentro del `Video_Recording_Worker`.
4. THE Recording_Sampler SHALL seleccionar, aproximadamente cada `1 / Recording_FPS` segundos medidos con tiempo monotónico, el frame real más reciente elegible y escribirlo una única vez al `VideoRecorder`, usando el `Recording_FPS` del perfil activo.
5. THE Recording_Sampler SHALL NOT escribir todos los frames del stream de cámara al `VideoRecorder`.
6. IF durante un intervalo de grabación no existe un frame adecuado (por carga del sistema), THEN THE Recording_Sampler SHALL omitir ese intervalo y SHALL NOT crear frames artificiales, duplicados ni interpolados.
7. WHEN el `Video_Recording_Worker` recibe un frame de la cámara, THE `Video_Recording_Worker` SHALL actuar como orquestador: actualizar el `Latest_Preview_Frame` y consultar al `Recording_Sampler`, escribiendo al `VideoRecorder` solo cuando el sampler lo indique.
8. THE Design SHALL documentar explícitamente cómo se realiza el muestreo temporal y por qué preserva el contenido y la cadencia del video dentro de tolerancias razonables.

### Requirement 4: La grabación conserva el `Recording_FPS` propio de cada perfil

**User Story:** Como equipo de desarrollo, quiero que el video almacenado mantenga la cadencia de grabación actual de cada perfil (EDGE ≈ 5 FPS, FULL ≈ 10 FPS), para que el análisis diferido, el tamaño del video y las métricas existentes no cambien.

#### Acceptance Criteria

1. WHILE la cámara opera a ~`Camera_Stream_FPS`, THE VideoRecorder SHALL persistir `monitoring.mp4` a aproximadamente el `Recording_FPS` del perfil activo.
2. THE System SHALL conservar `Recording_FPS`≈5 en EDGE y `Recording_FPS`≈10 en FULL, y SHALL NOT cambiar silenciosamente la cadencia de grabación de ningún perfil (en particular, SHALL NOT bajar FULL de 10 a 5).
3. WHEN se completa un monitoreo de aproximadamente 20 segundos con `Camera_Stream_FPS`≈20, THE VideoRecorder SHALL haber escrito aproximadamente `20 × Recording_FPS` frames: ~100 en EDGE (`Recording_FPS`≈5) y ~200 en FULL (`Recording_FPS`≈10), sujeto a pequeñas desviaciones operacionales; en ningún caso SHALL escribir ~400 (todos los frames de cámara).
4. THE VideoRecorder SHALL mantener su `fps` de contenedor igual al `Recording_FPS` del perfil (sin recomputar, remux ni re-encode); `recording_target_fps` sigue siendo el FPS nominal del video/contenedor.
5. THE System SHALL NOT resolver esta especificación aumentando `Recording_FPS` para igualar la cadencia de la cámara.

### Requirement 5: Pipeline de análisis intacto

**User Story:** Como equipo de tesis, quiero que el pipeline de inferencia no cambie, para preservar la reproducibilidad y la validez de los resultados existentes.

#### Acceptance Criteria

1. THE Analysis_Pipeline SHALL permanecer sin modificaciones en RetinaNet/Detectron2, clasificación de sanidad (ResNet-18), clasificación/colorimetría de madurez, Scene Gate, ORB, histogramas HSV, Optical Flow, tracking, deduplicación y thresholds de detección.
2. THE System SHALL NOT modificar la lógica, la frecuencia ni las reglas del análisis diferido del video ni los snapshots generados por el análisis.
3. WHEN se inicia el análisis diferido sobre un video finalizado bajo esta especificación, THE Analysis_Pipeline SHALL procesarlo sin requerir cambios en sus componentes.

### Requirement 6: Un único propietario físico de la cámara

**User Story:** Como equipo de desarrollo, quiero que nunca existan dos accesos concurrentes a la IMX500, para evitar errores de libcamera/Picamera2 y bloqueos de cámara.

#### Acceptance Criteria

1. THE System SHALL garantizar que nunca coexistan simultáneamente una instancia de Picamera2 para preview y una instancia de Picamera2 para monitoreo.
2. THE System SHALL NOT utilizar dos procesos Picamera2, dos instancias Picamera2 concurrentes, `cv2.VideoCapture` como segunda fuente física en Raspberry, acceso paralelo independiente al dispositivo ni procesos externos de cámara.
3. THE Camera_Lock SHALL continuar protegiendo el hardware, o evolucionar de forma compatible con el principio de único propietario, sin introducir un segundo cerrojo que permita accesos concurrentes.
4. WHILE el preview previo persistente está activo, THE System SHALL considerar la cámara ocupada para cualquier otro consumidor físico.

### Requirement 7: Transición segura preview → monitoreo

**User Story:** Como operario, quiero pasar de la vista previa al monitoreo sin errores de cámara, para iniciar la captura de forma confiable.

#### Acceptance Criteria

1. WHEN el operario inicia un monitoreo (POST de inicio) desde una pantalla con preview previo activo, THE backend SHALL garantizar de forma server-side y determinista el handoff: detener/revocar el preview previo activo, confirmar la liberación de la cámara y solo entonces iniciar la adquisición por parte del `Video_Recording_Worker`.
2. THE System SHALL NOT depender de la desconexión del navegador (cierre del stream MJPEG) como única garantía de liberación de la cámara al iniciar un monitoreo.
3. THE System SHALL respetar el tiempo de asentamiento existente que `RaspberryCameraFrameSource.release()` ya aplica, y SHALL NOT introducir una segunda espera redundante de `_CAMERA_SETTLE_SECONDS` ni retrasos adicionales innecesarios en la transición.
4. THE System SHALL NOT permitir el solapamiento de las fases de preview y de monitoreo sobre la cámara física (no debe existir ventana en la que ambos posean Picamera2).
5. WHEN se ejecuta la transición preview → monitoreo, THE System SHALL evitar regresiones asociadas a errores conocidos de libcamera/Picamera2 (`Camera in Running state`, errores de allocator, `camera already acquired`).

### Requirement 8: Transporte del preview hacia el navegador

**User Story:** Como operario, quiero que el preview llegue al navegador de forma eficiente y local, para tener una vista fluida sin saturar la Raspberry Pi con peticiones.

#### Acceptance Criteria

1. THE Preview_Transport SHALL funcionar completamente de forma local, sin dependencias runtime de servicios externos, CDN, WebRTC cloud, APIs remotas ni streaming externo.
2. THE Preview_Transport SHALL NOT resolverse simplemente reduciendo el intervalo de polling actual (por ejemplo de 200 ms a 50 ms) de forma que genere ~20 peticiones HTTP por segundo con codificaciones JPEG independientes.
3. THE Design SHALL evaluar e implementar preferentemente un stream HTTP local tipo MJPEG (`Content-Type: multipart/x-mixed-replace`) consumible por un elemento `<img>`, o documentar en `design.md` una alternativa más limpia dentro de la arquitectura existente.
4. THE System SHALL NOT introducir WebSockets ni WebRTC salvo que exista una razón técnica real documentada en `design.md`.
5. WHERE se implemente un endpoint de stream, THE endpoint de preview previo y/o el endpoint de preview de monitoreo SHALL ofrecer el stream sin abrir una segunda cámara durante el monitoreo.
6. THE System SHALL NOT exigir la eliminación del endpoint single-frame existente (`GET /api/camera/preview`); su conservación como fallback/diagnóstico y la migración de la pantalla de preparación al nuevo stream se decidirán en `design.md`.

### Requirement 9: Lifecycle del preview

**User Story:** Como operario, quiero que la cámara no quede bloqueada aunque cierre la pestaña o cambie de pantalla, para poder volver a usarla sin reiniciar la aplicación.

#### Acceptance Criteria

1. WHEN el operario abandona la pantalla de preview, el navegador cierra la conexión, refresca la página o pierde la conexión HTTP, THE System SHALL liberar los recursos de preview asociados cuando corresponda y SHALL NOT dejar la cámara permanentemente bloqueada.
2. IF hay cero clientes activos consumiendo el preview previo, THEN THE System SHALL NOT dejar la cámara tomada de forma indefinida (mediante conteo de suscriptores, seguimiento de conexión, idle timeout corto u otra estrategia segura definida en `design.md`).
3. WHEN un cliente se desconecta del preview mientras otros clientes siguen activos, THE System SHALL NOT afectar incorrectamente a los clientes todavía activos.
4. WHEN un monitoreo finaliza, se aborta o entra en un estado terminal, THE System SHALL detener el preview de monitoreo y liberar sus recursos asociados.
5. THE operación de cleanup del preview SHALL ser idempotente (segura ante múltiples invocaciones y ante invocación antes de iniciar).
6. IF ocurre un error de cámara o de encoder durante el preview, THEN THE System SHALL liberar los recursos asociados sin dejar locks ni threads huérfanos.
7. WHEN la aplicación se cierra, THE System SHALL liberar cualquier recurso de preview activo.

### Requirement 10: Concurrencia y backpressure

**User Story:** Como equipo de desarrollo, quiero que el preview sea seguro ante concurrencia y no acumule memoria, para mantener la estabilidad de la Raspberry Pi.

#### Acceptance Criteria

1. THE System SHALL evitar carreras al iniciar y detener el preview, y SHALL NOT permitir que múltiples clientes creen múltiples cámaras ni que dos streams controlen el hardware de forma independiente.
2. WHERE se implemente un preview previo persistente compartido, THE System SHALL garantizar un único runtime propietario del preview físico: el primer consumidor inicia el preview físico y los consumidores adicionales leen el mismo `Latest_Preview_Frame`, sin crear múltiples cámaras ni múltiples managers de preview independientes.
3. THE Latest_Preview_Frame SHALL mantener únicamente el frame más reciente necesario para mostrar el preview y SHALL NOT acumular una cola ilimitada de frames ni colas individuales por cliente.
4. IF el navegador consume frames más lentamente que la cámara, THEN THE Preview_Transport SHALL descartar frames antiguos y mostrar siempre el frame más reciente, priorizando baja latencia.
5. THE backpressure del preview SHALL NOT afectar los frames seleccionados para grabación.
6. THE acceso concurrente al `Latest_Preview_Frame` SHALL ser thread-safe y SHALL NOT provocar fugas de memoria ni acumulación ilimitada de frames.

### Requirement 11: Rendimiento en Raspberry Pi 5

**User Story:** Como equipo de tesis, quiero que la solución sea eficiente en hardware edge, para que el preview no comprometa la grabación ni la estabilidad térmica.

#### Acceptance Criteria

1. THE System SHALL evitar copias innecesarias de frames, colas grandes, threads sin lifecycle claro, *busy loops* y fugas de memoria.
2. WHERE varios clientes consuman el mismo preview, THE Design SHALL evaluar compartir el trabajo de codificación JPEG en lugar de codificar de forma independiente por cliente, documentando la decisión.
3. THE preview SHALL NOT comprometer la estabilidad del monitoreo: la grabación a `Recording_FPS` tiene prioridad funcional sobre el preview.
4. IF existe presión de recursos, THEN THE preview PODRÁ descartar frames, pero THE grabación SHALL NOT alterarse de forma silenciosa.

### Requirement 12: Gestión térmica preservada

**User Story:** Como equipo de tesis, quiero que la captura a mayor cadencia conviva con el monitoreo térmico actual, para no comprometer la protección del hardware.

#### Acceptance Criteria

1. WHILE un monitoreo está activo, THE System SHALL preservar íntegramente la gestión térmica existente (thresholds, `ThermalMonitor`, pausa/reanudación) sin modificarla.
2. THE preview previo al monitoreo SHALL NOT crear un `ThermalMonitor` ni introducir una nueva política/máquina de pausa térmica como parte de esta especificación.
3. THE System SHALL NOT introducir nuevos umbrales térmicos en esta especificación; la temperatura durante el preview previo SHALL medirse en la validación física (Requirement 18).
4. IF el benchmark físico demuestra que el preview persistente requiere protección térmica adicional, THEN dicha protección SHALL evaluarse a partir de evidencia real en una iteración futura y NOT asumirse ahora.

### Requirement 13: Configuración explícita y validación

**User Story:** Como equipo de desarrollo, quiero configuración clara y validada para las dos cadencias, para evitar reutilizaciones incorrectas y valores mágicos.

#### Acceptance Criteria

1. THE Execution_Profile SHALL exponer un parámetro explícito para `Camera_Stream_FPS` separado de `recording_target_fps` (`Recording_FPS`).
2. THE configuración inicial SHALL ser: EDGE → `camera_stream_fps`=20.0 y `recording_target_fps`=5.0; FULL → `camera_stream_fps`=20.0 y `recording_target_fps`=10.0 (salvo incompatibilidad técnica objetiva a discutir en `design.md`).
3. THE System SHALL NOT reutilizar `recording_target_fps` como cadencia física de la cámara.
4. THE Config_Validation SHALL exigir, por perfil, `Camera_Stream_FPS > 0`, `Recording_FPS > 0` y `Recording_FPS <= Camera_Stream_FPS`.
5. IF la configuración de cadencias es inválida, THEN THE System SHALL rechazarla o degradar a un valor seguro documentado, con un mensaje/registro claro, siguiendo el patrón de perfiles existente.
6. THE System SHALL NOT distribuir valores mágicos de cadencia repartidos entre varios archivos; la configuración SHALL residir en el modelo de perfiles centralizado.

### Requirement 14: Métricas de diagnóstico

**User Story:** Como equipo de tesis, quiero métricas que permitan validar la separación de cadencias en Raspberry, para sustentar la evidencia experimental.

#### Acceptance Criteria

1. THE System SHALL permitir observar, como mínimo: `configured_camera_stream_fps`, `effective_camera_stream_fps`, `configured_recording_fps` (por perfil), `effective_recording_fps`, `camera_frames_produced`, `camera_capture_elapsed_seconds`, `preview_frames_encoded`/`preview_frames_served` (o equivalente), los frames escritos al video (`frames_written`) y `recording_duration_seconds`.
2. THE `effective_camera_stream_fps` SHALL calcularse sobre la ventana de captura activa (medida entre el primer y el último frame recibido, descontando pausas mediante los signals de pausa existentes) usando intervalos (`(camera_frames_produced - 1) / camera_capture_elapsed_seconds`, o 0 si hay menos de dos frames) y NOT como los frames divididos por toda la vida del worker; THE semántica de `effective_recording_fps` existente NOT SHALL cambiarse, y el campo `frames_written` existente NOT SHALL renombrarse.
3. THE métricas SHALL distinguir claramente tres conceptos separados: `camera_frames_produced` (reads de cámara exitosos), `preview_frames_encoded`/`served` (frames codificados/entregados al navegador, que pueden ser menos por backpressure/latest-frame) y los frames escritos al video (`frames_written`); un encode fallido tras un read exitoso incrementa `camera_frames_produced` pero NOT `preview_frames_encoded`.
4. THE métricas PODRÁN ser de runtime/diagnóstico y NOT es obligatorio persistirlas en SQLite.
5. THE métricas SHALL permitir demostrar que `effective_camera_stream_fps` ≈ `Camera_Stream_FPS`, que el preview es perceptiblemente fluido y próximo al objetivo bajo carga normal, y que `effective_recording_fps` ≈ `Recording_FPS` del perfil, sin alterar el análisis.
6. THE System SHALL NOT convertir `preview_frames_served == camera_frames_produced` en una condición obligatoria; el descarte intencional de frames antiguos del preview es un comportamiento esperado.
7. THE System SHALL NOT exponer métricas técnicas del preview (FPS interno, tiempos de codificación) en las cadenas orientadas al operario; SHALL registrarlas o exponerlas solo como diagnóstico.

### Requirement 15: Seguridad y aislamiento multiusuario

**User Story:** Como operario, quiero que solo yo pueda ver el preview de mis monitoreos, para preservar el aislamiento entre usuarios.

#### Acceptance Criteria

1. THE endpoints nuevos de preview/stream SHALL requerir un usuario autenticado, respetando el mecanismo de autenticación existente.
2. WHEN se solicita un preview/stream asociado a un `monitoring_id`, THE System SHALL validar la propiedad del monitoreo por el usuario autenticado (cadena `Monitoring → Module → Greenhouse → owner_user_id`) y SHALL rechazar el acceso si el monitoreo pertenece a otro usuario. Esta corrección incluye el endpoint existente `GET /api/monitoring/{monitoring_id}/preview`, que hoy no valida ownership.
3. THE validación de ownership SHALL residir en un lugar compartido apropiado (dependency, helper de aplicación, método de repositorio o servicio de autorización, según encaje en la arquitectura, a decidir en `design.md`), y `monitoring_api.py` SHALL NOT importar el helper privado `_monitoring_owned_by_user` desde `agricultural_ui.py`.
4. WHEN un usuario solicita un recurso de preview perteneciente a otro usuario, THE System SHALL preservar una semántica tipo *not found* cuando sea apropiado, evitando filtrar la existencia de identificadores de otro usuario.
5. THE System SHALL NOT debilitar el aislamiento multiusuario introducido en la Spec 022.

### Requirement 16: Preservación de comportamientos existentes

**User Story:** Como equipo de desarrollo, quiero que las funcionalidades actuales sigan operando, para que esta especificación no introduzca regresiones.

#### Acceptance Criteria

1. THE System SHALL preservar el inicio de monitoreo, la grabación, la pausa (si existe), la finalización, el abort, la gestión térmica, el análisis diferido manual, el reporte, la sincronización, la recuperación, la exportación y el aislamiento multiusuario.
2. THE modificación del `Video_Recording_Worker` SHALL limitarse exclusivamente a: (a) actualizar el buffer de preview desacoplado de la grabación; (b) consultar el `Recording_Sampler` antes de persistir un frame; (c) incorporar las métricas estrictamente necesarias de esta especificación. THE System SHALL NOT aprovechar esta especificación para refactorizaciones generales del worker, ni modificar sus estados, signals existentes, semántica de START/FINALIZE/ABORT, pausas, thermal pause, cleanup, el `Monitoring_Runtime_Registry` ni el análisis diferido.
3. THE System SHALL NOT alterar el comportamiento del `Camera_Lock`, de las transiciones de estado del monitoreo, del cleanup del frame source ni del cierre del `VideoRecorder`.
4. THE máquina de estados del monitoreo (incluyendo `initializing`, `running`, `paused`, `ready_for_analysis`, `analyzing`, `completed`, `aborted`, `error`) SHALL permanecer sin nuevos estados.
5. THE flujo de START/FINALIZE/ABORT SHALL conservar su semántica actual, incluyendo la liberación de cámara y las reservas del registry.

### Requirement 17: Cobertura de pruebas y regresión

**User Story:** Como equipo de desarrollo, quiero pruebas automatizadas suficientes, para validar la nueva funcionalidad sin ocultar regresiones.

#### Acceptance Criteria

1. THE suite de pruebas SHALL cubrir el frame source según sus responsabilidades reales (adquisición, `read()`, `release()`, `Camera_Lock`, cleanup tras error y configuración física con `Camera_Stream_FPS` + validación de FPS), y SHALL NOT imponer una API de lifecycle `start()/stop()` inexistente en la interfaz `FrameSource` únicamente para satisfacer un test.
2. THE suite de pruebas SHALL cubrir el lifecycle del `Live_Preview_Manager` (start/stop, start/stop repetidos, idempotencia, cleanup y ausencia de una segunda Picamera2 concurrente), diferenciándolo de las responsabilidades del frame source.
3. THE suite de pruebas SHALL cubrir el muestreo de grabación (entrada simulada a ~20 FPS produciendo la salida esperada según el `Recording_FPS` del perfil, jitter en timestamps, entrada más lenta que 20 FPS, ausencia de duplicación, sin drift severo y uso de tiempo monotónico).
4. THE suite de pruebas SHALL cubrir el `Video_Recording_Worker` (actualiza el buffer de preview para los frames relevantes, escribe solo los seleccionados, mantiene el `Recording_FPS` del perfil y libera recursos en `finally`).
5. THE suite de pruebas SHALL cubrir el stream de preview (tipo `multipart/x-mixed-replace` si se usa MJPEG, terminación correcta ante desconexión del cliente, no abrir cámara durante monitoreo y no acumular backlog de frames).
6. THE suite de pruebas SHALL cubrir el lifecycle (pre-preview → start monitoring, monitoring → finalize, monitoring → abort, error de cámara, refresh/close del navegador y start/stop repetidos) y la seguridad (autenticación y ownership del monitoreo).
7. THE suite de pruebas SHALL cubrir las carreras de concurrencia críticas: (a) dos clientes abren el preview previo a la vez → un único frame source/Picamera2; (b) un cliente cierra el stream mientras otro sigue → la cámara no se libera; (c) último cliente se desconecta → idle-timeout libera la cámara; (d) preview activo + START monitoreo + nuevo request de preview-stream durante el handoff → el preview no reacquiere y el monitoreo obtiene la cámara, nunca dos owners; (e) `stop()` durante una lectura en curso → join acotado, release una sola vez, sin deadlock; (f) monitoreo activo + dos clientes de preview de monitoreo → ninguno abre Picamera2, ambos leen del mismo worker.
8. THE suite completa existente SHALL ejecutarse y pasar con el comando oficial del proyecto en PC sin cámara/GPIO/Raspberry; THE tests existentes SHALL NOT modificarse únicamente para ocultar una regresión. Las pruebas que requieran hardware SHALL marcarse con `@pytest.mark.raspberry` o `@pytest.mark.hardware` Y SHALL excluirse mediante el mecanismo real de exclusión del proyecto (no basta el marker), de modo que el comando oficial no intente acceder a hardware.

### Requirement 18: Validación en Raspberry Pi

**User Story:** Como equipo de tesis, quiero un procedimiento de validación real en Raspberry Pi 5, para demostrar el comportamiento con evidencia reproducible.

#### Acceptance Criteria

1. THE especificación SHALL NOT considerarse completamente validada únicamente mediante unit tests en PC.
2. THE procedimiento de validación en Raspberry SHALL medir como mínimo: FPS de preview previo, FPS de preview durante monitoreo, FPS efectivo de cámara, FPS efectivo de grabación, frames escritos, duración del monitoreo, CPU, RAM, temperatura (inicial, durante preview, durante monitoreo y pico) y errores de cámara/libcamera.
3. THE prueba sugerida en EDGE SHALL usar duración 20–30 s, `Camera_Stream_FPS`≈20 y `Recording_FPS`≈5, y en una ventana de 20 s SHALL demostrar aproximadamente `camera_frames_produced`≈400 y `recording_frames_written`≈100.
4. THE validación SHALL centrarse en las propiedades importantes — `effective_camera_stream_fps` ≈ objetivo, preview perceptiblemente fluido y próximo al objetivo bajo carga normal, y `effective_recording_fps` ≈ `Recording_FPS` del perfil — y SHALL NOT exigir que `preview_frames_served`/`encoded` iguale a `camera_frames_produced` (el descarte de frames antiguos del preview es intencional).
5. WHERE se valide FULL, THE conteo esperado de `recording_frames_written` en 20 s SHALL ser ~200 (`Recording_FPS`≈10), no ~100.
6. THE procedimiento SHALL documentar tolerancias razonables en lugar de exigir números exactos de hardware, siguiendo la plantilla de benchmarks existente.

## Out of Scope

Esta especificación NO incluye: dashboard analítico; estimación de cosecha; gestión/cambios de exportaciones; cambios a Supabase; sync; recovery; nuevos modelos de IA; nuevo detector; YOLO; Mask R-CNN; cambio de RetinaNet; nuevo modelo de sanidad; cambios en colorimetría; sensores ambientales; WebRTC; transmisión por Internet; grabación a 20/25/30 FPS; video 4K; streaming remoto; reconocimiento en tiempo real; análisis en tiempo real.

## Behaviors to Preserve (explicit)

- Video-first: el `Video_Recording_Worker` es el único propietario físico de la cámara durante el monitoreo; el análisis es diferido y manual.
- El video `monitoring.mp4` se persiste al `Recording_FPS` propio de cada perfil (EDGE ≈ 5 FPS, FULL ≈ 10 FPS) y alimenta el pipeline existente sin cambios.
- El `Camera_Lock` a nivel de módulo y `_CAMERA_SETTLE_SECONDS` siguen protegiendo el hardware.
- El preview de monitoreo nunca abre una segunda cámara (contrato de `test_019_task14_ui_preview.py` y `test_monitoring_preview_loop.py`).
- `get_last_frame()` devuelve una copia; el `VideoRecordingWorker` libera recursos en `finally`.
- Las transiciones de estado y las reservas del `Monitoring_Runtime_Registry` (Spec 020) permanecen intactas.
- El aislamiento multiusuario (Spec 022) no se debilita.
