from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.application.dto.inspection_summary import InspectionSummaryDTO


@dataclass(frozen=True)
class SessionDetailDTO:
    session_name: str
    summary: Optional[InspectionSummaryDTO]
    session_dir: str
    reports_dir: str
    summary_csv: str
    per_frame_csv: str
    per_detection_csv: str
    snapshot_files: list[str]
    annotated_videos: list[str]