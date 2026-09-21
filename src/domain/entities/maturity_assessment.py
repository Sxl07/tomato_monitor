from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# Canonical USDA maturity stages accepted by persistence (and the remote
# Supabase constraint). Any value outside this set (e.g. "unknown") represents
# a non-estimable maturity and must be persisted as NULL.
VALID_USDA_STAGES: frozenset[str] = frozenset(
    {"green", "breaker", "turning", "pink", "light_red", "red"}
)


def normalize_maturity_for_persistence(
    stage: Optional[str], percent: Optional[float]
) -> Tuple[Optional[str], Optional[float]]:
    """Normalize a maturity estimate for persistence.

    Returns the (stage, percent) pair unchanged when ``stage`` is one of the
    six valid USDA stages. For any other value (None, "unknown", or an
    unexpected label) both fields collapse to ``None`` so nothing outside the
    valid set is ever persisted.
    """
    if stage in VALID_USDA_STAGES:
        return stage, percent
    return None, None


@dataclass
class MaturityAssessment:
    usda_stage: str
    maturity_percent: float
    confidence: float
    occlusion_ratio: float = 0.0
    visible_fruit_ratio: float = 1.0
    warning: Optional[str] = None

    def is_valid_for_reporting(self) -> bool:
        return self.confidence >= 0.0 and self.maturity_percent >= 0.0

    def to_dict(self) -> dict:
        return {
            "usda_stage": self.usda_stage,
            "maturity_percent": self.maturity_percent,
            "confidence": self.confidence,
            "occlusion_ratio": self.occlusion_ratio,
            "visible_fruit_ratio": self.visible_fruit_ratio,
            "warning": self.warning,
        }