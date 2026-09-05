"""Spec 020, Task 8.2 — durable pipeline_metrics.json incremental merge.

Verifies that traceability marks are written durably and NON-destructively into
the per-monitoring pipeline_metrics.json:
    1. capture/recording metrics (+ capture_completed_at) at finalize;
    2. deferred_analysis metadata (manual_deferred_analysis=true,
       deferred_analysis_started_at, preflight_temperature_c) ONLY on accepted
       manual start — never overwriting the capture section;
    3. analysis metrics later — never dropping earlier sections.
Also verifies a failed launch leaves NO false start metadata, and that
completed_at is never written by this file.

Real filesystem via tmp_path. No DB/camera/inference.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
    SnapshotAnalysisReportWriter,
)


def _read_metrics(base: Path, mid: int) -> dict:
    p = base / str(mid) / "pipeline_metrics.json"
    return json.loads(p.read_text(encoding="utf-8"))


class TestReportWriterMerge:
    def test_capture_then_deferred_then_analysis_are_non_destructive(self, tmp_path):
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        mid = 1

        # 1) capture/recording metrics + capture_completed_at
        writer.write_capture_metrics(
            mid,
            {"frames_written": 50, "capture_completed_at": "2026-01-01T00:00:00+00:00"},
            profile_name="edge",
        )
        data = _read_metrics(tmp_path, mid)
        assert data["capture"]["frames_written"] == 50
        assert data["capture"]["capture_completed_at"] == "2026-01-01T00:00:00+00:00"
        assert "deferred_analysis" not in data
        assert "analysis" not in data

        # 2) deferred_analysis metadata (accepted manual start)
        writer.write_deferred_analysis_metadata(
            mid,
            {
                "manual_deferred_analysis": True,
                "deferred_analysis_started_at": "2026-01-01T01:00:00+00:00",
                "preflight_temperature_c": 61.5,
            },
            profile_name="edge",
        )
        data = _read_metrics(tmp_path, mid)
        # capture section preserved intact
        assert data["capture"]["frames_written"] == 50
        assert data["capture"]["capture_completed_at"] == "2026-01-01T00:00:00+00:00"
        # deferred section added
        assert data["deferred_analysis"]["manual_deferred_analysis"] is True
        assert data["deferred_analysis"]["deferred_analysis_started_at"] == "2026-01-01T01:00:00+00:00"
        assert data["deferred_analysis"]["preflight_temperature_c"] == 61.5

        # 3) analysis metrics later — earlier sections preserved
        writer.write_reports(
            monitoring_id=mid,
            per_snapshot_rows=[],
            per_detection_rows=[],
            summary_row={"monitoring_id": mid, "status": "completed"},
            analysis_metrics={"unique_tomatoes": 3, "status": "completed"},
            profile_name="edge",
        )
        data = _read_metrics(tmp_path, mid)
        assert data["capture"]["frames_written"] == 50          # still there
        assert data["deferred_analysis"]["manual_deferred_analysis"] is True  # still there
        assert data["analysis"]["unique_tomatoes"] == 3         # newly added

    def test_capture_completed_at_present_without_start_marks(self, tmp_path):
        """At finalize (capture metrics only) the deferred marks must be absent."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        writer.write_capture_metrics(
            2, {"capture_completed_at": "2026-01-01T00:00:00+00:00"}, profile_name="edge"
        )
        data = _read_metrics(tmp_path, 2)
        assert "capture_completed_at" in data["capture"]
        assert "deferred_analysis" not in data

    def test_preflight_temperature_null_when_unavailable(self, tmp_path):
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        writer.write_deferred_analysis_metadata(
            3,
            {
                "manual_deferred_analysis": True,
                "deferred_analysis_started_at": "2026-01-01T01:00:00+00:00",
                "preflight_temperature_c": None,
            },
            profile_name="edge",
        )
        data = _read_metrics(tmp_path, 3)
        assert data["deferred_analysis"]["preflight_temperature_c"] is None

    def test_no_completed_at_written_by_this_file(self, tmp_path):
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        writer.write_capture_metrics(
            4, {"capture_completed_at": "2026-01-01T00:00:00+00:00"}, profile_name="edge"
        )
        writer.write_deferred_analysis_metadata(
            4,
            {"manual_deferred_analysis": True, "deferred_analysis_started_at": "x",
             "preflight_temperature_c": None},
            profile_name="edge",
        )
        data = _read_metrics(tmp_path, 4)
        # The standalone 'completed_at' key (full monitoring completion after
        # analysis) is NEVER written by this metrics file. capture_completed_at is
        # a distinct capture-phase mark and is allowed.
        assert "completed_at" not in data
        assert "completed_at" not in data.get("capture", {})
        assert "completed_at" not in data.get("deferred_analysis", {})
        assert "capture_completed_at" in data["capture"]


class TestServiceDoesNotWriteFalseStartMetadataOnRollback:
    """A launch failure must NOT persist deferred-analysis start metadata."""

    def test_failed_launch_does_not_write_deferred_metadata(self, tmp_path, monkeypatch):
        from src.application.services.monitoring_runtime_registry import (
            MonitoringRuntimeRegistry,
        )
        from src.application.services.monitoring_service import MonitoringService
        from src.domain.value_objects.monitoring_status import MonitoringState

        registry = MonitoringRuntimeRegistry()
        repo = MagicMock()
        m = SimpleNamespace(
            id=1, module_id=10,
            status=MonitoringState.READY_FOR_ANALYSIS.value,
            video_path="outputs/monitorings/1/video/monitoring.mp4",
        )
        repo.get_by_id.return_value = m
        repo.get_by_module.return_value = [m]
        service = MonitoringService(
            monitoring_repo=repo, snapshot_repo=MagicMock(),
            inspection_result_repo=MagicMock(), metrics_repo=MagicMock(),
            module_repo=MagicMock(), runtime_registry=registry,
        )

        profile = SimpleNamespace(
            name="edge", analysis_thermal_pause_threshold=78.0,
            analysis_thermal_resume_threshold=72.0, video_first_enabled=True,
        )

        # OUTPUTS_DIR points at tmp so any (unexpected) write would be observable.
        def boom_thread(*a, **k):
            t = MagicMock()
            t.start.side_effect = RuntimeError("cannot start")
            return t

        # Seed an existing pipeline_metrics.json with a capture section so we can
        # assert the file is NOT augmented with deferred_analysis on a failed launch.
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings")
        writer.write_capture_metrics(
            1, {"capture_completed_at": "2026-01-01T00:00:00+00:00"}, profile_name="edge"
        )

        with patch("src.infrastructure.config.settings.ACTIVE_PROFILE", profile), patch(
            "src.infrastructure.config.settings.OUTPUTS_DIR", tmp_path
        ), patch(
            "src.application.services.analysis_preflight._default_video_readable",
            return_value=True,
        ), patch(
            "src.application.services.analysis_preflight._read_cpu_temperature",
            return_value=50.0,
        ), patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=100
        ), patch(
            "src.infrastructure.security.path_sanitizer.validate_safe_path",
            return_value="/abs/monitoring.mp4",
        ), patch("threading.Thread", side_effect=boom_thread):
            service.start_deferred_analysis(1, True, MagicMock())

        # Rolled back to ready_for_analysis; claims released.
        repo.update_status.assert_any_call(
            1, MonitoringState.READY_FOR_ANALYSIS.value
        )
        assert registry.is_analysis_claimed(1) is False
        assert registry.is_global_analysis_active() is False

        # STRONG assertion: the durable metrics file must NOT contain a
        # deferred_analysis section (no false "analysis started" metadata).
        metrics_path = tmp_path / "monitorings" / "1" / "pipeline_metrics.json"
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        assert "deferred_analysis" not in data
        assert "manual_deferred_analysis" not in json.dumps(data)
        # The pre-existing capture section is untouched.
        assert data["capture"]["capture_completed_at"] == "2026-01-01T00:00:00+00:00"
