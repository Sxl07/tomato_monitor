"""Property-based tests for inference execution in monitoring worker.

Tests correctness properties of inference count matching snapshot count (Property 10)
and inference failure resilience (Property 11) using Hypothesis.

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

These tests mock heavy dependencies (detectron2, torch, cv2) to test the
worker's control flow logic on machines where these are not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Fixture: isolate tests from real outputs/ directory
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path.cwd()


@pytest.fixture(autouse=True)
def _isolate_from_outputs(tmp_path, monkeypatch):
    """Redirect CWD to tmp so MonitoringWorker.pipeline_metrics writes there."""
    monkeypatch.chdir(tmp_path)


# --- Module-level mocking before any project imports ---

def _install_mocks():
    """Install mock modules for heavy deps at module level."""
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

    # Mock vision submodules
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

# Now we can import the worker
from src.application.services.monitoring_worker import MonitoringWorker  # noqa: E402


# --- Helper classes ---


class FakeFrameSource:
    """A frame source that returns N frames then signals exhaustion via False."""

    def __init__(self, total_frames: int):
        self._total_frames = total_frames
        self._read_count = 0
        self._frame = np.zeros((64, 64, 3), dtype=np.uint8)

    def read(self) -> Tuple[bool, Optional[Any]]:
        if self._read_count >= self._total_frames:
            return False, None
        self._read_count += 1
        return True, self._frame.copy()

    def release(self) -> None:
        pass

    def is_available(self) -> bool:
        return self._read_count < self._total_frames


class CountingInferenceRunner:
    """An inference runner that counts calls and returns a detection."""

    def __init__(self):
        self.call_count = 0

    def run_inference_timed(self, frame: np.ndarray):
        """Returns (results, timings_mock)."""
        self.call_count += 1
        results = [
            {
                "bbox_x1": 10,
                "bbox_y1": 10,
                "bbox_x2": 50,
                "bbox_y2": 50,
                "detection_score": 0.95,
                "health_label": "healthy",
                "health_confidence": 0.92,
                "maturity_stage": "red",
                "maturity_percent": 85.0,
            }
        ]
        timings = MagicMock()
        timings.detection_ms = 10.0
        timings.health_ms = 5.0
        timings.maturity_ms = 3.0
        timings.total_ms = 18.0
        return results, timings

    def run_inference(self, frame: np.ndarray):
        """Backward compat."""
        results, _ = self.run_inference_timed(frame)
        return results


class FailingInferenceRunner:
    """An inference runner that raises on specified snapshot indices."""

    def __init__(self, fail_on_indices: set[int]):
        self._fail_on_indices = fail_on_indices
        self._call_count = 0
        self.total_calls = 0

    def run_inference_timed(self, frame: np.ndarray):
        """Raises on configured indices, else returns valid results."""
        current_index = self._call_count
        self._call_count += 1
        self.total_calls += 1
        if current_index in self._fail_on_indices:
            raise RuntimeError(f"Simulated inference failure at index {current_index}")
        results = [
            {
                "bbox_x1": 10,
                "bbox_y1": 10,
                "bbox_x2": 50,
                "bbox_y2": 50,
                "detection_score": 0.95,
                "health_label": "healthy",
                "health_confidence": 0.92,
                "maturity_stage": "red",
                "maturity_percent": 85.0,
            }
        ]
        timings = MagicMock()
        timings.detection_ms = 10.0
        timings.health_ms = 5.0
        timings.maturity_ms = 3.0
        timings.total_ms = 18.0
        return results, timings

    def run_inference(self, frame: np.ndarray):
        """Backward compat."""
        results, _ = self.run_inference_timed(frame)
        return results


def create_mock_snapshot_repo() -> MagicMock:
    """Create a mock snapshot repository that returns snapshots with IDs."""
    repo = MagicMock()
    counter = {"id": 0}

    def create_side_effect(monitoring_id, snapshot):
        counter["id"] += 1
        snapshot.id = counter["id"]
        return snapshot

    repo.create.side_effect = create_side_effect
    return repo


def build_worker(
    frame_source,
    inference_runner,
    snapshot_repo=None,
    monitoring_id: int = 1,
):
    """Build a MonitoringWorker with mocked dependencies.

    Scene gate parameters are set to force capture on every frame via timeout.
    The capture_gate.should_capture_new_image is already mocked to return True.
    """
    worker = MonitoringWorker(
        monitoring_id=monitoring_id,
        frame_source=frame_source,
        inference_runner=inference_runner,
        snapshot_repo=snapshot_repo or create_mock_snapshot_repo(),
        inspection_result_repo=MagicMock(),
        monitoring_repo=MagicMock(),
        db_session=MagicMock(),
        # Force every frame to trigger a snapshot:
        scene_gate_cooldown_frames=0,
        scene_gate_timeout_frames=1,
        scene_gate_orb_threshold=9999,
        scene_gate_hsv_threshold=0.0,
        min_seconds_between_snapshots=0.0,
        max_seconds_without_snapshot=0.0,
        capture_loop_fps=1000.0,  # No throttle
    )
    return worker


# --- Strategies ---

n_frames_strategy = st.integers(min_value=1, max_value=15)


@st.composite
def frames_with_failures(draw):
    """Generate (total_frames, fail_indices) where fail_indices is a subset."""
    n = draw(st.integers(min_value=2, max_value=12))
    fail_indices = draw(
        st.sets(st.integers(min_value=0, max_value=n - 1), min_size=1, max_size=max(1, n - 1))
    )
    return n, fail_indices


# --- Property 10: Inference count equals snapshot count ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 10: Inference count equals snapshot count


class TestProperty10InferenceCountEqualsSnapshotCount:
    """Property 10: Inference count equals snapshot count.

    For any monitoring session with N triggered snapshots (where inference did not
    fail), the total number of inference executions SHALL equal N.

    **Validates: Requirements 6.1, 6.2**
    """

    @given(n_frames=n_frames_strategy)
    @settings(max_examples=100, deadline=30000)
    def test_inference_called_once_per_snapshot(self, n_frames):
        """Inference runner is called exactly N times for N triggered snapshots."""
        inference_runner = CountingInferenceRunner()
        frame_source = FakeFrameSource(total_frames=n_frames)

        worker = build_worker(
            frame_source=frame_source,
            inference_runner=inference_runner,
        )

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker.run()

        # The worker captures exactly n_frames snapshots and calls inference each time
        assert worker.snapshot_count == n_frames
        assert inference_runner.call_count == n_frames

    @given(n_frames=n_frames_strategy)
    @settings(max_examples=100, deadline=30000)
    def test_inference_count_matches_snapshot_count_invariant(self, n_frames):
        """The invariant inference_calls == snapshot_count holds for any N."""
        inference_runner = CountingInferenceRunner()
        frame_source = FakeFrameSource(total_frames=n_frames)

        worker = build_worker(
            frame_source=frame_source,
            inference_runner=inference_runner,
        )

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker.run()

        # Core property: inference count == snapshot count
        assert inference_runner.call_count == worker.snapshot_count


# --- Property 11: Inference failure does not terminate session ---
# Feature: lightweight-hybrid-monitoring-pipeline, Property 11: Inference failure does not terminate session


class TestProperty11InferenceFailureDoesNotTerminateSession:
    """Property 11: Inference failure does not terminate session.

    For any snapshot cycle where InferenceRunner.run_inference() raises an exception,
    the monitoring worker SHALL continue to the next loop iteration and produce
    subsequent snapshots.

    **Validates: Requirements 6.5**
    """

    @given(data=frames_with_failures())
    @settings(max_examples=100, deadline=30000)
    def test_worker_continues_after_inference_failure(self, data):
        """Worker processes all N frames even when inference fails on some."""
        n_frames, fail_indices = data

        inference_runner = FailingInferenceRunner(fail_on_indices=fail_indices)
        frame_source = FakeFrameSource(total_frames=n_frames)

        worker = build_worker(
            frame_source=frame_source,
            inference_runner=inference_runner,
        )

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker.run()

        # Worker must have processed ALL frames as snapshots,
        # regardless of which ones had inference failures
        assert worker.snapshot_count == n_frames

        # Inference was attempted for every snapshot
        assert inference_runner.total_calls == n_frames

        # If there is an error_reason, it must be from frame source exhaustion
        # (not from inference failure — inference failure is non-fatal)
        if worker.error_reason is not None:
            assert "inference" not in worker.error_reason.lower()
            assert "Frame source" in worker.error_reason

    @given(data=frames_with_failures())
    @settings(max_examples=100, deadline=30000)
    def test_subsequent_snapshots_still_saved_after_failure(self, data):
        """Snapshots after inference failure are still persisted correctly."""
        n_frames, fail_indices = data

        inference_runner = FailingInferenceRunner(fail_on_indices=fail_indices)
        frame_source = FakeFrameSource(total_frames=n_frames)
        snapshot_repo = create_mock_snapshot_repo()

        worker = build_worker(
            frame_source=frame_source,
            inference_runner=inference_runner,
            snapshot_repo=snapshot_repo,
        )

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker.run()

        # Every snapshot was persisted to the repository
        assert snapshot_repo.create.call_count == n_frames

    @given(n_frames=st.integers(min_value=2, max_value=10))
    @settings(max_examples=100, deadline=30000)
    def test_all_failures_still_completes_session(self, n_frames):
        """Even if ALL inferences fail, the session completes all frames."""
        all_fail_indices = set(range(n_frames))
        inference_runner = FailingInferenceRunner(fail_on_indices=all_fail_indices)
        frame_source = FakeFrameSource(total_frames=n_frames)

        worker = build_worker(
            frame_source=frame_source,
            inference_runner=inference_runner,
        )

        with patch("src.application.services.monitoring_worker.cv2.imwrite", return_value=True):
            with patch("src.application.services.monitoring_worker.os.makedirs"):
                worker.run()

        # All frames processed as snapshots
        assert worker.snapshot_count == n_frames

        # If there is an error, it's from frame source exhaustion, not inference
        if worker.error_reason is not None:
            assert "inference" not in worker.error_reason.lower()
            assert "Frame source" in worker.error_reason
