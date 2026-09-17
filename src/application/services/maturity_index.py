"""Ordinal maturity index (Spec 024, Block 3).

Pure functions. The maturity index (MI) is a weighted-mean ordinal value in
[0, 1] computed over the covered subset (fruits with a classifiable maturity
stage). It is an OPERATIONAL indicator (an ordinal average), NOT an agronomic
measure and NOT the percentage of Light Red/Red fruits.

Ordinal scale (6 stages, 5 steps, divisor 5):
    green=0.0, breaker=0.2, turning=0.4, pink=0.6, light_red=0.8, red=1.0

    MI = ( Σ_s count[s] * v(s) ) / maturity_covered      if covered > 0
    MI = None                                            if covered == 0
"""

from __future__ import annotations

from typing import Optional

from src.application.dtos.dashboard_analytics_dtos import MaturityCounts

# Normalized ordinal value per stage (k / 5).
STAGE_ORDINAL_VALUE: dict[str, float] = {
    "green": 0.0,
    "breaker": 0.2,
    "turning": 0.4,
    "pink": 0.6,
    "light_red": 0.8,
    "red": 1.0,
}


def maturity_index(counts: MaturityCounts) -> Optional[float]:
    """Weighted-mean ordinal maturity index over the covered subset.

    Args:
        counts: Real per-stage counts (from ``counts_from_results``).

    Returns:
        Float in [0, 1], or None when coverage is 0.
    """
    covered = counts.covered
    if covered <= 0:
        return None
    d = counts.as_dict()
    weighted_sum = sum(d[stage] * STAGE_ORDINAL_VALUE[stage] for stage in d)
    return weighted_sum / covered
