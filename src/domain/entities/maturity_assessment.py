from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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