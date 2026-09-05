"""OpenCV adapter implementing VideoReaderPort (Spec 019, Task 2.2).

This is the ONLY layer that imports ``cv2`` for video reading in the video-first
analysis path. It owns exactly one ``cv2.VideoCapture`` per instance and uses that
same handle for ``open``, ``is_available``, ``metadata``, ``read`` and ``release``.

Design rules honored here:
    - Exactly one active ``cv2.VideoCapture`` per instance (created in ``open()``).
    - ``VideoFileFrameSource`` is not reused and its private ``_cap`` is not touched.
    - No second capture is opened for metadata.
    - Unreadable/corrupt sources surface via ``is_available() == False`` — no raw
      OpenCV exception leaks to the application layer.
    - ``metadata()``/``read()`` on an unavailable reader raise ``VideoReaderError``
      (defined by the port). ``read()`` returns ``(False, None)`` only for normal
      EOF of an open reader. Raw backend errors from ``cap.get``/``cap.read`` are
      wrapped in ``VideoReaderError`` (with exception chaining).
    - Invalid metadata (fps <= 0 or width/height <= 0) is rejected explicitly: the
      source is treated as unavailable rather than inventing a fallback fps. This
      preserves reproducible ``source_video_fps`` downstream.

Exhaustive path-traversal coverage belongs to Task 12; here we apply the minimal
integration consistent with the existing pattern: relative paths are validated
against a configurable base (project root by default) via
``path_sanitizer.validate_safe_path``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import cv2

from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderError,
    VideoReaderPort,
)
from src.infrastructure.config.settings import BASE_DIR
from src.infrastructure.security.path_sanitizer import PathTraversalError, validate_safe_path

logger = logging.getLogger(__name__)


class OpenCvVideoReader(VideoReaderPort):
    """VideoReaderPort backed by a single ``cv2.VideoCapture``.

    Lifecycle:
        - Before ``open()``: no active capture.
        - ``open()``: resolves/sanitizes the path, creates one ``VideoCapture``,
          and validates that it opened AND reports valid metadata. On any
          failure the handle is released and the reader stays unavailable.
        - ``is_available()``: inspects the existing handle; never (re)opens.
        - ``metadata()`` / ``read()``: operate on the same handle.
        - ``release()``: releases and clears the handle; idempotent; no reopen.
    """

    def __init__(self, video_path: str, *, allowed_base: Optional[Path] = None) -> None:
        """Initialize the reader without opening any resource.

        Args:
            video_path: RELATIVE path to the video (from the project root). It is
                validated against ``allowed_base`` via the shared
                ``path_sanitizer`` before any ``cv2.VideoCapture`` is opened.
                Traversal sequences (``..``) and absolute paths are rejected.
            allowed_base: Base directory for relative-path validation. Defaults
                to the project root (``BASE_DIR``).
        """
        self._video_path = video_path
        self._allowed_base = allowed_base if allowed_base is not None else BASE_DIR
        self._cap: Optional[cv2.VideoCapture] = None

    def _resolve_path(self) -> str:
        """Resolve+validate the video path, confining it to ``allowed_base``.

        Every path (Task 12) is validated by the shared ``validate_safe_path``:
        relative paths that stay inside ``allowed_base`` are resolved to an
        absolute string; traversal sequences and absolute paths are rejected
        BEFORE any capture is opened.

        Raises:
            PathTraversalError: If the path escapes ``allowed_base``, is absolute,
                or otherwise rejected by the sanitizer.
        """
        return str(validate_safe_path(self._video_path, self._allowed_base))

    def open(self) -> None:
        """Open the source into a single VideoCapture; stay unavailable on failure."""
        # Do not leak a previously-open handle if open() is called again.
        if self._cap is not None:
            self.release()

        try:
            resolved = self._resolve_path()
        except PathTraversalError as exc:
            # Path rejected: leave reader unavailable, do not raise a raw error.
            logger.warning("Video path rejected by sanitizer: %s", exc.reason)
            self._cap = None
            return

        try:
            cap = cv2.VideoCapture(resolved)
        except Exception as exc:  # pragma: no cover - defensive: cv2 rarely raises here
            logger.warning("cv2.VideoCapture raised while opening video: %s", exc)
            self._cap = None
            return

        if not cap.isOpened():
            # Corrupt/missing file: release and stay unavailable.
            cap.release()
            self._cap = None
            logger.warning("Video source could not be opened: %s", resolved)
            return

        # Validate metadata now so an opened-but-invalid source is reported as
        # unavailable rather than yielding an invented fps downstream.
        if not self._has_valid_metadata(cap):
            cap.release()
            self._cap = None
            logger.warning("Video source reported invalid metadata: %s", resolved)
            return

        self._cap = cap

    def is_available(self) -> bool:
        """Return True only if a handle exists and is open (never reopens)."""
        return self._cap is not None and self._cap.isOpened()

    def metadata(self) -> VideoMetadata:
        """Return metadata from the open handle without consuming frames."""
        if not self.is_available():
            raise VideoReaderError(
                "metadata() called on an unavailable video reader; call open() first"
            )
        try:
            return self._read_metadata(self._cap)
        except VideoReaderError:
            raise
        except Exception as exc:  # wrap raw backend errors (e.g. cv2.error)
            raise VideoReaderError(
                f"Failed to read video metadata: {exc}"
            ) from exc

    def read(self) -> "Tuple[bool, Optional[object]]":
        """Read the next frame from the same handle.

        Returns ``(False, None)`` ONLY for normal EOF of an open reader. Raises
        ``VideoReaderError`` if the reader is unavailable (before ``open()``,
        after ``release()``, or otherwise not open), so callers never confuse an
        invalid reader with the end of the video.
        """
        if not self.is_available():
            raise VideoReaderError(
                "read() called on an unavailable video reader; call open() first"
            )
        try:
            ret, frame = self._cap.read()
        except Exception as exc:  # wrap raw backend errors (e.g. cv2.error)
            raise VideoReaderError(f"Failed to read video frame: {exc}") from exc
        if not ret:
            return False, None
        return True, frame

    def release(self) -> None:
        """Release and clear the handle; idempotent; does not reopen."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # --- internal helpers -------------------------------------------------- #

    @staticmethod
    def _read_metadata(cap: "cv2.VideoCapture") -> VideoMetadata:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return VideoMetadata(
            fps=fps,
            total_frames=total_frames,
            width=width,
            height=height,
        )

    @classmethod
    def _has_valid_metadata(cls, cap: "cv2.VideoCapture") -> bool:
        """Return True only for a reproducible, non-degenerate source.

        Rejects fps <= 0 and non-positive dimensions. ``total_frames`` may be
        unreliable for some containers, so it is not used to reject the source.
        """
        meta = cls._read_metadata(cap)
        if meta.fps <= 0:
            return False
        if meta.width <= 0 or meta.height <= 0:
            return False
        return True
