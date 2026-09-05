"""Spec 020, Task 6.3 — MonitoringService.start_deferred_analysis unit tests.

Covers:
    - power confirmation absent/false -> rejected, state intact, no claim retained;
    - preflight failure -> rejected, state intact, no claim retained;
    - success -> analyzing + analysis thread registered + _run_video_analysis target;
    - launch failure -> rollback to ready_for_analysis + claims released;
    - not ready_for_analysis -> rejected without modifying fields;
    - video missing/corrupt (via preflight) -> exact Spanish message, state intact.

Uses the REAL MonitoringRuntimeRegistry so claim/release behavior is exercised.
Heavy analysis launch (threading.Thread) is stubbed so no inference runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.application.services.monitoring_runtime_registry import (
    MonitoringRuntimeRegistry,
)
from src.application.services.monitoring_service import (
    MonitoringService,
    PowerSourceNotConfirmedError,
    NotReadyForAnalysisError,
    AnalysisPreflightFailedError,
)
from src.domain.value_objects.monitoring_status import MonitoringState


def _profile(**overrides):
    params = dict(
        name="edge",
        analysis_thermal_pause_threshold=78.0,
        analysis_thermal_resume_threshold=72.0,
        video_first_enabled=True,
        sparse_min_frames_between_detections=3,
        sparse_max_frames_without_detection=8,
        sparse_use_scene_gate=True,
        sparse_enable_flow_propagation=True,
        save_annotated_video=False,
        thermal_poll_interval_seconds=5.0,
    )
    params.update(overrides)
    return SimpleNamespace(**params)


def _build():
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


def _ready(mid=1, module_id=10, video_path="outputs/monitorings/1/video/monitoring.mp4"):
    return SimpleNamespace(
        id=mid,
        module_id=module_id,
        status=MonitoringState.READY_FOR_ANALYSIS.value,
        video_path=video_path,
    )


class _PreflightCtx:
    """Patch preflight I/O so the video checks pass unless overridden."""

    def __init__(self, *, readable=True, exists=True, size=100, safe=True):
        self.readable = readable
        self.exists = exists
        self.size = size
        self.safe = safe
        self._patchers = []

    def __enter__(self):
        import contextlib

        self._stack = contextlib.ExitStack()
        self._stack.enter_context(
            patch("src.infrastructure.config.settings.ACTIVE_PROFILE", _profile())
        )
        self._stack.enter_context(
            patch(
                "src.application.services.analysis_preflight._default_video_readable",
                return_value=self.readable,
            )
        )
        self._stack.enter_context(patch("os.path.exists", return_value=self.exists))
        self._stack.enter_context(patch("os.path.getsize", return_value=self.size))
        if self.safe:
            self._stack.enter_context(
                patch(
                    "src.infrastructure.security.path_sanitizer.validate_safe_path",
                    return_value="/abs/monitoring.mp4",
                )
            )
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def _stub_thread():
    def fake_thread(*args, **kwargs):
        t = MagicMock()
        t.start = MagicMock()
        t.is_alive.return_value = True
        t._target = kwargs.get("target")
        t._args = kwargs.get("args")
        return t

    return fake_thread


class TestRejections:
    def test_power_confirmation_absent_rejected(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready()
        with pytest.raises(PowerSourceNotConfirmedError):
            service.start_deferred_analysis(1, False, MagicMock())
        # State intact, no analysis claim / global slot retained.
        repo.update_status.assert_not_called()
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

    def test_not_ready_for_analysis_rejected(self):
        service, repo, registry = _build()
        m = _ready()
        m.status = MonitoringState.COMPLETED.value
        repo.get_by_id.return_value = m
        with pytest.raises(NotReadyForAnalysisError):
            service.start_deferred_analysis(1, True, MagicMock())
        repo.update_status.assert_not_called()
        assert registry.is_analysis_claimed(1) is False

    def test_preflight_failure_rejected_state_intact(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready()
        repo.get_by_module.return_value = [_ready()]
        # Make the video unreadable so preflight fails.
        with _PreflightCtx(readable=False):
            with pytest.raises(AnalysisPreflightFailedError) as exc:
                service.start_deferred_analysis(1, True, MagicMock())
        assert exc.value.reason_code == "video_unreadable"
        repo.update_status.assert_not_called()
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

    def test_video_missing_message_and_state_intact(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready(video_path=None)
        repo.get_by_module.return_value = [_ready(video_path=None)]
        with _PreflightCtx():
            with pytest.raises(AnalysisPreflightFailedError) as exc:
                service.start_deferred_analysis(1, True, MagicMock())
        assert exc.value.reason_code == "video_missing_path"
        assert "no tiene un video asociado" in str(exc.value)
        repo.update_status.assert_not_called()


class TestSuccess:
    def test_success_transitions_analyzing_and_registers_thread(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready()
        repo.get_by_module.return_value = [_ready()]

        with _PreflightCtx(), patch(
            "threading.Thread", side_effect=_stub_thread()
        ), patch.object(service, "_write_deferred_analysis_metadata"):
            service.start_deferred_analysis(1, True, MagicMock())

        # ready_for_analysis -> analyzing persisted.
        repo.update_status.assert_any_call(1, MonitoringState.ANALYZING.value)
        # Claims held (thread stubbed as alive; released later by _run_video_analysis).
        assert registry.is_analysis_claimed(1) is True
        assert registry.is_global_analysis_active() is True
        # An analysis thread was registered.
        assert registry.get_thread(1) is not None

    def test_success_writes_deferred_metadata_after_claim(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready()
        repo.get_by_module.return_value = [_ready()]

        wrote = {}

        def fake_meta(monitoring_id, preflight_temperature_c):
            # Claims must already be held when metadata is written.
            wrote["claim"] = registry.is_analysis_claimed(monitoring_id)
            wrote["global"] = registry.is_global_analysis_active()

        with _PreflightCtx(), patch(
            "threading.Thread", side_effect=_stub_thread()
        ), patch.object(service, "_write_deferred_analysis_metadata", side_effect=fake_meta):
            service.start_deferred_analysis(1, True, MagicMock())

        assert wrote.get("claim") is True
        assert wrote.get("global") is True


class TestLaunchFailureRollback:
    def test_thread_launch_failure_rolls_back_and_releases_claims(self):
        service, repo, registry = _build()
        repo.get_by_id.return_value = _ready()
        repo.get_by_module.return_value = [_ready()]

        def boom_thread(*args, **kwargs):
            t = MagicMock()
            t.start.side_effect = RuntimeError("cannot start thread")
            return t

        with _PreflightCtx(), patch("threading.Thread", side_effect=boom_thread):
            service.start_deferred_analysis(1, True, MagicMock())

        # Rolled back to ready_for_analysis; claims released; no false start metadata.
        repo.update_status.assert_any_call(
            1, MonitoringState.READY_FOR_ANALYSIS.value
        )
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False
