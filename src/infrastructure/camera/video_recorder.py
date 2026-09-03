"""VideoRecorder infrastructure component (Spec 019, Task 3).

Wraps ``cv2.VideoWriter`` with a small, explicit contract: select a codec
(with ordered fallback), open with the frame size discovered from the first
real frame, write BGR frames without any silent correction, close cleanly and
idempotently, and validate the resulting file.

Scope boundaries honored here:
    - No camera, worker, monitoring, persistence or inference logic.
    - ``fps`` is fixed in the constructor (``configured_recording_fps``);
      ``frame_size`` is provided only in ``open()`` (from the first real frame).
    - At most ONE active ``cv2.VideoWriter`` at any time (no dangling writers
      during codec fallback).
    - No atomic rename (monitoring.recording.mp4 -> monitoring.mp4): that belongs
      to the later finalize flow. This component only writes/closes/validates the
      path it is given.
    - No fps recomputation, remux, two-pass or re-encode.
    - Raw backend exceptions (e.g. ``cv2.error``) are never leaked; they are
      wrapped in ``VideoRecorderError`` with exception chaining.

``validate()`` uses a temporary ``cv2.VideoCapture`` solely to verify the written
file; it is always released in a ``finally`` and never retained. This does not
conflict with the single-VideoCapture rule of the reader in Task 2 (a different
responsibility, a throwaway reader).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import cv2

from src.infrastructure.config.settings import BASE_DIR
from src.infrastructure.security.path_sanitizer import validate_safe_path

logger = logging.getLogger(__name__)


class VideoRecorderError(Exception):
    """Explicit, component-level error for VideoRecorder misuse or failures.

    Used instead of leaking raw OpenCV exceptions (``cv2.error``) across the
    component boundary.
    """


class VideoRecorder:
    """Writes and validates a recorded MP4 via a single ``cv2.VideoWriter``.

    Lifecycle:
        __init__(output_path, fps, codec_candidates)  # no writer yet
        open(frame_size=(w, h))                        # selects codec, opens writer
        write(frame_bgr)                               # frame must match frame_size
        close()                                        # idempotent release
        validate() -> bool                             # integrity check of output_path
    """

    def __init__(
        self,
        output_path: str,
        fps: float,
        codec_candidates: Tuple[str, ...] = ("mp4v", "avc1"),
        *,
        allowed_base: Optional[Path] = None,
    ) -> None:
        """Initialize the recorder without creating any writer.

        Args:
            output_path: RELATIVE destination path (the recorder writes exactly
                here; no atomic rename is performed in this component). It is
                ALWAYS sanitized against ``allowed_base`` via the shared
                ``path_sanitizer`` and resolved to an absolute path, rejecting
                traversal (``..``) and absolute paths BEFORE any file is opened,
                written or validated. There is no public mode that bypasses the
                sanitizer.
            fps: Nominal recording fps (``configured_recording_fps``). Must be > 0.
            codec_candidates: Ordered codecs to try in ``open()``. Must be non-empty.
            allowed_base: Base directory that ``output_path`` must resolve within.
                Defaults to the project root (``BASE_DIR``); tests may pass their
                own ``tmp_path``.

        Raises:
            VideoRecorderError: If ``fps <= 0`` or ``codec_candidates`` is empty.
            PathTraversalError: If ``output_path`` escapes ``allowed_base``, is
                absolute, or contains a traversal sequence.
        """
        if fps <= 0:
            raise VideoRecorderError(f"fps must be > 0, got {fps}")
        if not codec_candidates:
            raise VideoRecorderError("codec_candidates must not be empty")

        # Sanitize the write path up front (before any open/write/validate).
        # Always applied — path traversal protection is on by default. Reuses the
        # shared path_sanitizer; raises PathTraversalError on traversal/absolute.
        base = allowed_base if allowed_base is not None else BASE_DIR
        output_path = str(validate_safe_path(output_path, base))

        self._output_path = output_path
        self._fps = float(fps)
        self._codec_candidates = tuple(codec_candidates)

        self._writer: Optional[cv2.VideoWriter] = None
        self._frame_size: Optional[Tuple[int, int]] = None
        self._codec_used: str = ""
        self._frames_written: int = 0

    # --- properties -------------------------------------------------------- #

    @property
    def frames_written(self) -> int:
        """Number of frames successfully written so far."""
        return self._frames_written

    @property
    def codec_used(self) -> str:
        """Codec selected during ``open()`` (empty until a writer is open)."""
        return self._codec_used

    # --- lifecycle --------------------------------------------------------- #

    def open(self, frame_size: Tuple[int, int]) -> None:
        """Open the writer using OpenCV frame size ``(width, height)``.

        Tries codecs in order; the first that opens wins. Never keeps more than
        one writer active during fallback. ``open()`` does NOT write the first
        frame — the caller writes it afterwards.

        Args:
            frame_size: ``(width, height)`` discovered from the first real frame.

        Raises:
            VideoRecorderError: If dimensions are non-positive or no codec opens.
        """
        width, height = frame_size
        if width <= 0 or height <= 0:
            raise VideoRecorderError(
                f"frame_size must be positive (width, height), got {frame_size}"
            )

        # Ensure the parent directory exists.
        parent = Path(self._output_path).parent
        parent.mkdir(parents=True, exist_ok=True)

        # If already open, release the existing writer first (single-writer rule).
        if self._writer is not None:
            self.close()

        last_error: Optional[Exception] = None
        for codec in self._codec_candidates:
            writer = None
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                writer = cv2.VideoWriter(
                    self._output_path,
                    fourcc,
                    self._fps,
                    (int(width), int(height)),
                )
            except Exception as exc:  # defensive: wrap any backend error
                last_error = exc
                if writer is not None:
                    try:
                        writer.release()
                    except Exception:  # pragma: no cover - best effort cleanup
                        pass
                continue

            # Probe whether the writer opened. If the backend raises here, the
            # writer state is uncertain: abort open() (do NOT try another codec),
            # attempt best-effort cleanup, and surface a wrapped error.
            try:
                opened = writer.isOpened()
            except Exception as exc:
                try:
                    writer.release()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
                self._writer = None
                self._codec_used = ""
                self._frame_size = None
                raise VideoRecorderError(
                    f"writer.isOpened() failed for codec {codec!r}: {exc}"
                ) from exc

            if opened:
                # Keep this writer; stop fallback.
                self._writer = writer
                self._codec_used = codec
                self._frame_size = (int(width), int(height))
                return

            # Did not open: release before trying the next candidate. If the
            # release itself fails, we cannot guarantee the resource was freed,
            # so we must NOT open a second writer — abort with a wrapped error.
            try:
                writer.release()
            except Exception as exc:
                self._writer = None
                self._codec_used = ""
                self._frame_size = None
                raise VideoRecorderError(
                    f"Failed to release non-opened writer for codec {codec!r}: {exc}"
                ) from exc

        # No codec opened.
        self._writer = None
        self._codec_used = ""
        self._frame_size = None
        msg = (
            f"No codec could open the writer for {self._output_path}; "
            f"tried {self._codec_candidates}"
        )
        if last_error is not None:
            raise VideoRecorderError(msg) from last_error
        raise VideoRecorderError(msg)

    def write(self, frame_bgr) -> None:
        """Write a BGR frame that exactly matches the opened ``frame_size``.

        No resize/crop/pad/auto-correction is performed. ``frames_written`` is
        incremented only after a successful backend write.

        Raises:
            VideoRecorderError: If called before ``open()``, if the frame is
                invalid, if its dimensions differ from ``frame_size``, or if the
                backend write raises.
        """
        if self._writer is None or self._frame_size is None:
            raise VideoRecorderError("write() called before a successful open()")

        # Reject invalid frames explicitly (before touching the backend).
        if frame_bgr is None:
            raise VideoRecorderError("frame_bgr is None")
        shape = getattr(frame_bgr, "shape", None)
        if shape is None or len(shape) < 2:
            raise VideoRecorderError(
                f"frame_bgr has invalid shape: {shape!r}"
            )

        expected_w, expected_h = self._frame_size
        frame_h = shape[0]
        frame_w = shape[1]
        if frame_w != expected_w or frame_h != expected_h:
            raise VideoRecorderError(
                f"frame size mismatch: expected (w={expected_w}, h={expected_h}), "
                f"got (w={frame_w}, h={frame_h}); no resize is performed"
            )

        try:
            self._writer.write(frame_bgr)
        except Exception as exc:  # wrap raw backend errors, do not count
            raise VideoRecorderError(f"Failed to write frame: {exc}") from exc

        self._frames_written += 1

    def close(self) -> None:
        """Release the writer if present and clear the reference; idempotent.

        Does not validate, rename, change fps, remux or re-encode.

        Raises:
            VideoRecorderError: If the backend ``release()`` raises unexpectedly.
                The internal reference is cleared regardless, so the recorder is
                never left thinking a writer is still active.
        """
        writer = self._writer
        if writer is None:
            return

        # Clear the reference first so the object never believes the writer is
        # still active even if release() raises.
        self._writer = None
        try:
            writer.release()
        except Exception as exc:  # wrap raw backend errors
            raise VideoRecorderError(f"Failed to release writer: {exc}") from exc

    def validate(self) -> bool:
        """Return True if ``output_path`` is a non-empty, readable video.

        Criteria: file exists, size > 0, ``cv2.VideoCapture`` opens it, and at
        least one frame can be read. Any failure (including a raw backend
        exception) returns False. The temporary capture is always released.
        """
        path = Path(self._output_path)
        try:
            if not path.exists():
                return False
            if path.stat().st_size <= 0:
                return False
        except OSError:
            return False

        cap = None
        try:
            cap = cv2.VideoCapture(self._output_path)
            if not cap.isOpened():
                return False
            ret, frame = cap.read()
            return bool(ret) and frame is not None
        except Exception as exc:  # never leak cv2.error; validate is a boolean query
            logger.warning("validate() failed for %s: %s", self._output_path, exc)
            return False
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
