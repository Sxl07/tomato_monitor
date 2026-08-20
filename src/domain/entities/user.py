"""Domain entity: User.

Represents an authenticated operator of the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """An authenticated operator who performs monitoring and agricultural activities."""

    full_name: str
    email: str
    password_hash: str
    id: Optional[int] = None
    role: str = field(default="operator")
    is_active: bool = field(default=True)
    remote_user_id: Optional[str] = None
    sync_status: str = field(default="local_only")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.full_name or not self.full_name.strip():
            raise ValueError("full_name must not be empty")
        if not self.email or not self.email.strip():
            raise ValueError("email must not be empty")
        if not self.password_hash or not self.password_hash.strip():
            raise ValueError("password_hash must not be empty")
        if self.role not in ("operator", "admin"):
            raise ValueError(f"role must be 'operator' or 'admin', got '{self.role}'")
        valid_sync = ("local_only", "pending", "exported", "synced", "error", "pending_sync")
        if self.sync_status not in valid_sync:
            raise ValueError(f"sync_status must be one of {valid_sync}, got '{self.sync_status}'")
        if self.id is not None and self.id <= 0:
            raise ValueError("id must be positive")
