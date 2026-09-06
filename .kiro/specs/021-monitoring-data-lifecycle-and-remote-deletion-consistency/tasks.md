# Implementation Plan: Monitoring Data Lifecycle and Remote Deletion Consistency

## Overview

Plan de implementación incremental para la eliminación duradera de monitoreos, módulos e invernaderos, con borrado local independiente de la conectividad y propagación remota eventualmente consistente vía un `Deletion_Outbox` local. El borrado local sigue un protocolo de **dos transacciones** (TX1 `prepared` + COMMIT, TX2 `Local_Cascade` + `completed`); solo se propagan remotamente las entradas con `local_delete_status = completed`. La **limpieza física local** de `outputs/` es la **fase final** (Req 5.6, 13), usa rutas de artefacto **persistidas** (`deletion_outbox_local_artifact`) y se ejecuta después del core de eliminación, la propagación remota y el checkpoint de pruebas dirigidas. No se toca el pipeline de visión, Spec 019, el ciclo de captura/análisis de Spec 020, modelos de ML, `ExportService` ni el dashboard.

Lenguaje de implementación: **Python** (coincide con el código existente). El diseño es CRUD/orquestación con efectos externos, por lo que se usan pruebas de ejemplo/unitarias e integración con fakes/mocks — **no** property-based testing. Toda prueba corre sin hardware, cámara ni red.

Durante la implementación se ejecutan **solo pruebas dirigidas por componente** (los tests requeridos de cada componente). La suite completa (`python -m pytest -q`) se corre **una sola vez al final** (tarea 12.2). Las pruebas que validan el Requirement 14 son **obligatorias** (no opcionales). Cada tarea es pequeña, reversible y verifica sus tests dirigidos entre pasos.

## Tasks

- [x] 1. Extender puertos de aplicación y DTOs de eliminación
  - [x] 1.1 Extender `RemoteDataPort` con `delete_by_id` y `RemoteDeleteResult`
    - Añadir el dataclass `RemoteDeleteResult` (`success`, `already_absent`, `error_type`, `error_message`) y el método `delete_by_id(access_token, table, remote_id)` al `Protocol` en `src/application/interfaces/`
    - Solo depende de stdlib; sin imports de FastAPI/httpx/SQLAlchemy
    - _Requirements: 7.1, 7.2, 7.6_
  - [x] 1.2 Extender `RemoteStoragePort` con `remove_object` y `RemoteStorageDeleteResult`
    - Añadir el dataclass `RemoteStorageDeleteResult` y el método `remove_object(access_token, path)` al `Protocol` en `src/application/interfaces/`
    - _Requirements: 8.1, 8.2, 8.3_
  - [x] 1.3 Definir puertos `DeletionOutboxPort` y `LocalCascadePort`
    - Interfaces en `src/application/interfaces/` para encolado duradero del outbox (crear entrada + rutas de Storage + artefactos locales, marcar `local_delete_status` prepared/completed/failed, marcar `status` syncing/synced/error) y para el `Local_Cascade` local
    - _Requirements: 4.1, 4.3, 5.1, 5.2, 5.3_
  - [ ]* 1.4 Escribir tests unitarios de contrato de los DTOs/puertos
    - Verificar defaults de los dataclasses y firmas de los `Protocol` con implementaciones fake
    - _Requirements: 7.1, 8.1_

- [x] 2. Persistencia del Deletion_Outbox
  - [x] 2.1 Crear modelos ORM `DeletionOutboxModel`, `DeletionOutboxStoragePathModel` y `DeletionOutboxLocalArtifactModel`
    - En `src/infrastructure/persistence/models/`; columnas nuevas nulas o con default para compatibilidad de migración; sin modificar tablas existentes ni esquema remoto
    - `DeletionOutboxModel`: `entity_type`, `entity_local_id`, `remote_table` **NOT NULL**; `remote_id` **nullable** (nulo si nunca se sincronizó); `status` (`pending | syncing | synced | error`) rastrea la **propagación remota**; `local_delete_status` (`prepared | completed | failed`, default `prepared`) rastrea la **durabilidad del borrado local**; incluir `last_error`, `retry_count`, `cleanup_status` y `deleted_at`
    - `DeletionOutboxStoragePathModel`: `outbox_id` (FK), `storage_path`, `status` (`pending | removed | error`, `removed` único estado terminal)
    - `DeletionOutboxLocalArtifactModel`: `outbox_id` (FK), `relative_path`, `status` (`pending | done | error`), `last_error`
    - _Requirements: 4.1, 4.4, 4.5, 4.8, 4.12, 4.13_
  - [x] 2.2 Cablear la creación de tablas en `DatabaseManager` (`create_all`)
    - Registrar los tres nuevos modelos para que `create_all` cree las tablas de forma segura para datos existentes
    - _Requirements: 4.5_
  - [x] 2.3 Implementar `DeletionOutboxRepository` (commit sincrónico)
    - Crear entrada + rutas de Storage (`pending | removed | error`) + artefactos locales con commit inmediato; consultar entradas `pending`/`error` ordenadas por `created_at ASC`; marcar `local_delete_status` (prepared/completed/failed) y `status` (syncing/synced/error); incrementar `retry_count`; escribir `last_error` con timestamp UTC
    - Tratar entradas `syncing` persistidas como reintentables; idempotencia por (`entity_type`, `entity_local_id`) para reintento seguro de `prepared`/`failed`
    - _Requirements: 4.1, 4.3, 4.6, 4.11, 9.1, 11.1, 11.2, 11.3_
  - [x] 2.4 Tests de durabilidad del outbox (incluye reinicio)
    - SQLite en memoria/archivo temporal: crear entrada, recrear repositorio/sesión (simula reinicio) y verificar que persiste `remote_id` y rutas de Storage antes del `Local_Cascade`
    - Verificar rechazo de estados fuera de `{pending, syncing, synced, error}`
    - _Requirements: 14.2, 4.4, 4.6_

- [x] 3. Implementar el LocalCascadeRepository
  - [x] 3.1 Implementar `LocalCascadeRepository` (transaccional, ON DELETE CASCADE local)
    - Borrado local por raíz (monitoreo/módulo/invernadero) apoyado en cascada FK local; solo registros DB, no toca `outputs/`
    - Establecer `local_delete_status = completed` **dentro de la misma transacción** (TX2) que el cascade; un cascade fallido **no** marca `completed` y hace `rollback` dejando la jerarquía intacta; al finalizar, cero registros dependientes
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  - [x] 3.2 Tests de integración del Local_Cascade
    - SQLite: verificar cero registros hijos para monitoreo, módulo e invernadero; verificar `rollback` ante fallo simulado y que no se marca `local_delete_status = completed`
    - _Requirements: 14.3, 5.4, 5.5_

- [x] 4. Implementar DeletionService (autorización + protocolo de dos transacciones)
  - [x] 4.1 Autorización por estado FSM y verificación de existencia
    - Usar la clasificación de `MonitoringState` (deletable = `{ready_for_analysis, completed, error, aborted}`; prohibido = `{initializing, running, paused, finishing, analyzing}`); id inexistente → error "no encontrado"
    - Solo depende de puertos y stdlib
    - _Requirements: 1.1, 1.2, 1.4_
  - [x] 4.2 Chequeo de no interferencia vía `MonitoringRuntimeRegistry`
    - Rechazar si hay worker de captura/análisis activo para el monitoreo objetivo, sin alterar estado ni datos; no afectar workers de otros monitoreos
    - _Requirements: 3.1, 3.2_
  - [x] 4.3 Captura de `remote_id`, rutas de Storage y artefactos locales antes del cascade
    - Vía `SyncStateRepository`, capturar `remote_id` de la entidad raíz y rutas `raw`/`annotated` no nulas de snapshots descendientes
    - Persistir filas `deletion_outbox_local_artifact` **antes** del cascade: para un monitoreo, `monitorings/{monitoring_id}` (ruta **relativa a `OUTPUTS_DIR`**, sin prefijo `outputs/`); para módulo/invernadero, una fila por cada monitoreo descendiente
    - _Requirements: 4.2, 4.12, 4.13, 6.3_
  - [x] 4.4 Protocolo de dos transacciones: TX1 outbox `prepared` (commit) → TX2 cascade + `completed`
    - **TX1:** crear entrada(s) del outbox (+ rutas de Storage + artefactos locales) con `local_delete_status = prepared` y **COMMIT**; si TX1 falla, abortar sin cascade y conservar la jerarquía
    - **TX2:** ejecutar `Local_Cascade` y marcar `local_delete_status = completed` en la **misma transacción**; si TX2 falla, `rollback` del cascade y del marcado `completed` (la entrada queda `prepared`/`failed`) y la eliminación **NO** se propaga remotamente
    - Reintento seguro de entradas `prepared`/`failed` sin duplicación (idempotencia por `entity_type` + `entity_local_id`); ejecutar borrado local sin llamadas de red
    - _Requirements: 1.3, 2.1, 2.3, 4.3, 4.7, 4.8, 4.9, 4.10, 4.11_
  - [x] 4.5 Tests unitarios de autorización y no interferencia
    - Permitido/rechazado por estado; id inexistente; worker activo → rechazo sin alterar estado/datos; fallo de TX1 (outbox) → sin cascade; fallo de TX2 → sin propagación remota
    - _Requirements: 14.1, 14.8, 1.2, 1.4, 3.1, 4.7_

- [x] 5. Eliminación de módulos e invernaderos por el mismo camino duradero
  - [x] 5.1 Validar todos los monitoreos descendientes antes de TX1
    - **Antes** de crear cualquier entrada de outbox y **antes** de cualquier `Local_Cascade`, recorrer TODOS los monitoreos descendientes del módulo/invernadero y permitir la operación solo si TODOS están en `{ready_for_analysis, completed, error, aborted}` **y** ninguno tiene worker de captura/análisis activo en `MonitoringRuntimeRegistry`
    - Si algún descendiente está en estado prohibido o tiene worker activo, **rechazar la operación completa**: sin entrada de outbox, sin `Local_Cascade`, jerarquía intacta
    - _Requirements: 6.5, 6.6, 6.7, 6.8_
  - [x] 5.2 Generar entradas de outbox para jerarquía módulo/invernadero
    - Una sola entrada de datos por raíz (`remote_id` + `remote_table`) apoyada en `ON DELETE CASCADE` remoto; recorrer snapshots descendientes y registrar todas las rutas de Storage no nulas antes del cascade; persistir una fila `deletion_outbox_local_artifact` por monitoreo descendiente
    - Confirmar persistencia del outbox (TX1 `prepared` + COMMIT) antes de iniciar el `Local_Cascade`; abortar si el registro falla
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.5_
  - [x] 5.3 Tests de cascade, validación de descendientes y captura de rutas para módulo/invernadero
    - Verificar una entrada raíz de datos + N filas de rutas de Storage descendientes + N filas de artefactos locales; fallo de outbox → aborta sin cascade
    - Rechazo de la operación completa (sin outbox, sin cascade, jerarquía intacta) cuando algún descendiente está en estado prohibido o tiene worker activo; permitir cuando todos los descendientes son eliminables y sin worker
    - _Requirements: 14.3, 14.9, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

- [x] 6. Rutas de eliminación en la UI (`agricultural_ui.py`)
  - [x] 6.1 Endpoints de borrado con acción "Eliminar" visible, confirmación explícita y retorno inmediato
    - Exponer una acción "Eliminar" **visible** en el flujo de historial/detalle del módulo, ejecución y reporte, **sin depender de escribir la URL manualmente**, con confirmación explícita antes de invocar el borrado
    - Rutas para eliminar monitoreo/módulo/invernadero que delegan en `DeletionService` y retornan de inmediato tras el borrado local; sin lógica de negocio en el route
    - El backend (`DeletionService`) **re-valida** estado y ausencia de worker activo aunque la UI oculte el botón; en éxito, la entidad y sus hijos dejan de mostrarse; en fallo, mensaje de error conservando datos
    - _Requirements: 2.2, 2.4, 2.5, 2.6, 2.7_
  - [x] 6.2 Tests de las rutas de eliminación con `DeletionService` fake
    - Verificar acción visible/confirmación requerida; delegación en `DeletionService`; respuesta inmediata; error seguro con propagación del mensaje conservando datos; y que **NO** se ejecuta el borrado cuando el backend rechaza por estado prohibido o worker activo
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

- [x] 7. Adaptadores Supabase idempotentes
  - [x] 7.1 `SupabaseDataAdapter.delete_by_id` idempotente
    - DELETE PostgREST: `204`/`404` = éxito (`already_absent`); validación previa de token/tabla/remote_id; clasificar `CONNECTIVITY`/`REMOTE_UNAVAILABLE` como reintentable, `RLS_DENIED`/`UNKNOWN` como error reintentable
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6_
  - [x] 7.2 `SupabaseStorageAdapter.remove_object` idempotente
    - `404`/inexistente = éxito; otros errores → reintentable
    - _Requirements: 8.2, 8.3_
  - [x] 7.3 Tests unitarios con transporte httpx fake / mocks
    - DELETE de datos: ausencia → éxito; doble invocación → resultados idénticos; validación de campos vacíos; error de conectividad → reintentable con `last_error`
    - Storage: objeto inexistente → éxito
    - _Requirements: 14.4, 14.5, 7.3, 7.4, 8.2_

- [x] 8. Fase de eliminación en `RemoteSyncService` (antes de upsert)
  - [x] 8.1 Añadir FASE 0 de eliminación al inicio de `execute_sync`
    - Procesar entradas cuyo estado remoto sea `pending`, `error`, **o** `syncing` recuperado tras un crash/reinicio (`syncing` persistido es reintentable), ordenadas por `created_at ASC`, **solo cuando `local_delete_status = completed`**; entradas `prepared`/`failed` **nunca** se propagan; marcar `syncing`; emitir `delete_by_id` de la raíz apoyado en cascada remota; entrada sin `remote_id` omite DELETE de datos
    - Mantener unidireccionalidad local → remoto; conservar propiedad de token/lock en `sync_api.py`
    - _Requirements: 4.10, 9.1, 9.2, 9.3, 10.1, 10.2_
  - [x] 8.2 Limpieza de Storage sin huérfanos por entrada
    - Recorrer rutas `pending` **o** `error`; `remove_object` por ruta; marcar `removed` (único estado terminal); una ruta con error no-404 mantiene la entrada `error` reintentable y se reintenta en la siguiente sincronización; solo marcar entrada `synced` cuando datos y **todas** las rutas estén `removed`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - [x] 8.3 Manejo de fallos, continuación y anti-resurrección
    - Fallo de DELETE (conectividad/indisponible) → `error` + `last_error`, sin re-subir la entidad; continuar con las entradas restantes; nunca eliminar la entrada del outbox; entradas reintentables se reintentan en la siguiente sincronización
    - _Requirements: 9.4, 9.5, 10.3, 11.1, 11.2, 11.3_
  - [x] 8.4 Tests de integración de la fase de eliminación con fakes
    - Anti-resurrección: eliminación corre antes del upsert y la entidad no se re-sube; gating por `local_delete_status = completed`; sin huérfanos al marcar `synced`; reintento offline con `last_error`; continuación ante fallos
    - _Requirements: 14.6, 14.7, 14.5, 10.1, 10.2, 8.4_

- [x] 9. Checkpoint — Ejecutar las pruebas dirigidas acumuladas
  - Ensure all tests pass, ask the user if questions arise.
  - Ejecutar **solo los módulos de test dirigidos/relevantes** de los componentes implementados hasta ahora (puertos/DTOs, outbox, local cascade, DeletionService, módulo/invernadero, adaptadores Supabase, fase de eliminación de RemoteSyncService), **no** la suite completa. Sin hardware/cámara/red.

- [ ] 10. Documentar migración remota prerrequisito (SOLO documentación)
  - [~] 10.1 Escribir artefacto SQL de migración + nota de revisión humana
    - Crear `docs/migrations/021-monitorings-status-check.sql` con el `ALTER` de la restricción CHECK real `ck_monitorings_status` de `public.monitorings.status` para agregar `ready_for_analysis`, conservando los valores previos (`initializing`, `running`, `paused`, `finishing`, `analyzing`, `completed`, `aborted`, `error`); sin tocar PK/UUID/FK/RLS/relaciones
    - Incluir nota explícita: artefacto de diseño/documentación, **no** aplicar automáticamente; requiere revisión humana previa
    - _Requirements: 12.1, 12.2, 12.3_

- [ ] 11. FASE FINAL — Limpieza física local con retención configurable
  - [~] 11.1 Añadir `RETENTION_WINDOW_HOURS` en `settings.py`
    - Configurable en horas, default 24; valor no numérico válido → 24 y registrar advertencia; sin hardcode
    - _Requirements: 13.2, 13.3_
  - [~] 11.2 Implementar `CleanupProcess` (limpieza física diferida)
    - Usar `deleted_at`/`cleanup_status` como marcador; para `cleanup_status = pending` con `local_delete_status = completed`, si `now_utc - deleted_at >= Retention_Window`, procesar las rutas **persistidas** en `DeletionOutboxLocalArtifactModel` (nunca reconstruidas tras borrar SQLite)
    - Validar cada `relative_path` con `validate_safe_path(relative_path, OUTPUTS_DIR)` (anti-traversal) antes de borrar, **sin re-anteponer `outputs/`** porque `relative_path` ya es relativa a `OUTPUTS_DIR`; path inexistente → **éxito idempotente**; marcar cada fila de artefacto `done` (éxito) o `error` + `last_error` (fallo) con reintento; `cleanup_status = done` cuando todas las filas están `done`; no depende de `status = synced` ni de red
    - _Requirements: 5.6, 13.1, 13.4, 13.5, 13.6, 13.7_
  - [~] 11.3 Cablear trigger de `CleanupProcess`
    - Invocar en el arranque de la app (`app/main.py` startup) y opcionalmente tras la sincronización manual; operación ligera de filesystem, sin hooks pesados ni inferencia
    - _Requirements: 13.4_
  - [~] 11.4 Tests de retención y limpieza duradera
    - Verificar que el `Local_Cascade` no borra `outputs/` de inmediato; limpieza solo tras el `Retention_Window` y con `local_delete_status = completed`; uso de rutas persistidas; path inexistente → éxito idempotente; valor inválido → 24 + advertencia; fallo de borrado → reintento sin perder artefactos restantes; sin dependencia de red
    - _Requirements: 13.1, 13.3, 13.5, 13.6, 13.7, 13.8, 5.6_

- [ ] 12. Checkpoint final — Cobertura de Req 14 y suite completa
  - [~] 12.1 Consolidar/verificar cobertura del Requirement 14
    - Confirmar que existen pruebas para: estados permitidos/prohibidos (14.1), durabilidad tras reinicio (14.2), cascade local sin huérfanos (14.3), DELETE remoto idempotente (14.4), reintento offline con `last_error` (14.5), Storage sin huérfanos (14.6), anti-resurrección (14.7), no interferencia con workers activos (14.8), rechazo de borrado de módulo/invernadero con descendiente no eliminable o con worker activo (14.9); agregar los tests faltantes
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9_
  - [~] 12.2 Ejecutar la suite completa como verificación final
    - Ensure all tests pass, ask the user if questions arise. Ejecutar `python -m pytest -q` (sin hardware/cámara/red) — **única** ejecución de la suite completa.

## Notes

- La única tarea marcada con `*` es la 1.4 (contrato de DTOs/puertos). La 6.2 (rutas de eliminación, operación destructiva) es **obligatoria**. Las pruebas que validan el Requirement 14 (2.4, 3.2, 4.5, 5.3, 7.3, 8.4, 11.4, 12.1), incluida la 14.9 (rechazo de borrado de módulo/invernadero con descendiente no eliminable o con worker activo, cubierta por 5.3), son **obligatorias**.
- Durante la implementación se ejecutan solo pruebas dirigidas por componente; la suite completa se corre **una sola vez al final** (12.2).
- Cada tarea referencia sub-requisitos específicos para trazabilidad.
- El diseño usa verificación por ejemplos/integración con fakes; **no** property-based testing.
- La limpieza física local (`CleanupProcess`/`Retention_Window`) es la **última fase de implementación** por diseño (Req 5.6, 13); usa rutas persistidas en `deletion_outbox_local_artifact`, nunca reconstruidas.
- El borrado local usa dos transacciones (TX1 `prepared` + COMMIT, TX2 cascade + `completed`); solo se propagan remotamente entradas con `local_delete_status = completed`.
- La migración remota (tarea 10) es **solo documentación** y no debe aplicarse automáticamente.
- No se toca el pipeline de visión, Spec 019, el ciclo de captura/análisis de Spec 020, modelos de ML, `ExportService` ni el dashboard.
- Se sugiere documentar un ADR (`docs/decisions/ADR-NNN-durable-deletion-outbox.md`) para el buzón duradero y el orden anti-resurrección.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "2.1", "7.1", "7.2"] },
    { "id": 2, "tasks": ["2.2", "7.3"] },
    { "id": 3, "tasks": ["2.3", "3.1"] },
    { "id": 4, "tasks": ["2.4", "3.2", "4.1"] },
    { "id": 5, "tasks": ["4.2", "4.3"] },
    { "id": 6, "tasks": ["4.4"] },
    { "id": 7, "tasks": ["4.5", "5.1", "6.1"] },
    { "id": 8, "tasks": ["5.2", "6.2", "8.1"] },
    { "id": 9, "tasks": ["5.3", "8.2", "8.3"] },
    { "id": 10, "tasks": ["8.4"] },
    { "id": 11, "tasks": ["9"] },
    { "id": 12, "tasks": ["10.1"] },
    { "id": 13, "tasks": ["11.1", "11.2"] },
    { "id": 14, "tasks": ["11.3"] },
    { "id": 15, "tasks": ["11.4", "12.1"] },
    { "id": 16, "tasks": ["12.2"] }
  ]
}
```
