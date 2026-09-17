"""Application service for safely deleting export packages.

Deleting an export package means EXCLUSIVELY:

1. deleting the associated local ZIP file, if it exists and is safe; and
2. deleting the ExportPackage record from persistence.

It NEVER deletes source data (monitorings, snapshots, inspection results,
metrics, activity logs, etc.), and it never touches sync, outbox, or Supabase.

Design notes:

- All coordination (ownership, status guard, filesystem validation, unlink,
  repository delete) lives here so the HTTP route stays thin.
- This service intentionally does NOT reuse the agricultural ``DeletionService``
  (Spec 021), which handles hierarchy, outbox, and remote propagation.
- Filesystem safety is fail-closed: an unsafe ``file_path`` aborts the whole
  operation and preserves the record.
- Deletion order is strict: validate -> unlink (only if safe & present) ->
  repository.delete. It is never reversed. There is no atomic FS+SQLite
  transaction; partial failures leave a recoverable state (a record pointing to
  an absent ZIP, which a later attempt treats as ``SAFE_MISSING``).

Only the Python standard library (``pathlib``) is used for filesystem work.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from src.domain.repositories.export_package_repository import (
    ExportPackageRepository,
)

DEFAULT_EXPORTS_ROOT = "outputs/exports"

# States for which manual deletion is allowed (fail-closed whitelist).
#
# Only terminal states are deletable. ``pending`` and ``generating`` are
# excluded because both can represent a package that is CURRENTLY in use:
# ``POST /exportar`` and ``POST /sincronizacion/local`` both create an
# ExportPackage and then run ``ExportService.generate_export`` synchronously
# within the same request (``/sincronizacion/local`` even uses ``pending``
# during generation). There is no runtime/job registry to prove a non-terminal
# package is idle, so we prioritize integrity over cleanup and refuse deletion.
_DELETABLE_STATUSES = frozenset({"completed", "error"})


class ExportDeletionError(Exception):
    """Base error for export deletion failures."""


class ExportNotFoundError(ExportDeletionError):
    """The package does not exist or is not owned by the requesting user.

    Both cases are treated identically to avoid leaking metadata about another
    user's package.
    """


class ExportStatusNotDeletableError(ExportDeletionError):
    """The package status is not in the deletable whitelist (fail-closed).

    Only ``completed`` and ``error`` are deletable. ``pending`` and
    ``generating`` are refused because they can represent a package currently
    being generated.
    """


class ExportGeneratingError(ExportStatusNotDeletableError):
    """The package is currently generating and cannot be deleted.

    Kept as a specific subclass for backward-compatible handling/messaging.
    """


class UnsafeExportPathError(ExportDeletionError):
    """The stored ``file_path`` is not safe to delete (fail-closed)."""


class ExportFileDeletionError(ExportDeletionError):
    """Deleting the ZIP file failed; the record was preserved."""


class ExportRecordDeletionError(ExportDeletionError):
    """Deleting the record failed after the ZIP was already removed."""


class _PathClassification(Enum):
    """Result of classifying a stored ``file_path`` against the exports root."""

    SAFE_EXISTS = "safe_exists"
    SAFE_MISSING = "safe_missing"
    UNSAFE = "unsafe"


class ExportDeletionService:
    """Authorize and execute safe deletion of an export package.

    Args:
        repository: Persistence port for ExportPackage records.
        exports_root: Authorized directory for export ZIP files. Injectable so
            tests can point it at a temporary directory.
    """

    def __init__(
        self,
        repository: ExportPackageRepository,
        exports_root: str | Path = DEFAULT_EXPORTS_ROOT,
    ) -> None:
        self._repository = repository
        self._exports_root = Path(exports_root)

    def delete_export(self, package_id: int, user_id: int) -> None:
        """Delete an owned export package's ZIP file and record.

        Raises:
            ExportNotFoundError: package missing or owned by another user.
            ExportStatusNotDeletableError: status not in the deletable whitelist
                (``pending`` or any non-terminal state).
            ExportGeneratingError: package is generating (subclass of the above).
            UnsafeExportPathError: stored path escapes the authorized root or is
                not a regular file within it.
            ExportFileDeletionError: unlinking the ZIP raised OSError.
            ExportRecordDeletionError: record deletion failed after unlink.
        """
        # 1. Load + ownership (before any filesystem I/O).
        package = self._repository.get_by_id(package_id)
        if package is None or package.created_by_user_id != user_id:
            raise ExportNotFoundError("Exportación no encontrada")

        # 2. Status guard (fail-closed whitelist): only terminal states are
        # deletable. Reject before any filesystem I/O or repository delete.
        if package.status not in _DELETABLE_STATUSES:
            if package.status == "generating":
                raise ExportGeneratingError(
                    "No se puede eliminar una exportación mientras se está generando"
                )
            raise ExportStatusNotDeletableError(
                "No se puede eliminar una exportación en curso"
            )

        # 3. Classify the stored path.
        classification, resolved = self._classify_path(package.file_path)
        if classification is _PathClassification.UNSAFE:
            # Fail-closed: do not delete file nor record.
            raise UnsafeExportPathError(
                "No se pudo eliminar: la ruta del archivo no es segura"
            )

        # 4. Unlink the ZIP only when it is safe AND present.
        if classification is _PathClassification.SAFE_EXISTS:
            try:
                resolved.unlink()
            except OSError as exc:
                # Preserve the record; the operation is retryable.
                raise ExportFileDeletionError(
                    "No se pudo eliminar el archivo de la exportación"
                ) from exc

        # 5. Delete the record only after any unlink succeeded.
        try:
            self._repository.delete(package_id)
        except Exception as exc:  # noqa: BLE001 - surface as controlled error
            # ZIP already removed (if it existed); record remains. Recoverable:
            # a later attempt classifies the path as SAFE_MISSING.
            raise ExportRecordDeletionError(
                "No se pudo eliminar el registro de la exportación"
            ) from exc

    def _classify_path(
        self, file_path: Optional[str]
    ) -> tuple[_PathClassification, Optional[Path]]:
        """Classify ``file_path`` relative to the authorized exports root.

        Returns a tuple of the classification and the resolved path (or None
        when there is no path to act on).
        """
        if not file_path:
            # None or empty: nothing to unlink, record may be removed.
            return _PathClassification.SAFE_MISSING, None

        try:
            resolved = Path(file_path).resolve()
            root = self._exports_root.resolve()
        except (OSError, ValueError, RuntimeError):
            return _PathClassification.UNSAFE, None

        # Containment: resolved must live inside the authorized root. resolve()
        # follows symlinks, so a symlink escaping the root fails here.
        try:
            resolved.relative_to(root)
        except ValueError:
            return _PathClassification.UNSAFE, None

        if resolved.suffix.lower() != ".zip":
            return _PathClassification.UNSAFE, None

        if not resolved.exists():
            return _PathClassification.SAFE_MISSING, None

        if not resolved.is_file():
            # Exists but is a directory or other non-regular node.
            return _PathClassification.UNSAFE, None

        return _PathClassification.SAFE_EXISTS, resolved
