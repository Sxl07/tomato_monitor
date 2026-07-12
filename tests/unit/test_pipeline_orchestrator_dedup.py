"""Unit tests for pipeline_orchestrator deduplication and skip_maturity.

Tests Correction 1 (skip_maturity parameter) and Correction 1B (best_area
semantics after Track.update() fix) from Spec 009 hardening.

These tests use real SimpleTracker and DeduplicationPolicy but mock
the heavy ML components (detector, health, maturity).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np
import pytest

from src.infrastructure.vision.tracker_adapter import SimpleTracker, Track, bbox_area
from src.domain.services.deduplication_policy import DeduplicationPolicy


# ---------------------------------------------------------------------------
# Test: Track.update() does NOT update best_area
# ---------------------------------------------------------------------------


class TestTrackBestAreaSemantics:
    """Verify Track.update() no longer updates best_area."""

    def test_track_update_does_not_change_best_area(self):
        """Track.update() with larger bbox does NOT change best_area."""
        track = Track(
            track_id=1,
            bbox=(100, 100, 200, 200),
            score=0.9,
            best_area=bbox_area((100, 100, 200, 200)),  # 10000
        )
        initial_best = track.best_area

        # Update with a larger bbox
        track.update((50, 50, 300, 300), 0.95)

        # best_area should NOT change
        assert track.best_area == initial_best
        # But bbox, score, hits should update
        assert track.bbox == (50, 50, 300, 300)
        assert track.score == 0.95
        assert track.hits == 2
        assert track.missed == 0

    def test_new_track_has_initial_best_area(self):
        """A new track created by SimpleTracker has best_area = initial bbox area."""
        tracker = SimpleTracker()
        detections = [{"bbox": (100, 100, 200, 200), "score": 0.9}]

        result = tracker.update(detections)
        track = tracker.get_track(result[0]["track_id"])

        assert track.best_area == 100 * 100  # (200-100)*(200-100)

    def test_dedup_allows_better_view_when_area_exceeds_best(self):
        """DeduplicationPolicy returns better_view_available when current > best."""
        policy = DeduplicationPolicy()

        # Track was processed with area 10000, now we see area 20000
        decision = policy.should_reuse_result(
            is_new_track=False,
            has_been_processed=True,
            current_area=20000,
            best_area=10000,
        )

        assert decision.should_reuse_previous_result is False
        assert decision.reason == "better_view_available"

    def test_dedup_reuses_when_area_not_better(self):
        """DeduplicationPolicy reuses when current <= best."""
        policy = DeduplicationPolicy()

        decision = policy.should_reuse_result(
            is_new_track=False,
            has_been_processed=True,
            current_area=8000,
            best_area=10000,
        )

        assert decision.should_reuse_previous_result is True
        assert decision.reason == "reuse_previous_result"


# ---------------------------------------------------------------------------
# Test: Full dedup flow with real tracker
# ---------------------------------------------------------------------------


class TestFullDedupFlowWithTracker:
    """Integration test: SimpleTracker + DeduplicationPolicy + best_area logic."""

    def test_second_detection_larger_bbox_triggers_reprocessing(self):
        """Second detection with larger bbox → better_view_available → re-process."""
        tracker = SimpleTracker()
        policy = DeduplicationPolicy()

        # Frame 1: first detection (small bbox)
        dets_1 = [{"bbox": (100, 100, 150, 150), "score": 0.9}]
        tracked_1 = tracker.update(dets_1)
        track_id = tracked_1[0]["track_id"]
        track = tracker.get_track(track_id)

        # Simulate processing: mark as processed, set best_area
        track.has_been_processed = True
        # best_area remains initial: 50*50 = 2500

        # Frame 2: same track, larger bbox
        dets_2 = [{"bbox": (80, 80, 220, 220), "score": 0.92}]
        tracked_2 = tracker.update(dets_2)
        assert tracked_2[0]["track_id"] == track_id
        assert tracked_2[0]["is_new_track"] is False

        # Check dedup decision
        current_area = bbox_area((80, 80, 220, 220))  # 140*140 = 19600
        decision = policy.should_reuse_result(
            is_new_track=False,
            has_been_processed=track.has_been_processed,
            current_area=current_area,
            best_area=track.best_area,  # Still 2500 (not updated by update())
        )

        assert decision.should_reuse_previous_result is False
        assert decision.reason == "better_view_available"

        # After re-processing, update best_area manually (as process_frame does)
        track.best_area = current_area

        # Frame 3: same track, smaller bbox
        dets_3 = [{"bbox": (110, 110, 180, 180), "score": 0.88}]
        tracked_3 = tracker.update(dets_3)
        assert tracked_3[0]["track_id"] == track_id

        current_area_3 = bbox_area((110, 110, 180, 180))  # 70*70 = 4900
        decision_3 = policy.should_reuse_result(
            is_new_track=False,
            has_been_processed=track.has_been_processed,
            current_area=current_area_3,
            best_area=track.best_area,  # 19600
        )

        assert decision_3.should_reuse_previous_result is True
        assert decision_3.reason == "reuse_previous_result"


# ---------------------------------------------------------------------------
# Test: skip_maturity parameter in process_frame
# ---------------------------------------------------------------------------


class TestProcessFrameSkipMaturity:
    """Verify process_frame respects skip_maturity parameter."""

    def test_skip_maturity_true_does_not_call_estimate(self):
        """process_frame(skip_maturity=True) → estimate_maturity_for_crop not called."""
        from src.infrastructure.vision.pipeline_orchestrator import (
            PipelineComponents,
            process_frame,
        )

        # Build minimal components with mocks
        mock_detector = MagicMock()
        mock_health_model = MagicMock()
        mock_health_transform = MagicMock()
        tracker = SimpleTracker()
        dedup = DeduplicationPolicy()

        from src.domain.services.inspection_policy import InspectionPolicy
        policy = InspectionPolicy()

        components = PipelineComponents(
            detector=mock_detector,
            health_model=mock_health_model,
            health_transform=mock_health_transform,
            tracker=tracker,
            deduplication_policy=dedup,
            inspection_policy=policy,
        )

        # Mock detector to return one detection
        fake_image = np.zeros((480, 640, 3), dtype=np.uint8)

        with patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_detector"
        ) as mock_import_det, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_health"
        ) as mock_import_health, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_maturity"
        ) as mock_import_maturity:
            mock_run_det = MagicMock(return_value=MagicMock())
            mock_extract = MagicMock(return_value=[
                {"bbox": (100, 100, 200, 200), "score": 0.92, "class_id": 0, "detection_id": 0}
            ])
            mock_import_det.return_value = (MagicMock(), mock_run_det, mock_extract)

            mock_predict_health = MagicMock(
                return_value={"label": "healthy", "confidence": 0.95,
                             "prob_healthy": 0.95, "prob_unhealthy": 0.05}
            )
            mock_import_health.return_value = (MagicMock(), mock_predict_health)

            mock_estimate_maturity = MagicMock()
            mock_import_maturity.return_value = mock_estimate_maturity

            result = process_frame(
                fake_image, components, "test.jpg", skip_maturity=True
            )

        # maturity should NOT have been called
        mock_estimate_maturity.assert_not_called()
        # Check result
        assert result["maturity_executed_count"] == 0
        for det in result["detections"]:
            assert det["maturity_executed"] is False
            assert det["maturity_result"] is None

    def test_skip_maturity_false_calls_estimate_when_eligible(self):
        """process_frame(skip_maturity=False) → estimate_maturity called for eligible."""
        from src.infrastructure.vision.pipeline_orchestrator import (
            PipelineComponents,
            process_frame,
        )

        mock_detector = MagicMock()
        mock_health_model = MagicMock()
        mock_health_transform = MagicMock()
        tracker = SimpleTracker()
        dedup = DeduplicationPolicy()

        from src.domain.services.inspection_policy import InspectionPolicy
        policy = InspectionPolicy()

        components = PipelineComponents(
            detector=mock_detector,
            health_model=mock_health_model,
            health_transform=mock_health_transform,
            tracker=tracker,
            deduplication_policy=dedup,
            inspection_policy=policy,
        )

        fake_image = np.zeros((480, 640, 3), dtype=np.uint8)

        with patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_detector"
        ) as mock_import_det, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_health"
        ) as mock_import_health, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_maturity"
        ) as mock_import_maturity:
            mock_run_det = MagicMock(return_value=MagicMock())
            mock_extract = MagicMock(return_value=[
                {"bbox": (100, 100, 200, 200), "score": 0.92, "class_id": 0, "detection_id": 0}
            ])
            mock_import_det.return_value = (MagicMock(), mock_run_det, mock_extract)

            mock_predict_health = MagicMock(
                return_value={"label": "healthy", "confidence": 0.95,
                             "prob_healthy": 0.95, "prob_unhealthy": 0.05}
            )
            mock_import_health.return_value = (MagicMock(), mock_predict_health)

            mock_estimate_maturity = MagicMock(
                return_value={"estimate": {"usda_stage": "turning", "maturity_percent": 45.0},
                             "fruit_mask": None, "masked_crop": None}
            )
            mock_import_maturity.return_value = mock_estimate_maturity

            result = process_frame(
                fake_image, components, "test.jpg", skip_maturity=False
            )

        # maturity SHOULD have been called (healthy + eligible score)
        mock_estimate_maturity.assert_called_once()
        assert result["maturity_executed_count"] == 1


# ---------------------------------------------------------------------------
# Test: Integral 3-frame dedup flow with real process_frame
# ---------------------------------------------------------------------------


class TestIntegralThreeFrameDedup:
    """Integration test: real process_frame with 3 consecutive frames.

    Uses real SimpleTracker, DeduplicationPolicy, InspectionPolicy.
    Detector, health, maturity are mocked via lazy import patches.
    """

    def test_three_frames_dedup_flow(self):
        """
        Frame 1: small bbox → new track → health+maturity executed → best_area=2500
        Frame 2: same track larger bbox → better_view → re-process → best_area=19600
        Frame 3: same track smaller bbox → reuse → no health/maturity
        """
        from src.infrastructure.vision.pipeline_orchestrator import (
            PipelineComponents,
            process_frame,
        )
        from src.domain.services.inspection_policy import InspectionPolicy

        tracker = SimpleTracker()
        dedup = DeduplicationPolicy()
        policy = InspectionPolicy()

        components = PipelineComponents(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
            tracker=tracker,
            deduplication_policy=dedup,
            inspection_policy=policy,
        )

        fake_image = np.zeros((480, 640, 3), dtype=np.uint8)

        # We need the same track across 3 frames. The tracker matches by IoU/centroid.
        # Frame 1: bbox (100,100,150,150) → area 2500
        # Frame 2: bbox (80,80,220,220) → area 19600, overlaps with frame 1
        # Frame 3: bbox (110,110,180,180) → area 4900, overlaps with frame 2

        mock_predict_health = MagicMock(
            return_value={"label": "healthy", "confidence": 0.95,
                         "prob_healthy": 0.95, "prob_unhealthy": 0.05}
        )
        mock_estimate_maturity = MagicMock(
            return_value={"estimate": {"usda_stage": "turning", "maturity_percent": 45.0},
                         "fruit_mask": None, "masked_crop": None}
        )

        with patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_detector"
        ) as mock_det_import, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_health"
        ) as mock_health_import, patch(
            "src.infrastructure.vision.pipeline_orchestrator._import_maturity"
        ) as mock_maturity_import:

            # Frame 1: small bbox
            mock_run_det_1 = MagicMock(return_value=MagicMock())
            mock_extract_1 = MagicMock(return_value=[
                {"bbox": (100, 100, 150, 150), "score": 0.92, "class_id": 0, "detection_id": 0}
            ])
            mock_det_import.return_value = (MagicMock(), mock_run_det_1, mock_extract_1)
            mock_health_import.return_value = (MagicMock(), mock_predict_health)
            mock_maturity_import.return_value = mock_estimate_maturity

            result_1 = process_frame(fake_image, components, "frame1.jpg")

            # Verify frame 1
            assert len(result_1["detections"]) == 1
            det_1 = result_1["detections"][0]
            assert det_1["is_new_track"] is True
            assert det_1["reused_previous_result"] is False
            track_id = det_1["track_id"]

            # Check best_area on the track
            track = tracker.get_track(track_id)
            assert track.best_area == 50 * 50  # (150-100)*(150-100) = 2500

            # Frame 2: same track, larger bbox
            mock_run_det_2 = MagicMock(return_value=MagicMock())
            mock_extract_2 = MagicMock(return_value=[
                {"bbox": (80, 80, 220, 220), "score": 0.93, "class_id": 0, "detection_id": 0}
            ])
            mock_det_import.return_value = (MagicMock(), mock_run_det_2, mock_extract_2)

            result_2 = process_frame(fake_image, components, "frame2.jpg")

            det_2 = result_2["detections"][0]
            assert det_2["track_id"] == track_id
            assert det_2["is_new_track"] is False
            assert det_2["reused_previous_result"] is False
            # decision_reason reflects InspectionPolicy's final decision, not dedup
            # The key fact: it was NOT reused, so health was re-executed
            assert track.best_area == 140 * 140  # (220-80)*(220-80) = 19600

            # Frame 3: same track, smaller bbox
            mock_run_det_3 = MagicMock(return_value=MagicMock())
            mock_extract_3 = MagicMock(return_value=[
                {"bbox": (110, 110, 180, 180), "score": 0.88, "class_id": 0, "detection_id": 0}
            ])
            mock_det_import.return_value = (MagicMock(), mock_run_det_3, mock_extract_3)

            result_3 = process_frame(fake_image, components, "frame3.jpg")

            det_3 = result_3["detections"][0]
            assert det_3["track_id"] == track_id
            assert det_3["is_new_track"] is False
            assert det_3["reused_previous_result"] is True
            assert det_3["decision_reason"] == "reuse_previous_result"
            # best_area unchanged
            assert track.best_area == 19600

        # Final call count verification
        assert mock_predict_health.call_count == 2  # Frame 1 + Frame 2
        assert mock_estimate_maturity.call_count == 2  # Frame 1 + Frame 2
