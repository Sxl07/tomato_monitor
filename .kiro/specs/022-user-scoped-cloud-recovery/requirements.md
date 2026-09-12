# Requirements Document

## Introduction

Esta especificación define la **recuperación histórica manual desde Supabase hacia SQLite** para el usuario autenticado del sistema Tomato Monitor, incorporando además un modelo de **propiedad (ownership) multiusuario correcto** y **aislamiento por Row Level Security (RLS)**.

El caso prioritario es restaurar los datos de un operario en un **dispositivo nuevo** o con una base **SQLite vacía o parcialmente vacía**, respetando el principio offline-first del proyecto: SQLite sigue siendo la fuente de verdad operativa local y Supabase es un backend remoto opcional. La recuperación es una operación **manual** iniciada por el operario (no automática, no realtime).

El sistema actual sincroniza en dirección SQLite → Supabase mediante `RemoteSyncService`, con puertos remotos que solo permiten escritura (`upsert`, `delete_by_id`) y subida de objetos (`upload_file`, `remove_object`). Esta especificación añade la **capacidad de lectura/descarga remota** (fetch por tabla filtrada por propietario vía RLS y descarga de objetos de Storage) necesaria para la recuperación, sin romper los contratos existentes.

La recuperación es **import-missing-only**: si una entidad remota (identificada por su `remote_id`) no existe localmente, se inserta preservando su `remote_id`; si ya existe localmente, **no** se actualiza ni se sobrescribe ningún campo desde la nube, y la fila local existente solo se reutiliza para resolver las claves foráneas de sus descendientes. Nunca se actualiza una fila existente. Los conflictos ambiguos se omiten y se reportan; no hay merge automático.

Un objetivo transversal es la **idempotencia por `remote_id`**: una recuperación repetida no debe crear duplicados ni modificar filas existentes. Igualmente, la recuperación debe **proteger los datos locales pendientes** (que quedan inherentemente preservados porque su `remote_id` ya existe localmente y solo se reutiliza, nunca se escribe) y **respetar el contrato anti-resurrection** del `Deletion_Outbox` (Spec 021): una entidad borrada localmente cuyo borrado remoto aún no está completamente propagado no debe reimportarse aunque siga existiendo remotamente.

El modelo de propiedad exige un cambio estructural: hoy la columna `greenhouses.name` es única de forma **global** y no existe columna de propietario. Esta especificación introduce ownership por usuario en el `Greenhouse` (raíz de la jerarquía) y sustituye la unicidad global por **unicidad por propietario**. El resto de la jerarquía (`Module`, `Monitoring`, `Snapshot`, `InspectionResult`, `MonitoringMetrics`, `ActivityLog`) hereda el aislamiento a través de la cadena hacia el `Greenhouse` propietario.

## Glossary

- **Owner (Propietario)**: Usuario operario (`User`) al que pertenece un `Greenhouse` y, por herencia jerárquica, todos sus descendientes. Localmente se materializa como `greenhouses.owner_user_id`, una clave foránea entera al `users.id` local. Remotamente se materializa como `greenhouses.owner_user_id` de tipo UUID, igual al identificador del usuario en Supabase Auth (`auth.uid()`). El mapeo local→remoto se realiza vía `users.remote_user_id`, que almacena ese `auth.uid()`.
- **RLS (Row Level Security)**: Mecanismo del backend remoto (Supabase/PostgreSQL) que restringe, a nivel de fila, qué registros puede leer o escribir un usuario según la identidad de su JWT. En esta especificación garantiza que un usuario solo accede a las filas cuyo propietario efectivo coincide con su identidad remota.
- **JWT efímero**: Token de acceso remoto obtenido por re-autenticación con Supabase dentro del alcance de un request. No se persiste; vive solo durante la operación de sincronización o recuperación.
- **remote_id**: Columna de la fila **local** (SQLite) que guarda el `id` (UUID) de su contraparte remota en Supabase. La fila remota NO tiene columna `remote_id`: su identidad es su PK `id` (UUID). La correlación estable entre el mundo local (PK entera autoincremental + columna `remote_id`) y el remoto (PK `id` UUID) es `local_row.remote_id == remote_row.id`. Las FKs remotas (p. ej. `module.greenhouse_id` remoto) contienen el `id` remoto del padre.
- **Deletion_Outbox**: Mecanismo local durable (Spec 021) que registra borrados pendientes de propagar al remoto. Sus tablas locales son `deletion_outbox`, `deletion_outbox_storage_path` y `deletion_outbox_local_artifact`. Su contrato anti-resurrection impide reimportar una entidad borrada localmente con outbox pendiente.
- **Recuperación idempotente**: Propiedad por la cual ejecutar la recuperación una o varias veces produce el mismo estado local final, sin crear duplicados; se logra correlacionando por `remote_id` en cada nivel de la jerarquía.
- **Fila legacy ambigua**: Registro histórico de `Greenhouse` (y su jerarquía) creado antes del modelo de ownership, sin evidencia determinística de a qué usuario pertenece. No debe adjudicarse por defecto a ningún usuario.
- **Recovery_Service (Servicio de Recuperación)**: Servicio de aplicación que orquesta la recuperación Supabase → SQLite, respetando ownership, idempotencia, protección de datos locales y anti-resurrection.
- **RemoteReadPort / RemoteDownloadPort**: Puerto(s) abstracto(s) nuevo(s) que añaden capacidad de lectura remota (fetch por tabla filtrada por propietario vía RLS) y descarga de objetos de Storage, complementando los puertos de escritura existentes.
- **Runtime_Lock (Exclusión mutua de runtime)**: Estado compartido que garantiza exclusión mutua entre monitoreo activo, sincronización en curso y recuperación en curso.

## Requirements

### Requirement 1: Propiedad de Greenhouse por usuario

**User Story:** Como operario autenticado, quiero que cada invernadero tenga un propietario, para que mis datos queden asociados a mi cuenta y aislados de los de otros usuarios.

#### Acceptance Criteria

1. THE Greenhouse_Model SHALL incluir la columna `owner_user_id` que referencia al usuario dueño mediante una clave foránea entera al identificador (`id`) del `User` local.
2. WHEN un operario autenticado crea un `Greenhouse`, THE Greenhouse_Model SHALL asignar como propietario (`owner_user_id`) al usuario autenticado que ejecuta la creación.
3. IF un operario no autenticado intenta crear un `Greenhouse`, THEN THE Greenhouse_Model SHALL rechazar la creación, no persistir el registro y devolver un error indicando que se requiere autenticación.
4. WHEN el Recovery_Service persiste o correlaciona un `Greenhouse` con el modelo remoto, THE Recovery_Service SHALL mapear el propietario local (`owner_user_id` → `users.id`) al `remote_user_id` (UUID = `auth.uid()`) del usuario correspondiente, que en el modelo remoto es `greenhouses.owner_user_id`.
5. IF el usuario propietario de un `Greenhouse` no tiene `remote_user_id` asignado durante la correlación remota, THEN THE Recovery_Service SHALL omitir el mapeo del propietario, conservar el registro local sin alteraciones e indicar la condición de propietario no sincronizable.
6. THE Greenhouse_Model SHALL permitir que la columna `owner_user_id` sea nullable, de modo que las filas existentes antes de la migración conserven un valor nulo de propietario sin generar errores de integridad.

### Requirement 2: Unicidad de nombre de Greenhouse por propietario

**User Story:** Como operario, quiero poder nombrar mis invernaderos libremente, para que el nombre que elija no colisione con el de otro usuario.

#### Acceptance Criteria

1. THE Greenhouse_Model SHALL imponer una restricción de unicidad `UNIQUE(owner_user_id, name)` sobre la combinación (propietario, nombre), donde el nombre se compara tal como se almacena (sin normalización de mayúsculas/minúsculas ni recorte de espacios).
2. WHEN se ejecuta el Migration_Process, THE Migration_Process SHALL retirar la restricción de unicidad global existente sobre `greenhouses.name` conservando el 100% de los registros de invernadero preexistentes sin modificar sus valores de propietario ni de nombre.
3. IF el Migration_Process falla en cualquier paso antes de completarse, THEN THE Migration_Process SHALL revertir todos los cambios al estado previo a la migración y dejar la restricción de unicidad global original intacta.
4. WHERE existen nombres de invernadero duplicados entre distintos propietarios tras retirar la unicidad global, THE Greenhouse_Model SHALL aceptarlos como registros válidos y distintos.
5. IF dos invernaderos del mismo propietario intentan tener el mismo nombre (comparado tal como se almacena), THEN THE Greenhouse_Model SHALL rechazar la segunda inserción sin persistir el registro y devolver un error que indique la violación de la restricción de unicidad `UNIQUE(owner_user_id, name)`.

### Requirement 3: Ownership coherente en SQLite

**User Story:** Como equipo de desarrollo, quiero que el modelo local SQLite refleje el mismo modelo de propiedad que el remoto, para que la recuperación y la sincronización sean coherentes.

#### Acceptance Criteria

1. THE SQLite_Schema SHALL incluir en la tabla `greenhouses` una columna de propietario que referencie el identificador de usuario local (FK hacia la tabla de usuarios).
2. THE SQLite_Schema SHALL definir una restricción de unicidad compuesta sobre (propietario, nombre) que permita nombres de invernadero duplicados entre distintos propietarios y rechace nombres duplicados dentro del mismo propietario.
3. THE SQLite_Schema SHALL definir la columna de propietario como nullable con valor por defecto NULL, de modo que las filas existentes sin propietario asignado no bloqueen la creación del esquema ni la migración.
4. WHEN se crea o migra el esquema con filas de `greenhouses` preexistentes sin propietario, THE SQLite_Schema SHALL conservar dichas filas con propietario NULL sin pérdida de datos.
5. THE SQLite_Schema SHALL mantener la correlación entre el propietario local y el `remote_user_id` del usuario correspondiente, de modo que cada invernadero con propietario asignado pueda resolverse a un único identificador remoto cuando este exista.
6. IF se intenta persistir un invernadero cuyo propietario no corresponde a un usuario local existente, THEN THE SQLite_Schema SHALL rechazar la operación mediante la restricción de clave foránea, preservando el estado previo de la tabla e indicando el fallo de integridad referencial.

### Requirement 4: Aislamiento RLS a través de la jerarquía

**User Story:** Como operario, quiero que solo yo pueda leer y escribir mis propios datos con mi sesión, para que la información de otros operarios permanezca aislada.

#### Acceptance Criteria

1. THE RLS_Policies SHALL restringir el acceso a filas de `greenhouses` de modo que, para SELECT y DELETE, el predicado `USING` exija `owner_user_id = auth.uid()`; para INSERT, el predicado `WITH CHECK` exija `owner_user_id = auth.uid()`; y para UPDATE, el predicado `USING` exija la propiedad de la fila existente y el `WITH CHECK` impida reasignar la fila a otro propietario.
2. THE RLS_Policies SHALL restringir el acceso a filas de `modules`, `monitorings`, `snapshots`, `inspection_results`, `monitoring_metrics` y `activity_logs` definiendo, para cada tabla hija, las cuatro operaciones resolviendo el propietario efectivo mediante `EXISTS`/join subiendo la cadena de propiedad hasta un `greenhouses` cuyo `owner_user_id = auth.uid()`: SELECT y DELETE con `USING (EXISTS(... join hasta greenhouse WHERE greenhouse.owner_user_id = auth.uid()))`; INSERT con `WITH CHECK (EXISTS(... propiedad vía el padre ...))`; UPDATE con `USING (EXISTS(... propiedad actual ...))` y `WITH CHECK (EXISTS(... propiedad resultante ...))`. El propósito es impedir insertar o mover una entidad hija hacia la jerarquía de otro usuario. Las tablas hijas NO duplican `owner_user_id`; la propiedad se resuelve únicamente por join hacia `greenhouses`.
3. WHEN el usuario consulta el backend remoto con su JWT válido y no expirado, THE RLS_Policies SHALL devolver únicamente las filas cuyo propietario efectivo coincide con `auth.uid()`, filtrando (excluyendo del conjunto de resultados) todas las demás filas sin necesidad de emitir un error explícito.
4. IF un usuario intenta escribir (INSERT/UPDATE/DELETE) una o más filas —de `greenhouses` o de cualquier tabla hija— cuyo propietario efectivo no coincide con `auth.uid()`, THEN THE RLS_Policies SHALL denegar la operación de escritura sin aplicar cambios parciales; en particular, para las tablas hijas el `WITH CHECK` de INSERT/UPDATE SHALL impedir insertar o mover una fila hija hacia la jerarquía de otro usuario.
5. IF una operación se recibe sin JWT o con un JWT inválido o expirado, THEN THE RLS_Policies SHALL denegar el acceso a las filas de las tablas protegidas.
6. IF una fila de una tabla hija no puede resolver un `Greenhouse` propietario con `owner_user_id = auth.uid()` a través de la cadena de propiedad, THEN THE RLS_Policies SHALL dejar esa fila fuera del conjunto de resultados (no visible / filtrada) para el usuario.

### Requirement 5: Migración segura de registros legacy sin propietario

**User Story:** Como equipo de desarrollo, quiero migrar los registros históricos sin propietario de forma segura, para no adjudicar datos a un usuario que no sea su dueño real.

#### Acceptance Criteria

1. WHEN la migración procesa un `Greenhouse` legacy, THE Migration_Process SHALL realizar el backfill de `owner_user_id` únicamente cuando exista evidencia persistida, inequívoca y determinística de propietario (por ejemplo, un `created_by_user_id` persistido si y solo si existe y no es ambiguo); en cualquier otro caso SHALL dejar `owner_user_id = NULL` y registrar el registro afectado en un reporte de migración. La existencia de un único usuario activo NO constituye por sí sola evidencia suficiente para adjudicar propietario.
2. IF un `Greenhouse` legacy presenta más de un posible propietario o evidencia ambigua, THEN THE Migration_Process SHALL mantener `owner_user_id = NULL`, abstenerse de adjudicarlo a cualquier usuario, y preservar el registro sin modificar otros campos.
3. WHERE un `Greenhouse` tiene `owner_user_id = NULL`, THE RemoteSyncService SHALL abstenerse de propagarlo como propiedad de cualquier usuario, y sus filas remotas (con `owner_user_id` NULL) no serán visibles bajo RLS para un usuario normal. El conteo de registros sin propietario se reporta únicamente en el reporte de migración (orientado a administración), no en el resultado de recuperación.
4. WHERE un `Greenhouse` tiene `owner_user_id = NULL`, THE RemoteSyncService SHALL aplicar una validación explícita que omita su propagación (no lo sincroniza hasta que se asigne propietario manualmente) y marque la entidad usando el mecanismo de error/reporte existente (estado de error/reporte ya disponible), sin introducir un nuevo estado de sincronización. El `owner_user_id IS NULL` por sí solo basta para bloquear la sincronización de propiedad de esa entidad.
5. WHEN la migración se ejecuta más de una vez sobre el mismo conjunto de datos, THE Migration_Process SHALL producir un estado final idéntico al de la primera ejecución, sin crear registros duplicados ni modificar propietarios ya asignados.
6. WHERE la migración habilite reversión y esta sea aplicable, THE Migration_Process SHALL restaurar el estado previo conservando el 100% de los propietarios asignados legítimamente antes de la migración, sin pérdida de datos de propiedad. La restauración de la unicidad global sobre `name` NO se garantiza cuando, tras la migración, existan nombres legítimamente duplicados entre distintos propietarios.
7. IF ocurre un fallo durante la ejecución de la migración, THEN THE Migration_Process SHALL preservar los datos previos sin cambios parciales aplicados e indicar la condición de error que impidió completar la migración.

### Requirement 6: Sincronización SQLite → Supabase compatible con ownership

**User Story:** Como operario, quiero que la sincronización siga funcionando tras introducir el propietario, para que mis datos se propaguen al remoto con el aislamiento correcto.

#### Acceptance Criteria

1. THE RemoteSyncService SHALL incluir en el payload de sincronización del `Greenhouse` el campo remoto `owner_user_id` (UUID), obtenido mapeando el `owner_user_id` local a `users.remote_user_id` (= `auth.uid()`), con valor no nulo para invernaderos con propietario asignado.
2. THE RemoteSyncService SHALL preservar el contrato existente de orden jerárquico estricto (greenhouses, modules, monitorings, monitoring_metrics, snapshots, inspection_results, activity_logs) y de exclusión de campos internos del payload (`remote_id`, `remote_sync_status`, `last_synced_at`, `remote_sync_error`, `sync_status`).
3. THE RemoteSyncService SHALL preservar el comportamiento existente de la FASE 0 de propagación de borrados durables antes de los upserts.
4. THE RemoteSyncService SHALL preservar el comportamiento existente de aislamiento de fallos por entidad, marcando la entidad con error y continuando con las restantes sin abortar la sincronización completa.
5. IF el propietario efectivo de un `Greenhouse` no coincide con la identidad remota del JWT y RLS deniega la escritura, THEN THE RemoteSyncService SHALL conservar el estado local sin corrupción, marcar la entidad con error de forma reintentable y no re-subir la entidad denegada.

### Requirement 7: Recuperación Supabase → SQLite con JWT del usuario

**User Story:** Como operario en un dispositivo nuevo, quiero recuperar mis datos históricos desde la nube usando mi sesión, para volver a operar con mi información sin acceder a la de otros usuarios.

#### Acceptance Criteria

1. WHEN el operario inicia una recuperación, THE Recovery_Service SHALL utilizar el JWT efímero del usuario obtenido por re-autenticación con Supabase.
2. THE Recovery_API SHALL reutilizar la re-autenticación con Supabase y la validación de coincidencia de identidad remota ya existentes en la ruta de sincronización.
3. WHEN la recuperación consulta el backend remoto, THE Recovery_Service SHALL obtener únicamente las filas que RLS autoriza para la identidad remota del usuario.
4. IF la identidad remota re-autenticada no coincide con el usuario local, THEN THE Recovery_API SHALL denegar la recuperación con un código de estado 403 y conservar sin cambios la base de datos SQLite local.
5. THE Recovery_API SHALL requerir un usuario autenticado mediante la dependencia de autenticación existente.
6. IF la obtención del JWT efímero por re-autenticación falla o el backend remoto resulta inalcanzable **antes de iniciar cualquier escritura** (según los timeouts ya configurados en los adaptadores httpx existentes, sin introducir límites nuevos), THEN THE Recovery_Service SHALL abortar la recuperación conservando **íntegra y sin cambios** la base de datos SQLite local, y devolver un error que indique la causa del fallo (credenciales inválidas o backend remoto inalcanzable).
7. WHEN la recuperación completa la escritura de todas las filas autorizadas en la base de datos SQLite local, THE Recovery_Service SHALL finalizar la operación con un resultado de éxito que indique el número total de registros recuperados.
8. IF la conectividad con el backend remoto falla **después** de que la recuperación ya insertó algunas entidades, THEN THE Recovery_Service SHALL conservar las entidades ya recuperadas correctamente, no modificar ninguna fila local preexistente, reportar una recuperación parcial / error indicando la causa, y permitir que una ejecución posterior continúe de forma idempotente (import-missing-only hace segura la re-ejecución). THE Recovery_Service SHALL NOT ejecutar rollback global, prefetch total ni introducir infraestructura adicional.

### Requirement 8: Recuperación jerárquica preservando remote_id y remapeando FKs

**User Story:** Como operario, quiero que la recuperación reconstruya la jerarquía completa de mis datos, para que los invernaderos, módulos, monitoreos, snapshots y resultados queden correctamente relacionados en local.

#### Acceptance Criteria

1. THE Recovery_Service SHALL traer y materializar las entidades en orden padre → hijo estricto (greenhouses, luego modules, luego monitorings, luego monitoring_metrics, luego snapshots, luego inspection_results, luego activity_logs).
2. WHEN una entidad remota se materializa en local, THE Recovery_Service SHALL preservar el `id` de la fila remota (columna PK remota `id`, de tipo UUID) sin modificación en la columna `remote_id` de la fila local (mapeo `remote_row.id → local_row.remote_id`).
3. WHEN una entidad remota se inserta en local, THE Recovery_Service SHALL generar una clave primaria local autoincremental nueva (entera), independiente del `id` remoto.
4. THE Recovery_Service SHALL abstenerse de reutilizar el `id` remoto (UUID) como clave primaria local.
5. WHEN una entidad hija se inserta en local, THE Recovery_Service SHALL re-mapear cada clave foránea local resolviendo la referencia remota de la fila hija (que contiene el `id` remoto del padre, p. ej. `module.greenhouse_id` remoto = `id` remoto del greenhouse) a la clave primaria local del padre correspondiente (aquel cuyo `remote_id` es igual a ese `id` remoto del padre).
6. IF un padre requerido por una entidad hija no está presente localmente ni es recuperable, THEN THE Recovery_Service SHALL omitir la entidad hija, registrar en el resumen de errores el tipo de entidad, el `id` remoto de la hija y el `id` remoto del padre no resuelto, y conservar sin revertir las entidades ya recuperadas.
7. IF una entidad remota carece de un `id` remoto válido (nulo o vacío), THEN THE Recovery_Service SHALL omitir su materialización e indicar la causa en el resumen.

### Requirement 9: Idempotencia por remote_id

**User Story:** Como operario, quiero poder repetir la recuperación sin miedo, para que no se dupliquen mis datos si la ejecuto más de una vez.

#### Acceptance Criteria

1. WHEN la recuperación procesa una entidad remota que incluye un `id` remoto válido, THE Recovery_Service SHALL consultar si existe localmente una fila del mismo tipo de entidad cuyo `remote_id` sea igual al `id` de la fila remota (`local_row.remote_id == remote_row.id`) antes de insertar (insert-if-missing / reuse-if-present).
2. IF una fila local del mismo tipo de entidad cuyo `remote_id` es igual al `id` remoto ya existe, THEN THE Recovery_Service SHALL abstenerse de crear una fila adicional y de modificar cualquier campo de la fila existente (nunca actualiza), y SHALL reutilizar el `id` local existente únicamente para resolver las claves foráneas de sus descendientes, previa validación de coherencia jerárquica (ver criterios 6 y 7).
3. IF una entidad remota no incluye un `id` remoto válido (nulo o vacío), THEN THE Recovery_Service SHALL omitir el procesamiento de esa entidad e indicar la omisión en el resultado de la recuperación indicando la causa.
4. THE Recovery_Service SHALL aplicar la detección de duplicados por correlación `local_row.remote_id == remote_row.id` en cada nivel de la jerarquía.
5. WHEN la recuperación se ejecuta 2 o más veces consecutivas sobre un estado remoto sin cambios, THE Recovery_Service SHALL producir un estado local final idéntico, con el mismo número total de filas por nivel de jerarquía y sin registros duplicados por `remote_id`.
6. IF una fila local con `remote_id == remote_row.id` ya existe pero su padre/propietario local NO corresponde al mismo padre/propietario remoto que la fila remota referencia (es decir, el padre local no mapea al mismo `id` remoto del padre que la fila remota, o —para el greenhouse— el propietario local no coincide con el propietario remoto ya resuelto), THEN THE Recovery_Service SHALL tratarlo como conflicto: no modificar la fila, no reutilizarla para importar descendientes, omitir esa entidad y todo su subárbol, y reportar el conflicto. THE Recovery_Service SHALL NOT hacer merge, sobrescribir ni auto-asociar.
7. IF existe localmente una entidad con la misma clave natural que bloquearía la inserción de la remota (p. ej. `UNIQUE(owner_user_id, name)` para greenhouse), pero dicha fila local NO tiene el mismo `remote_id` que el `id` remoto (por ejemplo, local `(owner=A, name=USB, remote_id=NULL)` frente a remoto `(owner=A, name=USB, id=XYZ)`), THEN THE Recovery_Service SHALL tratarlo como conflicto de clave natural: no auto-asociar el `id` remoto a la fila local, no sobrescribirla, omitir esa entidad y su subárbol, y reportar el conflicto. THE Recovery_Service SHALL NOT hacer merge ni adjudicar el UUID remoto a la fila local.

### Requirement 10: Protección de datos locales pendientes o no sincronizados

**User Story:** Como operario, quiero que la recuperación no destruya mi trabajo local reciente, para no perder datos capturados que aún no subí a la nube.

#### Acceptance Criteria

1. THE Recovery_Service SHALL aplicar una política no-touch / never-update: dado que la recuperación nunca actualiza filas existentes, cualquier fila local cuyo `remote_id` ya exista (incluidas las pendientes o no sincronizadas) queda inherentemente preservada, pues solo se reutiliza para resolver FKs y nunca se escribe.
2. IF una fila local tiene `remote_sync_status` igual a `pending` o `error`, THEN THE Recovery_Service SHALL omitir cualquier operación de escritura, actualización o borrado sobre esa fila durante la recuperación.
3. WHEN la operación de recuperación finaliza, THE Recovery_Service SHALL incluir en el resumen el conteo total y el identificador de cada fila local omitida por tener `remote_sync_status` igual a `pending` o `error`.
4. THE Recovery_Service SHALL mantener sin cambios el 100% de las filas locales con `remote_sync_status` igual a `pending` o `error` durante toda la operación de recuperación, verificable comparando el conteo y los identificadores de dichas filas antes y después de la operación.
5. IF la recuperación no puede garantizar la preservación de una fila local con cambios pendientes o no sincronizados, THEN THE Recovery_Service SHALL abstenerse de tocar esa fila, conservar su estado local previo y registrar en el resumen una indicación de omisión por riesgo de pérdida de datos.

### Requirement 11: Integración con Deletion_Outbox para impedir resurrection

**User Story:** Como operario, quiero que los datos que borré no reaparezcan tras recuperar, para que la eliminación que realicé se respete.

#### Acceptance Criteria

1. WHEN la recuperación evalúa una entidad remota, THE Recovery_Service SHALL consultar el `Deletion_Outbox` emparejando su `entity_type` (∈ {greenhouse, module, monitoring}) y comparando el `remote_id` de la entrada del outbox contra el `id` de la fila remota evaluada (`deletion_outbox.remote_id == remote_row.id`, donde el `remote_id` del outbox puede ser None).
2. IF existe una entrada de `Deletion_Outbox` cuyo `status` de propagación remota es distinto de `synced` (es decir, ∈ {pending, syncing, error}) y cuyo `entity_type` y `remote_id` coinciden con la entidad remota evaluada (`deletion_outbox.remote_id == remote_row.id`), THEN THE Recovery_Service SHALL omitir la importación de esa entidad aunque siga existiendo remotamente y SHALL preservar la entrada de `Deletion_Outbox` sin modificar su estado. La condición de que el borrado local ya ocurrió se referencia mediante `local_delete_status = 'completed'`.
3. THE Recovery_Service SHALL garantizar la supresión jerárquica: si un `greenhouse`, `module` o `monitoring` está marcado (tiene una entrada de `Deletion_Outbox` con `status` no-`synced`), sus descendientes (`modules`, `monitorings`, `snapshots`, `inspection_results`, `monitoring_metrics`, `activity_logs`) tampoco SHALL recuperarse, aunque sigan existiendo remotamente; la recuperación propaga el bloqueo hacia abajo omitiendo el subárbol completo del ancestro marcado.
4. IF la consulta al `Deletion_Outbox` falla o no está disponible, THEN THE Recovery_Service SHALL omitir la importación de la entidad evaluada y SHALL registrar la entidad como omitida con una indicación de fallo de verificación anti-resurrection.
5. WHEN la recuperación finaliza, THE Recovery_Service SHALL incluir en el resumen la lista de entidades omitidas por bloqueo anti-resurrection, indicando para cada una su `remote_id`, su `entity_type` y el motivo de la omisión.

### Requirement 12: Recuperación de snapshots desde Supabase Storage

**User Story:** Como operario, quiero recuperar las imágenes de mis snapshots desde la nube, para volver a ver la evidencia visual de mis monitoreos.

#### Acceptance Criteria

1. WHEN el operario inicia una recuperación y existe un objeto de imagen de snapshot en Supabase Storage, THE Recovery_Service SHALL descargarlo a la ruta local esperada bajo `outputs/monitorings/{...}`, reutilizando el contrato de ruta/almacenamiento existente (objetos bajo `monitorings/{monitoring_remote_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg`) y los timeouts ya configurados en los adaptadores httpx existentes (sin introducir límites nuevos).
2. IF un objeto de imagen esperado no existe en Supabase Storage, THEN THE Recovery_Service SHALL registrar la ausencia como imagen omitida, incrementar el contador de omitidas y continuar la recuperación de los objetos restantes sin abortar la operación completa.
3. IF la descarga de un objeto de imagen falla, THEN THE Recovery_Service SHALL registrar la falla, incrementar el contador de fallidas, preservar los archivos ya descargados y continuar con los objetos restantes, sin lógica de reintentos propia (se apoya en el comportamiento del adaptador existente).
4. WHEN la recuperación finaliza, THE Recovery_Service SHALL producir un resumen que contabilice por separado el número de imágenes descargadas, omitidas y fallidas, cumpliendo que la suma de las tres cantidades sea igual al total de objetos esperados.
5. THE RemoteDownloadPort SHALL exponer la capacidad de descarga de objetos de Storage de forma provider-agnostic en la capa de puertos.
6. THE Storage_Policy SHALL restringir el acceso a objetos de Storage de modo que el JWT de un usuario NO pueda descargar objetos pertenecientes a otra jerarquía: el acceso a un objeto se limita a aquellos cuya jerarquía propietaria (resuelta a partir del `monitoring_remote_uuid` de la ruta → `monitoring` → `module` → `greenhouse.owner_user_id = auth.uid()`) pertenezca al usuario solicitante, reutilizando el contrato de ruta/almacenamiento existente sin rediseñar Storage.

### Requirement 13: Exclusión de recuperación de videos

**User Story:** Como equipo de desarrollo, quiero declarar explícitamente que los videos no se recuperan, para evitar expectativas incorrectas sobre el alcance.

La recuperación de videos está **fuera de alcance**: no existe almacenamiento remoto de video, por lo que no se implementa lógica, contadores ni pruebas de recuperación de video (ver Non-Goals).

### Requirement 14: Exclusión mutua con monitoreo, sincronización y recuperación activos

**User Story:** Como operario, quiero que la recuperación no se ejecute cuando hay otra operación crítica en curso, para evitar conflictos de recursos y estado inconsistente.

#### Acceptance Criteria

1. THE Recovery_API SHALL adquirir el mismo `Runtime_Lock` de runtime compartido usado por monitoreo y sincronización antes de iniciar cualquier operación de recuperación.
2. IF hay un monitoreo activo en estado running o analyzing cuando se solicita una recuperación, THEN THE Recovery_API SHALL rechazar la recuperación con un código de estado 409, no realizar ningún cambio de estado sobre el monitoreo activo y retornar un mensaje de error que indique que existe un monitoreo en curso.
3. IF hay una sincronización en progreso cuando se solicita una recuperación, THEN THE Recovery_API SHALL rechazar la recuperación con un código de estado 409, no interrumpir la sincronización en curso y retornar un mensaje de error que indique que existe una sincronización en curso.
4. IF hay otra recuperación en progreso cuando se solicita una nueva recuperación, THEN THE Recovery_API SHALL rechazar la nueva recuperación con un código de estado 409, preservar la recuperación en curso sin alterar su estado y retornar un mensaje de error que indique que existe una recuperación en curso.
5. WHEN una operación de recuperación finaliza con éxito, THE Recovery_API SHALL liberar el `Runtime_Lock` dentro de los 5 segundos posteriores a la finalización.
6. IF una operación de recuperación finaliza con error o excepción, THEN THE Recovery_API SHALL liberar el `Runtime_Lock` dentro de los 5 segundos posteriores a la finalización y preservar el estado previo de los datos sin cambios parciales.

### Requirement 15: Acción manual de UI "Recuperar datos desde la nube"

**User Story:** Como operario, quiero un botón claro para recuperar mis datos desde la nube, para iniciar la operación sin conocimientos técnicos.

#### Acceptance Criteria

1. THE Recovery_UI SHALL ofrecer una acción manual etiquetada en español con lenguaje de operario (por ejemplo, "Recuperar datos desde la nube"), sin terminología técnica ni lenguaje robótico/autónomo.
2. THE Recovery_UI SHALL renderizar el control de recuperación como objetivo táctil de al menos 60×48 px con separación mínima de 8 px respecto a otros elementos interactivos, y con texto de al menos 16 px.
3. IF ningún usuario autenticado tiene sesión activa cuando se intenta iniciar la recuperación, THEN THE Recovery_UI SHALL bloquear el inicio, no ejecutar ninguna recuperación y mostrar un mensaje en español indicando que se requiere iniciar sesión.
4. THE Recovery_UI SHALL iniciar la recuperación únicamente cuando el operario active de forma explícita el control de recuperación, sin comportamiento automático, programado ni en tiempo real.
5. THE Recovery_UI SHALL cumplir las reglas de diseño touchscreen portrait-first del steering de UX, con orientación primaria vertical (480×800) y márgenes de seguridad de 16 px.
6. WHILE la recuperación está en curso (durante la petición POST), THE Recovery_UI SHALL mostrar un indicador de actividad visible (spinner) y deshabilitar el control de recuperación hasta que la operación termine, sin sondeo (polling) ni endpoint de estado adicional.
7. IF la recuperación falla, THEN THE Recovery_UI SHALL detener el indicador de progreso, mostrar un mensaje de error en español que indique la causa en lenguaje de operario y ofrecer una acción para reintentar, preservando los datos locales existentes sin modificarlos.

### Requirement 16: Métricas, resumen y tolerancia a fallos por fila

**User Story:** Como operario, quiero ver un resumen claro del resultado de la recuperación, para saber qué se recuperó y qué falló.

#### Acceptance Criteria

1. WHEN la recuperación finaliza, THE Recovery_Service SHALL producir un resultado que incluya, por cada tipo de entidad recuperada (invernaderos, módulos, monitoreos, métricas de monitoreo, snapshots, resultados de inspección, actividades), tres contadores enteros no negativos: entidades recuperadas, entidades omitidas y entidades fallidas.
2. WHEN la recuperación finaliza, THE Recovery_Service SHALL incluir tres contadores enteros no negativos de imágenes: descargadas, omitidas y fallidas.
3. WHEN la recuperación finaliza, THE Recovery_Service SHALL incluir una lista de errores en la que cada elemento identifique el tipo de entidad afectada, el identificador de la entidad afectada y una descripción del fallo.
4. IF falla la recuperación de una fila individual o de un objeto individual, THEN THE Recovery_Service SHALL registrar el fallo en los contadores de fallidas y en la lista de errores, y continuar procesando las entidades restantes sin abortar la operación completa.
5. WHEN la recuperación finaliza sin haber procesado ninguna entidad ni imagen, THE Recovery_Service SHALL producir un resultado con todos los contadores en cero y una lista de errores vacía.
6. IF la conectividad remota se pierde después de haber insertado algunas entidades, THEN THE Recovery_Service SHALL reportar el resultado como recuperación parcial (éxito=falso), reflejar en los contadores las entidades ya recuperadas, registrar la causa en la lista de errores y no revertir lo ya recuperado ni tocar filas locales preexistentes, dejando el estado listo para una re-ejecución idempotente posterior.

### Requirement 17: Verificación mediante pruebas

**User Story:** Como equipo de desarrollo, quiero que los comportamientos críticos de la recuperación estén cubiertos por pruebas, para asegurar la corrección del aislamiento, la idempotencia y la tolerancia a fallos.

#### Acceptance Criteria

Esta especificación requiere únicamente pruebas unitarias y de integración **dirigidas** (targeted), no una batería de pruebas basadas en propiedades (Hypothesis). Los invariantes descritos en el diseño existen como afirmaciones, pero no se exige su verificación mediante PBT. Las pruebas 1, 3, 4, 5, 6 y 9 son **obligatorias** (se ejecutan en la suite offline). Las pruebas 2 y 8 (RLS/Storage reales) son de **validación manual**, dependen de backend Supabase y quedan fuera de la suite offline por defecto. Las pruebas menores de dataclasses/UI son opcionales. Las pruebas requeridas son exactamente:

1. **Ownership y unicidad (obligatoria):** THE Test_Suite SHALL verificar que `UNIQUE(owner_user_id, name)` acepta el mismo nombre bajo dos propietarios distintos y rechaza el mismo nombre bajo un mismo propietario (nombre comparado tal como se almacena).
2. **RLS con dos usuarios (manual):** THE Test_Suite SHALL verificar, con dos identidades, que cada usuario solo obtiene sus propias filas (las ajenas quedan filtradas) y que las escrituras que violan la propiedad son denegadas. *(Validación MANUAL; requiere backend Supabase; fuera de la suite offline por defecto.)*
3. **Recuperación en SQLite vacío (obligatoria):** THE Test_Suite SHALL verificar que sobre un dispositivo nuevo con SQLite vacío se reconstruye la jerarquía (Greenhouse → Module → Monitoring → Snapshot → InspectionResult → MonitoringMetrics), conservando el `remote_id` local igual al `id` de la fila remota y remapeando cada FK al `id` local del padre (resuelto por `local_row.remote_id == remote_row.id` del padre).
4. **Idempotencia import-missing-only (obligatoria):** THE Test_Suite SHALL verificar que dos ejecuciones consecutivas no crean filas adicionales (conteo por `remote_id` == 1 por nivel) y que las filas preexistentes no se modifican.
5. **Preservación local (obligatoria — nunca se actualizan filas existentes):** THE Test_Suite SHALL verificar que las filas locales existentes (incluidas `pending`/`error`) conservan sus valores sin modificación tras la recuperación.
6. **RecoveryService núcleo — conflictos y anti-resurrection (obligatoria):** THE Test_Suite SHALL verificar (a) una entidad con entrada de `Deletion_Outbox` no-`synced` no se reinserta; (b) si un ancestro está marcado (tombstoned), sus descendientes tampoco se recuperan aunque existan remotamente; (c) conflicto por padre/propietario no coincidente (fila local con mismo `remote_id` pero cuyo padre/propietario no corresponde al remoto → se omite entidad y subárbol y se reporta, sin merge); (d) conflicto de clave natural sin `remote_id` coincidente (p. ej. local `(owner=A, name=USB, remote_id=NULL)` vs remoto `(owner=A, name=USB, id=XYZ)` → se omite y reporta, sin auto-asociar ni sobrescribir).
7. **Snapshot presente/ausente (obligatoria):** THE Test_Suite SHALL verificar que un objeto presente se descarga y uno ausente se registra como omitido, continuando sin abortar.
8. **Aislamiento de Storage (manual):** THE Test_Suite SHALL verificar que el JWT de un usuario no puede descargar objetos de otra jerarquía. *(Validación MANUAL; requiere backend/Storage; fuera de la suite offline por defecto.)*
9. **Exclusión mutua y RemoteSyncService (obligatoria):** THE Test_Suite SHALL verificar que una solicitud de recuperación mientras existe un monitoreo, sincronización o recuperación activa se rechaza con 409 sin modificar datos locales (Recovery API / exclusión mutua), y que `RemoteSyncService` mantiene la regresión de ownership (owner en payload, orden/FASE 0/exclusión de campos, `RLS_DENIED` → entidad reintentable no re-subida).

### Requirement 18: Migraciones idempotentes y reversibles

**User Story:** Como equipo de desarrollo, quiero que las migraciones locales y remotas sean seguras de re-ejecutar, para poder aplicarlas sin riesgo en dispositivos y en el backend.

#### Acceptance Criteria

1. THE SQLite_Migration SHALL añadir nuevas columnas únicamente como nullable o con valor por defecto, usando creación de esquema con verificación previa (checkfirst) que omita la creación de tablas o columnas ya existentes sin generar error.
2. IF una migración de SQLite se ejecuta sobre un esquema que ya contiene las tablas o columnas objetivo, THEN THE SQLite_Migration SHALL completar sin modificar los datos existentes y sin generar error.
3. THE Supabase_Migration SHALL ejecutar scripts SQL idempotentes que, al aplicarse entre 1 y N veces consecutivas, produzcan el mismo estado final de esquema sin generar error ni crear objetos duplicados.
4. THE RLS_Policies SHALL definirse de forma idempotente, de modo que su aplicación repetida no genere error ni políticas duplicadas, eliminando o reemplazando la política previa antes de crearla cuando ya exista.
5. WHERE una migración declare soporte de reversión y esta sea aplicable, THE Migration_Process SHALL ofrecer un camino reversible que restaure el esquema al estado inmediatamente anterior conservando todos los datos legítimos previos a la migración. La restauración de la unicidad global sobre `name` no está garantizada cuando ya existan nombres duplicados legítimos entre propietarios distintos.
6. IF una migración falla durante su aplicación, THEN THE Migration_Process SHALL preservar el estado y los datos previos a la migración e indicar la condición de fallo, sin dejar el esquema en un estado parcialmente aplicado.

## Non-Goals

Las siguientes decisiones están explícitamente **fuera de alcance** de esta especificación y no deben ampliarse:

1. **No sincronización en tiempo real (realtime).** La recuperación y la sincronización son manuales.
2. **No colaboración entre usuarios.** Cada operario opera sobre sus propios datos.
3. **No invernaderos compartidos (shared greenhouses).** El ownership es de un único usuario.
4. **No uso de `service_role`** en el flujo normal. Solo se usa el JWT del usuario.
5. **No merge automático complejo de conflictos.** La política ante datos locales pendientes es no-touch/skip.
6. **No dashboard** ni nuevas vistas analíticas asociadas a la recuperación.
7. **No cambios al pipeline de visión** (detección, salud, madurez, tracking).
8. **No refactors generales del repositorio** más allá de lo requerido por el modelo de ownership y la capacidad de lectura/descarga remota.
9. **No recuperación de videos**, dado que los videos no se almacenan remotamente en el estado actual.

## Project Constraints (No Negotiables)

- **CPU-only y offline-first.** SQLite es la fuente de verdad operativa local; Supabase es remoto opcional y provider-agnostic en la capa de puertos.
- **Dominio y aplicación** no importan FastAPI, OpenCV, PyTorch, Detectron2, SQLAlchemy ni httpx; usan puertos abstractos.
- **Nuevas columnas** en tablas existentes deben ser nullable o con default (migración segura).
- **Autenticación** como `Depends()` inyectable, no como middleware global.
