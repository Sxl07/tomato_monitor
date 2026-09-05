# Requirements Document

## Introduction

**Feature: 020 — Deferred Manual Analysis Workflow**

Esta feature modifica el **ciclo operativo y energético** del monitoreo video-first entregado por Spec 019. NO modifica el pipeline de visión (RetinaNet, tracking, salud, madurez), que ya está validado en Raspberry Pi 5. El problema que resuelve es distinto del rendimiento del detector: es la **separación explícita entre la fase de adquisición y la fase de inferencia como eventos operativos independientes controlados por el operario**.

**Problema.** En el flujo actual (Spec 019), cuando el operario finaliza la grabación, el sistema **valida el video, lo promueve a `monitoring.mp4`, libera la cámara e inicia automáticamente el análisis diferido** (transición `running → analyzing`). Este arranque automático es indeseable para el prototipo portátil porque las dos fases tienen perfiles de consumo y térmicos muy distintos, y el operario no tiene oportunidad de cambiar de fuente de energía antes de que empiece la carga pesada de inferencia.

**Evidencia real (Monitoring 23, Raspberry Pi 5, perfil EDGE).** La fase de captura duró 46.19 s (223 frames, 960×720, 5 fps nominal, temperatura pico 61.5 °C). La fase de análisis, sobre el mismo video, duró 570.65 s (49 frames de detector, 116 detecciones, 29 tracks únicos, 25 pausas térmicas acumulando 235.14 s, temperatura pico 82.9 °C, estado `completed`, 0 errores). Esto evidencia que la adquisición es una operación ligera y breve, mientras que el análisis es una operación pesada, prolongada y térmicamente exigente. Durante la captura el dispositivo puede alimentarse con una batería portátil; el análisis conviene ejecutarlo con una fuente de energía adecuada.

**Objetivo.** Cambiar el flujo para que finalizar la grabación **no** inicie el análisis automáticamente. El flujo objetivo es:

```
monitoring/recording → finalizar grabación → persistir y validar video crudo → liberar cámara
  → ready_for_analysis → (el operario cambia a una fuente de energía adecuada)
    → el operario selecciona explícitamente "Iniciar análisis" → analyzing → completed | error
```

El video crudo (`monitoring.mp4`) permanece como la **fuente de verdad**. El nuevo estado conceptual `ready_for_analysis` representa: captura finalizada correctamente, video persistido y validado, cámara liberada, análisis aún no iniciado y monitoreo recuperable tras reinicio. Este estado **no** es una "pausa"; es un estado estable, no terminal, persistido y recuperable.

**Recuperación tras reinicio (requisito crítico).** Debe ser posible: grabar → finalizar → `ready_for_analysis` → apagar/reiniciar → reabrir la aplicación → el monitoreo permanece en `ready_for_analysis` → el operario presiona "Iniciar análisis" → el análisis continúa normalmente. Esto contrasta con el comportamiento actual de reconciliación al arranque, que transiciona todo estado no terminal (incluido `analyzing`) a `error`; `ready_for_analysis` debe quedar exento de esa reconciliación.

**Fuente de energía (nota importante).** El software NO puede afirmar de forma confiable que la Raspberry Pi está conectada específicamente a la fuente oficial. Esta spec NO diseña una detección falsa de "fuente oficial". La condición de energía adecuada se aborda con dos mecanismos complementarios: (1) un **preflight técnico** antes del análisis (video disponible y válido, sin análisis concurrente, estado correcto, temperatura dentro de rango seguro y, SI existe un mecanismo disponible, ausencia de subtensión actual) y (2) una **confirmación explícita del operario** de que el dispositivo está conectado a una fuente de energía adecuada.

**Reutilización estricta.** La feature REUTILIZA la arquitectura existente y NO reinventa el `Video_Analysis_Service` ni el pipeline de visión. El único análisis que cambia es **cuándo** y **cómo** se dispara: pasa de automático (al finalizar) a manual (acción explícita del operario tras un preflight y una confirmación).

**Alcance del cambio respecto al flujo capture-first legacy.** Esta spec modifica intencionalmente el flujo **video-first** de Spec 019: finalizar la grabación ya NO lanza el análisis automáticamente. El flujo **capture-first legacy** (fallback usado cuando `video_first_enabled=False`) permanece SIN cambios en sus estados, transiciones y salidas observables. Toda mención a "flujo preexistente que se preserva" se refiere al capture-first legacy, no al flujo video-first, que cambia deliberadamente aquí.

**Valores concretos.** Este documento evita fijar valores numéricos (tiempos máximos, dimensiones, contraste, ventanas de tiempo). Cualquier valor concreto que resulte técnicamente necesario se decide en la fase de diseño y debe ser configurable.

**Fuera de alcance.** Cambios en RetinaNet, ajuste de umbrales, umbral EDGE 0.60, exposición, resolución 960×720, scheduler disperso, Scene Gate, Optical Flow, modelo de salud, algoritmo de madurez, dashboard, rediseño de sincronización Supabase, robot, motores/GPIO y detección automática de "fuente oficial". Esta spec no reescribe el `Video_Analysis_Service`; preserva el pipeline de Spec 019 sin cambios de comportamiento.

## Glossary

- **Deferred_Analysis_System**: conjunto de servicios de aplicación e infraestructura que implementan la separación entre finalizar la grabación y el inicio manual del análisis, incluyendo el nuevo estado `ready_for_analysis`, el preflight y la confirmación del operario.
- **Monitoring_Service**: servicio de aplicación existente que orquesta el ciclo de vida del monitoreo (start, finalize, analysis, reprocess) y las transiciones de estado.
- **Monitoring_Status_FSM**: máquina de estados del monitoreo definida en `MonitoringStatus`/`MonitoringState`. Estados existentes: `initializing`, `running`, `paused`, `finishing`, `analyzing`, `completed`, `aborted`, `error`. Esta spec añade el estado `ready_for_analysis`.
- **ready_for_analysis**: estado no terminal, persistido y recuperable que representa que la captura finalizó correctamente, el video quedó persistido y validado, la cámara fue liberada y el análisis aún no ha iniciado.
- **Video_Recording_Worker**: worker existente (Spec 019), propietario exclusivo de la cámara durante la grabación; produce el video sin inferencia.
- **Video_Analysis_Service**: servicio existente (Spec 019) que ejecuta la inferencia diferida sobre `monitoring.mp4`. Esta spec NO modifica su lógica de análisis; solo cambia el momento de su invocación.
- **Video_Recorder**: componente existente (Spec 019) que valida el archivo grabado y realiza la promoción atómica `monitoring.recording.mp4 → monitoring.mp4`.
- **monitoring.mp4**: video crudo final y validado; fuente de verdad del monitoreo.
- **video_path**: campo del monitoreo (nullable, ruta relativa) que apunta al `monitoring.mp4` validado.
- **Analysis_Preflight**: verificación técnica que se ejecuta antes de iniciar el análisis manual y determina si es seguro y posible comenzarlo.
- **Power_Source_Confirmation**: confirmación explícita del operario de que el dispositivo está conectado a una fuente de energía adecuada para el análisis. Es una confirmación por intento (in-memory, asociada a la solicitud de inicio actual): no se persiste, no es reutilizable, no sobrevive a un reinicio y no constituye una autorización con ventana de tiempo. Cada nuevo intento manual de iniciar el análisis requiere su propia confirmación explícita.
- **Thermal_Monitor**: monitor existente que lee la temperatura de CPU vía `vcgencmd measure_temp` y opera en modo no-op fuera de la Raspberry Pi.
- **Undervoltage_Probe**: mecanismo OPCIONAL de detección de subtensión actual. Su disponibilidad no está garantizada; el preflight lo consulta solo si existe.
- **MonitoringRuntimeRegistry**: registro en memoria existente que rastrea workers/threads y provee reclamos de FINALIZACIÓN (`claim_finalization` / `release_finalization` / `is_finalization_claimed`). Actualmente NO provee un reclamo de análisis ni una consulta global de captura activa. Si se necesita un reclamo exclusivo para el análisis, la decisión de extender este registro o generalizar el mecanismo existente corresponde al diseño. El método existente `has_live_worker_for_module` es **por módulo** y NO es suficiente como fuente global de "captura activa"; para la coordinación device-global se añade una consulta thread-safe **global** `has_active_capture()` (o nombre equivalente coherente con el código) que detecta una captura activa con independencia del módulo y es testeable fuera de la Raspberry Pi.
- **Analysis_Exclusion_Mechanism**: mecanismo thread-safe de exclusión atómica que garantiza exactamente un análisis concurrente por monitoreo. Su implementación concreta (extender el MonitoringRuntimeRegistry con reclamos de análisis, generalizar el mecanismo existente u otra alternativa) es una decisión de diseño; este documento no la prescribe. La exclusión tiene tres alcances complementarios, todos realizados vía el MonitoringRuntimeRegistry: (1) por monitoreo (un análisis concurrente por monitoreo), (2) por módulo (una sesión activa por módulo según el Active_Status_Set) y (3) global del dispositivo (un único análisis pesado activo en toda la Raspberry Pi, ver Device_Analysis_Coordination).
- **Device_Analysis_Coordination**: conjunto de reglas de coordinación de recursos a nivel de dispositivo: una captura activa (detectada mediante la consulta global `has_active_capture()` del MonitoringRuntimeRegistry, independiente del módulo) bloquea el inicio de un análisis diferido; un análisis pesado activo bloquea el inicio de una nueva captura; se admite como máximo un análisis pesado activo en toda la Raspberry Pi a la vez; el estado `ready_for_analysis` no es bloqueante (no consume cámara ni cómputo pesado y no impide iniciar recorridos de otros módulos); la coordinación reutiliza el MonitoringRuntimeRegistry existente y no introduce un segundo coordinador global independiente. `is_camera_locked()` puede conservarse como safety net de hardware pero NO es la única fuente de la coordinación device-global.
- **Startup_Reconciliation**: rutina existente (`reconcile_orphaned_sessions_on_startup`) que, al arrancar, transiciona a `error` los monitoreos no terminales huérfanos (sin hilo vivo).
- **Active_Status_Set**: conjunto único de estados considerados "activos" (no terminales) para la regla de una sesión activa por módulo. Incluye exactamente: `initializing`, `running`, `paused`, `finishing`, `ready_for_analysis`, `analyzing`. Todas las reglas de concurrencia y de una sola sesión activa por módulo referencian este conjunto de forma consistente.
- **Pipeline_Metrics_File**: archivo `pipeline_metrics.json` con métricas de ejecución y configuración utilizada por el análisis.
- **operario**: farmer autenticado que transporta el dispositivo por el invernadero.

## Requirements

### Requirement 1: Transición al finalizar la captura

**User Story:** Como operario, quiero que al finalizar la grabación el monitoreo quede en un estado listo para analizar y no arranque el análisis por sí solo, para poder cambiar de fuente de energía antes de la inferencia pesada.

#### Acceptance Criteria

1. WHEN el operario finaliza la grabación de un monitoreo en estado `running`, THE Monitoring_Service SHALL transicionar el monitoreo al estado `ready_for_analysis` en lugar de `analyzing`.
2. WHEN un monitoreo transiciona a `ready_for_analysis`, THE Monitoring_Service SHALL abstenerse de lanzar automáticamente cualquier hilo o proceso de análisis diferido.
3. THE Monitoring_Status_FSM SHALL admitir únicamente la transición `ready_for_analysis → analyzing` cuando el operario la dispara explícitamente mediante la acción "Iniciar análisis".
4. THE Monitoring_Status_FSM SHALL admitir la transición `running → ready_for_analysis`.
5. THE Monitoring_Status_FSM SHALL admitir la transición `ready_for_analysis → analyzing`.
6. THE Monitoring_Status_FSM SHALL admitir la transición `ready_for_analysis → error`.
7. THE Monitoring_Status_FSM SHALL admitir la transición `ready_for_analysis → aborted` únicamente ante cancelación explícita del operario.
8. THE Monitoring_Status_FSM SHALL clasificar `ready_for_analysis` como estado no terminal.

### Requirement 2: Persistencia del estado ready_for_analysis

**User Story:** Como equipo de tesis, quiero que el estado `ready_for_analysis` quede persistido en la base de datos, para que sobreviva a cierres, reinicios y cortes de energía.

#### Acceptance Criteria

1. WHEN un monitoreo alcanza el estado `ready_for_analysis`, THE Monitoring_Service SHALL escribir el valor `ready_for_analysis` en el campo de estado del registro del monitoreo en SQLite y confirmar la transacción antes de retornar el control.
2. THE Deferred_Analysis_System SHALL aceptar `ready_for_analysis` como uno de los valores válidos del campo de estado del modelo de persistencia del monitoreo, reutilizando el esquema definido en Spec 019 sin agregar columnas, tablas ni migraciones adicionales.
3. WHEN la aplicación consulta un monitoreo cuyo estado persistido es `ready_for_analysis`, THE Monitoring_Service SHALL devolver el valor `ready_for_analysis` idéntico al leído desde SQLite, sin recalcularlo ni modificarlo.
4. WHEN la aplicación se reinicia o se recupera tras un cierre o corte de energía y consulta un monitoreo cuyo último estado persistido era `ready_for_analysis`, THE Monitoring_Service SHALL devolver el estado `ready_for_analysis` recuperado desde SQLite.
5. IF la escritura del estado `ready_for_analysis` en SQLite falla, THEN THE Monitoring_Service SHALL conservar el estado previo del monitoreo sin cambios parciales y reportar un error que indique que la persistencia del estado no se completó.

### Requirement 3: Validación del video antes de la transición

**User Story:** Como operario, quiero que el sistema confirme que el video quedó bien grabado antes de declarar el monitoreo listo para analizar, para no perder evidencia del recorrido.

#### Acceptance Criteria

1. WHEN el operario finaliza la grabación, THE Deferred_Analysis_System SHALL validar el archivo de grabación aplicando en orden los cuatro criterios de validación (el archivo existe en disco, su tamaño es mayor que 0 bytes, se abre correctamente y se lee al menos 1 frame) antes de transicionar a `ready_for_analysis`.
2. WHEN los cuatro criterios de validación resultan satisfactorios, THE Deferred_Analysis_System SHALL promover atómicamente `monitoring.recording.mp4` a `monitoring.mp4` y persistir `video_path` como ruta relativa desde la raíz del proyecto antes de transicionar a `ready_for_analysis`.
3. IF al menos uno de los cuatro criterios de validación falla al finalizar la grabación, THEN THE Monitoring_Service SHALL abstenerse de transicionar a `ready_for_analysis`, conservar el archivo temporal `monitoring.recording.mp4` sin modificarlo para diagnóstico, transicionar el monitoreo al estado `error` y registrar una indicación de error identificando el criterio de validación incumplido.
4. IF la operación de promoción atómica de `monitoring.recording.mp4` a `monitoring.mp4` falla, THEN THE Monitoring_Service SHALL abstenerse de transicionar a `ready_for_analysis`, conservar el archivo temporal para diagnóstico y transicionar el monitoreo al estado `error`.
5. THE Monitoring_Service SHALL abstenerse de transicionar a `ready_for_analysis` mientras `video_path` no apunte a un archivo `monitoring.mp4` que haya superado los cuatro criterios de validación.

### Requirement 4: Liberación de la cámara

**User Story:** Como operario, quiero que la cámara quede liberada en cuanto termina la grabación, para poder iniciar otras operaciones y para no bloquear la cámara durante la espera del análisis.

#### Acceptance Criteria

1. WHEN el operario finaliza la grabación, THE Video_Recording_Worker SHALL liberar la cámara antes de que el monitoreo alcance el estado `ready_for_analysis`.
2. WHILE un monitoreo permanece en estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL mantener la cámara liberada sin reabrirla.
3. WHEN un monitoreo está en estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL abstenerse de requerir la cámara para iniciar el análisis, dado que el análisis opera sobre `monitoring.mp4`.
4. IF la liberación de la cámara falla al finalizar la grabación, THEN THE Video_Recording_Worker SHALL registrar el fallo, marcar la cámara como no liberada e impedir la transición al estado `ready_for_analysis`.
5. WHEN la cámara es liberada por el Video_Recording_Worker, THE Video_Recording_Worker SHALL dejar la cámara disponible para una nueva apertura por parte de otro componente sin requerir reinicio de la aplicación.

### Requirement 5: No arranque automático del análisis

**User Story:** Como operario, quiero que finalizar la grabación nunca dispare la inferencia por sí solo, para controlar cuándo empieza la carga pesada según la fuente de energía disponible.

#### Acceptance Criteria

1. WHEN el operario finaliza la grabación con un video que superó la validación, THE Monitoring_Service SHALL abstenerse de lanzar el Video_Analysis_Service de forma automática.
2. WHILE un monitoreo permanece en estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL mantener cero hilos de análisis activos para ese monitoreo hasta recibir una acción explícita de inicio de análisis del operario.
3. WHEN un monitoreo alcanza `ready_for_analysis`, THE Monitoring_Service SHALL abstenerse de registrar un hilo de análisis en el MonitoringRuntimeRegistry hasta que el operario ejecute la acción explícita de inicio de análisis.
4. WHEN un monitoreo alcanza `ready_for_analysis`, THE Video_Recording_Worker SHALL haber liberado la cámara antes de completar la transición.
5. IF el operario finaliza la grabación con 0 frames capturados, THEN THE Monitoring_Service SHALL abstenerse de transicionar a `ready_for_analysis`, transicionar el monitoreo al estado `error` y registrar una indicación de que no hay contenido para analizar.

### Requirement 6: Inicio manual del análisis

**User Story:** Como operario, quiero iniciar el análisis con una acción explícita cuando el dispositivo ya esté conectado a una fuente adecuada, para ejecutar la inferencia en condiciones apropiadas.

#### Acceptance Criteria

1. WHEN el operario selecciona "Iniciar análisis" sobre un monitoreo en estado `ready_for_analysis`, el Analysis_Preflight resulta satisfactorio y la Power_Source_Confirmation está presente, THE Monitoring_Service SHALL transicionar el monitoreo al estado `analyzing` y lanzar el Video_Analysis_Service sobre el archivo `monitoring.mp4` previamente validado.
2. THE Monitoring_Service SHALL lanzar el Video_Analysis_Service reutilizando la lógica de análisis existente de Spec 019 sin modificar su comportamiento de inferencia.
3. IF el operario solicita iniciar el análisis sobre un monitoreo cuyo estado no es `ready_for_analysis`, THEN THE Monitoring_Service SHALL rechazar la solicitud, conservar el estado actual del monitoreo sin modificar ningún campo y presentar un mensaje de error que indique que el monitoreo no está listo para análisis.
4. IF el operario selecciona "Iniciar análisis" y el Analysis_Preflight falla o la Power_Source_Confirmation está ausente, THEN THE Monitoring_Service SHALL rechazar la solicitud, conservar el estado `ready_for_analysis` sin modificar el monitoreo y presentar un mensaje de error que indique la causa del rechazo.
5. WHEN el análisis se lanza manualmente, THE Monitoring_Service SHALL registrar el hilo de análisis en el MonitoringRuntimeRegistry para su seguimiento y SHALL proteger el inicio mediante el mecanismo de exclusión atómica que garantiza exactamente un análisis concurrente por monitoreo.
6. IF el registro del hilo de análisis en el MonitoringRuntimeRegistry falla, THEN THE Monitoring_Service SHALL revertir el monitoreo al estado `ready_for_analysis`, no lanzar el Video_Analysis_Service y presentar un mensaje de error que indique que el análisis no pudo iniciarse.

### Requirement 7: Preflight técnico antes del análisis

**User Story:** Como operario, quiero que el sistema verifique condiciones técnicas seguras antes de iniciar la inferencia, para evitar arrancar el análisis en un estado inseguro o inconsistente.

#### Acceptance Criteria

1. WHEN el operario solicita iniciar el análisis, THE Analysis_Preflight SHALL verificar que el monitoreo está en estado `ready_for_analysis`.
2. WHEN el operario solicita iniciar el análisis, THE Analysis_Preflight SHALL verificar que `video_path` es no nulo, que el archivo `monitoring.mp4` existe en disco, que su tamaño es mayor a 0 bytes, y que la ruta resuelta permanece dentro del directorio de salidas del monitoreo sin componentes de path traversal (`../`, rutas absolutas o enlaces que escapen del directorio permitido).
3. WHEN el operario solicita iniciar el análisis, THE Analysis_Preflight SHALL verificar que no existe otro análisis en curso para el mismo monitoreo ni otra sesión activa del mismo módulo.
4. WHILE el Thermal_Monitor puede leer la temperatura actual, WHEN el operario solicita iniciar el análisis, THE Analysis_Preflight SHALL verificar que la temperatura actual es menor o igual al umbral máximo seguro configurado en el perfil activo (edge/full).
5. IF el Thermal_Monitor no puede leer la temperatura actual, THEN THE Analysis_Preflight SHALL abstenerse de bloquear el análisis por temperatura y SHALL registrar que la verificación térmica no estuvo disponible.
6. WHERE existe un Undervoltage_Probe disponible, THE Analysis_Preflight SHALL verificar la ausencia de subtensión actual antes de permitir el inicio del análisis.
7. WHERE no existe un Undervoltage_Probe disponible, THE Analysis_Preflight SHALL abstenerse de bloquear el análisis por subtensión y SHALL delegar la garantía de energía en la Power_Source_Confirmation del operario.
8. IF alguna verificación del Analysis_Preflight falla, THEN THE Monitoring_Service SHALL rechazar el inicio del análisis, conservar el estado `ready_for_analysis` sin modificar los datos del monitoreo, y presentar un mensaje accionable en español que identifique la condición no cumplida y la acción de recuperación sugerida.
9. THE Analysis_Preflight SHALL abstenerse de afirmar que el dispositivo está conectado a una fuente de energía específica u oficial.

### Requirement 8: Confirmación de fuente de energía por el operario

**User Story:** Como operario, quiero confirmar explícitamente que conecté una fuente de energía adecuada antes de iniciar la inferencia, para asumir la responsabilidad de la condición que el software no puede verificar.

#### Acceptance Criteria

1. WHEN el operario intenta iniciar el análisis desde el estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL recibir la Power_Source_Confirmation como un campo de solicitud OPCIONAL con valor por defecto `false` (o equivalente), de modo que la ausencia de la confirmación NO produzca un rechazo automático del framework (por ejemplo, un 422) antes de ejecutar la lógica de la aplicación, y SHALL requerir que el operario exprese la confirmación mediante una acción afirmativa deliberada antes de transicionar a `analyzing`.
2. IF el operario intenta iniciar el análisis con la Power_Source_Confirmation ausente o en `false`, THEN THE Monitoring_Service SHALL evaluar la confirmación en la lógica de la aplicación, abstenerse de iniciar el análisis, conservar el estado `ready_for_analysis`, abstenerse de crear un hilo de análisis, abstenerse de retener cualquier exclusión atómica, y presentar un mensaje de error controlado en español que indique que se requiere confirmar la fuente de energía para continuar.
3. THE Deferred_Analysis_System SHALL presentar el texto de la confirmación en español, con texto legible conforme a las reglas de UI del proyecto, indicando que el dispositivo debe estar conectado a una fuente de energía adecuada, sin usar lenguaje de robot, autonomía, chasis o motores.
4. THE Deferred_Analysis_System SHALL abstenerse de presentar la Power_Source_Confirmation como una detección automática de la fuente de energía, requiriendo en todo momento la acción explícita del operario.

### Requirement 9: Análisis único y no concurrente

**User Story:** Como equipo de tesis, quiero que nunca se ejecuten dos análisis del mismo monitoreo a la vez, para evitar corrupción de resultados y condiciones de carrera.

#### Acceptance Criteria

1. WHEN el operario inicia el análisis de un monitoreo, THE Monitoring_Service SHALL proteger el inicio manual del análisis mediante un mecanismo thread-safe de exclusión atómica que garantice exactamente un análisis concurrente por monitoreo, antes de lanzar el hilo de análisis. La decisión de extender el MonitoringRuntimeRegistry con reclamos de análisis o de generalizar el mecanismo existente corresponde al diseño.
2. IF ya existe un análisis en curso protegido por el mecanismo de exclusión atómica para el mismo monitoreo, THEN THE Monitoring_Service SHALL rechazar la nueva solicitud de análisis, conservar el estado del monitoreo sin modificarlo y devolver una indicación de error que señale que ya hay un análisis en curso.
3. IF existe otra sesión de monitoreo del mismo módulo cuyo estado pertenece al Active_Status_Set (`initializing`, `running`, `paused`, `finishing`, `ready_for_analysis`, `analyzing`), excluyendo de la búsqueda el propio `monitoring_id` del monitoreo cuyo análisis se intenta iniciar, THEN THE Monitoring_Service SHALL rechazar el inicio del análisis, conservar el estado del monitoreo sin modificarlo y devolver una indicación de error que señale el conflicto de sesión en el módulo.
4. WHEN el hilo de análisis finaliza por cualquier desenlace (completed, aborted o error), THE Monitoring_Service SHALL liberar la exclusión atómica asociada al monitoreo al terminar el hilo.
5. IF la liberación de la exclusión atómica falla, THEN THE Monitoring_Service SHALL registrar el fallo y dejar el mecanismo en un estado que permita su liberación posterior sin bloquear indefinidamente futuros análisis del mismo monitoreo.

### Requirement 10: Recuperación tras reinicio

**User Story:** Como operario, quiero que un monitoreo listo para analizar siga estándolo después de apagar o reiniciar el equipo, para iniciar la inferencia cuando ya tenga la fuente de energía adecuada.

#### Acceptance Criteria

1. WHILE un monitoreo persistido está en estado `ready_for_analysis`, THE Startup_Reconciliation SHALL abstenerse de transicionarlo a `error` durante la reconciliación de arranque.
2. WHEN la aplicación reinicia con un monitoreo en `ready_for_analysis`, THE Deferred_Analysis_System SHALL conservar el estado `ready_for_analysis` y el `video_path` validado, verificando que el `monitoring.mp4` referenciado sigue existiendo en disco sin borrarlo ni modificarlo.
3. WHEN la aplicación reinicia y un monitoreo en `ready_for_analysis` no tiene hilo de análisis en memoria, THE Deferred_Analysis_System SHALL tratar esa ausencia como esperada y no como una sesión huérfana.
4. WHEN el operario selecciona "Iniciar análisis" sobre un monitoreo recuperado en `ready_for_analysis`, THE Monitoring_Service SHALL ejecutar el mismo flujo de preflight, confirmación y lanzamiento de análisis que para un monitoreo no reiniciado.
5. THE Active_Status_Set SHALL incluir `ready_for_analysis` para preservar la regla de una sesión activa por módulo mientras el análisis está pendiente.
6. IF tras reinicio el `monitoring.mp4` referenciado por un monitoreo en `ready_for_analysis` no existe o no es legible, THEN THE Deferred_Analysis_System SHALL conservar el estado `ready_for_analysis` y presentar el error al intentar iniciar el análisis conforme al manejo de video ausente o corrupto.

### Requirement 11: Video ausente o corrupto

**User Story:** Como operario, quiero que el sistema me impida iniciar el análisis si el video no está disponible o está dañado, para no lanzar una inferencia condenada a fallar.

#### Acceptance Criteria

1. IF `video_path` es nulo cuando el operario intenta iniciar el análisis, THEN THE Monitoring_Service SHALL rechazar el inicio del análisis, conservar el estado `ready_for_analysis` sin transicionar a `analyzing`, y presentar el mensaje "El monitoreo no tiene un video asociado para analizar."
2. IF el archivo `monitoring.mp4` no existe en disco cuando el operario intenta iniciar el análisis, THEN THE Monitoring_Service SHALL rechazar el inicio del análisis, conservar el estado `ready_for_analysis` sin transicionar a `analyzing`, y presentar el mensaje "El archivo de video del monitoreo no existe en disco."
3. IF el archivo `monitoring.mp4` existe pero no puede abrirse o no permite leer al menos un frame válido, THEN THE Monitoring_Service SHALL rechazar el inicio del análisis, conservar el estado `ready_for_analysis` sin transicionar a `analyzing`, y presentar el mensaje en español "El archivo de video del monitoreo no es legible. Verifica que el archivo no esté dañado."
4. WHEN el Monitoring_Service rechaza el inicio del análisis por video ausente o corrupto, THE Monitoring_Service SHALL conservar sin modificación los datos y metadatos previamente capturados del monitoreo.
5. WHEN el Monitoring_Service rechaza el inicio del análisis por video ausente o corrupto, THE Monitoring_Service SHALL permitir al operario reintentar el inicio del análisis sin reiniciar la aplicación.

### Requirement 12: Error al iniciar el análisis

**User Story:** Como operario, quiero que si el lanzamiento del análisis falla el monitoreo no quede en un estado ambiguo, para reintentar de forma segura.

#### Acceptance Criteria

1. IF el lanzamiento del hilo de análisis falla antes de que el análisis comience a procesar el primer frame, THEN THE Monitoring_Service SHALL liberar toda exclusión atómica adquirida para el inicio del análisis y conservar el monitoreo en estado `ready_for_analysis`.
2. IF el lanzamiento del hilo de análisis falla, THEN THE Monitoring_Service SHALL presentar al operario un mensaje de error que indique que el análisis no pudo iniciarse y ofrecer la acción de reintento, sin descartar el video capturado previamente.
3. WHEN el monitoreo permanece en estado `ready_for_analysis` tras un lanzamiento fallido, THE Monitoring_Service SHALL habilitar la acción "Iniciar análisis" para que el operario reintente.
4. IF el Monitoring_Service rechaza el inicio del análisis por preflight o confirmación, THEN THE Monitoring_Service SHALL abstenerse de dejar retenida cualquier exclusión atómica del análisis y conservar el monitoreo en su estado previo al intento.

### Requirement 13: Error durante el análisis

**User Story:** Como equipo de tesis, quiero que un fallo durante la inferencia deje el monitoreo en un estado terminal claro sin perder el video, para poder diagnosticar y reprocesar.

#### Acceptance Criteria

1. IF el análisis diferido falla de forma fatal, THEN THE Monitoring_Service SHALL transicionar el monitoreo de `analyzing` a `error` conservando el `monitoring.mp4` intacto.
2. WHEN el análisis transiciona a `error`, THE Deferred_Analysis_System SHALL abstenerse de modificar o eliminar el `monitoring.mp4` validado.
3. WHEN el análisis termina por cualquier desenlace, THE Video_Analysis_Service SHALL escribir el Pipeline_Metrics_File con las métricas del análisis.
4. WHEN el monitoreo transiciona a `error`, THE Monitoring_Service SHALL registrar un indicador de error consultable asociado al monitoreo que identifique la causa del fallo, sin exponer detalles técnicos internos al operario.
5. IF ocurre un fallo no fatal durante la inferencia de un frame individual, THEN THE Deferred_Analysis_System SHALL descartar ese frame, continuar con el siguiente y conservar los resultados de inferencia ya persistidos de los frames procesados con éxito.
6. IF el intento de escribir el Pipeline_Metrics_File falla, THEN THE Video_Analysis_Service SHALL preservar la transición de estado del monitoreo (`error` o `completed`) sin revertirla y registrar un indicador de fallo de escritura de métricas.

### Requirement 14: Estado completado

**User Story:** Como operario, quiero que el análisis exitoso lleve el monitoreo a completado con su reporte, para consultar las métricas agrícolas del recorrido.

#### Acceptance Criteria

1. WHEN el análisis diferido procesa con éxito todo el recorrido del video (sin frames pendientes), THE Monitoring_Service SHALL transicionar el monitoreo de `analyzing` a `completed`.
2. WHEN el monitoreo transiciona a `completed`, THE Monitoring_Service SHALL persistir las métricas agregadas del monitoreo (total de tomates, conteo y porcentaje de sanos/no sanos, y distribución porcentual por las 6 etapas de madurez USDA) mediante el mecanismo existente de Spec 019 antes de exponer el reporte.
3. WHEN el monitoreo transiciona a `completed`, THE Monitoring_Service SHALL registrar el timestamp de finalización (`completed_at`) del monitoreo.
4. THE Monitoring_Status_FSM SHALL clasificar `completed` como estado terminal, rechazando toda transición posterior desde `completed` hacia cualquier otro estado.
5. WHILE el análisis diferido está en curso (estado `analyzing`), THE Monitoring_Service SHALL exponer el estado del monitoreo como `analyzing` y no como `completed`.
6. IF la persistencia de las métricas agregadas falla durante la transición a `completed`, THEN THE Monitoring_Service SHALL transicionar el monitoreo a `error` y conservar los resultados de inferencia ya persistidos, indicando el fallo al operario.

### Requirement 15: Compatibilidad con monitoreos existentes

**User Story:** Como equipo de tesis, quiero que los monitoreos creados antes de esta feature sigan funcionando, para preservar la estabilidad de la plataforma validada.

#### Acceptance Criteria

1. THE Deferred_Analysis_System SHALL preservar el flujo capture-first legacy (fallback usado cuando `video_first_enabled=False`) conservando su FSM, su lógica interna, su análisis y sus resultados/artefactos, con la ÚNICA excepción nueva de que el inicio de una nueva sesión capture-first PUEDE ser rechazado por el guard device-global cuando hay un análisis pesado activo en el dispositivo (ver Requirement 21). Una vez ADMITIDA (iniciada) una sesión capture-first, su comportamiento permanece idéntico al previo (sin cambios en sus transiciones, análisis ni salidas). THE Deferred_Analysis_System SHALL modificar intencionalmente el flujo video-first de Spec 019 de modo que finalizar la grabación ya no lance el análisis automáticamente, transicionando en su lugar a `ready_for_analysis`.
2. WHEN se ejecuta la suite completa de pruebas automatizadas preexistente, THE Deferred_Analysis_System SHALL abstenerse de introducir nuevas regresiones respecto a la línea base previa a Spec 020 y SHALL abstenerse de introducir nuevos errores de recolección o ejecución. El fallo de línea base preexistente y conocido `tests/unit/test_sync_routes.py::TestSyncLocalTrigger::test_post_with_pending_records_triggers_export` queda FUERA DE ALCANCE: puede permanecer sin cambios y Spec 020 SHALL abstenerse de intentar corregirlo.
3. THE Deferred_Analysis_System SHALL preservar el mecanismo de reprocesamiento controlado existente de Spec 019 sobre monitoreos terminales, manteniéndolo FUERA del nuevo flujo de `ready_for_analysis`, sin introducir el estado `ready_for_analysis` en dicho flujo y sin convertir las transiciones `completed → analyzing` o `error → analyzing` en transiciones estándar de la Monitoring_Status_FSM.
4. WHEN un monitoreo en estado terminal (`completed`, `aborted`, `error`) creado antes de esta feature es consultado o transicionado, THE Deferred_Analysis_System SHALL mantener válidas sus transiciones y consultas sin requerir el estado `ready_for_analysis`.
5. THE Deferred_Analysis_System SHALL mantener operativas, sin cambios de comportamiento observable, la autenticación, el dashboard, los invernaderos, los módulos, el historial de monitoreos, los reportes, la bitácora agrícola, la exportación ZIP, la sincronización manual, la persistencia SQLite y la interfaz kiosk/táctil.
6. IF un monitoreo terminal preexistente es consultado y no contiene el estado `ready_for_analysis`, THEN THE Deferred_Analysis_System SHALL devolver sus datos y métricas sin error y sin requerir migración de estado.
7. THE Deferred_Analysis_System SHALL ejecutar la suite completa de pruebas automatizadas en entornos distintos a la Raspberry Pi sin requerir cámara, GPIO ni hardware específico.

### Requirement 16: Idempotencia de las acciones

**User Story:** Como operario, quiero que repetir accidentalmente una acción (finalizar o iniciar análisis) no produzca efectos duplicados, para operar el dispositivo con seguridad en campo.

#### Acceptance Criteria

1. WHEN el operario finaliza la grabación de un monitoreo que ya está en estado `ready_for_analysis`, THE Monitoring_Service SHALL abstenerse de repetir la promoción del video, mantener el monitoreo en estado `ready_for_analysis` sin registrar una segunda transición, y responder con una indicación de éxito equivalente a la primera finalización.
2. WHEN el operario selecciona "Iniciar análisis" sobre un monitoreo que ya está en estado `analyzing`, THE Monitoring_Service SHALL abstenerse de lanzar un segundo hilo de análisis y devolver una indicación de que el análisis ya está en curso, conservando el hilo de análisis existente sin interrupción.
3. WHEN dos o más solicitudes de inicio de análisis para el mismo monitoreo se reciben de forma concurrente, THE Monitoring_Service SHALL conceder la exclusión atómica a una sola solicitud e iniciar exactamente un hilo de análisis.
4. IF una solicitud de inicio de análisis se recibe mientras otra idéntica ya obtuvo la exclusión atómica, THEN THE Monitoring_Service SHALL rechazar la solicitud duplicada con una indicación de que el análisis ya fue reclamado, sin alterar ni reiniciar el análisis en curso ni modificar el estado del monitoreo.

### Requirement 17: Comportamiento de la interfaz

**User Story:** Como operario, quiero que la interfaz muestre claramente que el monitoreo está listo para analizar y ofrezca la acción de iniciar el análisis, para saber qué hacer a continuación.

#### Acceptance Criteria

1. WHILE un monitoreo está en estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL presentar un indicador en español que comunique que la captura terminó y el análisis está pendiente, sin usar la palabra "pausado", con texto legible y contraste suficiente conforme a las reglas de UI del proyecto.
2. WHILE un monitoreo está en estado `ready_for_analysis`, THE Deferred_Analysis_System SHALL exponer una única acción primaria etiquetada "Iniciar análisis" disponible y habilitada, con un área táctil adecuada conforme a las reglas de UI del proyecto.
3. WHEN el operario pulsa la acción "Iniciar análisis", THE Deferred_Analysis_System SHALL solicitar la Power_Source_Confirmation antes de iniciar el análisis diferido.
4. IF el operario rechaza o cancela la Power_Source_Confirmation, THEN THE Deferred_Analysis_System SHALL abstenerse de iniciar el análisis diferido y mantener el monitoreo en estado `ready_for_analysis` con la acción "Iniciar análisis" disponible.
5. WHILE el análisis diferido está en curso, THE Deferred_Analysis_System SHALL presentar el progreso del análisis y ocultar la acción "Iniciar análisis".
6. THE Deferred_Analysis_System SHALL presentar todos los textos de esta interfaz en español y sin lenguaje de robot, autonomía, chasis o motores.

### Requirement 18: Trazabilidad y métricas

**User Story:** Como equipo de tesis, quiero registrar cuándo la captura terminó y cuándo el análisis inició de forma diferida, para documentar el ciclo operativo y energético con evidencia reproducible.

#### Acceptance Criteria

1. WHEN un monitoreo transiciona a `ready_for_analysis`, THE Deferred_Analysis_System SHALL registrar el instante de finalización de la captura (`capture_completed_at` o equivalente) como marca temporal UTC de forma durable, de modo que sobreviva a un apagado o reinicio ocurrido entre la captura y el análisis y sea recuperable tras el reinicio. THE Deferred_Analysis_System SHALL abstenerse de reutilizar `completed_at` para este instante, dado que `completed_at` conserva su significado actual (finalización completa del monitoreo tras el análisis). El lugar concreto de almacenamiento de esta marca es una decisión de diseño.
2. WHEN un monitoreo transiciona a `ready_for_analysis`, THE Deferred_Analysis_System SHALL abstenerse de registrar `manual_deferred_analysis=true` y `deferred_analysis_started_at`, dado que esas marcas NO se escriben en la finalización de la captura.
3. WHEN el inicio manual del análisis ha sido aceptado correctamente (Analysis_Preflight satisfactorio, Power_Source_Confirmation presente y exclusión atómica adquirida), THE Deferred_Analysis_System SHALL registrar `manual_deferred_analysis=true`, `deferred_analysis_started_at` (marca temporal UTC durable y recuperable tras un reinicio) y, WHERE la lectura de temperatura está disponible, la temperatura observada por el preflight en grados Celsius (valor nulo si no está disponible), preservando la separación temporal respecto de `capture_completed_at`.
4. IF el hilo de análisis no logra arrancar y se hace rollback a `ready_for_analysis`, THEN THE Deferred_Analysis_System SHALL abstenerse de dejar metadata que afirme falsamente que el análisis comenzó, sin persistir `manual_deferred_analysis=true` ni `deferred_analysis_started_at` para ese intento fallido.
5. THE Pipeline_Metrics_File SHALL registrar el indicador `manual_deferred_analysis` únicamente cuando el análisis diferido manual fue efectivamente iniciado, y no de forma automática al finalizar la captura.

### Requirement 19: Restricciones de energía y seguridad operativa

**User Story:** Como operario, quiero que el sistema me guíe hacia condiciones de energía adecuadas sin hacer afirmaciones que no puede sostener, para tomar una decisión informada sobre cuándo analizar.

#### Acceptance Criteria

1. WHEN el operario solicita iniciar la fase de análisis diferido, THE Deferred_Analysis_System SHALL recibir la Power_Source_Confirmation como un campo de solicitud OPCIONAL con valor por defecto `false` (o equivalente), de modo que la ausencia de la confirmación NO produzca un rechazo automático del framework (por ejemplo, un 422) antes de ejecutar la lógica de la aplicación, y SHALL condicionar el inicio a que el Analysis_Preflight haya finalizado con resultado satisfactorio Y a que la Power_Source_Confirmation explícita del operario esté presente en el intento de inicio actual. La Power_Source_Confirmation pertenece al intento de inicio en curso: no se persiste, no es reutilizable, no sobrevive a un reinicio y no admite ventana de tiempo; cada nuevo intento manual requiere su propia confirmación explícita.
2. IF el Analysis_Preflight no ha finalizado con resultado satisfactorio O la Power_Source_Confirmation está ausente o es `false`, THEN THE Deferred_Analysis_System SHALL evaluar la confirmación en la lógica de la aplicación, rechazar el inicio del análisis, conservar el estado `ready_for_analysis`, abstenerse de crear un hilo de análisis, abstenerse de retener cualquier exclusión atómica, preservar el video capturado sin modificarlo, y presentar un mensaje de error controlado y accionable en español indicando la condición faltante.
3. THE Deferred_Analysis_System SHALL abstenerse de implementar una detección automática de "fuente oficial" de la Raspberry Pi.
4. WHERE el Undervoltage_Probe está disponible y detecta subtensión actual, THE Analysis_Preflight SHALL rechazar el inicio del análisis, mantener el estado del monitoreo sin transición a la fase de análisis, y presentar un mensaje accionable en español indicando revisar la fuente de energía.
5. WHERE el Undervoltage_Probe no está disponible, THE Analysis_Preflight SHALL continuar la evaluación de preflight sin bloquear por subtensión y registrar la condición de sonda no disponible.
6. THE Deferred_Analysis_System SHALL abstenerse de bloquear la fase de captura por condiciones de energía, dado que la captura es la fase ligera y el objetivo es diferir únicamente la fase pesada de análisis.

### Requirement 20: Preservación del pipeline de Spec 019

**User Story:** Como equipo de tesis, quiero que esta feature no toque el pipeline de visión ni la lógica de análisis validada, para mantener intactos los resultados y la reproducibilidad de Spec 019.

#### Acceptance Criteria

1. THE Deferred_Analysis_System SHALL reutilizar el Video_Analysis_Service existente sin modificar su lógica de detección, tracking, salud, madurez ni su decisión de muestreo disperso.
2. THE Deferred_Analysis_System SHALL abstenerse de modificar RetinaNet, sus umbrales, la exposición, la resolución de grabación, el Scene Gate, el Optical Flow, el modelo de salud y el algoritmo de madurez.
3. THE Deferred_Analysis_System SHALL abstenerse de reescribir el Video_Analysis_Service o duplicar su lógica de análisis.
4. WHEN el análisis se inicia de forma diferida sobre una entrada con la misma configuración usada en Spec 019, THE Video_Analysis_Service SHALL producir el mismo conjunto de artefactos (snapshots anotados, crops, CSV de detección y `pipeline_metrics.json`) que produce en el flujo de Spec 019.
5. WHEN el análisis diferido finaliza sobre una entrada con la misma configuración usada en Spec 019, THE Video_Analysis_Service SHALL producir métricas agregadas (conteo total de tomates, porcentajes de salud y porcentajes por etapa de madurez USDA) idénticas a las del flujo de Spec 019.
6. THE Deferred_Analysis_System SHALL ejecutar toda inferencia con `DEVICE = "cpu"` sin introducir dependencias nuevas ni lenguaje de robot, motores o navegación autónoma.
7. IF cualquier operación del análisis diferido intenta abrir la cámara mientras otro componente es el owner exclusivo, THEN THE Deferred_Analysis_System SHALL rechazar la apertura y preservar el estado de la sesión sin alterar los artefactos ya capturados, indicando la condición mediante un error observable.

### Requirement 21: Coordinación de captura y análisis a nivel de dispositivo

**User Story:** Como operario, quiero poder iniciar nuevos recorridos de otros módulos mientras hay monitoreos con video pendiente de analizar, y que el dispositivo evite ejecutar una captura y un análisis pesado al mismo tiempo, para aprovechar el equipo sin saturar sus recursos ni provocar conflictos.

#### Acceptance Criteria

1. THE Deferred_Analysis_System SHALL permitir que varios monitoreos de distintos módulos coexistan simultáneamente en estado `ready_for_analysis`, dado que `ready_for_analysis` no consume cámara ni cómputo pesado y no bloquea el inicio de nuevos recorridos de otros módulos.
2. IF existe una captura activa en el dispositivo (detectada mediante la consulta thread-safe global `has_active_capture()` del MonitoringRuntimeRegistry, con independencia del módulo), THEN THE Deferred_Analysis_System SHALL rechazar el inicio de un análisis diferido y presentar un mensaje accionable en español indicando que hay una captura en curso.
3. IF existe un análisis pesado activo en el dispositivo, THEN THE Deferred_Analysis_System SHALL rechazar el inicio de una nueva captura (start de una nueva sesión de grabación, tanto en el flujo video-first como en el capture-first legacy) y presentar un mensaje accionable en español indicando que hay un análisis en curso. Este rechazo al iniciar la sesión es la ÚNICA restricción nueva sobre el flujo capture-first legacy; una sesión ya admitida no se ve afectada por lo demás.
4. THE Deferred_Analysis_System SHALL permitir como máximo un análisis pesado activo en toda la Raspberry Pi a la vez.
5. WHEN una captura o un análisis termina por cualquier desenlace, THE Deferred_Analysis_System SHALL volver a dejar disponible el recurso del dispositivo para la siguiente operación.
6. THE Deferred_Analysis_System SHALL abstenerse de bloquear el inicio de nuevos recorridos únicamente porque existan otros monitoreos con videos pendientes en estado `ready_for_analysis`.
7. THE Device_Analysis_Coordination SHALL reutilizar el MonitoringRuntimeRegistry existente (incluida la consulta global `has_active_capture()`) y SHALL abstenerse de introducir un segundo coordinador global independiente. El método por módulo `has_live_worker_for_module` NO es suficiente como fuente global de captura activa, y `is_camera_locked()` solo puede usarse como safety net de hardware, no como única fuente de la coordinación device-global.
