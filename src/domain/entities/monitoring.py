"""Domain entity: Monitoring.

Represents a single portable monitoring session for a module.
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
    created_by_user_id: Optional[int] = None
    sync_status: str = field(default="pending")

    def __post_init__(self) -> None:
        if self.created_by_user_id is not None and self.created_by_user_id <= 0:
            raise ValueError("created_by_user_id must be positive")
        valid_sync = ("pending", "exported", "synced", "error")
        if self.sync_status not in valid_sync:
            raise ValueError(f"sync_status must be one of {valid_sync}, got '{self.sync_status}'")
