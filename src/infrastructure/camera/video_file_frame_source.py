import cv2
from typing import Any, Optional, Tuple
from src.domain.interfaces.frame_source import FrameSource


class VideoFileFrameSource(FrameSource):
    """Frame source that reads from an offline video file using OpenCV."""

    def __init__(self, video_path: str):
        self._video_path = video_path
        self._cap: Optional[cv2.VideoCapture] = None

    def read(self) -> Tuple[bool, Optional[Any]]:
        if self._cap is None:
            self._cap = cv2.VideoCapture(self._video_path)
        return self._cap.read()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_available(self) -> bool:
        if self._cap is None:
            self._cap = cv2.VideoCapture(self._video_path)
        return self._cap.isOpened()
