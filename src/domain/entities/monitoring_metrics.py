"""Domain entity: MonitoringMetrics.

Pre-computed aggregated metrics for a completed monitoring session.
Calculated once at monitoring completion and stored for fast retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MonitoringMetrics:
    """Aggregated metrics for a completed or aborted monitoring session."""

    monitoring_id: int
    total_tomatoes: int
    healthy_count: int
    unhealthy_count: int
    pct_healthy: float
    pct_unhealthy: float
    snapshots_with_detections: int
    id: Optional[int] = None
    pct_green: float = field(default=0.0)
    pct_breaker: float = field(default=0.0)
    pct_turning: float = field(default=0.0)
    pct_pink: float = field(default=0.0)
    pct_light_red: float = field(default=0.0)
    pct_red: float = field(default=0.0)
    computed_at: Optional[datetime] = None
