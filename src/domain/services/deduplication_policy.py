from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeduplicationDecision:
    should_reuse_previous_result: bool
    reason: str


class DeduplicationPolicy:
    def should_reuse_result(
        self,
        is_new_track: bool,
        has_been_processed: bool,
        current_area: int,
        best_area: int,
    ) -> DeduplicationDecision:
        if is_new_track:
            return DeduplicationDecision(
                should_reuse_previous_result=False,
                reason="new_track",
            )

        if not has_been_processed:
            return DeduplicationDecision(
                should_reuse_previous_result=False,
                reason="not_processed_yet",
            )

        if current_area > best_area:
            return DeduplicationDecision(
                should_reuse_previous_result=False,
                reason="better_view_available",
            )

        return DeduplicationDecision(
            should_reuse_previous_result=True,
            reason="reuse_previous_result",
        )