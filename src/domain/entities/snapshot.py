"""Domain entity: Snapshot.

Represents a single image captured during a monitoring session
when the change detector triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Snapshot:
    """A captured image from a monitoring session."""

    monitoring_id: int
    image_path: str
    frame_index: int
    id: Optional[int] = None
    captured_at: Optional[datetime] = None
    change_score: Optional[float] = None
    has_detections: bool = field(default=False)
