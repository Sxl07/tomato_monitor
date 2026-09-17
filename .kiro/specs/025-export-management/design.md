# Spec 025 — Diseño: Gestión segura de exportaciones

## Resumen

Se añade eliminación manual, owner-scoped y segura de paquetes de exportación,
reutilizando la arquitectura y los patrones de UI existentes. No se reconstruye
la generación, el manifest ni la descarga. No hay cambio de esquema.

Eliminar una exportación =
1. borrar el ZIP local asociado, solo si es seguro y existe; y
2. borrar el registro `ExportPackage`.

Nunca se tocan datos fuente (monitoreos, snapshots, métricas, actividades),
outbox, sync ni Supabase.

---

## Auditoría del estado actual (base del diseño)

### Rutas (`app/routes/agricultural_ui.py`)

| Ruta | Método | Estado actual |
|---|---|---|
| `/exportar` | GET | Lista `export_repo.list_by_user(user.id)`. No lee `?error`/`?success`. |
| `/exportar` | POST | Generación **síncrona** dentro de la request. Crea registro `generating`, llama `ExportService.generate_export()` inline, actualiza a `completed`/`error`, redirige a `/exportar/{id}`. |
| `/exportar/{id}` | GET | Ownership OK: si `None` o `created_by_user_id != user.id` → redirige a `/exportar?error=...`. |
| `/exportar/{id}/descargar` | GET | Ownership OK. Exige `status == completed`. Usa `_is_safe_export_path`. |

`_is_safe_export_path(file_path)` (existente): `Path(file_path).resolve()`,
`relative_to(Path("outputs/exports").resolve())`, exige sufijo `.zip` y
`is_file()`. Rechaza `None`. Maneja colisión de prefijo (`exports_evil`) vía
`relative_to`.

### Repositorio

- Interfaz `ExportPackageRepository` (`src/domain/repositories/`): `create`,
  `get_by_id`, `list_by_user`, `update`, `list_pending`. **Sin `delete`.**
- Implementación `SqlExportPackageRepository`
  (`src/infrastructure/persistence/repositories/`): commit por método.
- Modelo `ExportPackageModel` (`src/infrastructure/persistence/models/`): tabla
  `export_packages`, FK a `users.id`.
- Entidad `ExportPackage` (`src/domain/entities/`): valida `scope` y `status`
  (`pending`, `generating`, `completed`, `error`).

### Servicio

- `ExportService.generate_export()` (`src/application/services/`): stdlib puro,
  escribe `outputs/exports/tomato_monitor_export_{ts}.zip`.

### UI (patrones reutilizables)

- `partials/confirm_dialog.html`: diálogo reutilizable (variables `dialog_id`,
  `dialog_message`, `confirm_url`, `confirm_text`, `cancel_text`), botón
  destructivo `btn-danger`, helpers `showConfirmDialog`/`hideConfirmDialog`, form
  POST al confirmar.
- Rutas de borrado existentes (`/invernaderos/{id}/eliminar`,
  `/modulos/{id}/eliminar`, `/monitoreos/{id}/eliminar`): re-validan ownership,
  redirigen con `?error=` (usando `urllib.parse.quote`). **Este es el patrón a
  seguir.**
- Iconos: `btn-icon btn-icon-danger` con `icon('delete')` y `aria-label`. Filtro
  `to_bogota` para fechas.

### Gaps encontrados

1. No existe `ExportPackageRepository.delete(id)` ni su implementación SQL.
2. No existe ruta `POST /exportar/{id}/eliminar`.
3. No existe acción de eliminación en `export_list.html` ni en
   `export_detail.html`.
4. No existe una validación de contención pensada para **borrado** (la existente
   `_is_safe_export_path` exige `is_file()`, lo que impide limpiar registros con
   ZIP ausente). Se necesita un chequeo de contención separado del `is_file()`.
5. `GET /exportar` no muestra feedback (`?success`/`?error`).

---

## Decisiones de diseño

1. **Servicio de aplicación dedicado + ruta delgada.** Se crea
   `ExportDeletionService` (`src/application/services/export_deletion_service.py`)
   que concentra toda la coordinación: carga del paquete, ownership, guard de
   estado, validación de filesystem, `unlink` y `repository.delete`. La ruta HTTP
   solo autentica, obtiene el repositorio, invoca el servicio y traduce el
   resultado a `RedirectResponse`. **No** se reutiliza el `DeletionService`
   agrícola (Spec 021), que maneja jerarquía, outbox y propagación remota. La
   eliminación de `ExportPackage` no usa outbox, no sincroniza, no llama a
   Supabase y no borra datos remotos.
2. **`delete` en el repositorio, sin FS I/O.** Se agrega
   `ExportPackageRepository.delete(id: int) -> None` y su implementación SQL.
   Solo elimina el registro (no-op idempotente si el id no existe). La
   coordinación ZIP+registro vive en `ExportDeletionService`.
3. **Seguridad de path fail-closed en el servicio.** La validación de contención
   vive en `ExportDeletionService`: valida que el path resuelto esté dentro de
   `exports_root` y sea `.zip`, **sin** requerir `is_file()`. Ante un path
   inseguro (traversal, ruta absoluta externa, symlink que resuelve fuera) se
   aborta toda la operación, se conserva el registro y se devuelve error. Un nodo
   existente que no sea archivo regular tampoco se elimina. Se reutiliza la
   semántica de contención de `_is_safe_export_path` (misma base `resolve()` +
   `relative_to`), pero `_is_safe_export_path` **no se modifica**.
4. **`exports_root` inyectable.** El servicio recibe `exports_root` (default
   producción `outputs/exports`) para permitir `tmp_path` en tests.
5. **Orden estricto y fallos parciales.** (1) validar; (2) `unlink` si seguro y
   existe; (3) `repository.delete`. Si `unlink` falla → no se borra el registro.
   Si `unlink` tiene éxito pero `repository.delete` falla → archivo eliminado,
   registro conservado (recuperable como `SAFE_MISSING`). No se invierte el
   orden. No hay tombstones ni transacción atómica FS+SQLite.
6. **Whitelist fail-closed de estados: solo `completed`/`error`.** Se bloquean
   `pending` y `generating` porque ambos pueden representar un paquete en uso:
   `POST /exportar` y `POST /sincronizacion/local` crean el `ExportPackage` y
   luego ejecutan `ExportService.generate_export` de forma síncrona en la misma
   request (`/sincronizacion/local` usa `pending` durante la generación). No hay
   registry/job para probar que un no-terminal está inactivo. Se usa la constante
   `_DELETABLE_STATUSES = frozenset({"completed", "error"})` como gate real. No
   se crea worker/cola/registry/jobs; no se modifica `/sincronizacion/local`.
   Limitación conocida: un `pending`/`generating` huérfano por interrupción no se
   puede limpiar manualmente en esta Spec (integridad sobre cleanup).
7. **Sin cambio de esquema.** `ExportPackage` ya tiene la metadata necesaria. No
   se introduce soft-delete.
8. **Ownership reutilizado.** En el servicio: `get_by_id` + comprobación
   `created_by_user_id == user_id`; en caso contrario, tratar como no encontrado,
   **antes** de cualquier filesystem I/O y sin revelar owner/status/file_path/
   metadata.
9. **UI mínima, solo en la lista.** Reutilizar `confirm_dialog.html`; añadir
   icono de basura (`btn-icon btn-icon--danger`) en la lista. **No** se añade
   borrado en el detalle; el detalle solo recibe feedback `error`/`success`. Sin
   JS nuevo más allá de los helpers existentes; form POST.

---

## Arquitectura

### Capas afectadas

- **Dominio:** `ExportPackageRepository.delete(id)` (nuevo método en la
  interfaz).
- **Infraestructura:** `SqlExportPackageRepository.delete(id)` (implementación
  SQL, solo registro; no-op idempotente si no existe).
- **Aplicación:** nuevo `ExportDeletionService` con toda la coordinación y la
  validación de filesystem. `exports_root` inyectable.
- **Presentación:** nueva ruta delgada `POST /exportar/{id}/eliminar`; ajustes
  en `export_list.html` (basura + confirm + tamaño + feedback) y
  `export_detail.html` (solo feedback); feedback `?success`/`?error` en
  `GET /exportar` y `GET /exportar/{id}`.

Se respeta la regla de arquitectura: la capa SQL no hace FS I/O; la validación y
el `unlink` viven en la capa de aplicación (`ExportDeletionService`), no en la
ruta ni en el repositorio.

### Responsabilidades

```
ROUTE (app/routes/agricultural_ui.py)
    - autenticación (Depends require_current_user_html)
    - obtiene ExportPackageRepository
    - crea ExportDeletionService(repo)  [exports_root por defecto]
    - invoca service.delete_export(package_id=id, user_id=user.id)
    - traduce resultado/errores a RedirectResponse 303

APPLICATION SERVICE (ExportDeletionService)
    - repo.get_by_id(package_id)
    - ownership (created_by_user_id == user_id) -> si no, NotFound
    - status guard (generating -> Blocked)
    - validación filesystem (contención + .zip, sin is_file)
    - unlink (si SAFE_EXISTS)
    - repo.delete(package_id)

REPOSITORY (SqlExportPackageRepository)
    - exclusivamente SQLite ExportPackage
    - delete(id): buscar, si no existe return; session.delete; commit
```

### Contrato del servicio

```
class ExportDeletionService:
    def __init__(self, repository, exports_root="outputs/exports"): ...
    def delete_export(self, package_id: int, user_id: int) -> None:
        # raises ExportNotFoundError
        #        | ExportStatusNotDeletableError (ExportGeneratingError subclass)
        #        | UnsafeExportPathError | ExportFileDeletionError
        #        | ExportRecordDeletionError
```

Se usan excepciones de aplicación específicas (definidas en el mismo módulo)
para que la ruta las traduzca a mensajes en español. Éxito = retorno normal.

### Flujo de eliminación

```
Operario pulsa el icono de basura (lista)
  → showConfirmDialog abre el diálogo de confirmación (sin borrar aún)
  → Operario confirma → form POST /exportar/{id}/eliminar
  → ROUTE llama service.delete_export(id, user.id)
      1. package = repo.get_by_id(id)
      2. Ownership: si package is None o created_by_user_id != user_id
             → raise ExportNotFoundError            (no FS I/O previo)
      3. Estado (whitelist fail-closed): si status not in {completed, error}
             → raise ExportGeneratingError (si generating)
             → raise ExportStatusNotDeletableError (pending u otro no-terminal)
      4. Clasificar file_path:
           - None            → SAFE_MISSING (sin unlink)
           - inseguro        → raise UnsafeExportPathError (fail-closed)
           - existe y no es archivo regular → raise UnsafeExportPathError
           - seguro y existe → SAFE_EXISTS
           - seguro y ausente→ SAFE_MISSING
      5. si SAFE_EXISTS: unlink(ZIP)
             - si OSError → raise ExportFileDeletionError (registro intacto)
      6. repo.delete(id)
             - si falla → raise ExportRecordDeletionError (archivo ya borrado,
               registro conservado, recuperable)
  → ROUTE traduce:
      éxito                 → 303 /exportar?success=Exportación+eliminada
      ExportNotFoundError          → 303 /exportar?error=Exportación+no+encontrada
      ExportGeneratingError        → 303 /exportar?error=No+se+puede+eliminar+una+exportación+mientras+se+está+generando
      ExportStatusNotDeletableError→ 303 /exportar?error=No+se+puede+eliminar+una+exportación+en+curso
      UnsafeExportPathError        → 303 /exportar?error=No+se+pudo+eliminar:+la+ruta+del+archivo+no+es+segura
      *FileDeletionError /
      *RecordDeletionError  → 303 /exportar?error=<mensaje+controlado>  (sin 500)
```

Orden estricto: validar ownership/estado/path → `unlink` (solo si seguro y
existe) → `repository.delete`. No se invierte.

### Clasificación de filesystem (dentro del servicio)

- Entrada: `file_path: str | None`, `exports_root: Path`.
- `None` → `SAFE_MISSING` (el registro puede borrarse; sin `unlink`).
- `resolved = Path(file_path).resolve()`.
- Contención: `resolved.relative_to(exports_root.resolve())`; si falla → `UNSAFE`.
- Sufijo: `resolved.suffix.lower() != ".zip"` → `UNSAFE`.
- Existencia con `resolved.exists()`:
  - no existe → `SAFE_MISSING`
  - existe y `is_file()` → `SAFE_EXISTS`
  - existe y no es archivo regular (p. ej. directorio) → `UNSAFE`
- Symlink que resuelve fuera: `resolve()` sigue el symlink, `relative_to` falla →
  `UNSAFE` → fail-closed.
- **No** se modifica `_is_safe_export_path` (usado por la descarga, que sí exige
  `is_file()`).

### Fallos parciales (sin transacción atómica FS+SQLite)

- `unlink` lanza `OSError` → **no** se ejecuta `repository.delete`; registro
  conservado; error controlado.
- `unlink` OK pero `repository.delete` falla → archivo ya eliminado, registro
  conservado; error controlado. Recuperable: un intento posterior clasifica el
  path como `SAFE_MISSING` y elimina el registro.
- Se prefiere un registro visible/reintentable con ZIP ausente antes que un ZIP
  huérfano sin registro. Sin tombstones ni protocolo transaccional adicional.

---

## Comportamiento por estado

| Estado | ¿Eliminable? | Comportamiento |
|---|---|---|
| `completed` | Sí | Borra ZIP (si seguro/existe) + registro. |
| `error` | Sí | Limpia registro; borra ZIP si existe y es seguro. |
| `pending` | No | Rechazar (sin FS I/O, sin `repo.delete`); conservar registro. |
| `generating` | No | Rechazar (sin FS I/O, sin `repo.delete`); conservar registro. |

**Nota de generación (por qué se bloquea `pending`):** `POST /exportar` y
`POST /sincronizacion/local` crean el `ExportPackage` y ejecutan
`ExportService.generate_export` de forma síncrona dentro de la misma request.
`POST /sincronizacion/local` crea el paquete con `status="pending"` y genera el
ZIP a continuación, por lo que `pending` puede representar un paquete en uso. Sin
un runtime/job registry no hay forma fiable de distinguir un `pending` inactivo
de uno en uso, así que se aplica fail-closed. No se crea sistema de jobs ni se
modifica `/sincronizacion/local`.

---

## Estrategia de ownership

- La comprobación vive en `ExportDeletionService`: `repo.get_by_id(id)` y
  comprobar `created_by_user_id == user_id`, **antes** de cualquier filesystem
  I/O.
- Un id ajeno o inexistente se trata idénticamente como `ExportNotFoundError`
  (la ruta redirige a `/exportar?error=Exportación+no+encontrada`), sin revelar
  owner, status, file_path ni metadata.
- Auditoría de ownership existente: detalle y descarga ya validan ownership
  correctamente; no se detectó fuga de metadata. No se requieren cambios de
  ownership en esas rutas; solo se añade la nueva ruta con el mismo patrón,
  delegando al servicio.

---

## Riesgos de filesystem

1. **Path traversal / ruta absoluta externa / symlink fuera.** Mitigado con
   `resolve()` + `relative_to(outputs/exports)` y fail-closed.
2. **Archivo ausente.** No es un error: se limpia el registro.
3. **Prefijo colisionante** (`outputs/exports_evil`). Mitigado por `relative_to`
   (ya cubierto por los tests del helper existente).
4. **Borrado fuera del directorio autorizado.** Imposible por diseño: solo se
   hace `unlink` tras validar contención.
5. **Condición de carrera con generación.** Mitigada bloqueando `generating`.

---

## UI / UX (800×480, touch-first)

### Lista (`export_list.html`)

- Cada item conserva: `#id`, fecha (`to_bogota`), estado (badge), registros,
  imágenes; añadir tamaño si disponible.
- Acciones por item: "Ver detalle", "Descargar" (solo `completed`) y un
  icono/botón de basura con clase **`btn-icon btn-icon--danger`** (no
  `btn-icon-danger`), `icon('delete')` y `aria-label="Eliminar exportación"`.
  El objetivo táctil ≥ 44×44 px ya lo cubre `btn-icon`. La basura se muestra
  SOLO cuando `pkg.status in ('completed', 'error')`; `pending`/`generating`
  quedan visibles pero sin acción destructiva.
- El botón de basura invoca `showConfirmDialog` de un diálogo por item
  (`dialog_id` único, p. ej. `confirm-delete-{{ pkg.id }}`). El primer toque solo
  abre la confirmación; la eliminación ocurre únicamente por el POST del form al
  confirmar.
- Diálogo (partial `confirm_dialog.html` reutilizado):
  - Mensaje: "¿Eliminar esta exportación? Se eliminarán el archivo ZIP y el
    registro de esta exportación. Los monitoreos, imágenes y demás datos
    originales no se eliminarán."
  - `confirm_url = /exportar/{id}/eliminar`, `confirm_text = "Eliminar
    exportación"`, `cancel_text = "Cancelar"`.
- Estado vacío conservado: "No hay exportaciones registradas."
- Feedback: `/exportar` renderiza `?success`/`?error` con los componentes de
  alerta existentes.

### Detalle (`export_detail.html`) — solo feedback

- Conservar todo lo actual (status, error, scope, fechas, registros, imágenes,
  tamaño, manifest, warnings, descarga).
- **No** se añade botón de eliminación en el detalle (decisión de MVP compacto;
  la acción destructiva vive únicamente en `/exportar`).
- Se añade únicamente el render de `?error`/`?success` (query params), lo que
  hace visible el error de descarga ya usado (`?error=Archivo+no+disponible`).

### Errores y mensajes (Spanish, accionables)

- Ownership/no encontrado: "Exportación no encontrada".
- Generating: "No se puede eliminar una exportación mientras se está generando".
- Path inseguro: "No se pudo eliminar: la ruta del archivo no es segura".
- Éxito: "Exportación eliminada".

---

## Compatibilidad y límites

- Offline/local-first: la eliminación no llama a Supabase, no toca RLS, no usa
  sync, no crea outbox, no depende de red.
- Sin nuevas dependencias; solo stdlib/`pathlib` y arquitectura existente.
- Sin frameworks JS ni `fetch`; form POST + `confirm_dialog.html`.
- Sin cambio de esquema; sin soft-delete.

## Archivos a crear/modificar (implementación)

Crear:
- `src/application/services/export_deletion_service.py` — `ExportDeletionService`
  + excepciones de aplicación.
- `tests/application/test_export_deletion_service.py` — tests del servicio
  (sin `TestClient`), incluidos filesystem y fallos parciales.
- `tests/infrastructure/persistence/` — tests de `delete` (o ampliar
  `test_new_repositories.py`).

Modificar:
- `src/domain/repositories/export_package_repository.py` — añadir `delete`.
- `src/infrastructure/persistence/repositories/sql_export_package_repository.py`
  — implementar `delete` (solo registro, idempotente).
- `app/routes/agricultural_ui.py` — ruta `POST /exportar/{id}/eliminar`
  (delgada), feedback en `GET /exportar` y `GET /exportar/{id}`.
- `app/templates/agricultural/export_list.html` — acción de basura + diálogo +
  tamaño + feedback.
- `app/templates/agricultural/export_detail.html` — solo feedback `error`/
  `success`.
- `tests/unit/test_export_routes.py` — tests de la ruta/UI.

No se toca: `ExportService`, formato ZIP, manifest, esquema, `DeletionService`
agrícola, `_is_safe_export_path`.

---

## Estrategia de tests (detallada en tasks.md)

### Repositorio
- `delete` de paquete existente elimina el registro.
- Semántica de `delete` de id inexistente (idempotente-seguro / comportamiento
  documentado).

### Ownership
- Usuario elimina su propia exportación (éxito).
- Usuario NO puede eliminar exportación ajena (no borra, tratado como no
  encontrado).
- Detalle ajeno no accesible.
- Descarga ajena no accesible.

### Filesystem (en `ExportDeletionService`, con `exports_root=tmp_path`)
- ZIP válido dentro de `exports_root` → se elimina + registro borrado.
- `file_path` None → no unlink; registro borrado.
- ZIP ausente → registro borrado, sin error.
- Traversal `../` fuera → fail-closed (repo.delete NO llamado).
- Ruta absoluta externa → fail-closed (repo.delete NO llamado).
- Colisión de prefijo (`exports_evil`) → fail-closed.
- Symlink que resuelve fuera → fail-closed.
- Path existente que no es archivo regular (directorio) → fail-closed.
- `unlink` lanza `OSError` → registro permanece (repo.delete NO llamado).
- `repo.delete` falla tras `unlink` OK → archivo eliminado, registro permanece.

### Data safety (tras eliminar `ExportPackage`)
- Monitoring, snapshots, inspection results, métricas y actividades permanecen.
- Sin cambios en sync/outbox.
- Sin acción de Supabase.

### Estados
- `completed` eliminable, `error` eliminable.
- `pending` bloqueado (sin FS I/O, sin `repo.delete`, registro conservado).
- `generating` bloqueado (sin FS I/O, sin `repo.delete`, registro conservado).

### UI
- Acción de basura/eliminar presente en la lista SOLO para `completed`/`error`.
- `pending` y `generating` visibles pero SIN acción destructiva.
- Confirmación previa (no borra al primer toque).
- Botón "Cancelar" presente.
- Texto destructivo correcto y consistente.
- Objetivo táctil ≥ 44×44 px (cubierto por `btn-icon`).
- Descarga/detalle preservados.
- Estado vacío.
- Renderizable en 800×480.

### Baseline dirigido (antes de implementar)
Capturar baseline de: tests de export service, export repository, export
routes/UI y ownership relacionados. **Recordatorio:** la suite general tiene
fallos `TestClient` preexistentes en Python 3.14; no atribuirlos a Spec 025.
