"""Pipeline metrics accumulator for monitoring sessions.

Collects per-snapshot-cycle timing, temperature, memory, and throughput
measurements. Written to a JSON file at session end for thesis data.

Does NOT persist to the database — lives in memory during session and
outputs to filesystem only.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Valid snapshot reasons
VALID_SNAPSHOT_REASONS = frozenset({"first_frame", "scene_change", "timeout"})


@dataclass(frozen=True)
class CycleMetrics:
    """Metrics for a single snapshot cycle.

    All timing fields are in milliseconds. Temperature in Celsius.
    Memory in megabytes. Timestamp is time.monotonic() for rate calculations.
    """

    cycle_index: int
    snapshot_reason: str  # "first_frame" | "scene_change" | "timeout"
    camera_read_ms: float
    scene_gate_ms: float
    snapshot_save_ms: float
    inference_total_ms: float
    detection_ms: float
    health_ms: float
    maturity_ms: float
    persistence_ms: float
    temperature_c: Optional[float]
    rss_mb: Optional[float]
    timestamp: float

    def __post_init__(self):
        if self.snapshot_reason not in VALID_SNAPSHOT_REASONS:
            raise ValueError(
                f"Invalid snapshot_reason: '{self.snapshot_reason}'. "
                f"Must be one of: {sorted(VALID_SNAPSHOT_REASONS)}"
            )


@dataclass
class PipelineMetrics:
    """Accumulates metrics across all snapshot cycles in a monitoring session.

    Not persisted to the database. Written to JSON file at session end.
    """

    cycles: list[CycleMetrics] = field(default_factory=list)
    session_start_time: float = 0.0
    peak_temperature_c: float = 0.0
    peak_rss_mb: float = 0.0
    _has_temperature: bool = field(default=False, repr=False)
    _has_rss: bool = field(default=False, repr=False)

    def add_cycle(self, cycle: CycleMetrics) -> None:
        """Record a completed snapshot cycle's metrics."""
        self.cycles.append(cycle)

        # Update peak temperature — first non-None sets the baseline
        if cycle.temperature_c is not None:
            if not self._has_temperature or cycle.temperature_c > self.peak_temperature_c:
                self.peak_temperature_c = cycle.temperature_c
                self._has_temperature = True
        # Update peak RSS memory — first non-None sets the baseline
        if cycle.rss_mb is not None:
            if not self._has_rss or cycle.rss_mb > self.peak_rss_mb:
                self.peak_rss_mb = cycle.rss_mb
                self._has_rss = True

    def snapshots_per_minute(self) -> float:
        """Calculate snapshots captured per minute based on elapsed time."""
        if not self.cycles:
            return 0.0
        elapsed_seconds = self.cycles[-1].timestamp - self.session_start_time
        if elapsed_seconds <= 0:
            return 0.0
        return len(self.cycles) / (elapsed_seconds / 60.0)

    def inferences_per_minute(self) -> float:
        """Calculate inferences executed per minute.

        Same as snapshots_per_minute since each snapshot triggers one inference.
        """
        return self.snapshots_per_minute()

    def get_session_summary(self) -> dict:
        """Generate a summary dict suitable for JSON serialization."""
        n = len(self.cycles)
        if n == 0:
            return {
                "total_cycles": 0,
                "snapshots_per_minute": 0.0,
                "inferences_per_minute": 0.0,
                "peak_temperature_c": self.peak_temperature_c,
                "peak_rss_mb": self.peak_rss_mb,
                "average_timings_ms": {},
                "snapshot_reasons": {},
            }

        # Average timings
        avg_timings = {
            "camera_read_ms": sum(c.camera_read_ms for c in self.cycles) / n,
            "scene_gate_ms": sum(c.scene_gate_ms for c in self.cycles) / n,
            "snapshot_save_ms": sum(c.snapshot_save_ms for c in self.cycles) / n,
            "inference_total_ms": sum(c.inference_total_ms for c in self.cycles) / n,
            "detection_ms": sum(c.detection_ms for c in self.cycles) / n,
            "health_ms": sum(c.health_ms for c in self.cycles) / n,
            "maturity_ms": sum(c.maturity_ms for c in self.cycles) / n,
            "persistence_ms": sum(c.persistence_ms for c in self.cycles) / n,
        }

        # Snapshot reasons breakdown — always include all three valid reasons
        reasons: dict[str, int] = {reason: 0 for reason in sorted(VALID_SNAPSHOT_REASONS)}
        for c in self.cycles:
            reasons[c.snapshot_reason] = reasons.get(c.snapshot_reason, 0) + 1

        return {
            "total_cycles": n,
            "snapshots_per_minute": round(self.snapshots_per_minute(), 2),
            "inferences_per_minute": round(self.inferences_per_minute(), 2),
            "peak_temperature_c": round(self.peak_temperature_c, 1),
            "peak_rss_mb": round(self.peak_rss_mb, 1),
            "average_timings_ms": {k: round(v, 2) for k, v in avg_timings.items()},
            "snapshot_reasons": reasons,
        }

    def to_json_file(self, output_path: str, profile_name: str) -> None:
        """Write session metrics summary to a JSON file for thesis data.

        Args:
            output_path: Full path to the output JSON file.
            profile_name: Name of the execution profile used ("edge" or "full").
        """
        session_summary = self.get_session_summary()
        # Build output with profile_name first for readability
        summary = {"profile_name": profile_name, **session_summary}

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Pipeline metrics written to: {output_path}")
        except Exception as e:
            logger.error(f"Failed to write pipeline metrics JSON: {e}")
