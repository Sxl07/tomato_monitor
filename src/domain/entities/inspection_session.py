from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class InspectionSession:
    session_id: str
    strategy_name: str
    source_video: str
    started_at: datetime
    status: str = "created"
    completed_at: Optional[datetime] = None
    parameters: dict = field(default_factory=dict)

    def mark_running(self) -> None:
        self.status = "running"

    def mark_completed(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.utcnow()

    def mark_failed(self) -> None:
        self.status = "failed"
        self.completed_at = datetime.utcnow()

    def is_finished(self) -> bool:
        return self.status in {"completed", "failed"}