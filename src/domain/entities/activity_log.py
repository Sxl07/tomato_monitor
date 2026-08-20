"""Domain entity: ActivityLog.

Represents a single agricultural activity performed on a module by an operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ActivityLog:
    """A record of an agricultural activity performed on a module."""

    module_id: int
    activity_type_id: int
    user_id: int
    id: Optional[int] = None
    product_name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    notes: Optional[str] = None
    occurred_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    sync_status: str = field(default="pending")

    def __post_init__(self) -> None:
        if self.module_id <= 0:
            raise ValueError("module_id must be positive")
        if self.activity_type_id <= 0:
            raise ValueError("activity_type_id must be positive")
        if self.user_id <= 0:
            raise ValueError("user_id must be positive")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("quantity must be non-negative")
        valid_sync = ("pending", "exported", "synced", "error")
        if self.sync_status not in valid_sync:
            raise ValueError(f"sync_status must be one of {valid_sync}, got '{self.sync_status}'")
        if self.id is not None and self.id <= 0:
            raise ValueError("id must be positive")
