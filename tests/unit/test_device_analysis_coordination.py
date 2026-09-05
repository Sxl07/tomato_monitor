"""Spec 020, Task 3B — device-global capture/analysis coordination.

Verifies (via MonitoringService + the REAL MonitoringRuntimeRegistry, with worker
threads and heavy launches stubbed) that:
    - an active heavy analysis blocks starting a NEW capture (start_session),
      for BOTH video-first and capture-first legacy;
    - an active capture blocks starting a deferred analysis (start_deferred_analysis);
    - a ready_for_analysis session WITHOUT a runtime thread does NOT block a
      capture of another module (non-blocking);
    - with two ready_for_analysis monitorings, only ONE can analyze at a time
      (device-global heavy-analysis slot);
    - when the analysis finishes, the next operation is allowed again;
    - has_active_capture() is GLOBAL (module-independent) and is the authoritative
      source (not has_live_worker_for_module, which is per-module).

No cv2, no camera, no detectron2. Worker/analysis launches are stubbed.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.application.services.monitoring_runtime_registry import (
    MonitoringRuntimeRegistry,
)
from src.application.services.monitoring_service import (
    MonitoringService,
    DeviceBusyError,
    AnalysisPreflightFailedError,
)
from src.domain.value_objects.monitoring_status import MonitoringState


def _profile(video_first_enabled=True, **overrides):
    params = dict(
        name="edge",
        video_first_enabled=video_first_enabled,
        recording_target_fps=5.0,
        video_codec_candidates=("mp4v",),
        capture_loop_fps=5.0,
        min_seconds_between_snapshots=1.0,
        max_seconds_without_snapshot=3.0,
        gate_resolution=(240, 240),
        scene_gate_orb_threshold=35,
        scene_gate_hsv_threshold=0.38,
        thermal_poll_interval_seconds=5.0,
        thermal_warning_temp=72.0,
        thermal_critical_temp=78.0,
        thermal_resume_temp=65.0,
        analysis_thermal_pause_threshold=78.0,
        analysis_thermal_resume_threshold=72.0,
    )
    params.update(overrides)
    return SimpleNamespace(**params)


def _build_service():
    registry = MonitoringRuntimeRegistry()
    monitoring_repo = MagicMock()
    service = MonitoringService(
        monitoring_repo=monitoring_repo,
        snapshot_repo=MagicMock(),
        inspection_result_repo=MagicMock(),
        metrics_repo=MagicMock(),
        module_repo=MagicMock(),
        runtime_registry=registry,
    )
    return service, monitoring_repo, registry


def _monitoring(mid, module_id, status):
    return SimpleNamespace(
        id=mid,
        module_id=module_id,
        status=status,
        video_path="outputs/monitorings/%d/video/monitoring.mp4" % mid,
    )


class TestAnalysisActiveBlocksNewCapture:
    """A heavy analysis owning the device-global slot blocks start_session."""

    def _try_start(self, service, monitoring_repo, video_first):
        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []
        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE",
            _profile(video_first_enabled=video_first),
        ), patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())

    def test_video_first_capture_rejected_when_analysis_active(self):
        service, monitoring_repo, registry = _build_service()
        registry.claim_global_analysis(999)  # another monitoring is analyzing
        with pytest.raises(DeviceBusyError):
            self._try_start(service, monitoring_repo, video_first=True)
        monitoring_repo.create.assert_not_called()

    def test_capture_first_legacy_capture_rejected_when_analysis_active(self):
        service, monitoring_repo, registry = _build_service()
        registry.claim_global_analysis(999)
        with pytest.raises(DeviceBusyError):
            self._try_start(service, monitoring_repo, video_first=False)
        monitoring_repo.create.assert_not_called()

    def test_capture_allowed_after_analysis_releases_slot(self):
        service, monitoring_repo, registry = _build_service()
        registry.claim_global_analysis(999)
        registry.release_global_analysis(999)  # analysis finished

        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []
        created = MagicMock(); created.id = 5
        monitoring_repo.create.return_value = created

        started = {}
        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
        ), patch.object(
            service, "_check_disk_space_or_raise"
        ), patch.object(
            service, "_start_video_first",
            side_effect=lambda *a, **k: started.setdefault("ok", True),
        ), patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())

        assert started.get("ok") is True


class TestCaptureActiveBlocksDeferredAnalysis:
    """An active capture (global) blocks start_deferred_analysis via preflight."""

    def test_deferred_analysis_rejected_when_capture_active(self):
        service, monitoring_repo, registry = _build_service()

        # A live capture thread on ANOTHER module (id=2, module 20).
        stop = threading.Event()
        cap_thread = threading.Thread(target=lambda: stop.wait(5))
        cap_thread.start()
        try:
            registry.register(2, MagicMock(), cap_thread)
            registry.mark_capture_active(2)
            assert registry.has_active_capture() is True

            # Monitoring id=1 (module 10) is ready_for_analysis and wants to start.
            m = _monitoring(1, 10, MonitoringState.READY_FOR_ANALYSIS.value)
            monitoring_repo.get_by_id.return_value = m
            monitoring_repo.get_by_module.return_value = [m]

            with patch(
                "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
            ), patch(
                "src.application.services.analysis_preflight._default_video_readable",
                return_value=True,
            ), patch("os.path.exists", return_value=True), patch(
                "os.path.getsize", return_value=100
            ), patch(
                "src.infrastructure.security.path_sanitizer.validate_safe_path",
                return_value="/abs/monitoring.mp4",
            ):
                with pytest.raises(AnalysisPreflightFailedError) as exc:
                    service.start_deferred_analysis(1, True, MagicMock())

            assert exc.value.reason_code == "device_capture_active"
            # State untouched, no analysis claim retained.
            monitoring_repo.update_status.assert_not_called()
            assert registry.is_analysis_claimed(1) is False
            assert registry.is_global_analysis_active() is False
        finally:
            stop.set()
            cap_thread.join()


class TestReadyForAnalysisIsNonBlocking:
    """A ready_for_analysis session without a runtime thread does NOT block a
    capture of another module."""

    def test_pending_ready_for_analysis_does_not_block_other_module_capture(self):
        service, monitoring_repo, registry = _build_service()

        # Module 10 has a monitoring parked in ready_for_analysis, NO runtime.
        # We are starting a capture for a DIFFERENT module (id=1).
        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []  # module 1 has no session
        created = MagicMock(); created.id = 7
        monitoring_repo.create.return_value = created

        # No capture marked, no analysis slot taken -> device is idle.
        assert registry.has_active_capture() is False
        assert registry.is_global_analysis_active() is False

        started = {}
        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
        ), patch.object(
            service, "_check_disk_space_or_raise"
        ), patch.object(
            service, "_start_video_first",
            side_effect=lambda *a, **k: started.setdefault("ok", True),
        ), patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())

        assert started.get("ok") is True


class TestOnlyOneAnalysisAtATime:
    """With two ready_for_analysis monitorings, only ONE may analyze (device-global)."""

    def _start(self, service, monitoring_repo, registry, mid, module_id):
        m = _monitoring(mid, module_id, MonitoringState.READY_FOR_ANALYSIS.value)
        monitoring_repo.get_by_id.return_value = m
        monitoring_repo.get_by_module.return_value = [m]
        launched = {}

        def fake_thread(*args, **kwargs):
            launched["target"] = kwargs.get("target")
            t = MagicMock()
            t.start = MagicMock()  # do NOT actually run analysis
            t.is_alive.return_value = True
            return t

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
        ), patch(
            "src.application.services.analysis_preflight._default_video_readable",
            return_value=True,
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=100
        ), patch(
            "src.infrastructure.security.path_sanitizer.validate_safe_path",
            return_value="/abs/monitoring.mp4",
        ), patch("threading.Thread", side_effect=fake_thread):
            return service.start_deferred_analysis(mid, True, MagicMock())

    def test_second_analysis_rejected_while_first_active(self):
        service, monitoring_repo, registry = _build_service()

        # First analysis starts and takes the device-global slot.
        self._start(service, monitoring_repo, registry, mid=1, module_id=10)
        assert registry.is_global_analysis_active() is True
        assert registry.is_analysis_claimed(1) is True

        # A second ready_for_analysis monitoring (different module) tries to start.
        with pytest.raises(AnalysisPreflightFailedError) as exc:
            self._start(service, monitoring_repo, registry, mid=2, module_id=20)
        assert exc.value.reason_code == "device_analysis_active"

    def test_next_analysis_allowed_after_first_releases(self):
        service, monitoring_repo, registry = _build_service()
        self._start(service, monitoring_repo, registry, mid=1, module_id=10)
        assert registry.is_global_analysis_active() is True

        # First analysis thread ends -> registry.remove() frees the slot.
        registry.remove(1)
        assert registry.is_global_analysis_active() is False

        # Now a second analysis may start.
        result = self._start(service, monitoring_repo, registry, mid=2, module_id=20)
        assert registry.is_analysis_claimed(2) is True
        assert result is not None


class TestNoAnalysisWinDuringStartWindow:
    """Spec 020 final: during the window between monitoring creation and worker
    start inside start_session, a concurrent claim_global_analysis MUST fail —
    the provisional capture reservation is held continuously (no release→reserve
    gap)."""

    def test_analysis_cannot_win_during_start_session_window(self):
        service, monitoring_repo, registry = _build_service()  # REAL registry
        service._module_repo.get_by_id.return_value = MagicMock(id=1)
        monitoring_repo.get_by_module.return_value = []
        created = MagicMock(); created.id = 42
        monitoring_repo.create.return_value = created

        import threading

        in_window = threading.Event()
        release_window = threading.Event()
        analysis_result = {}

        def slow_start_video_first(monitoring, frame_source, db_session, log_service):
            # We are now INSIDE start_session, AFTER create() and BEFORE the
            # worker "starts". Simulate the worker registering + marking active
            # capture (what the real _start_video_first does) then hold here so a
            # competing analysis-start races against us.
            registry.register(monitoring.id, MagicMock(), MagicMock())
            registry.mark_capture_active(monitoring.id)
            in_window.set()
            release_window.wait(2.0)

        def competing_analysis():
            in_window.wait(2.0)
            # Try to grab the device-global heavy-analysis slot mid-window.
            analysis_result["won"] = registry.claim_global_analysis(999)
            release_window.set()

        with patch(
            "src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()
        ), patch.object(
            service, "_check_disk_space_or_raise"
        ), patch.object(
            service, "_start_video_first", side_effect=slow_start_video_first
        ), patch.dict(
            "sys.modules",
            {
                "src.infrastructure.camera.raspberry_camera_frame_source": MagicMock(
                    is_camera_locked=MagicMock(return_value=False)
                ),
            },
        ):
            t = threading.Thread(target=competing_analysis)
            t.start()
            service.start_session(1, 5.0, 2.0, None, MagicMock(), MagicMock())
            t.join(3.0)

        # The competing analysis must NOT have acquired the slot during the window.
        assert analysis_result.get("won") is False
        # After start completed, the provisional module reservation is released,
        # but the worker's active-capture mark still covers the device.
        assert registry.has_active_capture() is True
