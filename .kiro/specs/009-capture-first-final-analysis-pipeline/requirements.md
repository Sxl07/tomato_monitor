# Requirements Document

## Introduction

Este documento especifica los requisitos para reestructurar el pipeline de monitoreo live del sistema Tomato Monitor, separando la **fase de captura** de la **fase de análisis**. El problema actual es que el `MonitoringWorker` ejecuta inferencia (Detectron2 ~7-8 segundos) dentro del loop de captura, bloqueando la adquisición de frames. En 30 segundos de recorrido manual, solo se capturan unos pocos snapshots.

La solución establece un flujo de dos fases:
1. **Fase de captura**: adquisición rápida de snapshots sin inferencia, guiada por Scene Gate.
2. **Fase de análisis**: procesamiento diferido de todos los snapshots capturados (detección, tracking, deduplicación, salud, madurez) para generar el reporte final.

### Justificación de estados — Agregar ANALYZING de forma aditiva

Se agrega el estado `analyzing` de forma **estrictamente aditiva**:
- Se **mantienen** `PAUSED` y `FINISHING` en el enum y en `VALID_TRANSITIONS` para compatibilidad con registros existentes, endpoints y tests previos.
- Se **agrega** `ANALYZING` como nuevo estado con sus propias transiciones.
- No se elimina ninguna transición existente en esta iteración.

### Decisión: Progreso de análisis — Opción A (runtime, sin migración BD)

El progreso del análisis (`analysis_processed`, `analysis_total`) se mantiene **en memoria** dentro del runtime registry:
- El endpoint de status consulta el progreso desde el registry.
- Al completar, se persisten solo las métricas finales en `MonitoringMetrics`.
- Si el servidor reinicia durante `analyzing`, orphan reconciliation marca error y preserva snapshots raw.
- **No se agregan columnas a la base de datos** para progreso intermedio.

### Decisión: Tracking — conteo en memoria, persistencia de resultados finales

El modelo `InspectionResultModel` (SQLAlchemy) actualmente **no tiene campo `track_id`**. La entidad de dominio `FruitDetection` sí lo tiene. Para evitar migraciones de BD:
- El conteo por tracks únicos se calcula **en memoria** durante el análisis usando `SimpleTracker`.
- Se persiste un `DetectionInspectionResult` por cada detección final (después de deduplicación).
- Las métricas finales (`total_tomatoes`) reflejan tracks únicos, no detecciones brutas.
- No se agrega `track_id` a la tabla `inspection_results` en esta iteración.

### Decisión: Análisis incluye madurez por defecto

Dado que la nueva arquitectura elimina el bloqueo de inferencia durante captura:
- `analysis_skip_maturity=False` por defecto — el reporte completo incluye madurez.
- Si se necesita saltar madurez por temperatura o rendimiento, queda como configuración de perfil registrada en métricas.

## Glossary

- **Sistema_Monitoreo**: El sistema completo de monitoreo Tomato Monitor ejecutándose en Raspberry Pi 5.
- **Capture_Worker**: Componente que ejecuta el loop de captura de frames en hilo daemon, sin ejecutar inferencia.
- **Analysis_Service**: Componente responsable de procesar los snapshots capturados ejecutando detección, tracking, deduplicación, clasificación de salud y estimación de madurez.
- **Scene_Gate**: Mecanismo de detección de cambio de escena basado en ORB + histograma HSV que determina si un frame merece ser guardado como snapshot.
- **Fase_Captura**: Período durante el cual el sistema adquiere snapshots de la cámara sin ejecutar modelos de inferencia.
- **Fase_Análisis**: Período posterior a la captura durante el cual el sistema procesa los snapshots guardados con modelos de inferencia.
- **Snapshot_Crudo**: Imagen JPEG capturada durante la fase de captura, sin anotaciones ni procesamiento de inferencia.
- **Snapshot_Anotado**: Imagen generada durante la fase de análisis que incluye bounding boxes, IDs de track y etiquetas de salud/madurez.
- **SimpleTracker**: Componente de tracking por IoU y distancia de centroide que asigna IDs persistentes a detecciones entre snapshots consecutivos.
- **DeduplicationPolicy**: Política de dominio que decide si un track debe ser reprocesado o reutilizar resultados previos.
- **InspectionPolicy**: Política de dominio que decide si ejecutar clasificación de salud y/o estimación de madurez para una detección.
- **Finalizar_Captura**: Acción del operador que indica que el recorrido terminó y el sistema debe proceder al análisis. NO representa cancelación ni abort.
- **Pipeline_Metrics**: Archivo JSON que registra métricas de rendimiento separadas por fase (captura y análisis).
- **Operador**: El agricultor que opera el sistema mediante la pantalla táctil DSI 7".
- **ThermalMonitor**: Componente que registra temperatura del SoC y puede pausar operaciones si se supera un umbral crítico.

## Requirements

### Requirement 1: Separación de fases captura y análisis

**User Story:** Como operador, quiero que el sistema capture imágenes rápidamente durante mi recorrido y las analice después, para que no se pierdan tomates por bloqueos de procesamiento.

#### Acceptance Criteria

1. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Capture_Worker SHALL capturar frames y guardar snapshots sin ejecutar modelos de inferencia (Detectron2, ResNet health, maturity estimator).
2. WHEN el Sistema_Monitoreo transiciona al estado `analyzing`, THE Analysis_Service SHALL procesar todos los Snapshots_Crudos persistidos durante la Fase_Captura, ejecutando detección, tracking, deduplicación, clasificación de salud y estimación de madurez, en orden ascendente de `frame_index`.
3. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Sistema_Monitoreo SHALL rechazar toda invocación a `run_inference()`, `run_inference_timed()`, `run_detection()`, `predict_health()` y `estimate_maturity_for_crop()`, de modo que ninguna de estas funciones se ejecute durante la Fase_Captura.
4. WHEN el Sistema_Monitoreo transiciona al estado `analyzing` y el conteo de Snapshots_Crudos persistidos es cero, THEN THE Analysis_Service SHALL omitir el procesamiento de inferencia y THE Sistema_Monitoreo SHALL transicionar directamente al estado `completed` con métricas en cero.
5. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Capture_Worker SHALL persistir cada Snapshot_Crudo en almacenamiento local antes de continuar al siguiente frame, garantizando que los snapshots estén disponibles para la Fase_Análisis independientemente de cuándo ocurra la transición de estado.

### Requirement 2: Rendimiento del loop de captura

**User Story:** Como operador, quiero que el sistema capture suficientes imágenes durante mi recorrido de 30 segundos, para obtener cobertura visual completa del módulo.

#### Acceptance Criteria

1. THE Capture_Worker SHALL completar cada iteración del loop de captura en un tiempo menor a 500 milisegundos (excluyendo sleep de throttling).
2. WHILE el Sistema_Monitoreo se encuentra en estado `running` y el Operador realiza un recorrido de 30 segundos con movimiento continuo, THE Capture_Worker SHALL capturar un mínimo de 8 snapshots (asumiendo parámetros por defecto de `min_seconds_between_snapshots=1.0`).
3. THE Capture_Worker SHALL controlar la frecuencia del loop mediante el parámetro configurable `capture_loop_fps` (valor por defecto: 5.0 Hz).

### Requirement 3: Scene Gate durante captura

**User Story:** Como operador, quiero que el sistema evite guardar imágenes duplicadas pero sin ser tan restrictivo que pierda tomates, para obtener cobertura eficiente del módulo.

#### Acceptance Criteria

1. WHEN el Scene_Gate determina que la escena cambió respecto al último snapshot guardado, THE Capture_Worker SHALL guardar el frame actual como Snapshot_Crudo.
2. WHEN transcurren más de `max_seconds_without_snapshot` segundos sin capturar un snapshot, THE Capture_Worker SHALL forzar la captura del frame actual independientemente del resultado del Scene_Gate.
3. WHEN no han transcurrido al menos `min_seconds_between_snapshots` segundos desde el último snapshot, THE Capture_Worker SHALL omitir la evaluación del Scene_Gate y continuar al siguiente frame.
4. THE Capture_Worker SHALL evaluar el Scene_Gate usando resolución reducida configurable (`gate_resolution`, valor por defecto: (240, 240)).

### Requirement 4: Persistencia de snapshots durante captura

**User Story:** Como operador, quiero que cada imagen capturada se guarde de forma confiable con sus metadatos, para que el análisis posterior tenga toda la información necesaria.

#### Acceptance Criteria

1. WHEN el Capture_Worker decide guardar un snapshot, THE Sistema_Monitoreo SHALL persistir la imagen JPEG en la ruta `outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg`.
2. WHEN el Capture_Worker persiste un snapshot, THE Sistema_Monitoreo SHALL crear un registro Snapshot en la base de datos con `monitoring_id`, `image_path`, `frame_index`, `captured_at` y `has_detections=False`.
3. WHEN el Capture_Worker persiste un snapshot exitosamente, THE Sistema_Monitoreo SHALL incrementar el contador `total_snapshots` del Monitoring y emitir el conteo actualizado para la UI.

### Requirement 5: Flujo de finalización de captura

**User Story:** Como operador, quiero presionar un botón para indicar que terminé mi recorrido y que el sistema proceda a analizar las imágenes, sin que esto se interprete como cancelación.

#### Acceptance Criteria

1. WHEN el Operador presiona el botón "Finalizar captura", THE Sistema_Monitoreo SHALL señalizar al Capture_Worker para detener el loop de captura y esperar un máximo de 10 segundos a que el loop finalice su iteración actual antes de proceder.
2. WHEN el Capture_Worker detiene su loop de captura tras la señal de finalización, THE Sistema_Monitoreo SHALL liberar la cámara invocando `frame_source.release()` antes de transicionar al siguiente estado.
3. WHEN la cámara se libera exitosamente tras finalización y el Monitoring tiene al menos 1 snapshot capturado, THE Sistema_Monitoreo SHALL transicionar el estado del Monitoring de `running` a `analyzing`.
4. IF la cámara no se libera dentro de 5 segundos tras la señal de finalización, THEN THE Sistema_Monitoreo SHALL transicionar el estado del Monitoring a `error` y registrar el motivo del fallo de liberación.
5. IF el Operador presiona "Finalizar captura" y el Monitoring tiene 0 snapshots capturados, THEN THE Sistema_Monitoreo SHALL transicionar el estado del Monitoring a `completed` con métricas vacías (total_tomatoes=0) sin iniciar la Fase_Análisis.
6. THE Sistema_Monitoreo SHALL distinguir la finalización normal (botón "Finalizar captura") de la cancelación real (acción "Cancelar/Abortar"), reservando el estado `aborted` exclusivamente para cancelaciones explícitas del Operador. Los fallos de cámara, filesystem, base de datos o modelos transicionan a `error`, no a `aborted`.

### Requirement 6: Máquina de estados del monitoreo

**User Story:** Como desarrollador, quiero que la máquina de estados refleje con precisión las fases del pipeline, para mantener trazabilidad y claridad en el código.

#### Acceptance Criteria

1. THE Sistema_Monitoreo SHALL agregar `ANALYZING = "analyzing"` al enum `MonitoringState` de forma aditiva, sin eliminar `PAUSED` ni `FINISHING` del enum ni de las transiciones existentes.
2. THE Sistema_Monitoreo SHALL agregar las siguientes transiciones válidas al conjunto existente: `running → analyzing`, `running → completed` (caso 0 snapshots), `analyzing → completed`, `analyzing → error`.
3. IF se intenta una transición no definida en el conjunto de transiciones válidas, THEN THE Sistema_Monitoreo SHALL rechazar la transición lanzando `InvalidTransitionError` con el estado actual, estado destino y transiciones permitidas.
4. THE Sistema_Monitoreo SHALL tratar `analyzing` como un estado activo (no terminal), permitiendo que la detección de sesiones huérfanas lo considere como sesión en progreso.
5. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Sistema_Monitoreo SHALL mostrar la etiqueta visual "Capturando snapshots" en la interfaz del Operador.
6. WHILE el Sistema_Monitoreo se encuentra en estado `analyzing`, THE Sistema_Monitoreo SHALL mostrar la etiqueta visual "Analizando snapshots..." en la interfaz del Operador.
7. WHILE el Sistema_Monitoreo se encuentra en estado `completed`, THE Sistema_Monitoreo SHALL mostrar la etiqueta visual "Reporte listo" en la interfaz del Operador.
8. THE Sistema_Monitoreo SHALL representar el estado `aborted` con la etiqueta visual "Cancelado" en la interfaz del Operador exclusivamente cuando el Operador cancela explícitamente. Fallos del sistema muestran estado `error`.

### Requirement 7: Fase de análisis — procesamiento de snapshots

**User Story:** Como operador, quiero que el sistema analice todas las imágenes capturadas y produzca un conteo real de tomates con sus clasificaciones, para tomar decisiones informadas sobre mi cultivo.

#### Acceptance Criteria

1. WHEN el Sistema_Monitoreo entra en estado `analyzing`, THE Analysis_Service SHALL cargar la lista de snapshots del Monitoring ordenados por `frame_index` ascendente y cargar los modelos de inferencia (Detectron2, ResNet) una única vez antes de iniciar el loop de procesamiento.
2. IF la lista de snapshots del Monitoring está vacía (0 snapshots capturados), THEN THE Sistema_Monitoreo SHALL transicionar directamente a estado `completed` registrando métricas finales con todos los conteos en cero.
3. WHILE el Sistema_Monitoreo se encuentra en estado `analyzing`, THE Analysis_Service SHALL procesar cada snapshot en orden secuencial ejecutando: detección (Detectron2), tracking (SimpleTracker), evaluación de DeduplicationPolicy, clasificación de salud (ResNet), y estimación de madurez según InspectionPolicy.
4. THE Analysis_Service SHALL mantener el SimpleTracker activo a lo largo de todos los snapshots del Monitoring para asignar IDs de track consistentes entre snapshots consecutivos.
5. THE Analysis_Service SHALL aplicar DeduplicationPolicy para decidir si reprocesar o reutilizar resultados previos de un track, evitando contar el mismo tomate más de una vez.
6. WHEN el Analysis_Service completa el procesamiento de todos los snapshots, THE Sistema_Monitoreo SHALL calcular las métricas finales (total_tomatoes, healthy_count, unhealthy_count, y porcentajes por cada una de las 6 etapas USDA de madurez: green, breaker, turning, pink, light_red, red) basándose en tracks únicos.
7. IF el Analysis_Service encuentra un error recuperable al procesar un snapshot individual (fallo de detección, fallo de clasificación de salud, o fallo de estimación de madurez), THEN THE Analysis_Service SHALL registrar el error, omitir el snapshot fallido, y continuar con el siguiente snapshot sin interrumpir el análisis completo.
8. THE Analysis_Service SHALL incluir estimación de madurez por defecto (`analysis_skip_maturity=False`). IF se configura `analysis_skip_maturity=True` por perfil, THEN la decisión se registra en pipeline_metrics.json.

### Requirement 8: Generación de snapshots anotados

**User Story:** Como operador, quiero ver las imágenes de mis tomates con marcas visuales que indiquen qué detectó el sistema, para verificar la precisión del conteo.

#### Acceptance Criteria

1. WHEN el Analysis_Service procesa un snapshot con detecciones, THE Analysis_Service SHALL generar un Snapshot_Anotado con bounding boxes, IDs de track, etiquetas de salud y etiqueta de madurez superpuestos sobre la imagen original.
2. THE Analysis_Service SHALL guardar cada Snapshot_Anotado en la ruta `outputs/monitorings/{monitoring_id}/annotated_snapshots/snapshot_{frame_index:06d}.jpg`.
3. WHERE el sistema tiene la funcionalidad de crops habilitada, THE Analysis_Service SHALL guardar crops individuales por detección en `outputs/monitorings/{monitoring_id}/crops/snapshot_{frame_index:06d}/track_{track_id:03d}.jpg`.
4. THE Sistema_Monitoreo SHALL actualizar `build_snapshot_gallery()` y la ruta que sirve snapshots al reporte para mostrar preferiblemente Snapshots_Anotados (desde `annotated_snapshots/`), conservando Snapshots_Crudos (en `snapshots/raw/`) para trazabilidad.

### Requirement 9: Conteo por tracking y deduplicación

**User Story:** Como operador, quiero que el conteo final refleje tomates únicos y no cuente el mismo tomate múltiples veces por aparecer en varios snapshots.

#### Acceptance Criteria

1. THE Analysis_Service SHALL utilizar SimpleTracker para asignar IDs de track persistentes a detecciones que aparecen en snapshots consecutivos basándose en IoU y distancia de centroide.
2. THE Analysis_Service SHALL contar el total de tomates únicos como el número de track IDs distintos generados durante el análisis completo del Monitoring.
3. THE Analysis_Service SHALL aplicar InspectionPolicy para determinar qué detecciones reciben clasificación de salud y estimación de madurez, basándose en tamaño de crop y score de detección.
4. THE Analysis_Service SHALL calcular métricas finales en memoria usando tracks únicos y persistir solo los resultados agregados en MonitoringMetrics (sin agregar columna `track_id` a la tabla `inspection_results` en esta iteración).
5. THE Analysis_Service SHALL persistir un `DetectionInspectionResult` por cada detección final después de deduplicación (una detección por track, usando la mejor vista del tomate según DeduplicationPolicy).

### Requirement 10: Estructura de salida y métricas del pipeline

**User Story:** Como investigador de la tesis, quiero que las métricas de rendimiento estén separadas por fase y que los artefactos sigan una estructura predecible, para documentar y reproducir resultados.

#### Acceptance Criteria

1. THE Sistema_Monitoreo SHALL organizar los artefactos de salida bajo la estructura: `outputs/monitorings/{monitoring_id}/snapshots/raw/`, `outputs/monitorings/{monitoring_id}/annotated_snapshots/`, `outputs/monitorings/{monitoring_id}/crops/`, `outputs/monitorings/{monitoring_id}/reports/`, `outputs/monitorings/{monitoring_id}/pipeline_metrics.json`.
2. THE Sistema_Monitoreo SHALL generar un archivo `pipeline_metrics.json` que separe métricas de la Fase_Captura (duración total, snapshots capturados, fps efectivo del loop, razones de captura, eventos térmicos) de métricas de la Fase_Análisis (duración total, tiempo por snapshot, tiempo de detección, tracking, salud, madurez, pausas térmicas).
3. WHEN el Analysis_Service completa el procesamiento, THE Sistema_Monitoreo SHALL generar reportes CSV: `per_snapshot.csv` (resumen por snapshot), `per_detection.csv` (detalle por detección), y `summary.csv` (métricas agregadas del Monitoring).

### Requirement 11: Liberación de cámara y reutilización de recursos

**User Story:** Como operador, quiero poder iniciar una vista previa o un segundo monitoreo después de finalizar uno, sin necesidad de reiniciar la aplicación.

#### Acceptance Criteria

1. WHEN la Fase_Captura termina (por finalización o por cancelación), THE Capture_Worker SHALL liberar la cámara invocando `frame_source.release()` antes de que el Sistema_Monitoreo transicione al siguiente estado.
2. WHEN la cámara se libera correctamente tras un Monitoring, THE Sistema_Monitoreo SHALL permitir iniciar una nueva vista previa de cámara sin reiniciar la aplicación.
3. WHEN la cámara se libera correctamente tras un Monitoring, THE Sistema_Monitoreo SHALL permitir iniciar un nuevo Monitoring para el mismo módulo sin reiniciar la aplicación.

### Requirement 12: Interfaz de usuario durante captura y análisis

**User Story:** Como operador, quiero ver el progreso del sistema en cada fase con lenguaje claro, para saber en qué punto se encuentra el monitoreo.

#### Acceptance Criteria

1. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Sistema_Monitoreo SHALL mostrar en la interfaz el contador "Snapshots capturados: {N}" actualizado en tiempo real.
2. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Sistema_Monitoreo SHALL mostrar el botón principal con la etiqueta "Finalizar captura" (no "Cancelar" ni "Detener").
3. WHILE el Sistema_Monitoreo se encuentra en estado `analyzing`, THE Sistema_Monitoreo SHALL mostrar un indicador de progreso con la etiqueta "Analizando snapshots... ({procesados}/{total})".
4. WHILE el Sistema_Monitoreo se encuentra en estado `analyzing`, THE Sistema_Monitoreo SHALL deshabilitar interacciones que modifiquen el estado del Monitoring (no se puede cancelar durante el análisis en MVP).
5. WHEN el Sistema_Monitoreo transiciona a estado `completed`, THE Sistema_Monitoreo SHALL redirigir automáticamente al Operador a la pantalla de reporte.

### Requirement 13: Historial de monitoreos sin estado "Cancelado" para flujo normal

**User Story:** Como operador, quiero que mi historial de monitoreos refleje correctamente cuáles fueron completados exitosamente, para no confundir un monitoreo válido con uno cancelado.

#### Acceptance Criteria

1. WHEN un Monitoring completa el flujo normal (captura → análisis → completado), THE Sistema_Monitoreo SHALL registrar su estado final como `completed` en el historial del módulo.
2. THE Sistema_Monitoreo SHALL mostrar el estado `aborted` ("Cancelado") en el historial del módulo exclusivamente para monitoreos que fueron cancelados explícitamente por el Operador. Los monitoreos con fallos del sistema muestran estado `error`.
3. THE Sistema_Monitoreo SHALL mostrar el estado `completed` ("Completado") con un indicador visual positivo (ícono verde) en el historial del módulo.

### Requirement 14: Parámetros de captura configurables por perfil

**User Story:** Como desarrollador, quiero que los parámetros del loop de captura sean configurables por perfil (desarrollo vs edge), para poder ajustar el comportamiento sin modificar código.

#### Acceptance Criteria

1. THE Sistema_Monitoreo SHALL exponer los siguientes parámetros de captura como configurables por perfil: `capture_loop_fps`, `min_seconds_between_snapshots`, `max_seconds_without_snapshot`, `gate_resolution`, `scene_gate_orb_threshold`, `scene_gate_hsv_threshold`.
2. THE Sistema_Monitoreo SHALL exponer los siguientes parámetros de análisis como configurables por perfil: `analysis_skip_maturity`, `analysis_thermal_pause_threshold`, `analysis_thermal_resume_threshold`.
3. THE Sistema_Monitoreo SHALL aplicar valores por defecto para modo edge en Raspberry Pi: `capture_loop_fps=5.0`, `min_seconds_between_snapshots=1.0`, `max_seconds_without_snapshot=3.0`, `gate_resolution=(240, 240)`, `analysis_skip_maturity=False`.
4. THE Sistema_Monitoreo SHALL permitir sobreescribir los valores por defecto mediante configuración de perfil activo sin modificar código fuente.

### Requirement 15: Compatibilidad con flujos existentes

**User Story:** Como desarrollador, quiero que la refactorización no rompa la vista previa, la cancelación real, ni el ciclo de vida de la cámara, para mantener estabilidad del sistema.

#### Acceptance Criteria

1. THE Sistema_Monitoreo SHALL mantener funcional la vista previa de cámara sin modificaciones a su API o comportamiento.
2. THE Sistema_Monitoreo SHALL mantener funcional la cancelación real (abort) de un Monitoring en estado `running`, liberando la cámara y transicionando a estado `aborted`.
3. THE Sistema_Monitoreo SHALL mantener el lock global de cámara Picamera2 sin modificaciones, preservando la protección contra acceso concurrente.
4. THE Sistema_Monitoreo SHALL mantener la compatibilidad con la detección de sesiones huérfanas (`_reconcile_orphaned_sessions`) incluyendo el nuevo estado `analyzing` como estado activo.
5. IF el Capture_Worker encuentra un error irrecuperable durante la captura (cámara desconectada, error de filesystem), THEN THE Sistema_Monitoreo SHALL transicionar al estado `error` (no `aborted`) y registrar el motivo del fallo.
6. IF el Analysis_Service encuentra un error irrecuperable durante el análisis, THEN THE Sistema_Monitoreo SHALL transicionar al estado `error` (no `aborted`), preservar los snapshots crudos ya capturados, y registrar el motivo del fallo.
7. THE Sistema_Monitoreo SHALL mantener las transiciones `running → paused`, `running → finishing`, `paused → running` en el código existente para no romper endpoints ni tests previos, aunque la nueva UI no las invoque.

### Requirement 16: Protección térmica durante captura y análisis

**User Story:** Como desarrollador, quiero que el sistema registre temperatura durante captura y pause el análisis si la Raspberry se calienta demasiado, para evitar throttling o daño.

#### Acceptance Criteria

1. WHILE el Sistema_Monitoreo se encuentra en estado `running`, THE Capture_Worker SHALL registrar la temperatura del SoC periódicamente y emitir eventos térmicos en logs y pipeline_metrics.json.
2. WHILE el Sistema_Monitoreo se encuentra en estado `analyzing`, IF la temperatura del SoC supera `analysis_thermal_pause_threshold`, THEN THE Analysis_Service SHALL pausar el procesamiento entre snapshots hasta que la temperatura baje por debajo de `analysis_thermal_resume_threshold`.
3. THE Analysis_Service SHALL registrar cada evento de pausa térmica en logs y en pipeline_metrics.json con timestamp, temperatura de inicio de pausa, y duración.

### Requirement 17: Pruebas unitarias obligatorias

**User Story:** Como desarrollador, quiero pruebas que validen el comportamiento correcto de cada fase del pipeline, para detectar regresiones al modificar el código.

#### Acceptance Criteria

1. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que el Capture_Worker no invoca funciones de inferencia durante la Fase_Captura.
2. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que el Capture_Worker guarda snapshots raw y actualiza el contador `total_snapshots`.
3. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen la transición correcta de estado: `running → analyzing` al recibir señal de "Finalizar captura".
4. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que finalizar captura no marca `aborted`.
5. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que finalizar captura libera la cámara antes de transicionar a `analyzing`.
6. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que el Analysis_Service procesa snapshots en orden ascendente de `frame_index`.
7. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que el conteo final no usa detecciones brutas duplicadas sino tracks únicos.
8. THE Sistema_Monitoreo SHALL incluir pruebas unitarias obligatorias que verifiquen que el reporte usa snapshots anotados (de `annotated_snapshots/`) y no solo raw.
