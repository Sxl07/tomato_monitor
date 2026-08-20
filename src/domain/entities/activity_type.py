"""Domain entity: ActivityType.

Represents a type of agricultural activity from the backend-defined catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ActivityType:
    """A type of agricultural activity (e.g., riego, poda, cosecha)."""

    code: str
    name: str
    category: str
    id: Optional[int] = None
    requires_product: bool = field(default=False)
    allows_quantity: bool = field(default=False)
    default_unit: Optional[str] = None
    is_active: bool = field(default=True)

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("code must not be empty")
        if not self.name or not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.category or not self.category.strip():
            raise ValueError("category must not be empty")
        if self.id is not None and self.id <= 0:
            raise ValueError("id must be positive")
