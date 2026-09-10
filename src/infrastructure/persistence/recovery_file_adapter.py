"""Infrastructure adapter: LocalRecoveryFileAdapter (Spec 022, block D3.2).

Implements RecoveryFilePort by writing recovered snapshot images under a base
outputs directory. All operations validate that the final path is strictly
contained within the configured base directory (defense-in-depth on top of
Storage RLS), reject absolute paths and traversal, and write atomically via a
same-directory temporary file plus ``os.replace``.

This adapter never deletes or truncates an existing final file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.application.interfaces.recovery_file_port import RecoveryFileResult
from src.infrastructure.security.path_sanitizer import (
    PathTraversalError,
    validate_safe_path,
)


class LocalRecoveryFileAdapter:
    """Writes recovered files under a base outputs directory, path-safely.

    ``relative_path`` is always relative to ``outputs_dir`` (no leading
    ``outputs/``). The resolved final path must remain inside ``outputs_dir``;
    otherwise the operation fails closed.
    """

    def __init__(self, outputs_dir: Path) -> None:
        """Initialize the adapter.

        Args:
            outputs_dir: Base directory containing recovered artifacts. Passed
                explicitly (never derived from the current working directory).
        """
        self._outputs_dir = Path(outputs_dir)

    def exists(self, relative_path: str) -> bool:
        """Return True if the resolved, contained target exists as a file.

        A path that is invalid or escapes the base directory is treated as
        non-existent (the caller then attempts a write, which fails closed).
        The filesystem is never modified by this check.
        """
        try:
            target = self._resolve(relative_path)
        except PathTraversalError:
            return False
        return target.is_file()

    def write_atomic(
        self,
        relative_path: str,
        content: bytes,
    ) -> RecoveryFileResult:
        """Atomically write ``content`` to the target relative path.

        Steps: validate containment, short-circuit if the file already exists,
        create parent directories, write to a same-directory temp file, then
        ``os.replace`` it into place. On any failure the temp file is cleaned
        up best-effort and no partial final file is left behind.
        """
        try:
            target = self._resolve(relative_path)
        except PathTraversalError as exc:
            return RecoveryFileResult(success=False, error_message=exc.reason)

        # Never overwrite/truncate an already-present final file.
        if target.is_file():
            return RecoveryFileResult(success=True, already_exists=True)

        parent = target.parent
        tmp_path: str | None = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            # Temp file in the SAME directory so os.replace is atomic.
            fd, tmp_path = tempfile.mkstemp(
                prefix=".recovery-", suffix=".tmp", dir=str(parent)
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
            except Exception:
                # fdopen took ownership of fd; ensure temp is removed below.
                raise
            os.replace(tmp_path, target)
            tmp_path = None  # replaced successfully; nothing to clean up.
            return RecoveryFileResult(success=True)
        except Exception as exc:  # noqa: BLE001 - fail closed, report safely
            return RecoveryFileResult(
                success=False, error_message=type(exc).__name__
            )
        finally:
            if tmp_path is not None and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _resolve(self, relative_path: str) -> Path:
        """Validate and resolve ``relative_path`` inside the base directory.

        Defense-in-depth: the shared ``validate_safe_path`` performs the primary
        validation, but this adapter does NOT trust it blindly (we write
        downloaded bytes). The adapter's final authority is ``Path.relative_to``
        over resolved paths, not a string prefix check.

        Raises:
            PathTraversalError: If the path is absolute, empty, contains a
                traversal sequence, or resolves outside the base directory.
        """
        target = validate_safe_path(relative_path, self._outputs_dir)

        resolved_base = self._outputs_dir.resolve()
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(resolved_base)
        except ValueError as exc:
            raise PathTraversalError(
                "Resolved path escapes outputs directory"
            ) from exc

        return resolved_target
