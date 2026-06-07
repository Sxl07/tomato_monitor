from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple


class FrameSource(ABC):
    """Abstract interface for frame input sources.

    Implementations provide frames from either offline video files
    or live camera capture. The interface is agnostic to the source.
    """

    @abstractmethod
    def read(self) -> Tuple[bool, Optional[Any]]:
        """Read the next frame from the source.

        Returns:
            A tuple (success: bool, frame: ndarray or None).
            success is True if a frame was captured, False if source exhausted or failed.
            frame is the image as a numpy ndarray (BGR format) or None on failure.
        """
        ...

    @abstractmethod
    def release(self) -> None:
        """Release resources held by the frame source (file handle, camera device)."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the frame source is available and ready to produce frames."""
        ...
