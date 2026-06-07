"""Domain entity: Greenhouse.

Represents the physical greenhouse infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Greenhouse:
    """A physical greenhouse that contains one or more cultivation modules."""

    name: str
    id: Optional[int] = None
    location: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
