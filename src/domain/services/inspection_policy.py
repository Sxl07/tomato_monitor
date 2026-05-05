from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class InspectionDecision:
    should_run_health: bool
    should_run_maturity: bool
    reason: str


class InspectionPolicy:
    def decide(
        self,
        detection_score: float,
        crop_is_large_enough: bool,
        maturity_min_score: float,
        run_maturity_only_for_healthy: bool,
        health_result: Optional[dict],
    ) -> InspectionDecision:
        if not crop_is_large_enough:
            return InspectionDecision(
                should_run_health=False,
                should_run_maturity=False,
                reason="crop_too_small",
            )

        should_run_health = True

        if detection_score < maturity_min_score:
            return InspectionDecision(
                should_run_health=should_run_health,
                should_run_maturity=False,
                reason="low_detection_score_for_maturity",
            )

        if run_maturity_only_for_healthy:
            if not health_result:
                return InspectionDecision(
                    should_run_health=should_run_health,
                    should_run_maturity=False,
                    reason="missing_health_result",
                )

            if health_result.get("label") != "healthy":
                return InspectionDecision(
                    should_run_health=should_run_health,
                    should_run_maturity=False,
                    reason="maturity_only_for_healthy",
                )

        return InspectionDecision(
            should_run_health=should_run_health,
            should_run_maturity=True,
            reason="full_inspection_allowed",
        )