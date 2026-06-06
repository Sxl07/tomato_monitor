from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.entities.fruit_detection import FruitDetection
from src.domain.entities.health_assessment import HealthAssessment
from src.domain.entities.maturity_assessment import MaturityAssessment


@dataclass
class InspectionResult:
    detection: FruitDetection
    health_assessment: Optional[HealthAssessment] = None
    maturity_assessment: Optional[MaturityAssessment] = None

    def has_health(self) -> bool:
        return self.health_assessment is not None

    def has_maturity(self) -> bool:
        return self.maturity_assessment is not None

    def to_record_dict(self) -> dict:
        base = self.detection.to_record_dict()

        if self.health_assessment is not None:
            base.update({
                "health_label": self.health_assessment.label,
                "health_confidence": self.health_assessment.confidence,
                "prob_healthy": self.health_assessment.prob_healthy,
                "prob_unhealthy": self.health_assessment.prob_unhealthy,
            })
        else:
            base.update({
                "health_label": None,
                "health_confidence": None,
                "prob_healthy": None,
                "prob_unhealthy": None,
            })

        if self.maturity_assessment is not None:
            base.update({
                "usda_stage": self.maturity_assessment.usda_stage,
                "maturity_percent": self.maturity_assessment.maturity_percent,
                "maturity_confidence": self.maturity_assessment.confidence,
                "occlusion_ratio": self.maturity_assessment.occlusion_ratio,
                "visible_fruit_ratio": self.maturity_assessment.visible_fruit_ratio,
                "maturity_warning": self.maturity_assessment.warning,
            })
        else:
            base.update({
                "usda_stage": None,
                "maturity_percent": None,
                "maturity_confidence": None,
                "occlusion_ratio": None,
                "visible_fruit_ratio": None,
                "maturity_warning": None,
            })

        return base