from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.value_objects.bounding_box import BoundingBox
from src.domain.value_objects.frame_reference import FrameReference


@dataclass
class FruitDetection:
    session_id: str
    frame_ref: FrameReference
    detection_id: int
    track_id: int
    bbox: BoundingBox
    detection_score: float
    class_id: int = 0
    is_new_track: bool = False
    track_hits: int = 0
    reused_previous_result: bool = False
    propagated: bool = False

    def is_reliable(self, min_score: float) -> bool:
        return self.detection_score >= min_score and self.bbox.is_valid()

    def to_record_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "source_name": self.frame_ref.source_name,
            "frame_index": self.frame_ref.frame_index,
            "detection_id": self.detection_id,
            "track_id": self.track_id,
            "x1": self.bbox.x1,
            "y1": self.bbox.y1,
            "x2": self.bbox.x2,
            "y2": self.bbox.y2,
            "detection_score": self.detection_score,
            "class_id": self.class_id,
            "is_new_track": self.is_new_track,
            "track_hits": self.track_hits,
            "reused_previous_result": self.reused_previous_result,
            "propagated": self.propagated,
        }