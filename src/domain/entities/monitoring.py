"""Domain entity: Monitoring.

Represents a single robot traversal and monitoring session for a module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Monitoring:
    """A monitoring session capturing snapshots and running inference on a module."""

    module_id: int
    width_m: float
    length_m: float
    id: Optional[int] = None
    status: str = field(default="initializing")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
    total_snapshots: int = field(default=0)
    total_detections: int = field(default=0)
