# Requirements Document

## Introduction

Esta funcionalidad habilita la eliminación de monitoreos, módulos e invernaderos desde la interfaz de usuario, con borrado local duradero e independiente de la conectividad. La propagación de la eliminación al backend remoto (Supabase) es eventualmente consistente y se apoya en un buzón de eliminación local duradero (`deletion_outbox`) integrado en el flujo de sincronización manual existente, que se mantiene unidireccional (local → remoto). La limpieza física de artefactos locales (`outputs/`) es la última fase, con retención configurable.

El diseño no modifica el pipeline de visión, ni el ciclo de vida de captura/análisis (Spec 019 y Spec 020), ni los modelos de ML, ni `ExportService`, ni el dashboard. Tampoco altera el esquema remoto de Supabase (PK, UUID, FK, RLS, columnas de eliminación); se apoya en `ON DELETE CASCADE` y `DELETE` autenticado ya existentes.

## Glossary

- **Deletion_Outbox**: Tabla local duradera que registra eliminaciones pendientes de propagar al backend remoto. Conserva `entity_type`, `entity_local_id`, `remote_table`, `remote_id` (nulo si nunca se sincronizó), `created_at`, `status`, `last_error`, `retry_count` y `local_delete_status`.
- **local_delete_status**: Campo duradero de una entrada del Deletion_Outbox que refleja la durabilidad del borrado local, con valores `prepared` (entrada creada antes del cascade), `completed` (cascade ejecutado con éxito) y `failed` (cascade fallido).
- **deletion_outbox_local_artifact**: Registro hijo duradero del Deletion_Outbox que persiste las rutas locales físicas a limpiar con al menos `outbox_id`, `relative_path`, `status` y `last_error`, poblado antes del Local_Cascade. El campo `relative_path` se almacena relativo a `OUTPUTS_DIR` (sin el prefijo `outputs/`).
- **Idempotent_Delete**: Operación de eliminación remota que trata como éxito el intento de borrar un recurso que ya no existe en el backend remoto.
- **Orphaned_Storage_Object**: Objeto de Supabase Storage cuya fila asociada ya fue eliminada del backend remoto, quedando sin referencia y sin mecanismo de limpieza.
- **Retention_Window**: Intervalo configurable (por defecto 24 horas) durante el cual los artefactos locales (`outputs/`) se conservan tras eliminar una entidad, antes de la limpieza física.
- **Local_Cascade**: Borrado en la base local SQLite que se propaga por las relaciones FK existentes (invernadero → módulo → monitoreo → snapshot → resultado → métricas; módulo → bitácora).
- **Resurrection**: Reaparición no deseada de una entidad eliminada por su re-subida al backend remoto en una sincronización posterior.
- **Deletion_Service**: Servicio de aplicación responsable de autorizar la eliminación, encolar entradas en el `Deletion_Outbox` y ejecutar el `Local_Cascade`.
- **Remote_Sync_Service**: Servicio de aplicación existente que procesa la sincronización unidireccional local → remoto.
- **Remote_Data_Port**: Puerto agnóstico de motor para escrituras remotas; se extiende con eliminación idempotente.
- **Monitoring_Runtime_Registry**: Registro de workers/threads activos de captura y análisis.

## Requirements

### Requirement 1: Autorización de eliminación de monitoreo por estado

**User Story:** Como operario, quiero eliminar un monitoreo solo cuando está en un estado seguro, para no interrumpir capturas o análisis en curso.

#### Acceptance Criteria

1. WHERE el monitoreo está en estado `ready_for_analysis`, `completed`, `error` o `aborted`, THE Deletion_Service SHALL permitir la eliminación del monitoreo.
2. IF el monitoreo está en estado `initializing`, `running`, `paused`, `finishing` o `analyzing`, THEN THE Deletion_Service SHALL rechazar la eliminación, conservar el monitoreo y todos sus datos asociados sin modificación, y devolver un mensaje de error que indique el estado actual del monitoreo y que dicho estado no permite la eliminación.
3. WHEN el operario solicita eliminar un monitoreo en estado permitido, THE Deletion_Service SHALL ejecutar el borrado local del monitoreo junto con sus snapshots, resultados de inspección y métricas asociadas en cascada.
4. IF el operario solicita eliminar un monitoreo cuyo identificador no existe, THEN THE Deletion_Service SHALL rechazar la operación y devolver un mensaje de error que indique que el monitoreo no fue encontrado.

### Requirement 2: Eliminación local inmediata e independiente de conectividad

**User Story:** Como operario, quiero que el elemento eliminado desaparezca de inmediato sin depender de la red, para trabajar sin interrupciones offline.

#### Acceptance Criteria

1. WHEN el operario confirma la eliminación de un monitoreo, módulo o invernadero, THE Deletion_Service SHALL completar el borrado local en la base de datos local sin realizar ninguna llamada de red.
2. WHEN el borrado local finaliza correctamente, THE Agricultural_UI SHALL dejar de mostrar la entidad eliminada y sus entidades hijas en cascada en las vistas correspondientes.
3. IF no hay conectividad con el backend remoto, THEN THE Deletion_Service SHALL completar el borrado local con éxito sin bloquear la operación ni reintentar conexión.
4. IF el borrado local falla, THEN THE Deletion_Service SHALL conservar la entidad y sus entidades hijas sin cambios y THE Agricultural_UI SHALL mostrar un mensaje de error indicando que la eliminación no se completó.
5. WHERE un monitoreo está en un estado eliminable, THE Agricultural_UI SHALL mostrar una acción visible "Eliminar" accesible desde su flujo normal (historial del módulo, o pantalla de detalle, ejecución o reporte), sin depender de conocer una URL.
6. WHEN el operario activa "Eliminar", THE Agricultural_UI SHALL requerir una confirmación explícita antes de invocar el borrado local.
7. THE Deletion_Service SHALL re-validar el estado del monitoreo y la ausencia de worker activo en el backend aunque la Agricultural_UI oculte el botón de eliminación.

### Requirement 3: No interferencia con captura o análisis activos

**User Story:** Como operario, quiero que las eliminaciones no afecten monitoreos con workers activos, para preservar la integridad de las sesiones en ejecución.

#### Acceptance Criteria

1. IF el Monitoring_Runtime_Registry registra un worker de captura o análisis activo para el monitoreo objetivo, THEN THE Deletion_Service SHALL rechazar la eliminación, preservar sin cambios el estado y los datos del monitoreo, y retornar un indicador de error que señale que existe un worker activo.
2. WHEN se solicita eliminar un monitoreo sin worker activo registrado en el Monitoring_Runtime_Registry, THE Deletion_Service SHALL completar el borrado sin detener, pausar ni alterar el estado de workers asociados a otros monitoreos.

### Requirement 4: Buzón de eliminación local duradero (Deletion_Outbox)

**User Story:** Como sistema, quiero registrar de forma duradera las eliminaciones pendientes, para propagarlas al backend remoto más adelante.

#### Acceptance Criteria

1. THE Deletion_Outbox SHALL persistir por cada eliminación los campos `entity_type`, `entity_local_id`, `remote_table`, `remote_id`, `created_at`, `status`, `last_error`, `retry_count` y `local_delete_status`, donde `entity_type`, `entity_local_id` y `remote_table` son obligatorios, `remote_id` es nulo cuando la entidad nunca se sincronizó, `created_at` se registra como timestamp UTC, `status` inicializa en `pending`, `last_error` es nulo hasta que ocurra un fallo y `retry_count` inicializa en cero.
2. WHEN se elimina un monitoreo con snapshots que tienen rutas de Storage, THE Deletion_Service SHALL registrar en el Deletion_Outbox las rutas de Storage asociadas antes de ejecutar el Local_Cascade.
3. THE Deletion_Service SHALL crear la entrada en el Deletion_Outbox antes de ejecutar el Local_Cascade que elimina los registros locales.
4. THE Deletion_Outbox SHALL soportar exclusivamente los estados `pending`, `syncing`, `synced` y `error`, y rechazar cualquier valor de estado fuera de ese conjunto.
5. WHEN se crea la tabla local del Deletion_Outbox, THE DatabaseManager SHALL crearla mediante `create_all` con todas las columnas nuevas definidas como nulas o con valor por defecto para preservar la compatibilidad de migración con datos existentes.
6. WHEN una entrada del Deletion_Outbox se persiste, THE Deletion_Outbox SHALL confirmar la escritura de forma sincrónica antes de retornar el control al llamador, de modo que la entrada permanezca disponible tras un reinicio del proceso.
7. IF la escritura de una entrada en el Deletion_Outbox falla, THEN THE Deletion_Service SHALL abortar el Local_Cascade, conservar los registros locales sin modificar y devolver un error indicando que la eliminación no pudo registrarse de forma duradera.
8. THE Deletion_Service SHALL crear la entrada del Deletion_Outbox con `local_delete_status = prepared` y confirmar esa transacción (TX1) antes de ejecutar el Local_Cascade.
9. WHEN se ejecuta el Local_Cascade, THE Deletion_Service SHALL eliminar los registros locales y establecer `local_delete_status = completed` dentro de la misma transacción (TX2).
10. IF la transacción TX2 (Local_Cascade y marcado `completed`) falla, THEN THE Deletion_Service SHALL revertir el cascade y el marcado `completed`, dejar la entrada en un estado no-completado (`prepared` o `failed`) y NO SHALL propagar la eliminación al backend remoto.
11. THE Deletion_Service SHALL permitir reintentar de forma segura una entrada del Deletion_Outbox en estado `prepared` o `failed` sin crear una entrada duplicada, garantizando idempotencia por la combinación `entity_type` + `entity_local_id`.
12. WHEN se elimina un monitoreo, THE Deletion_Service SHALL persistir la ruta local `monitorings/{monitoring_id}` relativa a `OUTPUTS_DIR` como un registro `deletion_outbox_local_artifact` (con al menos `outbox_id`, `relative_path`, `status` y `last_error`) antes de ejecutar el Local_Cascade, donde `relative_path` es relativa a `OUTPUTS_DIR` y no incluye el prefijo `outputs/`.
13. WHEN se elimina un módulo o invernadero, THE Deletion_Service SHALL persistir una fila `deletion_outbox_local_artifact` por cada monitoreo descendiente antes de ejecutar el Local_Cascade.

### Requirement 5: Semántica del Local_Cascade

**User Story:** Como sistema, quiero que el borrado local elimine toda la jerarquía dependiente, para no dejar registros huérfanos en la base local.

#### Acceptance Criteria

1. WHEN se elimina un invernadero, THE Deletion_Service SHALL ejecutar el Local_Cascade sobre todos sus módulos y, para cada módulo, sobre sus monitoreos, snapshots, resultados de inspección, métricas y bitácoras asociadas.
2. WHEN se elimina un módulo, THE Deletion_Service SHALL ejecutar el Local_Cascade sobre todos sus monitoreos, snapshots, resultados de inspección, métricas y bitácoras asociadas.
3. WHEN se elimina un monitoreo, THE Deletion_Service SHALL ejecutar el Local_Cascade sobre todos sus snapshots, resultados de inspección y métricas asociadas.
4. WHEN finaliza el Local_Cascade de una entidad, THE Deletion_Service SHALL garantizar que el número de registros locales dependientes de esa entidad sea igual a cero y establecer `local_delete_status = completed` dentro de la misma transacción que el cascade.
5. IF el Local_Cascade falla antes de eliminar todos los registros dependientes, THEN THE Deletion_Service SHALL revertir la operación dejando la jerarquía intacta en su estado previo a la eliminación, no marcar `local_delete_status = completed` y retornar un indicador de error que identifique la entidad no eliminada.
6. THE Local_Cascade SHALL eliminar únicamente los registros de la base de datos local y NO SHALL eliminar de inmediato los artefactos físicos en `outputs/`; la eliminación física de dichos artefactos se difiere al Retention_Window y al Cleanup_Process definidos en el Requirement 13.

### Requirement 6: Eliminación de módulos e invernaderos con el mismo mecanismo duradero

**User Story:** Como operario, quiero eliminar módulos e invernaderos con el mismo comportamiento duradero que los monitoreos, para tener una gestión consistente.

#### Acceptance Criteria

1. WHEN el operario confirma la eliminación de un módulo, THE Deletion_Service SHALL registrar todas las entradas requeridas en el Deletion_Outbox y confirmar su persistencia antes de iniciar el Local_Cascade del módulo.
2. WHEN el operario confirma la eliminación de un invernadero, THE Deletion_Service SHALL registrar todas las entradas requeridas en el Deletion_Outbox y confirmar su persistencia antes de iniciar el Local_Cascade del invernadero.
3. WHEN se elimina un módulo o invernadero que contiene monitoreos con rutas de Storage, THE Deletion_Service SHALL preservar cada ruta de Storage asociada en el Deletion_Outbox antes de iniciar el Local_Cascade.
4. IF el registro en el Deletion_Outbox falla antes de iniciar el Local_Cascade, THEN THE Deletion_Service SHALL abortar la eliminación, conservar el módulo o invernadero y sus datos sin modificar, y devolver un indicador de error señalando el fallo de registro.
5. WHEN el operario confirma la eliminación de un módulo o invernadero, THE Deletion_Service SHALL validar todos los monitoreos descendientes antes de crear cualquier entrada del Deletion_Outbox y antes de ejecutar cualquier Local_Cascade (antes de TX1).
6. WHERE todos los monitoreos descendientes del módulo o invernadero están en estado `ready_for_analysis`, `completed`, `error` o `aborted`, THE Deletion_Service SHALL permitir continuar con la eliminación.
7. IF cualquier monitoreo descendiente está en estado `initializing`, `running`, `paused`, `finishing` o `analyzing`, THEN THE Deletion_Service SHALL rechazar toda la operación, no crear ninguna entrada del Deletion_Outbox y no ejecutar ningún Local_Cascade, conservando la jerarquía intacta.
8. IF cualquier monitoreo descendiente tiene un worker de captura o análisis activo en el Monitoring_Runtime_Registry, THEN THE Deletion_Service SHALL rechazar toda la operación, no crear ninguna entrada del Deletion_Outbox y no ejecutar ningún Local_Cascade, conservando la jerarquía intacta.

### Requirement 7: Eliminación remota idempotente vía Remote_Data_Port

**User Story:** Como sistema, quiero eliminar recursos remotos de forma idempotente, para que los reintentos no fallen aunque el recurso ya no exista.

#### Acceptance Criteria

1. THE Remote_Data_Port SHALL exponer una operación de eliminación remota que reciba el token de acceso, la tabla y el `remote_id` del recurso.
2. IF el token de acceso, la tabla o el `remote_id` están ausentes o vacíos, THEN THE Remote_Data_Port SHALL devolver un error de validación sin realizar la llamada remota.
3. WHEN la operación de eliminación remota se ejecuta sobre un recurso que ya no existe en el backend remoto, THE Remote_Data_Port SHALL tratar el resultado como éxito.
4. WHEN se invoca dos veces consecutivas la eliminación remota sobre el mismo `remote_id`, THE Remote_Data_Port SHALL devolver resultados de éxito idénticos (idempotencia).
5. WHEN se elimina un recurso padre en el backend remoto, THE Remote_Data_Port SHALL apoyarse en las cascadas `ON DELETE CASCADE` existentes para eliminar los recursos hijos.
6. IF la eliminación remota falla por conectividad o indisponibilidad del backend, THEN THE Remote_Data_Port SHALL devolver un tipo de error que permita el reintento posterior sin marcar el recurso como eliminado.

### Requirement 8: Limpieza de objetos de Storage sin huérfanos

**User Story:** Como sistema, quiero eliminar los objetos de Storage asociados a snapshots, para no dejar archivos huérfanos en el backend remoto.

#### Acceptance Criteria

1. THE ruta de Storage registrada en el Deletion_Outbox SHALL soportar exclusivamente los estados `pending`, `removed` y `error`, donde `removed` es el único estado terminal.
2. WHEN se propaga la eliminación remota de un monitoreo, THE Remote_Sync_Service SHALL procesar tanto las rutas de Storage en estado `pending` como en estado `error`, eliminando cada objeto de Storage cuya ruta fue preservada en el Deletion_Outbox para ese monitoreo.
3. WHEN un objeto de Storage se elimina con éxito o se solicita eliminar uno que ya no existe en el backend remoto, THE Remote_Sync_Service SHALL marcar la ruta correspondiente como `removed`.
4. IF la eliminación de un objeto de Storage falla por un error distinto de inexistencia, THEN THE Remote_Sync_Service SHALL marcar la ruta correspondiente como `error` y reintentarla en la siguiente propagación.
5. WHEN todas las rutas de Storage asociadas a una entidad han alcanzado el estado `removed`, THE Remote_Sync_Service SHALL marcar la entrada del Deletion_Outbox como `synced` y garantizar que no queden Orphaned_Storage_Object asociados a esa entidad.

### Requirement 9: Integración en el flujo de sincronización manual unidireccional

**User Story:** Como operario, quiero que las eliminaciones pendientes se propaguen durante la sincronización manual existente, para no necesitar un proceso separado.

#### Acceptance Criteria

1. WHEN el operario ejecuta la sincronización manual, THE Remote_Sync_Service SHALL procesar únicamente las entradas del Deletion_Outbox cuyo `status` remoto sea `pending`, `error` o un `syncing` recuperado tras un cierre inesperado o reinicio, y solo cuando `local_delete_status = completed`, ordenadas de forma ascendente por fecha de creación.
2. THE Remote_Sync_Service SHALL nunca propagar al backend remoto una entrada del Deletion_Outbox cuyo `local_delete_status` sea `prepared` o `failed`.
3. WHEN una entrada del Deletion_Outbox se encuentra en estado `syncing` persistido tras un reinicio del proceso, THE Remote_Sync_Service SHALL tratarla como reintentable y volver a procesar su propagación.
4. THE Remote_Sync_Service SHALL mantener la sincronización unidireccional local → remoto sin introducir propagación remoto → local.
5. WHEN una entrada del Deletion_Outbox se propaga con éxito al backend remoto, THE Remote_Sync_Service SHALL marcar la entrada como `synced`.
6. IF la propagación de una entrada del Deletion_Outbox falla, THEN THE Remote_Sync_Service SHALL conservar la entrada sin eliminarla, marcarla como `error` y registrar el detalle del fallo en `last_error`.
7. WHEN la propagación de una entrada falla, THE Remote_Sync_Service SHALL continuar procesando las entradas restantes del Deletion_Outbox hasta agotar la totalidad de las entradas seleccionadas.

### Requirement 10: Ausencia de resurrección

**User Story:** Como operario, quiero que las entidades eliminadas no reaparezcan tras una sincronización, para confiar en que el borrado es definitivo.

#### Acceptance Criteria

1. WHEN se ejecuta la sincronización manual después de eliminar localmente una entidad, THE Remote_Sync_Service SHALL excluir de la subida al backend remoto toda entidad ya eliminada localmente.
2. WHILE existe una entrada de eliminación no propagada para una entidad, THE Remote_Sync_Service SHALL propagar la eliminación al backend remoto antes de procesar cualquier escritura o actualización pendiente de esa entidad.
3. IF la propagación de una eliminación al backend remoto falla, THEN THE Remote_Sync_Service SHALL conservar la entrada de eliminación como reintentable, no re-subir la entidad y registrar la operación como fallida para reintento en la siguiente sincronización.

### Requirement 11: Reintento offline con seguimiento de error

**User Story:** Como operario, quiero que las eliminaciones remotas fallidas se reintenten en la siguiente sincronización, para que la consistencia eventual se alcance sin intervención manual adicional.

#### Acceptance Criteria

1. IF la sincronización manual se ejecuta sin conectividad, THEN THE Remote_Sync_Service SHALL conservar las entradas del Deletion_Outbox en estado reintentable (`pending` o `error`) sin descartarlas.
2. WHEN una entrada del Deletion_Outbox está en estado `error`, THE Remote_Sync_Service SHALL reintentar su propagación al iniciar la siguiente sincronización manual.
3. WHEN una entrada del Deletion_Outbox falla, THE Deletion_Outbox SHALL conservar en `last_error` la descripción del error junto con la marca de tiempo UTC del fallo.

### Requirement 12: Migración remota prerrequisito (solo diseño/documentación)

**User Story:** Como equipo de tesis, quiero documentar una migración remota mínima para el CHECK de estado, para revisarla manualmente antes de aplicarla.

#### Acceptance Criteria

1. THE Feature SHALL documentar una migración remota separada y mínima que modifique la restricción CHECK `ck_monitorings_status` de la columna `public.monitorings.status`, agregando el valor `ready_for_analysis` y preservando los valores `initializing`, `running`, `paused`, `finishing`, `analyzing`, `completed`, `aborted` y `error`.
2. THE Feature SHALL entregar la migración documentada únicamente como artefacto de diseño y documentación destinado a revisión humana previa, sin ejecutarla ni aplicarla sobre ninguna base de datos remota.
3. THE documented migration SHALL limitar su ámbito exclusivamente a la restricción CHECK `ck_monitorings_status` de la columna `status`, sin modificar PK, UUID, FK, RLS ni relaciones remotas.

### Requirement 13: Limpieza física local con retención configurable (fase final)

**User Story:** Como operario, quiero que los artefactos locales se conserven un tiempo tras eliminar y luego se limpien de forma duradera, para poder recuperar información reciente y liberar espacio después.

#### Acceptance Criteria

1. WHEN se elimina un monitoreo, módulo o invernadero, THE Deletion_Service SHALL conservar los artefactos locales asociados en `outputs/`, marcarlos con la marca de tiempo UTC de eliminación y no borrarlos de inmediato.
2. THE Feature SHALL exponer un Retention_Window configurable, expresado en horas, con valor por defecto de 24 horas.
3. IF el Retention_Window configurado no es un valor numérico válido, THEN THE Feature SHALL usar el valor por defecto de 24 horas y registrar una advertencia.
4. WHEN han transcurrido las horas del Retention_Window desde la marca de tiempo de eliminación de una entidad, THE Cleanup_Process SHALL eliminar de forma duradera los artefactos locales asociados usando las rutas persistidas en los registros `deletion_outbox_local_artifact`, sin reconstruirlas tras haber perdido los registros de SQLite.
5. THE Cleanup_Process SHALL ejecutar la eliminación duradera de los artefactos de una entidad únicamente cuando la entrada del Deletion_Outbox tenga `local_delete_status = completed`.
6. WHEN el Cleanup_Process procesa cada ruta persistida en `deletion_outbox_local_artifact`, THE Cleanup_Process SHALL validarla mediante `validate_safe_path(relative_path, OUTPUTS_DIR)` antes de eliminarla, sin volver a anteponer el prefijo `outputs/`, dado que `relative_path` ya es relativa a `OUTPUTS_DIR`.
7. IF una ruta persistida no existe durante la limpieza, THEN THE Cleanup_Process SHALL tratar la eliminación de esa ruta como un éxito idempotente.
8. IF la eliminación duradera de los artefactos de una entidad falla, THEN THE Cleanup_Process SHALL conservar los artefactos restantes, registrar un error indicando la entidad afectada y reintentar la limpieza en la siguiente ejecución del proceso.

### Requirement 14: Cobertura de pruebas

**User Story:** Como equipo de tesis, quiero pruebas que verifiquen el ciclo de vida de eliminación, para respaldar la corrección con evidencia reproducible.

#### Acceptance Criteria

1. THE Test_Suite SHALL verificar que la eliminación de un monitoreo es permitida en `ready_for_analysis`, `completed`, `aborted` y `error`, y rechazada en `initializing`, `running`, `paused`, `finishing` y `analyzing`.
2. THE Test_Suite SHALL verificar que, tras registrar una eliminación en el Deletion_Outbox, la entrada persiste tras reiniciar el proceso y conserva `remote_id` y las rutas de Storage antes de ejecutar el Local_Cascade.
3. THE Test_Suite SHALL verificar que el Local_Cascade elimina todos los registros hijos para monitoreo, módulo e invernadero, sin dejar registros huérfanos.
4. THE Test_Suite SHALL verificar que la eliminación remota es idempotente cuando el recurso remoto ya no existe.
5. THE Test_Suite SHALL verificar el reintento offline y que la entrada del Deletion_Outbox conserva la causa en `last_error`.
6. THE Test_Suite SHALL verificar que, tras la limpieza de Storage, no queda ningún Orphaned_Storage_Object asociado al recurso eliminado.
7. THE Test_Suite SHALL verificar la ausencia de Resurrection tras la sincronización posterior a una eliminación.
8. THE Test_Suite SHALL verificar la no interferencia con capturas o análisis activos.
9. THE Test_Suite SHALL verificar que la eliminación de un módulo o invernadero se rechaza por completo, sin crear entradas del Deletion_Outbox ni ejecutar el Local_Cascade, cuando algún monitoreo descendiente está en un estado prohibido (`initializing`, `running`, `paused`, `finishing` o `analyzing`) o tiene un worker activo en el Monitoring_Runtime_Registry, y se permite cuando todos los descendientes están en estados eliminables (`ready_for_analysis`, `completed`, `error` o `aborted`) sin workers activos.

## Non-Goals

- No se implementa sincronización bidireccional (remoto → local).
- No se agregan columnas de eliminación (`deleted_at` u otras) a las tablas de Supabase.
- No se modifican PK, UUID, FK, RLS ni relaciones remotas de Supabase.
- No se altera el pipeline de visión ni sus componentes.
- No se altera la Spec 019 ni el ciclo de vida de captura/análisis de la Spec 020.
- No se modifican los modelos de ML (Detectron2/RetinaNet, ResNet-18, estimación de madurez).
- No se modifica `ExportService` ni el dashboard.
