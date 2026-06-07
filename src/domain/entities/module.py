"""Domain entity: Module.

Represents a rectangular, delimited growing area within a greenhouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Module:
    """A cultivation module within a greenhouse."""

    greenhouse_id: int
    name: str
    id: Optional[int] = None
    crop_type: str = field(default="Tomate Cherry")
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
