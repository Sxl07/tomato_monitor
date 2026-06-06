from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class InspectionRequest:
    strategy_name: str
    video_index: int
    min_frames_between_detections: int
    max_frames_without_detection: int
    use_scene_gate: bool
    max_frames_to_process: Optional[int] = None
    save_detection_snapshots: bool = True
    save_detection_crops: bool = True
    save_annotated_video: bool = True