"""Domain entity: OperationalAlert.

Represents a computed operational alert based on system state.
Not persisted — computed dynamically when needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class OperationalAlert:
    """A computed operational alert based on system state."""

    alert_type: str
    severity: str
    title: str
    message: str
    module_id: Optional[int] = None
    greenhouse_id: Optional[int] = None
    monitoring_id: Optional[int] = None
    source: str = field(default="computed")
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.alert_type or not self.alert_type.strip():
            raise ValueError("alert_type must not be empty")
        if self.severity not in ("info", "warning", "critical"):
            raise ValueError(
                f"severity must be 'info', 'warning', or 'critical', got '{self.severity}'"
            )
        if not self.title or not self.title.strip():
            raise ValueError("title must not be empty")
        if not self.message or not self.message.strip():
            raise ValueError("message must not be empty")
        if self.module_id is not None and self.module_id <= 0:
            raise ValueError("module_id must be positive")
        if self.greenhouse_id is not None and self.greenhouse_id <= 0:
            raise ValueError("greenhouse_id must be positive")
        if self.monitoring_id is not None and self.monitoring_id <= 0:
            raise ValueError("monitoring_id must be positive")
