"""Application service: CleanupService (Spec 021, Task 11).

Deferred physical cleanup of local artifacts under OUTPUTS_DIR after a
monitoring/module/greenhouse deletion. This runs LONG after the Local_Cascade
(which never touches the filesystem) and is driven entirely by durable
Deletion_Outbox state — it does NOT depend on Supabase or remote propagation
(``status='synced'``).

Rules enforced here (Spec 021):
    - Only processes entries with ``local_delete_status = 'completed'`` whose
      ``cleanup_status`` is still ``pending`` AND whose Retention_Window has
      elapsed (``now_utc - deleted_at >= RETENTION_WINDOW_HOURS``).
    - Uses ONLY the ``deletion_outbox_local_artifact`` rows persisted before the
      Local_Cascade. Never reconstructs paths from (now-deleted) SQLite records.
    - ``relative_path`` is RELATIVE to OUTPUTS_DIR; the service calls
      ``validate_safe_path(relative_path, OUTPUTS_DIR)`` and never prepends
      ``outputs/``.
    - A non-existent path is an idempotent SUCCESS (mark the artifact ``done``).
    - An individual failure is durable and retryable (mark the artifact
      ``error`` with detail); remaining artifacts are still processed.
    - ``cleanup_status='done'`` is set for the entry ONLY when every artifact
      row is ``done``.

This module belongs to the application layer. It depends on the
DeletionOutboxPort abstraction, a filesystem-remover callable, a path
validator callable, and stdlib. It performs NO network calls and does NOT
import FastAPI/SQLAlchemy/torch/cv2. It never touches vision/camera/inference.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from src.application.interfaces.deletion_outbox_port import DeletionOutboxPort

logger = logging.getLogger(__name__)


def _default_remove_path(path: Path) -> None:
    """Remove a file or directory tree at ``path``.

    A non-existent path is a no-op (idempotent success is decided by the
    caller, which checks existence first).
    """
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Interpret a naive datetime as UTC; normalize aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CleanupResult:
    """Summary of a cleanup run (informational)."""

    def __init__(self) -> None:
        self.entries_examined = 0
        self.entries_completed = 0
        self.artifacts_removed = 0
        self.artifacts_failed = 0

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            "CleanupResult("
            f"entries_examined={self.entries_examined}, "
            f"entries_completed={self.entries_completed}, "
            f"artifacts_removed={self.artifacts_removed}, "
            f"artifacts_failed={self.artifacts_failed})"
        )


class CleanupService:
    """Deferred physical cleanup of OUTPUTS_DIR artifacts (Retention_Window)."""

    def __init__(
        self,
        deletion_outbox: DeletionOutboxPort,
        outputs_dir: Path,
        retention_window_hours: int,
        validate_safe_path: Callable[[str, Path], Path],
        remove_path: Optional[Callable[[Path], None]] = None,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        """Initialize the cleanup service.

        Args:
            deletion_outbox: Durable outbox port used to read cleanup-pending
                entries + their local-artifact rows and to mark statuses.
            outputs_dir: Base directory that all ``relative_path`` values are
                relative to (OUTPUTS_DIR). Also the ``allowed_base`` for
                path validation.
            retention_window_hours: Hours to keep artifacts after ``deleted_at``.
            validate_safe_path: Callable (path_str, allowed_base) -> resolved
                Path, raising on traversal. Injected for testability.
            remove_path: Callable that removes a resolved Path (file or dir
                tree). Defaults to shutil-based removal.
            now_fn: Callable returning the current UTC datetime (injectable).
        """
        self._outbox = deletion_outbox
        self._outputs_dir = outputs_dir
        self._retention = timedelta(hours=retention_window_hours)
        self._validate_safe_path = validate_safe_path
        self._remove_path = remove_path or _default_remove_path
        self._now_fn = now_fn

    def run(self) -> CleanupResult:
        """Process all cleanup-pending entries whose Retention_Window elapsed.

        For each eligible entry, process its local-artifact rows: remove the
        resolved path (non-existent path is idempotent success), marking each
        row ``done`` or ``error``. Mark the entry ``cleanup_status='done'`` only
        when every artifact row is ``done``. Best-effort per entry: a failure on
        one entry/artifact never aborts the run.
        """
        result = CleanupResult()
        now = _as_utc(self._now_fn())

        try:
            entries = self._outbox.get_pending_cleanup_entries()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Cleanup: could not read pending entries: %s", exc)
            return result

        for entry in entries:
            result.entries_examined += 1

            # Retention gate: deleted_at must be set (it is, for completed
            # entries) AND the window must have elapsed.
            if entry.deleted_at is None:
                continue
            if now - _as_utc(entry.deleted_at) < self._retention:
                continue

            try:
                completed = self._cleanup_entry(entry.id, result)
            except Exception as exc:  # pragma: no cover - defensive per-entry
                logger.warning(
                    "Cleanup: entry outbox_id=%s failed: %s", entry.id, exc
                )
                continue

            if completed:
                try:
                    self._outbox.mark_cleanup_done(entry.id)
                    result.entries_completed += 1
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Cleanup: could not mark entry outbox_id=%s done: %s",
                        entry.id,
                        exc,
                    )

        return result

    def _cleanup_entry(self, outbox_id: int, result: CleanupResult) -> bool:
        """Process one entry's artifact rows. Returns True if all are ``done``."""
        rows = self._outbox.get_local_artifacts_for_entry(outbox_id)
        all_done = True

        for row in rows:
            if row.status == "done":
                continue  # terminal, already cleaned

            # Process both 'pending' and 'error' rows (retry).
            try:
                resolved = self._validate_safe_path(
                    row.relative_path, self._outputs_dir
                )
            except Exception as exc:
                # A path that fails validation cannot be safely removed; keep it
                # retryable and record the reason.
                all_done = False
                result.artifacts_failed += 1
                self._safe_mark_artifact(
                    row.id, "error", f"unsafe path: {exc}"
                )
                continue

            try:
                if resolved.exists():
                    self._remove_path(resolved)
                    result.artifacts_removed += 1
                # Non-existent path -> idempotent success (nothing to remove).
                # The physical removal (or absence) is only durable once the
                # 'done' status is persisted. If persisting 'done' fails, the
                # artifact must stay retryable so the parent is NOT completed.
                if self._safe_mark_artifact(row.id, "done", None):
                    pass
                else:
                    all_done = False
                    result.artifacts_failed += 1
            except Exception as exc:
                # Individual failure -> durable, retryable error.
                all_done = False
                result.artifacts_failed += 1
                self._safe_mark_artifact(row.id, "error", str(exc))

        return all_done

    def _safe_mark_artifact(
        self, artifact_id: int, status: str, last_error: Optional[str]
    ) -> bool:
        """Persist an artifact-row status. Returns True on success, False on failure.

        Returning False lets the caller keep ``all_done`` False so a physical
        removal that could NOT be durably recorded as ``done`` never lets the
        parent be marked ``cleanup_status='done'``. On the next run the path is
        already gone (idempotent success) and persisting ``done`` is retried.
        """
        try:
            self._outbox.mark_local_artifact_status(
                artifact_id, status, last_error=last_error
            )
            return True
        except Exception as exc:  # defensive: persistence failure is retryable
            logger.warning(
                "Cleanup: could not mark artifact id=%s as %s: %s",
                artifact_id,
                status,
                exc,
            )
            return False
