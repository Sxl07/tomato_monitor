"""Regression tests for VideoAnalysisProgress fields consumed by /status.

HOTFIX post-Spec019:
- Bug 3: GET /monitoring/{id}/status read progress.processed_snapshots /
  progress.total_snapshots, which VideoAnalysisProgress did not expose, so the
  UI showed 0/0. Progress now surfaces frame-based processed/total (total taken
  from video metadata).
- Bug 5: the same status endpoint read thermal_current_temperature_c / peak /
  count / duration off progress, which VideoAnalysisProgress also lacked, so no
  temperature was shown during ANALYZING. Progress now mirrors the thermal
  telemetry from the ThermalMonitor live during the run.

Reuses the pure-I/O fakes (no cv2, no torch, no real video, no disk).
"""

from __future__ import annotations

import threading

import pytest

from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
)
from src.application.interfaces.video_reader_port import VideoMetadata

from tests.unit.test_video_analysis_service_io import (
    FakeVideoReaderPort,
    _FakeSnapshotRepo,
    _FakeInspectionRepo,
    _FakeDbSession,
    _frames,
)


@pytest.fixture(autouse=True)
def _no_filesystem(monkeypatch):
    monkeypatch.setattr(
        VideoAnalysisService, "_save_image",
        lambda self, relative_path, image: True,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_crops",
        lambda self, frame, detections, frame_idx: None,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_reports",
        lambda self, result: None,
    )


def _config():
    return VideoAnalysisConfig(
        min_frames_between_detections=3,
        max_frames_without_detection=8,
        use_scene_gate=False,
        enable_flow_propagation=False,
    )


def _make_service(reader, thermal_monitor=None):
    return VideoAnalysisService(
        monitoring_id=1,
        video_path="outputs/monitorings/1/video/monitoring.mp4",
        snapshot_repo=_FakeSnapshotRepo(),
        inspection_result_repo=_FakeInspectionRepo(),
        monitoring_repo=object(),
        db_session=_FakeDbSession(),
        config=_config(),
        video_reader=reader,
        components_factory=lambda: object(),
        process_frame_fn=lambda frame, components, name: {"detections": []},
        thermal_monitor=thermal_monitor,
    )


class TestProgressFrameCounts:
    """Bug 3: processed/total frames surfaced instead of 0/0."""

    def test_total_snapshots_equals_total_frames_metadata(self):
        meta = VideoMetadata(fps=30.0, total_frames=42, width=64, height=48)
        reader = FakeVideoReaderPort(frames=_frames(3), metadata=meta)
        svc = _make_service(reader)

        svc.run()

        # total (denominator) comes from video metadata, not 0.
        assert svc.progress.total_snapshots == 42

    def test_processed_snapshots_tracks_frames_read(self):
        meta = VideoMetadata(fps=30.0, total_frames=5, width=64, height=48)
        reader = FakeVideoReaderPort(frames=_frames(5), metadata=meta)
        svc = _make_service(reader)

        svc.run()

        # numerator reflects the frames actually read/processed.
        assert svc.progress.processed_snapshots == 5
        assert svc.progress.total_frames_read == 5

    def test_progress_attributes_present_before_run(self):
        # The status endpoint uses getattr on these names; they must exist.
        reader = FakeVideoReaderPort(frames=_frames(1))
        svc = _make_service(reader)
        assert hasattr(svc.progress, "processed_snapshots")
        assert hasattr(svc.progress, "total_snapshots")


class _FakePauseEvent:
    """Pause event that reports set N times then clears (bounded loop safe)."""

    def __init__(self, set_times=0):
        self._remaining = set_times

    def is_set(self):
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False


class _FakeThermalMonitor:
    """Minimal thermal monitor exposing the ThermalMonitor read contract."""

    def __init__(self, *, current=None, peak=0.0, pause_count=0, pause_seconds=0.0,
                 set_times=0):
        self.current_temperature = current
        self.peak_temperature = peak
        self.pause_count = pause_count
        self.total_pause_duration_seconds = pause_seconds
        self.pause_event = _FakePauseEvent(set_times=set_times)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TestThermalTelemetryOnProgress:
    """Bug 5: thermal telemetry mirrored onto progress during the run."""

    def test_thermal_fields_populated_from_monitor(self):
        meta = VideoMetadata(fps=30.0, total_frames=2, width=64, height=48)
        reader = FakeVideoReaderPort(frames=_frames(2), metadata=meta)
        tm = _FakeThermalMonitor(
            current=68.5, peak=75.0, pause_count=1, pause_seconds=3.0
        )
        svc = _make_service(reader, thermal_monitor=tm)

        svc.run()

        assert svc.progress.thermal_current_temperature_c == 68.5
        assert svc.progress.thermal_peak_temperature_c == 75.0
        assert svc.progress.thermal_pause_count == 1
        assert svc.progress.thermal_pause_duration_seconds == 3.0

    def test_thermal_paused_flag_reflects_pause_event(self):
        meta = VideoMetadata(fps=30.0, total_frames=1, width=64, height=48)
        reader = FakeVideoReaderPort(frames=_frames(1), metadata=meta)
        # Pause event set once then clears; after the loop finishes the flag
        # must be back to False.
        tm = _FakeThermalMonitor(current=80.0, peak=80.0, set_times=1)
        svc = _make_service(reader, thermal_monitor=tm)

        svc.run()

        assert svc.progress.thermal_paused is False
        # Temperature still surfaced despite the transient pause.
        assert svc.progress.thermal_current_temperature_c == 80.0

    def test_broken_thermal_monitor_does_not_crash(self):
        meta = VideoMetadata(fps=30.0, total_frames=1, width=64, height=48)
        reader = FakeVideoReaderPort(frames=_frames(1), metadata=meta)

        class _Broken:
            # No pause_event attribute at all — getattr returns None and the
            # cooperative pause becomes a no-op (thermal telemetry stays default).
            def start(self):
                pass

            def stop(self):
                pass

        svc = _make_service(reader, thermal_monitor=_Broken())
        result = svc.run()

        # Analysis still completes; telemetry stays at safe defaults.
        assert result.status == "completed"
        assert svc.progress.thermal_current_temperature_c is None
