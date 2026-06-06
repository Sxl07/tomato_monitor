from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HealthAssessment:
    label: str
    confidence: float
    prob_healthy: float
    prob_unhealthy: float

    def is_healthy(self) -> bool:
        return self.label == "healthy"

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "prob_healthy": self.prob_healthy,
            "prob_unhealthy": self.prob_unhealthy,
        }