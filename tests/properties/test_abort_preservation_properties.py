"""Property-based tests for abort preservation behavior.

Tests that aborting a monitoring session after N completed snapshot cycles
preserves all N snapshots and their associated inspection results.

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: lightweight-hybrid-monitoring-pipeline, Property 12: Abort preserves data
"""

from __future__ import annotations

import sys
import threading
from typing import Any, Optional, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# --- Module-level mocking before any project imports ---


def _install_mocks():
    """Install mock modules for heavy deps (torch, detectron2, cv2) at module level."""
    mock_cv2 = MagicMock()
    mock_cv2.resize = lambda img, size, *a, **kw: np.zeros(
        (size[1], size[0], 3), dtype=np.uint8
    )
    mock_cv2.imwrite = MagicMock(return_value=True)
    mock_cv2.INTER_AREA = 3

    mocks = {
        "detectron2": MagicMock(),
        "detectron2.config": MagicMock(),
        "detectron2.engine": MagicMock(),
        "detectron2.model_zoo": MagicMock(),
        "detectron2.data": MagicMock(),
        "detectron2.utils": MagicMock(),
        "torch": MagicMock(),
        "torch.nn": MagicMock(),
        "torch.nn.functional": MagicMock(),
        "torchvision": MagicMock(),
        "torchvision.transforms": MagicMock(),
        "torchvision.models": MagicMock(),
        "cv2": mock_cv2,
    }

    # Mock vision submodules that the worker imports transitively
    mock_capture_gate = MagicMock()
    mock_capture_gate.should_capture_new_image = MagicMock(
        return_value=(True, {"orb_matches": 0, "hist_diff": 1.0})
    )

    mocks["src.infrastructure.vision.detectron_detector"] = MagicMock()
    mocks["src.infrastructure.vision.resnet_health_classifier"] = MagicMock()
    mocks["src.infrastructure.vision.maturity_estimator"] = MagicMock()
    mocks["src.infrastructure.vision.capture_gate"] = mock_capture_gate
    mocks["src.infrastructure.vision.snapshot_inference_runner"] = MagicMock()
    mocks["src.infrastructure.vision.cropper"] = MagicMock()

    # Install mocks into sys.modules
    for name, mock in mocks.items():
        sys.modules.setdefault(name, mock)

    return mocks


_MOCKS = _install_mocks()

# Clear worker module if already loaded, to force fresh import with mocks
for _mod_key in list(sys.modules.keys()):
    if "monitoring_worker" in _mod_key:
        del sys.modules[_mod_key]

# Now import project modules with heavy deps mocked
from src.domain.entities.snapshot import Snapshot  # noqa: E402
from src.application.services.monitoring_worker import MonitoringWorker  # noqa: E402


# --- Fake/Mock components ---


class FakeFrameSource:
    """A frame source that returns frames indefinitely until released."""

    def __init__(self) -> None:
        self._released = False
        self._frame = np.zeros((64, 64, 3), dtype=np.uint8)

    def read(self) -> Tuple[bool, Optional[Any]]:
        if self._released:
            return False, None
        return True, self._frame.copy()

    def release(self) -> None:
        self._released = True

    def is_available(self) -> bool:
        return not self._released


class FakeInferenceRunner:
    """A mock inference runner that returns a configurable number of detections."""

    def __init__(self, detections_per_snapshot: int = 2) -> None:
        self._detections_per_snapshot = detections_per_snapshot

    def run_inference_timed(self, image: Any) -> Tuple[list[dict], Any]:
        """Return fake detections with valid structure."""
        detections = []
        for i in range(self._detections_per_snapshot):
            detections.append(
                {
                    "bbox_x1": 10 + i * 20,
                    "bbox_y1": 10 + i * 20,
                    "bbox_x2": 30 + i * 20,
                    "bbox_y2": 30 + i * 20,
                    "detection_score": 0.95,
                    "health_label": "healthy",
                    "health_confidence": 0.92,
                    "maturity_stage": "red",
                    "maturity_percent": 85.0,
                }
            )
        timings = MagicMock()
        timings.detection_ms = 50.0
        timings.health_ms = 20.0
        timings.maturity_ms = 15.0
        timings.total_ms = 85.0
        return detections, timings


def _build_mock_snapshot_repo(persisted_snapshots: list):
    """Build a mock SnapshotRepository that tracks created snapshots."""
    mock_repo = MagicMock()
    snapshot_id_counter = [0]

    def fake_create(monitoring_id: int, snapshot: Snapshot) -> Snapshot:
        snapshot_id_counter[0] += 1
        snapshot.id = snapshot_id_counter[0]
        persisted_snapshots.append(snapshot)
        return snapshot

    mock_repo.create.side_effect = fake_create
    return mock_repo


def _build_mock_inspection_result_repo(persisted_results: list):
    """Build a mock InspectionResultRepository that tracks created results."""
    mock_repo = MagicMock()

    def fake_create(snapshot_id: int, result: Any) -> Any:
        result.id = len(persisted_results) + 1
        persisted_results.append((snapshot_id, result))
        return result

    mock_repo.create.side_effect = fake_create
    return mock_repo


def _build_mock_monitoring_repo():
    """Build a mock MonitoringRepository."""
    mock_repo = MagicMock()
    mock_monitoring = MagicMock()
    mock_repo.update_counters.return_value = mock_monitoring
    return mock_repo


def _build_mock_session():
    """Build a mock SQLAlchemy session."""
    return MagicMock()


# --- Property 12: Abort preserves completed cycle data ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 12: Abort preserves data


class TestProperty12AbortPreservesData:
    """Property 12: Abort preserves completed cycle data.

    For any abort event occurring after N completed snapshot cycles, all N
    snapshots and their associated inspection results SHALL remain persisted
    in the database.

    **Validates: Requirements 7.5**
    """

    @given(
        n_completed_cycles=st.integers(min_value=1, max_value=10),
        detections_per_snapshot=st.integers(min_value=0, max_value=5),
    )
    @settings(
        max_examples=100,
        deadline=30000,  # 30s deadline for threaded tests
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_abort_preserves_all_completed_snapshots(
        self, n_completed_cycles: int, detections_per_snapshot: int
    ):
        """After N completed snapshot cycles, abort preserves exactly N snapshots.

        The worker commits after each snapshot cycle. When abort fires after
        N cycles complete, the repository must contain all N snapshots.
        """
        persisted_snapshots: list[Snapshot] = []
        persisted_results: list[tuple] = []

        frame_source = FakeFrameSource()
        inference_runner = FakeInferenceRunner(
            detections_per_snapshot=detections_per_snapshot
        )
        snapshot_repo = _build_mock_snapshot_repo(persisted_snapshots)
        inspection_result_repo = _build_mock_inspection_result_repo(persisted_results)
        monitoring_repo = _build_mock_monitoring_repo()
        db_session = _build_mock_session()

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker = MonitoringWorker(
                    monitoring_id=1,
                    frame_source=frame_source,
                    inference_runner=inference_runner,
                    snapshot_repo=snapshot_repo,
                    inspection_result_repo=inspection_result_repo,
                    monitoring_repo=monitoring_repo,
                    db_session=db_session,
                    # Make the gate always trigger via timeout (fires immediately)
                    scene_gate_cooldown_frames=0,
                    scene_gate_timeout_frames=1,
                    min_seconds_between_snapshots=0.0,
                    max_seconds_without_snapshot=0.0,
                    capture_loop_fps=1000.0,  # No throttle
                )

                # Track completed cycles and trigger abort after N
                commit_count = [0]

                def commit_side_effect():
                    commit_count[0] += 1
                    if commit_count[0] >= n_completed_cycles:
                        worker.abort_event.set()

                db_session.commit.side_effect = commit_side_effect

                # Run worker in a thread (it exits on abort_event)
                worker_thread = threading.Thread(target=worker.run, daemon=True)
                worker_thread.start()
                worker_thread.join(timeout=15.0)

                # Ensure the thread actually completed
                assert not worker_thread.is_alive(), (
                    "Worker thread did not exit within timeout"
                )

                # --- Property assertion ---
                # All N completed cycles should have their snapshots persisted
                assert len(persisted_snapshots) == n_completed_cycles, (
                    f"Expected {n_completed_cycles} persisted snapshots, "
                    f"got {len(persisted_snapshots)}"
                )

                # Each snapshot should have the expected number of inspection results
                expected_total_results = n_completed_cycles * detections_per_snapshot
                assert len(persisted_results) == expected_total_results, (
                    f"Expected {expected_total_results} inspection results, "
                    f"got {len(persisted_results)}"
                )

                # Verify each snapshot has an assigned ID (was properly persisted)
                for i, snap in enumerate(persisted_snapshots):
                    assert snap.id is not None, (
                        f"Snapshot {i} was not assigned an ID (not persisted)"
                    )

                # Verify inspection results reference valid snapshot IDs
                snapshot_ids = {s.id for s in persisted_snapshots}
                for snap_id, result in persisted_results:
                    assert snap_id in snapshot_ids, (
                        f"Inspection result references snapshot_id={snap_id} "
                        f"which is not among persisted snapshots"
                    )

    @given(
        n_completed_cycles=st.integers(min_value=1, max_value=10),
    )
    @settings(
        max_examples=100,
        deadline=30000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_abort_preserves_commit_count_matches_cycles(
        self, n_completed_cycles: int
    ):
        """Session commit is called once per completed cycle, ensuring persistence.

        The worker calls self._session.commit() after each snapshot cycle.
        After N completed cycles and abort, commit should have been called
        exactly N times.
        """
        persisted_snapshots: list[Snapshot] = []
        persisted_results: list[tuple] = []

        frame_source = FakeFrameSource()
        inference_runner = FakeInferenceRunner(detections_per_snapshot=1)
        snapshot_repo = _build_mock_snapshot_repo(persisted_snapshots)
        inspection_result_repo = _build_mock_inspection_result_repo(persisted_results)
        monitoring_repo = _build_mock_monitoring_repo()
        db_session = _build_mock_session()

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker = MonitoringWorker(
                    monitoring_id=1,
                    frame_source=frame_source,
                    inference_runner=inference_runner,
                    snapshot_repo=snapshot_repo,
                    inspection_result_repo=inspection_result_repo,
                    monitoring_repo=monitoring_repo,
                    db_session=db_session,
                    scene_gate_cooldown_frames=0,
                    scene_gate_timeout_frames=1,
                    min_seconds_between_snapshots=0.0,
                    max_seconds_without_snapshot=0.0,
                    capture_loop_fps=1000.0,
                )

                commit_count = [0]

                def commit_side_effect():
                    commit_count[0] += 1
                    if commit_count[0] >= n_completed_cycles:
                        worker.abort_event.set()

                db_session.commit.side_effect = commit_side_effect

                worker_thread = threading.Thread(target=worker.run, daemon=True)
                worker_thread.start()
                worker_thread.join(timeout=15.0)

                assert not worker_thread.is_alive(), (
                    "Worker thread did not exit within timeout"
                )

                # --- Property assertion ---
                # commit was called exactly N times (once per completed cycle)
                assert commit_count[0] == n_completed_cycles, (
                    f"Expected {n_completed_cycles} commits, got {commit_count[0]}"
                )
