# Spec 025 — Tareas: Gestión segura de exportaciones

Estado: **implementada y verificada** con tests dirigidos.

Resultado final de tests dirigidos (Python 3.14.3, sin venv, `py -m pytest`):
- `test_export_deletion_service.py` + `test_export_deletion_data_safety.py` +
  `test_new_repositories.py` + `test_export_service.py` + `test_export_routes.py`
  → **88 passed, 1 skipped** (symlink en Windows).
- `test_imports.py` + `test_architecture_boundaries.py` → **39 passed, 1 skipped**.
- `git diff --check` → limpio.

> Nota de baseline: la suite general tiene fallos `TestClient` preexistentes en
> Python 3.14; no atribuirlos a Spec 025. El único fallo del baseline dirigido
> (`test_download_rejects_unsafe_external_file_path`) era un defecto de datos del
> propio test de export (mock sin `created_by_user_id`) y quedó corregido.

---

- [x] 0. Baseline dirigido
  - Registrado: 1 failed, 50 passed (fallo preexistente por mock incompleto).

- [x] 1. Contrato del repositorio: `delete`
  - [x] 1.1 `ExportPackageRepository.delete(id) -> None` (solo registro, no FS
    I/O, no-op idempotente).
  - [x] 1.2 `SqlExportPackageRepository.delete`: buscar; si no existe `return`;
    `session.delete`; `commit`.

- [x] 2. `ExportDeletionService` (aplicación)
  - [x] 2.1 Servicio con `exports_root` inyectable + excepciones de aplicación
    (`ExportNotFoundError`, `ExportStatusNotDeletableError`,
    `ExportGeneratingError`, `UnsafeExportPathError`, `ExportFileDeletionError`,
    `ExportRecordDeletionError`).
  - [x] 2.2 Clasificación de filesystem (`resolve()` + `relative_to` + `.zip`,
    sin `is_file()`; `SAFE_EXISTS`/`SAFE_MISSING`/`UNSAFE`; directorio → UNSAFE).
  - [x] 2.3 Orden estricto: ownership → whitelist de estados
    (`_DELETABLE_STATUSES = {"completed", "error"}`) → path → `unlink` →
    `repo.delete`, con fallos parciales controlados.

- [x] 3. Ruta delgada `POST /exportar/{id}/eliminar`
  - [x] 3.1 Autentica, obtiene repo, invoca el servicio, traduce
    resultado/errores a `RedirectResponse` 303. Sin lógica de filesystem.

- [x] 4. Feedback en rutas
  - [x] 4.1 `GET /exportar` lee `?success`/`?error`.
  - [x] 4.2 `GET /exportar/{id}` lee `?error`/`?success`.

- [x] 5. UI — lista (`export_list.html`)
  - [x] 5.1 Tamaño (si disponible); basura `btn-icon btn-icon--danger` +
    `aria-label`; diálogo por item; feedback. Basura SOLO para
    `completed`/`error`. Conserva detalle/descarga/empty state.

- [x] 6. UI — detalle (`export_detail.html`)
  - [x] 6.1 Solo render de `?error`/`?success`. Sin botón de eliminación.

- [x] 7. Tests — servicio
  - [x] 7.1 Ownership/estados: own completed borra; foreign → not found; missing
    → not found; error borra; **pending → bloqueado**; generating → bloqueado.
  - [x] 7.2 Filesystem (`tmp_path`): None; ZIP válido; ausente; traversal;
    absoluto externo; prefijo `exports_evil`; symlink fuera (skip Windows);
    directorio. `repo.delete` NO llamado en casos unsafe.
  - [x] 7.3 Fallos parciales: `unlink` `OSError` → registro permanece;
    `repo.delete` falla tras `unlink` OK → archivo borrado, registro permanece.

- [x] 8. Tests — repositorio
  - [x] 8.1 delete existing → gone; nonexistent → no exception; solo el objetivo.

- [x] 9. Tests — data safety
  - [x] 9.1 Tras eliminar el ExportPackage, fuentes (Monitoring, Snapshot,
    InspectionResult, MonitoringMetrics, ActivityLog, User) permanecen.

- [x] 10. Tests — ruta/UI
  - [x] 10.1 Ruta: delega; éxito 303 `?success`; not found; generating;
    **pending bloqueado**; unsafe; error controlado (sin 500).
  - [x] 10.2 UI lista: basura `btn-icon--danger` + `aria-label`; diálogo;
    Cancelar; Eliminar exportación; wording de datos originales;
    detalle/descarga; empty state; tamaño; feedback; **completed/error muestran
    eliminar; pending/generating NO lo muestran**.
  - [x] 10.3 UI detalle: muestra error de descarga; sin botón de eliminación.

- [x] 11. Verificación final
  - [x] 11.1 Tests dirigidos verdes; `git diff --check` limpio; sin cambio de
    esquema, sin nuevas dependencias, sin Supabase/RLS/outbox/sync.
