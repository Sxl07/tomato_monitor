"""Unit tests for CaptureWorker — Spec 009, Phase B.

Validates that CaptureWorker:
- Does NOT import or call any inference functions
- Captures the first frame always
- Saves snapshots to correct paths with correct naming
- Creates Snapshot records with has_detections=False
- Increments total_snapshots correctly
- Keeps total_detections at 0 during capture
- Respects min_seconds_between_snapshots cooldown
- Forces capture on max_seconds_without_snapshot timeout
- Stops cleanly on finalize_event
- Stops cleanly on abort_event
- Always calls frame_source.release() on exit
"""

from __future__ import annotations

import ast
import inspect
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.application.services.capture_worker import CaptureWorker, CaptureMetrics


# ---------------------------------------------------------------------------
# Test helpers / fakes
# ---------------------------------------------------------------------------


class FakeFrameSource:
    """Fake frame source that returns synthetic frames."""

    def __init__(self, num_frames: int = 100, frame_shape: tuple = (480, 640, 3)):
        self._num_frames = num_frames
        self._frame_shape = frame_shape
        self._current = 0
        self._released = False
        self._read_count = 0

    def read(self) -> Tuple[bool, Optional[Any]]:
        if self._current >= self._num_frames:
            return (False, None)
        self._current += 1
        self._read_count += 1
        # Return a frame with slight variation per read (different pixel values)
        frame = np.full(self._frame_shape, self._current % 256, dtype=np.uint8)
        return (True, frame)

    def release(self) -> None:
        self._released = True

    def is_available(self) -> bool:
        return self._current < self._num_frames

    @property
    def released(self) -> bool:
        return self._released


class FakeSnapshotRepo:
    """Fake snapshot repository that records create calls."""

    def __init__(self):
        self.snapshots: list[dict] = []

    def create(self, monitoring_id: int, snapshot) -> Any:
        record = {
            "monitoring_id": monitoring_id,
            "image_path": snapshot.image_path,
            "frame_index": snapshot.frame_index,
            "has_detections": snapshot.has_detections,
        }
        self.snapshots.append(record)
        # Return a fake with id assigned
        snapshot.id = len(self.snapshots)
        return snapshot

    def get_by_monitoring(self, monitoring_id: int):
        return [s for s in self.snapshots if s["monitoring_id"] == monitoring_id]


class FakeMonitoringRepo:
    """Fake monitoring repository that records update_counters calls."""

    def __init__(self):
        self.counter_updates: list[dict] = []

    def update_counters(self, id: int, total_snapshots: int, total_detections: int):
        self.counter_updates.append({
            "id": id,
            "total_snapshots": total_snapshots,
            "total_detections": total_detections,
        })
        return MagicMock()


class FakeDbSession:
    """Fake DB session that tracks commits."""

    def __init__(self):
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1


def _build_worker(
    num_frames: int = 50,
    capture_loop_fps: float = 100.0,  # Fast for tests
    min_seconds_between_snapshots: float = 0.0,  # No cooldown for basic tests
    max_seconds_without_snapshot: float = 999.0,  # No timeout for basic tests
    monitoring_id: int = 42,
) -> tuple[CaptureWorker, FakeFrameSource, FakeSnapshotRepo, FakeMonitoringRepo, FakeDbSession]:
    """Build a CaptureWorker with fakes for testing."""
    frame_source = FakeFrameSource(num_frames=num_frames)
    snapshot_repo = FakeSnapshotRepo()
    monitoring_repo = FakeMonitoringRepo()
    db_session = FakeDbSession()

    worker = CaptureWorker(
        monitoring_id=monitoring_id,
        frame_source=frame_source,
        snapshot_repo=snapshot_repo,
        monitoring_repo=monitoring_repo,
        db_session=db_session,
        capture_loop_fps=capture_loop_fps,
        min_seconds_between_snapshots=min_seconds_between_snapshots,
        max_seconds_without_snapshot=max_seconds_without_snapshot,
        gate_resolution=(64, 64),  # Small for fast tests
        scene_gate_orb_threshold=35,
        scene_gate_hsv_threshold=0.38,
    )
    return worker, frame_source, snapshot_repo, monitoring_repo, db_session


# ---------------------------------------------------------------------------
# Test: No inference imports or calls
# ---------------------------------------------------------------------------


class TestNoInference:
    """Verify that CaptureWorker does not import or call inference functions."""

    def test_no_inference_imports_in_module(self):
        """CaptureWorker module must not import any inference modules."""
        source_path = Path("src/application/services/capture_worker.py")
        source_code = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source_code)

        forbidden_modules = [
            "detectron2",
            "detectron_detector",
            "resnet_health_classifier",
            "maturity_estimator",
            "maturity_colorimetry",
            "snapshot_inference_runner",
        ]

        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_names.append(node.module)

        for forbidden in forbidden_modules:
            for imported in imported_names:
                assert forbidden not in imported, (
                    f"CaptureWorker imports forbidden module: {imported} "
                    f"(contains '{forbidden}')"
                )

    def test_no_inference_function_calls_in_source(self):
        """CaptureWorker source must not contain calls to inference functions."""
        source_path = Path("src/application/services/capture_worker.py")
        source_code = source_path.read_text(encoding="utf-8")

        forbidden_calls = [
            "run_inference",
            "run_inference_timed",
            "run_detection",
            "predict_health",
            "estimate_maturity_for_crop",
        ]

        for func_name in forbidden_calls:
            assert func_name not in source_code, (
                f"CaptureWorker source contains forbidden call: '{func_name}'"
            )

    def test_worker_run_does_not_call_inference(self):
        """Running the worker with mocked gate does not invoke inference."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=5,
            min_seconds_between_snapshots=0.0,
        )

        # Patch Scene Gate to always trigger (so we capture every frame)
        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ):
            # Stop after a few frames
            def stop_after_frames():
                time.sleep(0.1)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_after_frames)
            t.start()
            worker.run()
            t.join()

        # Verify no inference was called (they don't exist in the module at all)
        # If we got here without ImportError, no inference was attempted
        assert worker.error_reason is None or "inference" not in (worker.error_reason or "")


# ---------------------------------------------------------------------------
# Test: First frame always captured
# ---------------------------------------------------------------------------


class TestFirstFrameCapture:
    """Verify first frame is always captured regardless of gate settings."""

    def test_first_frame_captured(self):
        """First frame must be captured even with strict cooldown."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=3,
            min_seconds_between_snapshots=999.0,  # Very high cooldown
            max_seconds_without_snapshot=999.0,  # Very high timeout
        )

        # Stop after first capture
        def stop_soon():
            time.sleep(0.05)
            worker.finalize_event.set()

        t = threading.Thread(target=stop_soon)
        t.start()
        worker.run()
        t.join()

        assert worker.snapshot_count >= 1
        assert len(snapshot_repo.snapshots) >= 1
        assert snapshot_repo.snapshots[0]["frame_index"] == 0


# ---------------------------------------------------------------------------
# Test: Snapshot paths and naming
# ---------------------------------------------------------------------------


class TestSnapshotPaths:
    """Verify snapshots are saved with correct path and naming convention."""

    def test_snapshot_path_format(self):
        """Snapshots must be saved to snapshots/raw/snapshot_NNNNNN.jpg."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=5,
            min_seconds_between_snapshots=0.0,
            monitoring_id=99,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        # Check paths
        assert len(snapshot_repo.snapshots) >= 1
        first = snapshot_repo.snapshots[0]
        assert first["image_path"] == "outputs/monitorings/99/snapshots/raw/snapshot_000000.jpg"

        if len(snapshot_repo.snapshots) >= 2:
            second = snapshot_repo.snapshots[1]
            assert second["image_path"] == "outputs/monitorings/99/snapshots/raw/snapshot_000001.jpg"

    def test_frame_index_sequential(self):
        """Frame indices must be sequential starting from 0."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.08)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        indices = [s["frame_index"] for s in snapshot_repo.snapshots]
        assert indices == list(range(len(indices)))


# ---------------------------------------------------------------------------
# Test: Snapshot has_detections=False
# ---------------------------------------------------------------------------


class TestHasDetections:
    """Verify all snapshots during capture have has_detections=False."""

    def test_all_snapshots_have_detections_false(self):
        """Every snapshot persisted during capture must have has_detections=False."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.06)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        assert len(snapshot_repo.snapshots) >= 1
        for snap in snapshot_repo.snapshots:
            assert snap["has_detections"] is False


# ---------------------------------------------------------------------------
# Test: Counter updates
# ---------------------------------------------------------------------------


class TestCounterUpdates:
    """Verify total_snapshots increments and total_detections stays 0."""

    def test_total_snapshots_increments(self):
        """total_snapshots must increment with each captured snapshot."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.06)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        assert worker.snapshot_count == len(snapshot_repo.snapshots)
        # Verify counter updates are sequential
        for i, update in enumerate(monitoring_repo.counter_updates):
            assert update["total_snapshots"] == i + 1

    def test_total_detections_always_zero(self):
        """total_detections must remain 0 during capture (no inference)."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.06)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        for update in monitoring_repo.counter_updates:
            assert update["total_detections"] == 0

    def test_db_session_commit_per_snapshot(self):
        """DB session must be committed after each snapshot persistence."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.06)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        assert db_session.commit_count == len(snapshot_repo.snapshots)


# ---------------------------------------------------------------------------
# Test: Cooldown (min_seconds_between_snapshots)
# ---------------------------------------------------------------------------


class TestCooldown:
    """Verify no capture within min_seconds_between_snapshots."""

    def test_no_capture_within_cooldown(self):
        """Second frame must NOT be captured if cooldown hasn't elapsed."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=50,
            capture_loop_fps=200.0,  # Very fast loop
            min_seconds_between_snapshots=5.0,  # 5 second cooldown
            max_seconds_without_snapshot=999.0,  # No timeout
        )

        # Gate always says "capture" — but cooldown should block it
        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_soon():
                time.sleep(0.15)  # Run for 150ms — well under 5s cooldown
                worker.finalize_event.set()

            t = threading.Thread(target=stop_soon)
            t.start()
            worker.run()
            t.join()

        # Only first_frame should be captured (cooldown blocks all others)
        assert worker.snapshot_count == 1


# ---------------------------------------------------------------------------
# Test: Timeout (max_seconds_without_snapshot)
# ---------------------------------------------------------------------------


class TestTimeout:
    """Verify forced capture after max_seconds_without_snapshot."""

    def test_forced_capture_on_timeout(self):
        """Snapshot must be forced when timeout exceeds, even if gate says no."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=200,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.01,  # Very short cooldown
            max_seconds_without_snapshot=0.05,  # 50ms timeout
        )

        # Gate always says NO change — but timeout should force capture
        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(False, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_later():
                time.sleep(0.2)  # Run for 200ms with 50ms timeout → expect ~4 forced captures
                worker.finalize_event.set()

            t = threading.Thread(target=stop_later)
            t.start()
            worker.run()
            t.join()

        # First frame + at least 1 timeout-forced capture
        assert worker.snapshot_count >= 2


# ---------------------------------------------------------------------------
# Test: finalize_event stops loop
# ---------------------------------------------------------------------------


class TestFinalizeEvent:
    """Verify finalize_event stops the capture loop cleanly."""

    def test_finalize_stops_loop(self):
        """Setting finalize_event should stop the worker."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=1000,
            capture_loop_fps=50.0,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_after_short_time():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=stop_after_short_time)
            t.start()
            worker.run()
            t.join()

        # Worker should have stopped (not consumed all 1000 frames)
        assert frame_source._current < 1000
        assert frame_source.released is True


# ---------------------------------------------------------------------------
# Test: abort_event stops loop
# ---------------------------------------------------------------------------


class TestAbortEvent:
    """Verify abort_event stops the capture loop cleanly."""

    def test_abort_stops_loop(self):
        """Setting abort_event should stop the worker."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=1000,
            capture_loop_fps=50.0,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def abort_soon():
                time.sleep(0.05)
                worker.abort_event.set()

            t = threading.Thread(target=abort_soon)
            t.start()
            worker.run()
            t.join()

        assert frame_source._current < 1000
        assert frame_source.released is True


# ---------------------------------------------------------------------------
# Test: frame_source.release() always called
# ---------------------------------------------------------------------------


class TestFrameSourceRelease:
    """Verify frame_source.release() is always called on exit."""

    def test_release_on_finalize(self):
        """frame_source.release() called when finalize_event set."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=100)

        with patch("cv2.imwrite", return_value=True):
            def stop():
                time.sleep(0.02)
                worker.finalize_event.set()

            t = threading.Thread(target=stop)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True

    def test_release_on_abort(self):
        """frame_source.release() called when abort_event set."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=100)

        with patch("cv2.imwrite", return_value=True):
            def stop():
                time.sleep(0.02)
                worker.abort_event.set()

            t = threading.Thread(target=stop)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True

    def test_release_on_frame_source_exhausted(self):
        """frame_source.release() called when source runs out of frames."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=2)

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        assert frame_source.released is True

    def test_release_on_error(self):
        """frame_source.release() called even if an exception occurs."""
        worker, frame_source, snapshot_repo, _, db_session = _build_worker(num_frames=10)

        # Make imwrite raise to simulate filesystem error
        with patch("cv2.imwrite", side_effect=OSError("disk full")):
            worker.run()

        assert frame_source.released is True


# ---------------------------------------------------------------------------
# Test: CaptureMetrics
# ---------------------------------------------------------------------------


class TestCaptureMetrics:
    """Verify capture_metrics property returns correct data."""

    def test_metrics_reflect_snapshot_count(self):
        """capture_metrics.total_snapshots matches snapshot_count."""
        worker, frame_source, snapshot_repo, _, _ = _build_worker(
            num_frames=10,
            min_seconds_between_snapshots=0.0,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=stop)
            t.start()
            worker.run()
            t.join()

        metrics = worker.capture_metrics
        assert metrics.total_snapshots == worker.snapshot_count
        assert metrics.exit_reason == "finalize"
        assert isinstance(metrics, CaptureMetrics)


# ---------------------------------------------------------------------------
# Test: capture_metrics safe before run()
# ---------------------------------------------------------------------------


class TestMetricsBeforeRun:
    """Verify capture_metrics can be accessed before run() without exception."""

    def test_capture_metrics_before_run_does_not_raise(self):
        """capture_metrics property must work before run() is called."""
        worker, _, _, _, _ = _build_worker(num_frames=5)

        # This should NOT raise AttributeError
        metrics = worker.capture_metrics
        assert isinstance(metrics, CaptureMetrics)
        assert metrics.total_snapshots == 0
        assert metrics.exit_reason == ""
        assert metrics.capture_reasons == {
            "first_frame": 0,
            "scene_change": 0,
            "timeout": 0,
        }


# ---------------------------------------------------------------------------
# Test: cv2.imwrite returning False → no persistence, error exit
# ---------------------------------------------------------------------------


class TestImwriteFalse:
    """Verify that cv2.imwrite returning False prevents persistence."""

    def test_imwrite_false_no_snapshot_persisted(self):
        """If cv2.imwrite returns False, Snapshot must NOT be persisted."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=5,
            min_seconds_between_snapshots=0.0,
        )

        with patch("cv2.imwrite", return_value=False):
            worker.run()

        # No snapshot should be persisted (first frame triggers capture, imwrite fails)
        assert len(snapshot_repo.snapshots) == 0
        assert worker.snapshot_count == 0

    def test_imwrite_false_sets_error_reason(self):
        """If cv2.imwrite returns False, error_reason must be set."""
        worker, _, _, _, _ = _build_worker(num_frames=5, min_seconds_between_snapshots=0.0)

        with patch("cv2.imwrite", return_value=False):
            worker.run()

        assert worker.error_reason is not None
        assert "snapshot" in worker.error_reason.lower() or "escribir" in worker.error_reason.lower()

    def test_imwrite_false_exit_reason_is_error(self):
        """If cv2.imwrite returns False, capture_metrics.exit_reason must be 'error'."""
        worker, _, _, _, _ = _build_worker(num_frames=5, min_seconds_between_snapshots=0.0)

        with patch("cv2.imwrite", return_value=False):
            worker.run()

        assert worker.capture_metrics.exit_reason == "error"


# ---------------------------------------------------------------------------
# Test: Persistence failure → exit_reason="error"
# ---------------------------------------------------------------------------


class TestPersistenceFailure:
    """Verify that repo failures set exit_reason='error' (not 'abort')."""

    def test_snapshot_repo_failure_exit_reason_error(self):
        """If snapshot_repo.create() raises, exit_reason must be 'error'."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=5,
            min_seconds_between_snapshots=0.0,
        )

        # Make snapshot_repo.create() raise
        snapshot_repo.create = MagicMock(side_effect=RuntimeError("DB locked"))

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        assert worker.capture_metrics.exit_reason == "error"
        assert worker.error_reason is not None

    def test_monitoring_repo_failure_exit_reason_error(self):
        """If monitoring_repo.update_counters() raises, exit_reason must be 'error'."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=5,
            min_seconds_between_snapshots=0.0,
        )

        # Make update_counters raise
        monitoring_repo.update_counters = MagicMock(
            side_effect=RuntimeError("Constraint violated")
        )

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        assert worker.capture_metrics.exit_reason == "error"
        assert worker.error_reason is not None


# ---------------------------------------------------------------------------
# Test: ThermalMonitor start/stop integration
# ---------------------------------------------------------------------------


class TestThermalMonitorIntegration:
    """Verify ThermalMonitor.start() and stop() are called safely."""

    def test_thermal_monitor_start_and_stop_called(self):
        """If thermal_monitor has start() and stop(), both are called exactly once."""
        frame_source = FakeFrameSource(num_frames=10)
        snapshot_repo = FakeSnapshotRepo()
        monitoring_repo = FakeMonitoringRepo()
        db_session = FakeDbSession()

        thermal_monitor = MagicMock()
        thermal_monitor.start = MagicMock()
        thermal_monitor.stop = MagicMock()
        thermal_monitor.peak_temperature = 55.0
        thermal_monitor.pause_count = 0

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=frame_source,
            snapshot_repo=snapshot_repo,
            monitoring_repo=monitoring_repo,
            db_session=db_session,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
            max_seconds_without_snapshot=999.0,
            gate_resolution=(64, 64),
            thermal_monitor=thermal_monitor,
        )

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop():
                time.sleep(0.03)
                worker.finalize_event.set()

            t = threading.Thread(target=stop)
            t.start()
            worker.run()
            t.join()

        thermal_monitor.start.assert_called_once()
        thermal_monitor.stop.assert_called_once()

    def test_thermal_monitor_none_does_not_crash(self):
        """Worker with thermal_monitor=None runs fine."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=3)

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        # No crash — frame source exhausted naturally
        assert frame_source.released is True

    def test_thermal_monitor_without_start_stop_does_not_crash(self):
        """Worker with a thermal_monitor lacking start/stop still runs."""
        frame_source = FakeFrameSource(num_frames=3)
        snapshot_repo = FakeSnapshotRepo()
        monitoring_repo = FakeMonitoringRepo()
        db_session = FakeDbSession()

        # Object without start/stop methods
        thermal_monitor = object()

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=frame_source,
            snapshot_repo=snapshot_repo,
            monitoring_repo=monitoring_repo,
            db_session=db_session,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
            max_seconds_without_snapshot=999.0,
            gate_resolution=(64, 64),
            thermal_monitor=thermal_monitor,
        )

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        assert frame_source.released is True


# ---------------------------------------------------------------------------
# Test: Cooperative pause (pause_event)
# ---------------------------------------------------------------------------


class TestPauseEvent:
    """Verify pause_event blocks capture and resumes correctly."""

    def test_pause_blocks_capture(self):
        """While pause_event is set, no new snapshots are captured."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=100,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
        )

        # Set pause BEFORE run starts — worker should not capture during pause
        worker.pause_event.set()

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_after_pause():
                # Let it sit paused for a bit, then abort
                time.sleep(0.15)
                worker.abort_event.set()

            t = threading.Thread(target=stop_after_pause)
            t.start()
            worker.run()
            t.join()

        # No snapshots should have been captured (was paused the whole time)
        assert worker.snapshot_count == 0

    def test_pause_then_resume_captures(self):
        """After pause_event is cleared, worker resumes capturing."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=100,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
        )

        # Start paused
        worker.pause_event.set()

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def unpause_then_stop():
                time.sleep(0.05)  # Wait while paused
                worker.pause_event.clear()  # Resume
                time.sleep(0.08)  # Let it capture some
                worker.finalize_event.set()  # Stop

            t = threading.Thread(target=unpause_then_stop)
            t.start()
            worker.run()
            t.join()

        # Should have captured at least 1 snapshot after resume
        assert worker.snapshot_count >= 1

    def test_abort_during_pause_exits(self):
        """abort_event during pause exits the loop and releases frame_source."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=100,
        )

        worker.pause_event.set()

        with patch("cv2.imwrite", return_value=True):
            def abort_while_paused():
                time.sleep(0.05)
                worker.abort_event.set()

            t = threading.Thread(target=abort_while_paused)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True
        assert worker.snapshot_count == 0

    def test_finalize_during_pause_exits(self):
        """finalize_event during pause exits the loop and releases frame_source."""
        worker, frame_source, snapshot_repo, monitoring_repo, db_session = _build_worker(
            num_frames=100,
        )

        worker.pause_event.set()

        with patch("cv2.imwrite", return_value=True):
            def finalize_while_paused():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=finalize_while_paused)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True
        assert worker.snapshot_count == 0


# ---------------------------------------------------------------------------
# Test: complete_event alias for backward compatibility
# ---------------------------------------------------------------------------


class TestCompleteEventAlias:
    """Verify complete_event is an alias for finalize_event."""

    def test_complete_event_is_finalize_event(self):
        """complete_event must be the same object as finalize_event."""
        worker, _, _, _, _ = _build_worker(num_frames=5)

        assert worker.complete_event is worker.finalize_event

    def test_setting_complete_event_sets_finalize(self):
        """Setting complete_event.set() also activates finalize_event."""
        worker, _, _, _, _ = _build_worker(num_frames=5)

        assert not worker.finalize_event.is_set()
        worker.complete_event.set()
        assert worker.finalize_event.is_set()

    def test_complete_event_stops_worker(self):
        """worker.complete_event.set() stops the capture loop."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=1000)

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_via_complete():
                time.sleep(0.03)
                worker.complete_event.set()

            t = threading.Thread(target=stop_via_complete)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True
        assert frame_source._current < 1000


# ---------------------------------------------------------------------------
# Test: Thermal pause event (separate from manual pause)
# ---------------------------------------------------------------------------


class TestThermalPauseEvent:
    """Verify thermal_pause_event blocks capture independently of pause_event."""

    def test_thermal_pause_blocks_capture(self):
        """While thermal_pause_event is set, no new snapshots are captured."""
        worker, frame_source, snapshot_repo, _, _ = _build_worker(
            num_frames=100,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
        )

        worker.thermal_pause_event.set()

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def stop_after_pause():
                time.sleep(0.15)
                worker.abort_event.set()

            t = threading.Thread(target=stop_after_pause)
            t.start()
            worker.run()
            t.join()

        assert worker.snapshot_count == 0

    def test_thermal_pause_clear_resumes_capture(self):
        """After thermal_pause_event is cleared, worker resumes capturing."""
        worker, frame_source, snapshot_repo, _, _ = _build_worker(
            num_frames=100,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
        )

        worker.thermal_pause_event.set()

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def unpause_then_stop():
                time.sleep(0.05)
                worker.thermal_pause_event.clear()
                time.sleep(0.08)
                worker.finalize_event.set()

            t = threading.Thread(target=unpause_then_stop)
            t.start()
            worker.run()
            t.join()

        assert worker.snapshot_count >= 1

    def test_abort_during_thermal_pause_exits(self):
        """abort_event during thermal pause exits and releases frame_source."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=100)
        worker.thermal_pause_event.set()

        with patch("cv2.imwrite", return_value=True):
            def abort():
                time.sleep(0.05)
                worker.abort_event.set()

            t = threading.Thread(target=abort)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True
        assert worker.snapshot_count == 0

    def test_finalize_during_thermal_pause_exits(self):
        """finalize_event during thermal pause exits and releases frame_source."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=100)
        worker.thermal_pause_event.set()

        with patch("cv2.imwrite", return_value=True):
            def finalize():
                time.sleep(0.05)
                worker.finalize_event.set()

            t = threading.Thread(target=finalize)
            t.start()
            worker.run()
            t.join()

        assert frame_source.released is True
        assert worker.snapshot_count == 0

    def test_both_pauses_active_requires_both_cleared(self):
        """Both pause_event and thermal_pause_event active → must clear both to resume."""
        worker, frame_source, snapshot_repo, _, _ = _build_worker(
            num_frames=100,
            capture_loop_fps=100.0,
            min_seconds_between_snapshots=0.0,
        )

        worker.pause_event.set()
        worker.thermal_pause_event.set()

        with patch(
            "src.application.services.capture_worker.should_capture_new_image",
            return_value=(True, {}),
        ), patch("cv2.imwrite", return_value=True):
            def clear_sequence():
                time.sleep(0.05)
                # Clear only thermal — should still be paused (manual still active)
                worker.thermal_pause_event.clear()
                time.sleep(0.08)
                # Now clear manual too — should resume
                worker.pause_event.clear()
                time.sleep(0.08)
                worker.finalize_event.set()

            t = threading.Thread(target=clear_sequence)
            t.start()
            worker.run()
            t.join()

        # Should have captured only after BOTH were cleared
        assert worker.snapshot_count >= 1


# ---------------------------------------------------------------------------
# Test: release_resources() idempotency and thermal_monitor.start() failure
# ---------------------------------------------------------------------------


class TestReleaseResources:
    """Verify release_resources() is idempotent and handles failures."""

    def test_release_resources_idempotent(self):
        """Calling release_resources() twice only releases once."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=5)

        worker.release_resources()
        assert frame_source.released is True

        # Reset to check it doesn't call again
        frame_source._released = False
        worker.release_resources()
        assert frame_source._released is False  # NOT released again

    def test_release_resources_stops_thermal(self):
        """release_resources() stops thermal monitor."""
        frame_source = FakeFrameSource(num_frames=5)
        snapshot_repo = FakeSnapshotRepo()
        monitoring_repo = FakeMonitoringRepo()
        db_session = FakeDbSession()
        thermal = MagicMock()
        thermal.stop = MagicMock()

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=frame_source,
            snapshot_repo=snapshot_repo,
            monitoring_repo=monitoring_repo,
            db_session=db_session,
            thermal_monitor=thermal,
        )

        worker.release_resources()
        thermal.stop.assert_called_once()

        # Second call doesn't call stop again
        thermal.stop.reset_mock()
        worker.release_resources()
        thermal.stop.assert_not_called()

    def test_thermal_start_failure_releases_resources(self):
        """If thermal_monitor.start() raises, frame_source is still released."""
        frame_source = FakeFrameSource(num_frames=10)
        snapshot_repo = FakeSnapshotRepo()
        monitoring_repo = FakeMonitoringRepo()
        db_session = FakeDbSession()
        thermal = MagicMock()
        thermal.start = MagicMock(side_effect=RuntimeError("Hardware fault"))
        thermal.stop = MagicMock()

        worker = CaptureWorker(
            monitoring_id=1,
            frame_source=frame_source,
            snapshot_repo=snapshot_repo,
            monitoring_repo=monitoring_repo,
            db_session=db_session,
            thermal_monitor=thermal,
        )

        with patch("cv2.imwrite", return_value=True):
            worker.run()

        assert frame_source.released is True
        thermal.stop.assert_called_once()
        assert worker.error_reason is not None
        assert worker.capture_metrics.exit_reason == "error"

    def test_release_resources_callable_without_run(self):
        """release_resources() works even if run() was never called."""
        worker, frame_source, _, _, _ = _build_worker(num_frames=5)

        # Never called run() — should still work
        worker.release_resources()
        assert frame_source.released is True
