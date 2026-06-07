"""Path sanitizer utility for preventing directory traversal attacks.

Validates that file paths resolve within an allowed base directory,
preventing access to files outside the intended scope.
"""
from __future__ import annotations

from pathlib import Path


class PathTraversalError(Exception):
    """Raised when a path escapes the allowed base directory.

    Attributes:
        reason: Description of why the path was rejected.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Path traversal rejected: {reason}")


def validate_safe_path(path: str, allowed_base: Path) -> Path:
    """Validate that a path resolves within the allowed base directory.

    Algorithm:
    1. Reject if path contains null bytes.
    2. Reject if path is absolute (starts with / or drive letter).
    3. Construct candidate = allowed_base / path.
    4. Resolve candidate to absolute (follows symlinks).
    5. Verify resolved path starts with resolved allowed_base.
    6. Return resolved path if valid.

    Args:
        path: Relative path string (from user input or database).
        allowed_base: The directory that must contain the resolved path.

    Returns:
        Resolved absolute Path guaranteed to be within allowed_base.

    Raises:
        PathTraversalError: If the path escapes, contains null bytes,
            or uses absolute path prefixes.
    """
    # 1. Reject null bytes
    if "\x00" in path:
        raise PathTraversalError("Path contains null bytes")

    # 2. Reject empty paths
    if not path or not path.strip():
        raise PathTraversalError("Path is empty")

    # 3. Reject absolute paths
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":" and path[0].isalpha()):
        raise PathTraversalError("Path must be relative, not absolute")

    # 4. Reject explicit traversal sequences
    if ".." in path.split("/") or ".." in path.split("\\"):
        raise PathTraversalError("Path contains traversal sequence")

    # 5. Resolve and verify containment
    resolved_base = allowed_base.resolve()
    candidate = (allowed_base / path).resolve()

    if not str(candidate).startswith(str(resolved_base)):
        raise PathTraversalError("Resolved path escapes allowed directory")

    return candidate
