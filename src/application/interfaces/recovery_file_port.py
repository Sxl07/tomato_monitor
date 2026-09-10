"""Port: RecoveryFilePort (Spec 022, block D3.2).

Abstract contract for writing recovered snapshot images to the local
filesystem, keeping RecoveryService free of filesystem primitives. Concrete
implementations live in the infrastructure layer and own the base directory
(OUTPUTS_DIR), path-safety checks, and atomic write mechanics.

All paths handled by this port are RELATIVE to the outputs base directory and
never include a leading ``outputs/`` segment. Example::

    monitorings/12/snapshots/raw/snapshot_000003.jpg
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class RecoveryFileResult:
    """Result of a local recovery file write.

    Attributes:
        success: Whether the target file is present after the operation
            (either newly written or already existing).
        already_exists: Whether the target already existed and no write was
            performed.
        error_message: Short, safe error description on failure; None on success.
    """

    success: bool
    already_exists: bool = False
    error_message: Optional[str] = None


class RecoveryFilePort(Protocol):
    """Abstract port for local recovery file operations.

    Contract:
        - ``relative_path`` is ALWAYS relative to the outputs base directory and
          must resolve strictly inside it (no absolute paths, no traversal).
        - ``exists`` reports presence without modifying the filesystem.
        - ``write_atomic`` writes bytes atomically (temp file + os.replace),
          creating parent directories, and never truncates/deletes an existing
          final file on failure.
    """

    def exists(self, relative_path: str) -> bool:
        """Return whether the target file already exists.

        Args:
            relative_path: Path relative to the outputs base directory.

        Returns:
            True if the resolved, contained path exists as a file.
        """
        ...

    def write_atomic(
        self,
        relative_path: str,
        content: bytes,
    ) -> RecoveryFileResult:
        """Atomically write bytes to the target relative path.

        Args:
            relative_path: Path relative to the outputs base directory.
            content: Raw bytes to write.

        Returns:
            RecoveryFileResult with success=True on write (or when the file was
            already present), or success=False with an error_message on failure.
        """
        ...
