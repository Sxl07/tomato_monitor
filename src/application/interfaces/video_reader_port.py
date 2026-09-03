"""Port: VideoReaderPort (Spec 019, Task 2.1).

Defines the abstract contract used by the video-first analysis to read frames
and metadata from a recorded video. It exists because the domain `FrameSource`
interface only exposes ``read``/``release``/``is_available`` and offers **no**
metadata (fps, frame count, size), which the deferred analysis needs.

Boundary rules (enforced by architecture/import tests):
    - This module MUST NOT import ``cv2``, ``torch`` or ``detectron2``.
    - It MUST NOT open files or know about ``VideoFileFrameSource``.
    - The concrete OpenCV implementation (``OpenCvVideoReader``) lives in the
      infrastructure layer and is the only place that imports ``cv2`` for video
      reading.

``numpy`` is referenced only for type hints under ``TYPE_CHECKING`` so no runtime
import is required at this layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


class VideoReaderError(Exception):
    """Base error exposed by the VideoReaderPort contract.

    Raised for explicit, project-level reader misuse or invalid state (for
    example, calling ``metadata()`` or ``read()`` before ``open()`` or after
    ``release()``), and to wrap unexpected backend exceptions so raw errors
    (e.g. ``cv2.error``) never cross this application boundary.
    """


@dataclass(frozen=True)
class VideoMetadata:
    """Immutable metadata describing a video source.

    Attributes:
        fps: Frames per second declared by the container.
        total_frames: Total number of frames declared by the container.
        width: Frame width in pixels.
        height: Frame height in pixels.
    """

    fps: float
    total_frames: int
    width: int
    height: int


class VideoReaderPort(ABC):
    """Abstract port to read frames and metadata from a video source.

    Concrete implementations own their own reading resource and expose a clear,
    idempotent lifecycle:

        open -> is_available / metadata / read (repeatable) -> release

    Implementations must not leak raw backend exceptions (e.g. OpenCV errors)
    across this boundary; unreadable sources are reported via ``is_available``.
    """

    @abstractmethod
    def open(self) -> None:
        """Open the video source.

        Prepares the underlying reading resource. If the source cannot be
        opened (missing/corrupt file), the reader is left in an unavailable
        state (``is_available()`` returns False) rather than raising a raw
        backend exception.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether the source is currently open and readable.

        Must inspect the existing resource state and must not silently
        (re)open the source.
        """
        ...

    @abstractmethod
    def metadata(self) -> VideoMetadata:
        """Return the video metadata (fps, total_frames, width, height).

        Must be read from the already-open source without consuming frames.
        Behavior when called before ``open()`` or while unavailable is
        implementation-defined but must be an explicit, project-level error
        rather than a raw backend exception.
        """
        ...

    @abstractmethod
    def read(self) -> "Tuple[bool, Optional[np.ndarray]]":
        """Read the next frame from an open reader.

        Returns:
            ``(True, frame)`` when a frame is available, or ``(False, None)`` at
            end-of-stream. ``(False, None)`` represents **only** normal EOF of a
            correctly opened reader.

        Raises:
            VideoReaderError: If called before ``open()``, after ``release()``,
                or while the reader is otherwise unavailable — so callers never
                confuse an invalid reader with the normal end of the video.
        """
        ...

    @abstractmethod
    def release(self) -> None:
        """Release the underlying reading resource.

        Must be idempotent (safe to call multiple times). After release,
        ``is_available()`` returns False and the source is not reopened
        automatically.
        """
        ...
