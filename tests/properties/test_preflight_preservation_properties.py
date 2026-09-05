"""Property-based test: preflight/confirmation rejection preserves the monitoring.

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: 020-deferred-manual-analysis-workflow, Property 5
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

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


def _profile():
    return SimpleNamespace(
        name="edge",
        analysis_thermal_pause_threshold=78.0,
        analysis_thermal_resume_threshold=72.0,
        video_first_enabled=True,
    )


# Each strategy value selects ONE failing condition for the start attempt.
_FAIL_MODES = st.sampled_from(
    [
        "no_confirmation",     # power_source_confirmed=False
        "video_missing_path",  # video_path is None
        "video_not_found",     # os.path.exists -> False
        "video_empty",         # size 0
        "video_unreadable",    # reader -> False
        "unsafe_path",         # path traversal
        "temperature_high",    # temp above threshold
    ]
)


@settings(max_examples=100, deadline=None)
@given(mode=_FAIL_MODES)
def test_property_5_preflight_rejection_preserves_monitoring(mode):
    """Property 5: for any rejection cause (missing confirmation or any failing
    preflight check), start_deferred_analysis leaves the monitoring in
    ready_for_analysis with video_path/data intact and NO analysis claim retained.

    # Feature: 020-deferred-manual-analysis-workflow, Property 5
    """
    registry = MonitoringRuntimeRegistry()
    repo = MagicMock()
    service = MonitoringService(
        monitoring_repo=repo,
        snapshot_repo=MagicMock(),
        inspection_result_repo=MagicMock(),
        metrics_repo=MagicMock(),
        module_repo=MagicMock(),
        runtime_registry=registry,
    )

    video_path = None if mode == "video_missing_path" else (
        "../../etc/passwd" if mode == "unsafe_path"
        else "outputs/monitorings/1/video/monitoring.mp4"
    )
    monitoring = SimpleNamespace(
        id=1,
        module_id=10,
        status=MonitoringState.READY_FOR_ANALYSIS.value,
        video_path=video_path,
    )
    repo.get_by_id.return_value = monitoring
    repo.get_by_module.return_value = [monitoring]

    power_confirmed = mode != "no_confirmation"
    readable = mode != "video_unreadable"
    exists = mode != "video_not_found"
    size = 0 if mode == "video_empty" else 100
    temp = 90.0 if mode == "temperature_high" else 50.0

    patches = [
        patch("src.infrastructure.config.settings.ACTIVE_PROFILE", _profile()),
        patch(
            "src.application.services.analysis_preflight._default_video_readable",
            return_value=readable,
        ),
        patch(
            "src.application.services.analysis_preflight._read_cpu_temperature",
            return_value=temp,
        ),
        patch("os.path.exists", return_value=exists),
        patch("os.path.getsize", return_value=size),
    ]
    # Only stub path-sanitizer to succeed when we are NOT testing unsafe paths.
    if mode != "unsafe_path":
        patches.append(
            patch(
                "src.infrastructure.security.path_sanitizer.validate_safe_path",
                return_value="/abs/monitoring.mp4",
            )
        )

    import contextlib

    raised = None
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # A live thread must never be launched on a rejection.
        stack.enter_context(
            patch("threading.Thread", side_effect=AssertionError(
                "no analysis thread must be launched on rejection"
            ))
        )
        try:
            service.start_deferred_analysis(1, power_confirmed, MagicMock())
        except (
            PowerSourceNotConfirmedError,
            NotReadyForAnalysisError,
            AnalysisPreflightFailedError,
        ) as e:
            raised = e

    # A rejection must have been raised.
    assert raised is not None
    # State never transitioned (no update_status call at all).
    repo.update_status.assert_not_called()
    # video_path/data intact (we never mutate the monitoring entity).
    assert monitoring.status == MonitoringState.READY_FOR_ANALYSIS.value
    assert monitoring.video_path == video_path
    # No analysis claim / device-global slot retained.
    assert registry.is_analysis_claimed(1) is False
    assert registry.is_global_analysis_active() is False
