"""Maturity statistics from real InspectionResult data (Spec 024, Block 2).

Pure functions. No database access, no hardware, no ML. The source of truth for
maturity is the set of ``DetectionInspectionResult`` rows of a monitoring: each
row may carry a ``maturity_stage`` (one of the six USDA stages) or None.

- ``counts_from_results`` counts rows per stage (rows with a null stage are the
  uncovered fruits and are simply not counted in any stage).
- ``distribution_pcts`` yields per-stage percentages over the covered subset.
- ``predominant_stages`` returns the stage(s) with the maximum count, in
  phenological order, so a tie is deterministic ("Mixto — A / B").
- ``harvestable_share`` = (light_red + red) / covered (over covered subset).
- ``fallback_from_pcts`` builds the visual-only distribution used when there are
  no InspectionResult rows but MonitoringMetrics.pct_* exist.
"""

from __future__ import annotations

from typing import Iterable, Optional

from src.application.dtos.dashboard_analytics_dtos import (
    STAGE_ORDER,
    MaturityCounts,
    MaturityDistributionFallback,
)

_VALID_STAGES = frozenset(STAGE_ORDER)


def counts_from_results(results: Iterable) -> MaturityCounts:
    """Count real maturity stages from inspection results.

    Args:
        results: Iterable of objects with a ``maturity_stage`` attribute
            (e.g. ``DetectionInspectionResult``). A None or unknown stage is
            ignored (those detections are uncovered).

    Returns:
        MaturityCounts with per-stage counts.
    """
    tally = {stage: 0 for stage in STAGE_ORDER}
    for r in results:
        stage = getattr(r, "maturity_stage", None)
        if stage in _VALID_STAGES:
            tally[stage] += 1
    return MaturityCounts(
        green=tally["green"],
        breaker=tally["breaker"],
        turning=tally["turning"],
        pink=tally["pink"],
        light_red=tally["light_red"],
        red=tally["red"],
    )


def add_counts(a: MaturityCounts, b: MaturityCounts) -> MaturityCounts:
    """Sum two MaturityCounts stage-by-stage (used by "all" aggregation)."""
    return MaturityCounts(
        green=a.green + b.green,
        breaker=a.breaker + b.breaker,
        turning=a.turning + b.turning,
        pink=a.pink + b.pink,
        light_red=a.light_red + b.light_red,
        red=a.red + b.red,
    )


def distribution_pcts(counts: MaturityCounts) -> dict[str, float]:
    """Per-stage percentages over the covered subset.

    Returns all-zero percentages when coverage is 0 (no division by zero).
    """
    covered = counts.covered
    if covered <= 0:
        return {stage: 0.0 for stage in STAGE_ORDER}
    d = counts.as_dict()
    return {stage: (d[stage] / covered) * 100.0 for stage in STAGE_ORDER}


def predominant_stages(counts: MaturityCounts) -> list[str]:
    """Stage(s) with the maximum count, in phenological order.

    - [] when there is no coverage (max count is 0).
    - [stage] when a single stage dominates.
    - [stage_a, stage_b, ...] (in STAGE_ORDER) when two or more stages tie for
      the maximum — the caller renders this as "Mixto — A / B".
    """
    d = counts.as_dict()
    max_count = max(d.values())
    if max_count <= 0:
        return []
    return [stage for stage in STAGE_ORDER if d[stage] == max_count]


def harvestable_share(counts: MaturityCounts) -> Optional[float]:
    """(light_red + red) / covered, over the covered subset.

    Returns None when coverage is 0. This is a fraction in [0, 1]; the UI must
    label its denominator explicitly (fruits with classifiable maturity).
    """
    covered = counts.covered
    if covered <= 0:
        return None
    return (counts.light_red + counts.red) / covered


def fallback_from_pcts(
    pct_green: float,
    pct_breaker: float,
    pct_turning: float,
    pct_pink: float,
    pct_light_red: float,
    pct_red: float,
) -> MaturityDistributionFallback:
    """Build the visual-only fallback distribution from persisted pct_*.

    ``coverage_known`` for this structure is always False; it must not be used
    to derive counts, coverage, maturity index or harvestable share.
    """
    return MaturityDistributionFallback(
        pct_green=pct_green,
        pct_breaker=pct_breaker,
        pct_turning=pct_turning,
        pct_pink=pct_pink,
        pct_light_red=pct_light_red,
        pct_red=pct_red,
    )
