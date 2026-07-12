"""Unit tests for SnapshotAnalysisService — Spec 009, Phase C2A.

Validates the isolated analysis service without requiring Detectron2.
All heavy dependencies are injected via mocks/fakes.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

from src.domain.entities.snapshot import Snapshot
from src.application.services.snapshot_analysis_service import (
    SnapshotAnalysisService,
    AnalysisProgress,
    AnalysisResult,
    TrackBestResult,
)


# ---------------------------------------------------------------------------
# Test helpers / fakes
# ---------------------------------------------------------------------------


class FakeSnapshotRepo:
    """Fake snapshot repository returning controlled Snapshot objects."""

    def __init__(self, snapshots: list[Snapshot] = None):
        self._snapshots = snapshots or []
        self._has_detections_calls: list[tuple[int, bool]] = []

    def get_by_monitoring(self, monitoring_id: int) -> list[Snapshot]:
        return list(self._snapshots)

    def update_has_detections(self, id: int, has_detections: bool) -> Snapshot:
        self._has_detections_calls.append((id, has_detections))
        for s in self._snapshots:
            if s.id == id:
                s.has_detections = has_detections
                return s
        return MagicMock()


class FakeInspectionResultRepo:
    """Fake inspection result repository tracking creates."""

    def __init__(self, *, raise_on_create: bool = False):
        self.created: list[tuple[int, Any]] = []
        self._raise_on_create = raise_on_create

    def create(self, snapshot_id: int, result: Any) -> Any:
        if self._raise_on_create:
            raise RuntimeError("Persistence failure")
        self.created.append((snapshot_id, result))
        result.id = len(self.created)
        return result

    def get_by_snapshot(self, snapshot_id: int) -> list:
        return [r for sid, r in self.created if sid == snapshot_id]

    def get_by_monitoring(self, monitoring_id: int) -> list:
        return [r for _, r in self.created]


class FakeDbSession:
    """Fake DB session tracking commits and rollbacks."""

    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def _make_snapshot(
    id: int, monitoring_id: int, frame_index: int, image_path: str = None
) -> Snapshot:
    """Create a test Snapshot entity."""
    if image_path is None:
        image_path = f"outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg"
    return Snapshot(
        id=id,
        monitoring_id=monitoring_id,
        image_path=image_path,
        frame_index=frame_index,
        has_detections=False,
    )


def _make_frame_result(detections: list = None) -> dict:
    """Create a process_frame result dict."""
    dets = detections or []
    return {
        "detections_count": len(dets),
        "tracked_count": len(dets),
        "health_executed_count": len(dets),
        "maturity_executed_count": 0,
        "reused_count": 0,
        "new_tracks_count": 0,
        "times": {"detection_sec": 0.5, "tracking_sec": 0.01, "total_frame_sec": 0.6},
        "detections": dets,
    }


def _make_detection(
    track_id: int,
    bbox: list = None,
    det_score: float = 0.92,
    reused: bool = False,
    health_label: str = "healthy",
    health_confidence: float = 0.95,
    maturity_stage: str = "turning",
    maturity_percent: float = 45.0,
    is_new_track: bool = False,
) -> dict:
    """Create a single detection dict for testing."""
    return {
        "track_id": track_id,
        "is_new_track": is_new_track,
        "track_hits": 3,
        "detection_id": 0,
        "bbox": bbox or [100, 100, 200, 200],
        "det_score": det_score,
        "reused_previous_result": reused,
        "health_result": {"label": health_label, "confidence": health_confidence},
        "maturity_result": {"usda_stage": maturity_stage, "maturity_percent": maturity_percent},
    }


def _build_service(
    snapshots: list[Snapshot] = None,
    process_frame_results: list[dict] = None,
    analysis_skip_maturity: bool = False,
    thermal_monitor: Any = None,
    log_service: Any = None,
    components_factory_raises: bool = False,
    inspection_result_repo: FakeInspectionResultRepo = None,
) -> tuple[SnapshotAnalysisService, FakeSnapshotRepo, FakeInspectionResultRepo, FakeDbSession, MagicMock, MagicMock]:
    """Build a SnapshotAnalysisService with fakes for testing.

    Returns:
        (service, snapshot_repo, inspection_result_repo, db_session,
         components_mock, process_frame_mock)
    """
    snapshot_repo = FakeSnapshotRepo(snapshots or [])
    if inspection_result_repo is None:
        inspection_result_repo = FakeInspectionResultRepo()
    db_session = FakeDbSession()

    components_mock = MagicMock(name="PipelineComponents")
    if components_factory_raises:
        components_factory = MagicMock(side_effect=RuntimeError("Model load failed"))
    else:
        components_factory = MagicMock(return_value=components_mock)

    # Default: return empty detections if no results specified
    results_iter = iter(process_frame_results or [_make_frame_result()])
    process_frame_mock = MagicMock(side_effect=lambda img, comp, name, **kwargs: next(results_iter))

    annotation_renderer = MagicMock(
        return_value=np.zeros((480, 640, 3), dtype=np.uint8)
    )

    service = SnapshotAnalysisService(
        monitoring_id=1,
        snapshot_repo=snapshot_repo,
        inspection_result_repo=inspection_result_repo,
        db_session=db_session,
        components_factory=components_factory,
        process_frame_fn=process_frame_mock,
        annotation_renderer=annotation_renderer,
        analysis_skip_maturity=analysis_skip_maturity,
        thermal_monitor=thermal_monitor,
        log_service=log_service,
    )

    return (
        service,
        snapshot_repo,
        inspection_result_repo,
        db_session,
        components_factory,
        process_frame_mock,
    )


# ---------------------------------------------------------------------------
# Test 1: Zero snapshots → returns zeros, components_factory NOT called
# ---------------------------------------------------------------------------


class TestZeroSnapshots:
    """Verify behavior with no snapshots."""

    def test_zero_snapshots_returns_zeros(self):
        """Empty snapshot list returns AnalysisResult with zeros."""
        service, _, _, _, components_factory, _ = _build_service(snapshots=[])

        with patch("cv2.imread"), patch("cv2.imwrite", return_value=True):
            result = service.run()

        assert result.total_snapshots == 0
        assert result.processed_snapshots == 0
        assert result.unique_tomatoes == 0
        assert result.healthy_count == 0
        assert result.unhealthy_count == 0

    def test_zero_snapshots_components_factory_not_called(self):
        """components_factory must NOT be called when there are no snapshots."""
        service, _, _, _, components_factory, _ = _build_service(snapshots=[])

        with patch("cv2.imread"), patch("cv2.imwrite", return_value=True):
            service.run()

        components_factory.assert_not_called()

    def test_zero_snapshots_progress_completed(self):
        """Progress status must be 'completed' even with zero snapshots."""
        service, _, _, _, _, _ = _build_service(snapshots=[])

        with patch("cv2.imread"), patch("cv2.imwrite", return_value=True):
            service.run()

        assert service.progress.status == "completed"


# ---------------------------------------------------------------------------
# Test 2: Processes in frame_index ascending order
# ---------------------------------------------------------------------------


class TestProcessingOrder:
    """Verify snapshots are processed in frame_index ascending order."""

    def test_unordered_snapshots_processed_in_order(self):
        """Given unordered snapshots, process_frame_fn sees them sorted by frame_index."""
        snapshots = [
            _make_snapshot(id=3, monitoring_id=1, frame_index=5),
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=2),
        ]
        results = [_make_frame_result() for _ in range(3)]

        service, _, _, _, _, process_frame_mock = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Verify the calls happened in ascending frame_index order
        call_names = [c[0][2] for c in process_frame_mock.call_args_list]
        assert call_names == [
            "snapshot_000000.jpg",
            "snapshot_000002.jpg",
            "snapshot_000005.jpg",
        ]


# ---------------------------------------------------------------------------
# Test 3: components_factory called exactly once
# ---------------------------------------------------------------------------


class TestComponentsFactory:
    """Verify components_factory is called exactly once."""

    def test_components_factory_called_once(self):
        """components_factory must be called exactly once regardless of snapshot count."""
        snapshots = [
            _make_snapshot(id=i, monitoring_id=1, frame_index=i) for i in range(5)
        ]
        results = [_make_frame_result() for _ in range(5)]

        service, _, _, _, components_factory, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert components_factory.call_count == 1


# ---------------------------------------------------------------------------
# Test 4: Same components object passed to every process_frame_fn call
# ---------------------------------------------------------------------------


class TestComponentsReuse:
    """Verify same components object is passed to every process_frame_fn call."""

    def test_same_components_passed_every_call(self):
        """The same PipelineComponents instance must be passed to all calls."""
        snapshots = [
            _make_snapshot(id=i, monitoring_id=1, frame_index=i) for i in range(3)
        ]
        results = [_make_frame_result() for _ in range(3)]

        service, _, _, _, components_factory, process_frame_mock = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        components_obj = components_factory.return_value
        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        for call_args in process_frame_mock.call_args_list:
            assert call_args[0][1] is components_obj


# ---------------------------------------------------------------------------
# Test 5: Same track_id in 3 snapshots → unique_tomatoes=1
# ---------------------------------------------------------------------------


class TestSameTrackDeduplication:
    """Verify same track_id across snapshots counts as one unique tomato."""

    def test_same_track_three_snapshots_one_tomato(self):
        """Same track_id in 3 snapshots → unique_tomatoes=1, one InspectionResult."""
        snapshots = [
            _make_snapshot(id=i + 1, monitoring_id=1, frame_index=i) for i in range(3)
        ]
        # Same track_id=1 in all three snapshots
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
        ]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.unique_tomatoes == 1
        assert len(inspection_repo.created) == 1


# ---------------------------------------------------------------------------
# Test 6: Larger bbox area replaces previous best view
# ---------------------------------------------------------------------------


class TestBestAreaReplacement:
    """Verify larger area updates the best result for a track."""

    def test_larger_area_replaces_best(self):
        """A detection with larger area replaces the previous best for same track."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
            _make_snapshot(id=3, monitoring_id=1, frame_index=2),
        ]
        # Small → medium → large bbox for same track
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 150, 150])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 300, 300])]),
        ]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Best should be from snapshot id=3 (largest area: 200*200=40000)
        best = result.best_results_by_track[1]
        assert best.snapshot_id == 3
        assert best.best_area == 200 * 200  # (300-100)*(300-100)


# ---------------------------------------------------------------------------
# Test 7: reused_previous_result=True → no TrackBestResult update
# ---------------------------------------------------------------------------


class TestReusedDetections:
    """Verify reused detections don't update TrackBestResult."""

    def test_reused_no_best_result_update(self):
        """reused_previous_result=True → no TrackBestResult update, no extra persist."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        # First: real detection. Second: reused with larger area
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(
                track_id=1, bbox=[50, 50, 350, 350], reused=True
            )]),
        ]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Best should remain from snapshot 1 (reused doesn't update)
        best = result.best_results_by_track[1]
        assert best.snapshot_id == 1
        assert best.best_area == 100 * 100  # (200-100)*(200-100)
        # Only one InspectionResult persisted
        assert len(inspection_repo.created) == 1


# ---------------------------------------------------------------------------
# Test 8: Two distinct track_ids → unique_tomatoes=2
# ---------------------------------------------------------------------------


class TestMultipleTracks:
    """Verify distinct track_ids are counted separately."""

    def test_two_tracks_two_unique_tomatoes(self):
        """Two distinct track_ids → unique_tomatoes=2."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1, bbox=[100, 100, 200, 200]),
                _make_detection(track_id=2, bbox=[300, 300, 400, 400]),
            ]),
        ]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.unique_tomatoes == 2
        assert len(inspection_repo.created) == 2


# ---------------------------------------------------------------------------
# Test 9: Snapshot with detections → has_detections=True, annotated saved
# ---------------------------------------------------------------------------


class TestHasDetections:
    """Verify has_detections is updated correctly."""

    def test_snapshot_with_detections_marked_true(self):
        """Snapshot with detections gets has_detections=True."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1)]),
        ]

        service, snapshot_repo, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True) as mock_imwrite, \
             patch("os.makedirs"):
            result = service.run()

        # has_detections was updated to True
        assert (1, True) in snapshot_repo._has_detections_calls
        # Annotated snapshot saved (at least one imwrite call)
        assert mock_imwrite.call_count >= 1

    def test_annotated_snapshot_path(self):
        """Annotated snapshot saved to correct path."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=3),
        ]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True) as mock_imwrite, \
             patch("os.makedirs"):
            service.run()

        # Check annotated snapshot path
        annotated_calls = [
            c for c in mock_imwrite.call_args_list
            if "annotated_snapshots" in str(c)
        ]
        assert len(annotated_calls) >= 1
        path_arg = annotated_calls[0][0][0]
        assert "snapshot_000003.jpg" in path_arg


# ---------------------------------------------------------------------------
# Test 10: Snapshot without detections → has_detections=False
# ---------------------------------------------------------------------------


class TestNoDetections:
    """Verify snapshot without detections gets has_detections=False."""

    def test_snapshot_no_detections_marked_false(self):
        """Snapshot without detections gets has_detections=False."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [_make_frame_result([])]  # No detections

        service, snapshot_repo, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert (1, False) in snapshot_repo._has_detections_calls


# ---------------------------------------------------------------------------
# Test 11: Missing image (cv2.imread returns None) → failed_snapshots++
# ---------------------------------------------------------------------------


class TestMissingImage:
    """Verify failed image reads increment failed_snapshots and continue."""

    def test_imread_none_increments_failed_continues(self):
        """cv2.imread returning None → failed_snapshots==1, continues to next."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        # Only second snapshot will be processed
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        def imread_side_effect(path):
            if "missing" in path:
                return None
            return np.zeros((480, 640, 3), dtype=np.uint8)

        with patch("cv2.imread", side_effect=imread_side_effect), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Exact assertions
        assert result.failed_snapshots == 1
        assert result.processed_snapshots == 1
        assert result.failed_snapshots + result.processed_snapshots == result.total_snapshots
        # The second snapshot still got processed
        assert result.unique_tomatoes == 1


# ---------------------------------------------------------------------------
# Test 12: No health_result → health_label="unknown", still counted
# ---------------------------------------------------------------------------


class TestMissingHealth:
    """Verify missing health_result defaults to unknown."""

    def test_no_health_result_defaults_unknown(self):
        """Detection without health_result → health_label='unknown', still a tomato."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        # Detection with no health_result
        det = {
            "track_id": 1,
            "is_new_track": True,
            "track_hits": 1,
            "detection_id": 0,
            "bbox": [100, 100, 200, 200],
            "det_score": 0.90,
            "reused_previous_result": False,
            "health_result": None,
            "maturity_result": None,
        }
        results = [_make_frame_result([det])]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.unique_tomatoes == 1
        assert result.unknown_health_count == 1
        assert result.healthy_count == 0
        best = result.best_results_by_track[1]
        assert best.health_label == "unknown"
        assert best.health_confidence == 0.0


# ---------------------------------------------------------------------------
# Test 13: analysis_skip_maturity=True → maturity not stored
# ---------------------------------------------------------------------------


class TestSkipMaturity:
    """Verify analysis_skip_maturity=True nulls out maturity fields."""

    def test_skip_maturity_nulls_fields(self):
        """When analysis_skip_maturity=True, maturity data is not stored."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([_make_detection(
                track_id=1, maturity_stage="turning", maturity_percent=45.0
            )]),
        ]

        service, _, inspection_repo, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            analysis_skip_maturity=True,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        best = result.best_results_by_track[1]
        assert best.maturity_stage is None
        assert best.maturity_percent is None
        # Persisted result also has no maturity
        _, persisted = inspection_repo.created[0]
        assert persisted.maturity_stage is None
        assert persisted.maturity_percent is None


# ---------------------------------------------------------------------------
# Test 14: Progress updates after each snapshot
# ---------------------------------------------------------------------------


class TestProgressUpdates:
    """Verify progress is updated after each snapshot."""

    def test_progress_reflects_processed_count(self):
        """After run(), progress.processed_snapshots matches actual count."""
        snapshots = [
            _make_snapshot(id=i + 1, monitoring_id=1, frame_index=i) for i in range(4)
        ]
        results = [_make_frame_result() for _ in range(4)]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert service.progress.processed_snapshots == 4
        assert service.progress.total_snapshots == 4
        assert service.progress.status == "completed"

    def test_progress_current_frame_index_updated(self):
        """After run(), progress.current_frame_index reflects last processed."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=5),
            _make_snapshot(id=3, monitoring_id=1, frame_index=10),
        ]
        results = [_make_frame_result() for _ in range(3)]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert service.progress.current_frame_index == 10


# ---------------------------------------------------------------------------
# Test 15: Module importable without Detectron2
# ---------------------------------------------------------------------------


class TestImportability:
    """Verify the module can be imported without Detectron2."""

    def test_module_importable_without_detectron2(self):
        """Just importing the module must not raise ImportError."""
        from src.application.services.snapshot_analysis_service import (
            SnapshotAnalysisService,
            AnalysisProgress,
            AnalysisResult,
            TrackBestResult,
        )
        assert SnapshotAnalysisService is not None
        assert AnalysisProgress is not None
        assert AnalysisResult is not None
        assert TrackBestResult is not None


# ---------------------------------------------------------------------------
# Test 16: skip_maturity kwarg passed to process_frame (Correction 1)
# ---------------------------------------------------------------------------


class TestSkipMaturityPassedToProcessFrame:
    """Verify skip_maturity kwarg is forwarded to process_frame_fn."""

    def test_skip_maturity_true_passed_to_process_frame(self):
        """When analysis_skip_maturity=True, process_frame_fn is called with skip_maturity=True."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _, process_frame_mock = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            analysis_skip_maturity=True,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        # Verify process_frame_mock was called with skip_maturity=True
        assert process_frame_mock.call_count == 1
        _, kwargs = process_frame_mock.call_args
        assert kwargs.get("skip_maturity") is True

    def test_skip_maturity_false_passed_to_process_frame(self):
        """When analysis_skip_maturity=False, process_frame_fn is called with skip_maturity=False."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _, process_frame_mock = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            analysis_skip_maturity=False,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        _, kwargs = process_frame_mock.call_args
        assert kwargs.get("skip_maturity") is False


# ---------------------------------------------------------------------------
# Test 17: total_detection_rows counts correctly (Correction 2)
# ---------------------------------------------------------------------------


class TestTotalDetectionRows:
    """Verify total_detection_rows is accumulated correctly."""

    def test_one_snapshot_three_detections(self):
        """1 snapshot with 3 detections → total_detection_rows=3."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1, bbox=[100, 100, 200, 200]),
                _make_detection(track_id=2, bbox=[200, 200, 300, 300]),
                _make_detection(track_id=3, bbox=[300, 300, 400, 400]),
            ]),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.total_detection_rows == 3

    def test_two_snapshots_mixed_detections(self):
        """2 snapshots with 2 and 1 detections → total_detection_rows=3."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1, bbox=[100, 100, 200, 200]),
                _make_detection(track_id=2, bbox=[200, 200, 300, 300]),
            ]),
            _make_frame_result([
                _make_detection(track_id=3, bbox=[300, 300, 400, 400]),
            ]),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.total_detection_rows == 3

    def test_reused_detections_still_count(self):
        """Reused detections still count in detection_rows."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1, bbox=[100, 100, 200, 200]),
                _make_detection(track_id=2, bbox=[200, 200, 300, 300], reused=True),
            ]),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.total_detection_rows == 2


# ---------------------------------------------------------------------------
# Test 18: Failed snapshots exact count (Correction 3)
# ---------------------------------------------------------------------------


class TestFailedSnapshotsExactCount:
    """Verify failed_snapshots is not double-counted."""

    def test_failed_snapshots_exact_count(self):
        """1 missing image + 1 valid → failed=1, processed=1, total=2."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        def imread_side_effect(path):
            if "missing" in path:
                return None
            return np.zeros((480, 640, 3), dtype=np.uint8)

        with patch("cv2.imread", side_effect=imread_side_effect), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.failed_snapshots == 1
        assert result.processed_snapshots == 1
        assert result.failed_snapshots + result.processed_snapshots == result.total_snapshots

    def test_current_frame_index_updated_for_failed(self):
        """current_frame_index is updated even for failed snapshots."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=5,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=[]
        )

        with patch("cv2.imread", return_value=None), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert service.progress.current_frame_index == 5


# ---------------------------------------------------------------------------
# Test 19: ThermalMonitor integration (Correction 4)
# ---------------------------------------------------------------------------


class TestThermalMonitor:
    """Verify ThermalMonitor start/stop/pause integration."""

    def test_start_and_stop_called_once(self):
        """start() and stop() are called exactly once."""
        thermal = MagicMock()
        thermal.start = MagicMock()
        thermal.stop = MagicMock()
        thermal.pause_event = None

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            thermal_monitor=thermal,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        thermal.start.assert_called_once()
        thermal.stop.assert_called_once()

    def test_stop_called_even_on_fatal_error(self):
        """stop() is called even if components_factory raises."""
        thermal = MagicMock()
        thermal.start = MagicMock()
        thermal.stop = MagicMock()
        thermal.pause_event = None

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=[],
            thermal_monitor=thermal,
            components_factory_raises=True,
        )

        with patch("cv2.imread"), patch("cv2.imwrite", return_value=True):
            service.run()

        thermal.stop.assert_called_once()

    def test_pause_event_blocks_processing(self):
        """While pause_event is set, no snapshot is processed."""
        pause_event = threading.Event()
        pause_event.set()  # Start paused

        thermal = MagicMock()
        thermal.start = MagicMock()
        thermal.stop = MagicMock()
        thermal.pause_event = pause_event

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _, process_frame_mock = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            thermal_monitor=thermal,
        )

        # Clear pause after 0.3 seconds
        def clear_after_delay():
            time.sleep(0.3)
            pause_event.clear()

        t = threading.Thread(target=clear_after_delay, daemon=True)
        t.start()

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        t.join(timeout=2.0)
        # Should have eventually processed
        assert result.processed_snapshots == 1


# ---------------------------------------------------------------------------
# Test 20: error_reason and fatal error handling (Correction 5)
# ---------------------------------------------------------------------------


class TestErrorReasonAndFatalErrors:
    """Verify error_reason is set on fatal errors."""

    def test_components_factory_raises_sets_error_reason(self):
        """components_factory raises → error_reason set, progress.status='error'."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]

        service, _, _, db_session, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=[],
            components_factory_raises=True,
        )

        with patch("cv2.imread"), patch("cv2.imwrite", return_value=True):
            result = service.run()

        assert service.error_reason is not None
        assert "Fatal" in service.error_reason
        assert service.progress.status == "error"

    def test_persist_failure_sets_error_reason(self):
        """inspection_result_repo.create raises → error_reason set, rollback called."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        failing_repo = FakeInspectionResultRepo(raise_on_create=True)

        service, _, _, db_session, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            inspection_result_repo=failing_repo,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert service.error_reason is not None
        assert "Fatal" in service.error_reason
        assert service.progress.status == "error"
        assert db_session.rollback_count >= 1

    def test_error_reason_none_on_success(self):
        """Successful run has error_reason=None."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        assert service.error_reason is None


# ---------------------------------------------------------------------------
# Test 21: cv2.imwrite return value validation (Correction 6)
# ---------------------------------------------------------------------------


class TestImwriteValidation:
    """Verify cv2.imwrite return value is checked."""

    def test_imwrite_false_annotated_logs_error_continues(self):
        """imwrite returns False for annotated → error logged, analysis continues."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, snapshot_repo, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)

        def imwrite_side_effect(path, img):
            if "annotated" in path:
                return False
            return True

        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", side_effect=imwrite_side_effect), \
             patch("os.makedirs"):
            result = service.run()

        # Analysis continues despite imwrite failure
        assert result.processed_snapshots == 1
        # has_detections still updated
        assert (1, True) in snapshot_repo._has_detections_calls
        # Error was logged
        assert any("Failed to save annotated" in e for e in result.errors)

    def test_imwrite_false_crop_logs_error_continues(self):
        """imwrite returns False for crop → error logged, continues."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)

        def imwrite_side_effect(path, img):
            if "crop" in path or "track_" in path:
                return False
            return True

        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", side_effect=imwrite_side_effect), \
             patch("os.makedirs"):
            result = service.run()

        assert result.processed_snapshots == 1
        assert any("Failed to save crop" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 22: Path traversal rejection (Correction 7)
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Verify path traversal is rejected."""

    def test_path_traversal_rejected(self):
        """Snapshot with path containing '..' → failed_snapshots incremented, cv2.imread NOT called."""
        snapshots = [
            _make_snapshot(
                id=1, monitoring_id=1, frame_index=0,
                image_path="outputs/monitorings/1/../../secret.jpg",
            ),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [_make_frame_result()]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img) as mock_imread, \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert result.failed_snapshots == 1
        assert result.processed_snapshots == 1
        # cv2.imread should be called only for the valid snapshot
        # (the traversal one raises before imread)
        imread_paths = [c[0][0] for c in mock_imread.call_args_list]
        assert not any("secret" in p for p in imread_paths)

    def test_path_outside_monitoring_rejected(self):
        """Snapshot with path outside monitoring dir → failed."""
        snapshots = [
            _make_snapshot(
                id=1, monitoring_id=1, frame_index=0,
                image_path="outputs/monitorings/999/other.jpg",
            ),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=[]
        )

        with patch("cv2.imread") as mock_imread, \
             patch("cv2.imwrite", return_value=True):
            result = service.run()

        assert result.failed_snapshots == 1
        mock_imread.assert_not_called()


# ---------------------------------------------------------------------------
# Test 23: LogService integration (Correction 8)
# ---------------------------------------------------------------------------


class TestLogServiceIntegration:
    """Verify LogService integration."""

    def test_log_service_that_raises_does_not_crash_analysis(self):
        """LogService that raises → analysis still completes."""
        log_service = MagicMock()
        log_service.add_entry = MagicMock(side_effect=RuntimeError("Log broken"))

        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [_make_frame_result()]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=results,
            log_service=log_service,
        )

        def imread_side_effect(path):
            if "missing" in path:
                return None
            return np.zeros((480, 640, 3), dtype=np.uint8)

        with patch("cv2.imread", side_effect=imread_side_effect), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Analysis completed despite LogService failure
        assert result.processed_snapshots == 1
        assert service.progress.status == "completed"

    def test_log_service_called_on_error(self):
        """LogService.add_entry is called when a snapshot fails."""
        log_service = MagicMock()

        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
        ]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=[],
            log_service=log_service,
        )

        with patch("cv2.imread", return_value=None), \
             patch("cv2.imwrite", return_value=True):
            service.run()

        log_service.add_entry.assert_called_once()
        call_kwargs = log_service.add_entry.call_args[1]
        assert call_kwargs["monitoring_id"] == 1
        assert call_kwargs["source"] == "snapshot_analysis_service"


# ---------------------------------------------------------------------------
# Test 24: DB fatal errors stop analysis (Correction 4)
# ---------------------------------------------------------------------------


class TestDbFatalErrors:
    """Verify DB failures (update_has_detections, commit) are fatal."""

    def test_update_has_detections_failure_is_fatal(self):
        """If update_has_detections raises, analysis stops with error."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1)]),
            _make_frame_result([_make_detection(track_id=2)]),
        ]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        # Make update_has_detections raise
        snapshot_repo.update_has_detections = MagicMock(
            side_effect=RuntimeError("DB locked")
        )
        inspection_result_repo = FakeInspectionResultRepo()
        db_session = FakeDbSession()

        components_mock = MagicMock()
        components_factory = MagicMock(return_value=components_mock)
        results_iter = iter(results)
        process_frame_mock = MagicMock(
            side_effect=lambda img, comp, name, **kw: next(results_iter)
        )
        annotation_renderer = MagicMock(
            return_value=np.zeros((480, 640, 3), dtype=np.uint8)
        )

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=inspection_result_repo,
            db_session=db_session,
            components_factory=components_factory,
            process_frame_fn=process_frame_mock,
            annotation_renderer=annotation_renderer,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert service.error_reason is not None
        assert "Fatal" in service.error_reason
        assert service.progress.status == "error"
        assert db_session.rollback_count >= 1
        # Second snapshot was NOT processed
        assert result.processed_snapshots == 0

    def test_commit_failure_is_fatal(self):
        """If db_session.commit() raises during snapshot, analysis stops."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1)]),
            _make_frame_result([_make_detection(track_id=2)]),
        ]

        service, snapshot_repo, _, db_session, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        # Make commit raise on first call
        db_session.commit = MagicMock(side_effect=RuntimeError("Disk full"))

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        assert service.error_reason is not None
        assert service.progress.status == "error"


# ---------------------------------------------------------------------------
# Test 25: snapshot_repo.get_by_monitoring fails (Correction 3)
# ---------------------------------------------------------------------------


class TestRepoGetFails:
    """Verify get_by_monitoring failure is fatal."""

    def test_get_by_monitoring_raises_is_fatal(self):
        """If snapshot_repo.get_by_monitoring raises, analysis returns error."""
        snapshot_repo = FakeSnapshotRepo([])
        snapshot_repo.get_by_monitoring = MagicMock(
            side_effect=RuntimeError("Connection lost")
        )

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=FakeDbSession(),
            components_factory=MagicMock(),
            process_frame_fn=MagicMock(),
            annotation_renderer=MagicMock(),
        )

        result = service.run()

        assert service.error_reason is not None
        assert service.progress.status == "error"

    def test_thermal_start_raises_is_fatal(self):
        """If thermal_monitor.start() raises, analysis returns error."""
        thermal = MagicMock()
        thermal.start = MagicMock(side_effect=RuntimeError("Hardware error"))
        thermal.stop = MagicMock()

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]

        service, _, _, _, _, _ = _build_service(
            snapshots=snapshots,
            process_frame_results=[_make_frame_result()],
            thermal_monitor=thermal,
        )

        result = service.run()

        assert service.error_reason is not None
        assert service.progress.status == "error"
        # stop() still called in finally
        thermal.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Test 26: Annotation renderer failure is recoverable (Correction 3)
# ---------------------------------------------------------------------------


class TestAnnotationRendererFailure:
    """Verify annotation renderer failure doesn't stop analysis."""

    def test_renderer_exception_is_recoverable(self):
        """If annotation_renderer raises, analysis continues."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        inspection_result_repo = FakeInspectionResultRepo()
        db_session = FakeDbSession()
        components_factory = MagicMock(return_value=MagicMock())
        results_iter = iter(results)
        process_frame_mock = MagicMock(
            side_effect=lambda img, comp, name, **kw: next(results_iter)
        )
        # Renderer raises
        failing_renderer = MagicMock(side_effect=RuntimeError("Renderer crash"))

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=inspection_result_repo,
            db_session=db_session,
            components_factory=components_factory,
            process_frame_fn=process_frame_mock,
            annotation_renderer=failing_renderer,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Should still complete
        assert result.processed_snapshots == 1
        assert result.failed_snapshots == 0
        assert result.unique_tomatoes == 1
        assert service.progress.status == "completed"
        # Error was recorded
        assert any("Annotation failed" in e for e in result.errors)
        # has_detections still updated
        assert (1, True) in snapshot_repo._has_detections_calls


# ---------------------------------------------------------------------------
# Test 27: Crop generation failure is recoverable (Correction 3)
# ---------------------------------------------------------------------------


class TestCropGenerationFailure:
    """Verify crop generation failure doesn't stop analysis."""

    def test_generate_crops_exception_is_recoverable(self):
        """If _generate_crops raises, analysis continues."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, snapshot_repo, _, _, _, _ = _build_service(
            snapshots=snapshots, process_frame_results=results
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)

        # Make the cropper import fail inside _generate_crops
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch.object(
                 service, "_generate_crops",
                 side_effect=RuntimeError("Crop failed")
             ):
            result = service.run()

        # Should still complete
        assert result.processed_snapshots == 1
        assert result.failed_snapshots == 0
        assert result.unique_tomatoes == 1
        assert service.progress.status == "completed"
        assert any("Crop generation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 28: LogService failure during error recording doesn't crash
# ---------------------------------------------------------------------------


class TestLogServiceFailureDuringErrorRecording:
    """Verify LogService failure while recording error doesn't crash."""

    def test_log_failure_during_annotation_error_doesnt_crash(self):
        """If LogService raises while recording annotation error, analysis continues."""
        log_service = MagicMock()
        log_service.add_entry = MagicMock(side_effect=RuntimeError("Log broken"))

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        inspection_result_repo = FakeInspectionResultRepo()
        db_session = FakeDbSession()
        components_factory = MagicMock(return_value=MagicMock())
        results_iter = iter(results)
        process_frame_mock = MagicMock(
            side_effect=lambda img, comp, name, **kw: next(results_iter)
        )
        # Renderer raises → triggers _record_recoverable_error → LogService raises
        failing_renderer = MagicMock(side_effect=RuntimeError("Renderer crash"))

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=inspection_result_repo,
            db_session=db_session,
            components_factory=components_factory,
            process_frame_fn=process_frame_mock,
            annotation_renderer=failing_renderer,
            log_service=log_service,
        )

        dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
        with patch("cv2.imread", return_value=dummy_img), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            result = service.run()

        # Analysis still completed despite LogService failure
        assert service.progress.status == "completed"
        assert result.processed_snapshots == 1
