# Implementation Plan: Recuperación Supabase → SQLite por Usuario (Ownership + RLS)

## Overview

Este plan implementa de forma incremental la recuperación histórica manual Supabase → SQLite, el modelo de propiedad (ownership) multiusuario anclado en `Greenhouse` y el aislamiento por RLS, respetando Clean Architecture (dominio y aplicación sin FastAPI/SQLAlchemy/httpx), migración segura (columnas nullable, scripts idempotentes) y offline-first.

Cada tarea construye sobre las anteriores y termina integrando el código en el flujo existente. El orden sigue el diseño: modelo de datos → puertos → repositorios → servicio de recuperación → compatibilidad de sincronización → infraestructura Supabase → migraciones remotas + RLS + Storage → presentación → UI → wiring → pruebas.

Convenciones del proyecto aplicadas:
- Código, comentarios y docstrings en inglés; documentación de spec en español.
- Pruebas de lógica de aplicación sin hardware (fakes/in-memory de puertos, SQLite in-memory).
- **Solo pruebas dirigidas** (unitarias/integración por ejemplo). Esta spec NO requiere pruebas basadas en propiedades (Hypothesis).
- La recuperación es **import-missing-only**: inserta si falta, reutiliza si existe, nunca actualiza filas existentes.
- Pruebas dirigidas **obligatorias** (tareas normales `[ ]`): ownership/unicidad (1.3), núcleo de `RecoveryService` (4.8: recuperación en vacío + idempotencia + conflictos de padre/propietario y de clave natural + anti-resurrection directo y jerárquico), regresión de ownership de `RemoteSyncService` (6.3) y Recovery API / exclusión mutua 409 (9.2).
- Sub-tareas marcadas con `*` son opcionales (pruebas menores de dataclasses/UI) y NO se implementan automáticamente. Las tareas de validación de RLS/Storage reales contra Supabase se mantienen **manuales** (`*`/manual, dependientes de backend).
- Las tareas que requieren backend Supabase real o validación de RLS/Storage se marcan como manuales y quedan fuera de la suite offline.
- En los checkpoints intermedios se ejecutan **solo** las pruebas dirigidas del área modificada; la suite completa se ejecuta **a lo sumo una vez, al final**.

## Tasks

- [ ] 1. Modelo de datos y migración local de ownership
  - [ ] 1.1 Añadir `owner_user_id` y unicidad compuesta a `GreenhouseModel`
    - Añadir columna `owner_user_id` (Integer, FK → `users.id`, nullable, default NULL) en `src/infrastructure/persistence/models/greenhouse_model.py`.
    - Retirar la unicidad global sobre `name` (`unique=True`) y añadir `UniqueConstraint("owner_user_id", "name", name="uq_greenhouse_owner_name")` en `__table_args__`.
    - _Requirements: 1.1, 1.6, 2.1, 3.1, 3.2, 3.3_

  - [ ] 1.2 Implementar migración local segura (checkfirst + recreación de constraint) y backfill
    - En el módulo de init/migración de persistencia, usar `create_all(checkfirst=True)` para esquemas nuevos y una estrategia de recreación segura de constraint para bases existentes (retiro de unicidad global, alta de unicidad compuesta) sin pérdida de datos.
    - Backfill: asignar `owner_user_id` solo con evidencia persistida, inequívoca y determinística (p. ej. `created_by_user_id` persistido si y solo si existe y no es ambiguo); en cualquier otro caso dejar `owner_user_id = NULL`. La existencia de un único usuario activo NO basta para adjudicar propietario. Registrar las filas sin propietario en un reporte de migración (orientado a administración).
    - Garantizar idempotencia (re-ejecución sin error, sin columnas/tablas duplicadas). Reversibilidad **solo cuando aplique**: no se garantiza restaurar la unicidad global sobre `name` si tras la migración existen nombres duplicados legítimos entre propietarios. Ante fallo, preservar datos previos sin cambios parciales.
    - _Requirements: 2.2, 2.3, 3.4, 5.1, 5.2, 5.5, 5.6, 5.7, 18.1, 18.2, 18.5, 18.6_

  - [ ] 1.3 Unit tests de ownership/unicidad, columna nullable y FK inválida (obligatorio)
    - `UNIQUE(owner_user_id, name)`: mismo nombre bajo dos propietarios distintos → aceptado; mismo nombre bajo un mismo propietario → rechazado (nombre tal como se almacena) (Req 2.1, 2.4, 2.5). Columna acepta NULL en filas legacy (Req 1.6, 3.3, 3.4); insertar greenhouse con `owner_user_id` inexistente → rechazo por integridad referencial (Req 3.6).
    - _Requirements: 2.1, 2.4, 2.5, 1.6, 3.3, 3.4, 3.6_

- [ ] 2. Puertos abstractos de lectura y descarga remota (capa de aplicación)
  - [ ] 2.1 Definir `RemoteReadPort` y `RemoteQueryResult`
    - Crear `src/application/interfaces/remote_read_port.py` con `Protocol` `RemoteReadPort.fetch_by_owner(access_token, table, owner_user_id, filters=None)` (donde `owner_user_id` es el UUID remoto = `auth.uid()`) y dataclass `RemoteQueryResult` (`success`, `rows`, `error_type`, `error_message`).
    - Sin imports de infraestructura (httpx, PostgREST); reutilizar clasificación `CONNECTIVITY | REMOTE_UNAVAILABLE | RLS_DENIED | UNKNOWN`.
    - _Requirements: 7.3, 4.3_

  - [ ] 2.2 Definir `RemoteDownloadPort` y `RemoteDownloadResult`
    - Crear `src/application/interfaces/remote_download_port.py` con `Protocol` `RemoteDownloadPort.download_object(access_token, remote_path, local_file_path)` y dataclass `RemoteDownloadResult` (`success`, `already_absent`, `local_file_path`, `error_type`, `error_message`).
    - Provider-agnostic; `already_absent` como condición tolerada (no fatal).
    - _Requirements: 12.5, 12.2_

  - [ ]* 2.3 Unit tests de contrato de los dataclasses de resultado
    - Valores por defecto no negativos/coherentes; `already_absent` por defecto False.
    - _Requirements: 12.5_

- [ ] 3. Métodos de repositorio para recuperación idempotente (dominio ABC + impl. SQLAlchemy)
  - [ ] 3.1 Ampliar interfaces ABC de repositorios de la jerarquía
    - Añadir a los ABC (`greenhouse_repository.py`, `module_repository.py`, `monitoring_repository.py`, `monitoring_metrics_repository.py`, `snapshot_repository.py`, `inspection_result_repository.py`, `activity_log_repository.py`) los métodos: `find_by_remote_id`, `insert_preserving_remote_id`, `get_local_id_by_remote_id`.
    - **No** añadir un método de actualización desde remoto: la recuperación es import-missing-only (nunca actualiza filas existentes). No se añaden repos de recuperación para `User` ni `ActivityType` (fuera de alcance).
    - _Requirements: 8.5, 9.1, 9.4, 10.1_

  - [ ] 3.2 Añadir métodos específicos de ownership al ABC de `GreenhouseRepository`
    - Añadir `create_with_owner(name, owner_user_id, ...)` (rechaza owner nulo cuando se exige autenticación) y `find_owner_remote_id(greenhouse_id)` (correlación `owner_user_id → users.remote_user_id`, UUID = `auth.uid()`).
    - Validar la unicidad únicamente por `UNIQUE(owner_user_id, name)`, comparando el nombre **tal como se almacena** (sin normalización de mayúsculas/minúsculas ni recorte de espacios).
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 2.1, 3.5_

  - [ ] 3.3 Implementar métodos de recuperación en repositorios SQLAlchemy de la jerarquía
    - Implementar en `src/infrastructure/persistence/repositories/*` los métodos del 3.1: `find_by_remote_id` (si existe, se reutiliza sin modificar), `insert_preserving_remote_id` (PK autoincremental nueva, preserva `remote_id`, remapea FK del padre; solo cuando la fila no existe), `get_local_id_by_remote_id`.
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 10.1, 10.2_

  - [ ] 3.4 Implementar métodos de ownership en `SqlAlchemyGreenhouseRepository`
    - Implementar `create_with_owner` (rechazo sin persistir si owner nulo bajo autenticación exigida; validación de `UNIQUE(owner_user_id, name)` con el nombre comparado tal como se almacena) y `find_owner_remote_id`.
    - _Requirements: 1.2, 1.3, 2.1, 2.4, 2.5, 3.5_

  - [ ]* 3.5 Unit tests de ownership/unicidad y detección de existencia por remote_id
    - `UNIQUE(owner_user_id, name)`: mismo nombre bajo dos propietarios distintos → aceptado; mismo nombre bajo un mismo propietario → rechazado (nombre tal como se almacena) (Req 2.1, 2.4, 2.5). Con auth → owner asignado; sin auth → rechazo sin persistir (Req 1.2, 1.3); `find_by_remote_id` retorna fila existente y no se modifica.
    - _Requirements: 1.2, 1.3, 2.1, 2.4, 2.5, 9.1, 9.2, 10.1_

- [ ] 4. RecoveryService: orquestación jerárquica de la recuperación
  - [ ] 4.1 Definir dataclasses de resultado del servicio
    - Crear `src/application/services/recovery_service.py` con `EntityCounters`, `RecoveryError`, `RecoveryResult`. Contadores por entidad **solo** para las entidades recuperables (greenhouses, modules, monitorings, monitoring_metrics, snapshots, inspection_results, activity_logs) y contadores de imagen no negativos; listas `skipped_local_pending`, `skipped_anti_resurrection`, `errors`; `duration_seconds`. **No** incluir contadores de `users`, `activity_types`, videos ni `skipped_no_owner`.
    - Depende solo de puertos abstractos, interfaces de repositorio y stdlib.
    - _Requirements: 16.1, 16.2, 16.3, 16.5_

  - [ ] 4.2 Implementar recuperación jerárquica con remapeo de FK y preservación de remote_id
    - Implementar `execute_recovery(access_token, user_remote_id)`: orden padre→hijo idéntico a las fases de sincronización; mapa en memoria `(entity_type, remote_row.id) -> local_id`; preservar `remote_id = remote_row.id`, PK local entera nueva, remapeo de FK resolviendo el `id` remoto del padre (contenido en la FK remota de la hija) a su `id` local; omitir hija con padre no resuelto (registrar entity_type + `id` remoto de la hija + `id` remoto del padre) sin revertir lo ya recuperado; omitir entidades sin `id` remoto válido. El `activity_log` resuelve su `activity_type` a partir del catálogo global ya presente localmente (por `activity_type_code`/`id`).
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 7.7_

  - [ ] 4.3 Implementar idempotencia import-missing-only por remote_id (nunca update)
    - Por cada fila remota con `id` remoto válido: `find_by_remote_id(remote_row.id)` (localiza la fila local cuyo `remote_id == remote_row.id`); si existe → reutilizar su `id` local para resolver FKs de descendientes **sin modificar ningún campo** (previa validación de coherencia jerárquica, ver 4.4); si no → `insert_preserving_remote_id`. Aplicar en cada nivel de la jerarquía.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ] 4.4 Implementar protección no-touch / never-update y detección de conflictos
    - Como la recuperación nunca actualiza filas existentes, las filas locales (incluidas `remote_sync_status ∈ {pending, error}`) quedan inherentemente preservadas; registrar su `id` en `skipped_local_pending` cuando se reutilizan en vez de insertar; incluir conteo e identificadores en el resumen.
    - **Conflictos (nunca merge/overwrite/auto-asociar):** al reutilizar una fila local con `remote_id == remote_row.id`, validar ANTES que su padre/propietario local mapee al mismo `id` remoto de padre/propietario que la fila remota referencia (para greenhouse, que el propietario coincida); si NO coincide → conflicto: no modificar, no reutilizar para descendientes, omitir la entidad y todo su subárbol, y reportar. Además, conflicto de clave natural: si existe localmente una fila con la misma clave natural que bloquearía la inserción (p. ej. `UNIQUE(owner_user_id, name)`) pero NO tiene el mismo `remote_id` (p. ej. local `(owner=A, name=USB, remote_id=NULL)` vs remoto `(owner=A, name=USB, id=XYZ)`) → conflicto: no auto-asociar el UUID, no sobrescribir, omitir y reportar.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 9.6, 9.7_

  - [ ] 4.5 Implementar integración anti-resurrection (directo y jerárquico) con Deletion_Outbox
    - Antes de importar, consultar el `DeletionOutboxPort` emparejando por `(entity_type, remote_id)` comparando `deletion_outbox.remote_id == remote_row.id` (`entity_type ∈ {greenhouse, module, monitoring}`, el `remote_id` del outbox puede ser None); si existe una entrada cuyo `status` remoto es no-`synced` (∈ {pending, syncing, error}) → omitir importación y registrar en `skipped_anti_resurrection` sin modificar el outbox.
    - Supresión jerárquica: si un ancestro (greenhouse/module/monitoring) está marcado (tombstoned), omitir todo su subárbol de descendientes (modules/monitorings/snapshots/inspection_results/monitoring_metrics/activity_logs) aunque existan remotamente.
    - Si la consulta falla → fail-safe (omitir + registrar motivo).
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ] 4.6 Implementar descarga de imágenes de snapshots (sin reintentos propios)
    - Tras materializar cada snapshot, construir la ruta remota reutilizando el **mismo** builder de ruta existente (`monitorings/{monitoring_remote_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg`) y descargar vía `RemoteDownloadPort`, apoyándose en los timeouts del adaptador httpx existente (sin lógica de reintentos propia); `already_absent` → `images_skipped`; fallo → `images_failed`; éxito → `images_downloaded`. Mantener invariante `downloaded + skipped + failed == total esperado`.
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [ ] 4.7 Implementar tolerancia por fila, abort seguro y recuperación parcial
    - Tolerancia por fila: acumular fallo en `errors` (entity_type, entity_id, reason) y contador `failed`, continuar sin abortar; abortar antes de tocar SQLite solo si la re-autenticación falla o el backend resulta inalcanzable **antes de iniciar cualquier escritura** (según los timeouts de los adaptadores existentes, sin límites nuevos), dejando SQLite íntegro.
    - Recuperación parcial: si la conectividad se pierde **después** de haber insertado algunas entidades, conservar lo ya recuperado correctamente, no modificar filas locales preexistentes, reportar resultado parcial (éxito=falso) con la causa, y dejar el estado listo para re-ejecución idempotente. Sin rollback global, prefetch total ni infraestructura adicional.
    - _Requirements: 16.4, 7.6, 7.8, 16.6_

  - [ ] 4.8 Unit tests dirigidos del núcleo de RecoveryService (obligatorio)
    - SQLite vacío → jerarquía reconstruida, `remote_id` local == `id` remoto, FKs remapeadas por `id` remoto del padre (Req 8.1–8.5); idempotencia import-missing-only (2× sin filas nuevas, filas existentes no modificadas) (Req 9); preservación local never-update (Req 10); conflictos: padre/propietario no coincidente y clave natural sin `remote_id` coincidente → omitir entidad + subárbol y reportar, sin merge/overwrite/auto-asociar (Req 9.6/9.7); anti-resurrection directo Y jerárquico (ancestro tombstoned → descendientes no recuperados) (Req 11); snapshot presente/ausente (Req 12.2/12.3); padre no resuelto (Req 8.6), `id` remoto nulo (Req 8.7/9.3), fallo outbox (Req 11.3) con puertos fake.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.2, 10.4, 11.1, 11.2, 11.3, 12.2, 12.3_

- [ ] 5. Checkpoint - Núcleo de recuperación
  - Ejecutar **solo** las pruebas dirigidas del área modificada (modelo de datos, puertos, repositorios, `RecoveryService`); no ejecutar la suite completa aún. Ask the user if questions arise.

- [ ] 6. Compatibilidad de RemoteSyncService con ownership
  - [ ] 6.1 Incluir `owner_user_id` (UUID) en el payload de sincronización del Greenhouse
    - En `src/application/services/remote_sync_service.py`, mapear el propietario local (`owner_user_id` → `users.id`) a `users.remote_user_id` (UUID = `auth.uid()`) e incluirlo como `owner_user_id` en el payload remoto del greenhouse con valor no nulo para invernaderos con propietario asignado.
    - Preservar orden jerárquico estricto, exclusión de campos internos (`remote_id`, `remote_sync_status`, `last_synced_at`, `remote_sync_error`, `sync_status`), FASE 0 de borrados y aislamiento de fallos por entidad.
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ] 6.2 Manejar greenhouses legacy sin owner y denegación RLS en sincronización
    - Greenhouses sin propietario (`owner_user_id IS NULL`): aplicar una validación explícita que omita su propagación (no propagar como propiedad de nadie) y marcar la entidad usando el **mecanismo de error/reporte existente**, sin introducir un nuevo estado de sincronización. `owner_user_id IS NULL` por sí solo basta para bloquear la sincronización de propiedad. El reporte de migración para filas NULL-owner se mantiene.
    - Ante `RLS_DENIED` al escribir: conservar estado local sin corrupción, marcar entidad con error reintentable y no re-subir la entidad denegada.
    - _Requirements: 5.4, 6.5_

  - [ ] 6.3 Integration tests de RemoteSyncService compatible con ownership (obligatorio)
    - Regresión con owner en payload; preservación de orden/FASE 0/exclusión de campos; fake port con `RLS_DENIED` → entidad reintentable, no re-subida; greenhouse con `owner_user_id IS NULL` no propagado (validación explícita + mecanismo de error/reporte existente, sin nuevo estado).
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 5.4_

- [ ] 7. Implementaciones Supabase de los puertos de lectura y descarga
  - [ ] 7.1 Implementar `SupabaseRemoteReadAdapter` (PostgREST) para `RemoteReadPort`
    - Crear en `src/infrastructure/supabase/` la implementación de `fetch_by_owner` vía httpx/PostgREST usando el JWT del usuario; mapear errores a `CONNECTIVITY | REMOTE_UNAVAILABLE | RLS_DENIED | UNKNOWN`. Sin lógica de negocio; sin `service_role`.
    - _Requirements: 7.3, 4.3_

  - [ ] 7.2 Implementar `SupabaseRemoteDownloadAdapter` (Storage) para `RemoteDownloadPort`
    - Crear en `src/infrastructure/supabase/` la implementación de `download_object` vía httpx/Storage usando el JWT del usuario (apoyándose en los timeouts httpx existentes, sin reintentos propios); objeto ausente → `already_absent=True`; mapear fallos a `STORAGE_ERROR`; escribir el archivo en la ruta local esperada bajo `outputs/monitorings/{...}` reutilizando el contrato de ruta existente (`monitorings/{monitoring_remote_uuid}/{snapshot_type}/snapshot_{frame_index:06d}.jpg`).
    - _Requirements: 12.1, 12.2, 12.5_

  - [ ]* 7.3 Unit tests de adaptadores con httpx.MockTransport
    - Simular respuestas PostgREST/Storage (éxito, ausente, 401/403, error de red) y verificar el mapeo de `error_type` y `already_absent`. Sin backend real.
    - _Requirements: 4.3, 12.2, 12.3_

- [ ] 8. Migración remota Supabase y políticas RLS (SQL idempotente)
  - [ ] 8.1 Escribir migración SQL de ownership en `supabase/migrations/`
    - Crear `supabase/migrations/NNN_ownership_and_rls.sql` idempotente: añadir `owner_user_id` (uuid, referencia el id de Supabase Auth = `auth.uid()`, nullable) a `greenhouses`; añadir unicidad `UNIQUE (owner_user_id, name)` (sin normalización, nombre tal como se almacena, sin índice funcional); scripts re-ejecutables 1..N veces sin error ni objetos duplicados.
    - _Requirements: 18.3, 2.1_

  - [ ] 8.2 Escribir políticas RLS y de Storage idempotentes para toda la jerarquía
    - En el mismo archivo SQL, definir políticas con `DROP POLICY IF EXISTS ...; CREATE POLICY ...` para `greenhouses`: SELECT/DELETE con `USING (owner_user_id = auth.uid())`, INSERT con `WITH CHECK (owner_user_id = auth.uid())`, UPDATE con `USING (owner_user_id = auth.uid())` y `WITH CHECK (owner_user_id = auth.uid())` para impedir reasignación.
    - Para CADA tabla hija (`modules`, `monitorings`, `monitoring_metrics`, `snapshots`, `inspection_results`, `activity_logs`) definir las **cuatro** operaciones resolviendo la propiedad por `EXISTS`/join hacia `greenhouses.owner_user_id`: SELECT/DELETE con `USING (EXISTS(... join hasta greenhouse WHERE greenhouse.owner_user_id = auth.uid()))`; INSERT con `WITH CHECK (EXISTS(... propiedad vía el padre ...))`; UPDATE con `USING (EXISTS(... propiedad actual ...))` y `WITH CHECK (EXISTS(... propiedad resultante ...))`. Propósito: impedir insertar o mover una entidad hija hacia la jerarquía de otro usuario. Las tablas hijas NO duplican `owner_user_id` (propiedad resuelta solo por join hacia `greenhouses`). En SELECT las filas ajenas se filtran (sin error); las escrituras que violan la propiedad se deniegan.
    - Storage: definir una política que restrinja el acceso a objetos cuya jerarquía propietaria (resuelta `monitoring_remote_uuid` → `monitoring` → `module` → `greenhouse.owner_user_id = auth.uid()`) pertenezca al usuario, reutilizando el contrato de ruta existente sin rediseñar Storage.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 12.6, 18.4_

  - [ ]* 8.3 Validación manual de RLS, aislamiento de Storage y migración idempotente contra Supabase (fuera de suite offline)
    - MANUAL / requiere backend Supabase real: aplicar SQL 1..N veces (mismo esquema final, políticas sin duplicar); con dos identidades verificar aislamiento (SELECT filtra las filas ajenas sin error; INSERT/UPDATE/DELETE denegadas al violar propiedad) y que el JWT de un usuario NO puede descargar objetos de Storage de otra jerarquía. Documentar como prueba de entorno dedicado.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 12.6, 18.3, 18.4_

- [ ] 9. Ruta de presentación recovery_api.py
  - [ ] 9.1 Crear ruta `POST /api/recovery/start` con lock compartido y re-auth
    - Crear `app/routes/recovery_api.py` reutilizando la dependencia de auth existente (`Depends()`), re-autenticando con `SupabaseAuthAdapter` (JWT efímero no persistido) y validando `current_user.remote_user_id` contra la identidad remota (mismatch → 403, SQLite intacto).
    - Ser la única dueña de `SyncRuntimeState.try_acquire()`/`release()` para recuperación (contención → 409); si hay monitoreo activo (`running`/`analyzing`) → `release()` + 409; ejecutar `RecoveryService.execute_recovery` en `run_in_threadpool` (síncrono dentro del request) y devolver el resumen (`RecoveryResult`) al completar; liberar el lock en `finally` (≤ 5 s) en éxito o error.
    - Abortar antes de tocar SQLite si la re-autenticación falla o el backend resulta inalcanzable (según timeouts de los adaptadores existentes, sin límites nuevos). No se añade endpoint de estado ni polling.
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 7.6, 7.7, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 16.1, 16.2, 16.3_

  - [ ] 9.2 Integration tests de la ruta recovery_api / exclusión mutua (obligatorio, offline, fakes/patches)
    - Identidad no coincide → 403 + DB intacta; sin sesión → 401; re-auth falla/backend inalcanzable → abort + DB intacta; monitoreo/sync/recovery activo → 409 + DB intacta; liberación de lock en éxito y error.
    - _Requirements: 7.4, 7.5, 7.6, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

- [ ] 10. UI "Recuperar datos desde la nube" (portrait-first)
  - [ ] 10.1 Añadir la acción manual de recuperación en la sección Exportación/Sincronización
    - En los templates de exportación/sincronización y el JS (`app/static/js/remote_sync.js` o nuevo `recovery.js`), añadir el control "Recuperar datos desde la nube" (español, lenguaje de operario, sin jerga técnica/robótica), objetivo táctil ≥ 60×48 px, separación ≥ 8 px, texto ≥ 16 px, portrait-first (480×800), márgenes 16 px.
    - Iniciar solo con activación explícita (sin automático/programado/realtime); si no hay sesión activa → bloquear inicio y mostrar "Debes iniciar sesión para recuperar tus datos".
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [ ] 10.2 Implementar indicador de actividad y manejo de error con reintento
    - Durante la petición POST, mostrar un spinner y deshabilitar el control de recuperación (sin polling ni endpoint de estado); en error, detener el indicador, mostrar la causa en español en lenguaje de operario y ofrecer reintentar, preservando datos locales.
    - _Requirements: 15.6, 15.7_

  - [ ]* 10.3 Tests de render/ejemplo de la UI de recuperación
    - Etiqueta en español, objetivo táctil ≥ 60×48 px, texto ≥ 16 px, mensaje sin sesión, presencia de spinner/control deshabilitado durante el POST y acción de reintento.
    - _Requirements: 15.1, 15.2, 15.3, 15.6, 15.7_

- [ ] 11. Wiring e inyección de dependencias
  - [ ] 11.1 Cablear puertos, servicio y ruta en `app/dependencies.py` y `app/main.py`
    - Construir por request los adaptadores Supabase (`RemoteReadPort`, `RemoteDownloadPort`), el `RecoveryService` (con repositorios, `DeletionOutboxPort` y puertos), y proveer el `SyncRuntimeState` compartido; registrar el router de `recovery_api.py` en `app/main.py`.
    - _Requirements: 7.1, 7.2, 7.5, 14.1_

  - [ ]* 11.2 Smoke test de wiring de recuperación
    - La ruta declara la dependencia de auth existente y rechaza solicitudes sin sesión; el servicio se construye con `DeletionOutboxPort` no nulo y los puertos remotos.
    - _Requirements: 7.5, 11.1_

- [ ] 12. Checkpoint final - Ensure all tests pass
  - Ejecutar la suite completa (`python -m pytest -q`) **una sola vez** en este checkpoint final. Ask the user if questions arise.

## Notes

- Las sub-tareas marcadas con `*` son opcionales (pruebas menores de dataclasses/UI) y pueden omitirse para un MVP más rápido; el agente no las implementa automáticamente. Las pruebas dirigidas esenciales (1.3, 4.8, 6.3, 9.2) son **obligatorias** y NO llevan `*`. Las validaciones de RLS/Storage reales (8.3) se mantienen **manuales** por depender de un backend Supabase.
- Las sub-tareas 8.3 (RLS/Storage/migración Supabase real) son **manuales** y requieren un backend Supabase dedicado; quedan fuera de la suite offline por defecto según la regla del proyecto (sin cámara, GPIO ni Raspberry Pi, y sin servicios externos obligatorios).
- Cada tarea referencia criterios de aceptación granulares para trazabilidad spec → implementación → prueba.
- Esta spec usa **solo pruebas dirigidas** (unitarias/integración por ejemplo); no requiere pruebas basadas en propiedades (Hypothesis). Los invariantes del diseño se documentan como referencia.
- La recuperación es **import-missing-only**: inserta si falta, reutiliza si existe, nunca actualiza filas existentes; fuera de alcance: recuperación de usuarios, `activity_types` y videos.
- En los checkpoints intermedios se ejecutan solo las pruebas dirigidas del área modificada; la suite completa se ejecuta a lo sumo una vez, en el checkpoint final.
- Cambios pequeños, reversibles (cuando aplique) y aislados por capa (dominio/aplicación/infraestructura/presentación) conforme a las reglas de desarrollo.
- Filas legacy sin propietario determinístico → `owner_user_id IS NULL`; `RemoteSyncService` omite su propagación mediante validación explícita y las marca con el mecanismo de error/reporte existente (sin nuevo estado de sincronización). No se persiste un usuario centinela ni se adjudica propietario por defecto. El reporte de migración para filas NULL-owner se mantiene.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "2.2", "8.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.3", "3.1", "3.2", "7.1", "7.2", "8.2"] },
    { "id": 2, "tasks": ["3.3", "3.4", "6.1", "7.3", "8.3"] },
    { "id": 3, "tasks": ["3.5", "4.1", "6.2"] },
    { "id": 4, "tasks": ["4.2", "6.3"] },
    { "id": 5, "tasks": ["4.3", "4.4", "4.5", "4.6", "4.7"] },
    { "id": 6, "tasks": ["4.8", "9.1"] },
    { "id": 7, "tasks": ["9.2", "10.1"] },
    { "id": 8, "tasks": ["10.2", "10.3", "11.1"] },
    { "id": 9, "tasks": ["11.2"] }
  ]
}
```
