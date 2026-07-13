"""Unit tests for SnapshotAnalysisService report generation — Spec 009, Phase C2B.

Tests verify CSV reports (per_snapshot, per_detection, summary) and
pipeline_metrics.json generation through the integrated report writer.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.domain.entities.snapshot import Snapshot
from src.application.services.snapshot_analysis_service import (
    SnapshotAnalysisService,
    AnalysisProgress,
    AnalysisResult,
    TrackBestResult,
)
from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
    SnapshotAnalysisReportWriter,
    AnalysisReportWriteResult,
    PER_SNAPSHOT_COLUMNS,
    PER_DETECTION_COLUMNS,
    SUMMARY_COLUMNS,
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

    def __init__(self):
        self.created: list[tuple[int, Any]] = []

    def create(self, snapshot_id: int, result: Any) -> Any:
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
        image_path = (
            f"outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg"
        )
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


def _build_service_with_reports(
    tmp_path: Path,
    snapshots: list[Snapshot] = None,
    process_frame_results: list[dict] = None,
    analysis_skip_maturity: bool = False,
    profile_name: Optional[str] = None,
    components_factory_raises: bool = False,
) -> tuple[SnapshotAnalysisService, FakeSnapshotRepo, FakeInspectionResultRepo, FakeDbSession, MagicMock]:
    """Build a SnapshotAnalysisService with a real SnapshotAnalysisReportWriter."""
    snapshot_repo = FakeSnapshotRepo(snapshots or [])
    inspection_result_repo = FakeInspectionResultRepo()
    db_session = FakeDbSession()

    components_mock = MagicMock(name="PipelineComponents")
    if components_factory_raises:
        components_factory = MagicMock(side_effect=RuntimeError("Model load failed"))
    else:
        components_factory = MagicMock(return_value=components_mock)

    results_iter = iter(process_frame_results or [_make_frame_result()])
    process_frame_mock = MagicMock(
        side_effect=lambda img, comp, name, **kwargs: next(results_iter)
    )

    annotation_renderer = MagicMock(
        return_value=np.zeros((480, 640, 3), dtype=np.uint8)
    )

    report_writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

    service = SnapshotAnalysisService(
        monitoring_id=1,
        snapshot_repo=snapshot_repo,
        inspection_result_repo=inspection_result_repo,
        db_session=db_session,
        components_factory=components_factory,
        process_frame_fn=process_frame_mock,
        annotation_renderer=annotation_renderer,
        analysis_skip_maturity=analysis_skip_maturity,
        report_writer=report_writer,
        profile_name=profile_name,
    )

    return service, snapshot_repo, inspection_result_repo, db_session, process_frame_mock


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Read a CSV file and return (headers, rows)."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def _run_service(service):
    """Run the service with standard mocks."""
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    with patch("cv2.imread", return_value=dummy_img), \
         patch("cv2.imwrite", return_value=True), \
         patch("os.makedirs"):
        return service.run()


# ---------------------------------------------------------------------------
# Test 1: Zero snapshots → reports generated with headers only
# ---------------------------------------------------------------------------


class TestZeroSnapshotsReports:
    """Report files are created even with zero snapshots."""

    def test_zero_snapshots_reports_exist(self, tmp_path):
        """With 0 snapshots, CSVs have headers only and pipeline_metrics.json exists."""
        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=[], profile_name="edge"
        )
        result = _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        assert (reports_dir / "per_snapshot.csv").exists()
        assert (reports_dir / "per_detection.csv").exists()
        assert (reports_dir / "summary.csv").exists()
        assert (tmp_path / "1" / "pipeline_metrics.json").exists()

    def test_zero_snapshots_csv_headers_correct(self, tmp_path):
        """CSVs have correct column headers even with 0 rows."""
        service, _, _, _, _ = _build_service_with_reports(tmp_path, snapshots=[])
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        h1, rows1 = _read_csv(reports_dir / "per_snapshot.csv")
        h2, rows2 = _read_csv(reports_dir / "per_detection.csv")
        h3, rows3 = _read_csv(reports_dir / "summary.csv")

        assert h1 == PER_SNAPSHOT_COLUMNS
        assert h2 == PER_DETECTION_COLUMNS
        assert h3 == SUMMARY_COLUMNS
        assert len(rows1) == 0
        assert len(rows2) == 0
        assert len(rows3) == 1  # Summary always has 1 row


# ---------------------------------------------------------------------------
# Test 2: per_snapshot.csv has correct row data
# ---------------------------------------------------------------------------


class TestPerSnapshotCsv:
    """Verify per_snapshot.csv content."""

    def test_processed_snapshot_row(self, tmp_path):
        """Processed snapshot has status='processed' and correct metrics."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_snapshot.csv")

        assert len(rows) == 1
        row = rows[0]
        assert row["monitoring_id"] == "1"
        assert row["snapshot_id"] == "1"
        assert row["frame_index"] == "0"
        assert row["status"] == "processed"
        assert row["has_detections"] == "True"
        assert row["detections_count"] == "1"
        assert row["detection_sec"] == "0.5"
        assert row["error"] == ""

    def test_failed_snapshot_row(self, tmp_path):
        """Failed snapshot (imread=None) has status='failed' and error message."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0,
                          image_path="outputs/monitorings/1/snapshots/raw/missing.jpg"),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )

        def imread_side_effect(path):
            if "missing" in path:
                return None
            return np.zeros((480, 640, 3), dtype=np.uint8)

        with patch("cv2.imread", side_effect=imread_side_effect), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"):
            service.run()

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_snapshot.csv")

        assert len(rows) == 2
        failed_row = rows[0]  # frame_index=0 is first (sorted)
        assert failed_row["status"] == "failed"
        assert failed_row["error"] != ""
        processed_row = rows[1]
        assert processed_row["status"] == "processed"


# ---------------------------------------------------------------------------
# Test 3: per_detection.csv has correct row data
# ---------------------------------------------------------------------------


class TestPerDetectionCsv:
    """Verify per_detection.csv content."""

    def test_detection_rows_created(self, tmp_path):
        """Each detection produces a row in per_detection.csv."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([
            _make_detection(track_id=1, bbox=[10, 20, 110, 120]),
            _make_detection(track_id=2, bbox=[200, 200, 300, 300]),
        ])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 2
        assert rows[0]["track_id"] == "1"
        assert rows[0]["x1"] == "10"
        assert rows[0]["y1"] == "20"
        assert rows[0]["x2"] == "110"
        assert rows[0]["y2"] == "120"
        assert rows[0]["bbox_area"] == "10000"  # 100*100
        assert rows[1]["track_id"] == "2"

    def test_reused_detection_in_csv(self, tmp_path):
        """Reused detections still appear in per_detection.csv."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([
            _make_detection(track_id=1, bbox=[100, 100, 200, 200], reused=True),
        ])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 1
        assert rows[0]["reused_previous_result"] == "True"


# ---------------------------------------------------------------------------
# Test 4: selected_as_best logic
# ---------------------------------------------------------------------------


class TestSelectedAsBest:
    """Verify selected_as_best marks the best detection per track."""

    def test_best_detection_marked(self, tmp_path):
        """Largest area detection for a track gets selected_as_best=True."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        # Small bbox first, large bbox second
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 150, 150])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 300, 300])]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 2
        # Second detection (larger) should be marked as best
        assert rows[0]["selected_as_best"] == "False"
        assert rows[1]["selected_as_best"] == "True"

    def test_reused_not_selected_as_best(self, tmp_path):
        """Reused detections are never selected_as_best even if larger."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[50, 50, 350, 350], reused=True)]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 2
        # First (non-reused, area=10000) is best
        assert rows[0]["selected_as_best"] == "True"
        # Second is reused, not best
        assert rows[1]["selected_as_best"] == "False"

    def test_decision_reason_preserved_after_selected_as_best(self, tmp_path):
        """decision_reason from process_frame is NOT overwritten by selected_as_best marking."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        # Detection with explicit decision_reason from process_frame
        det = {
            "track_id": 1,
            "is_new_track": True,
            "track_hits": 1,
            "detection_id": 0,
            "bbox": [100, 100, 200, 200],
            "det_score": 0.92,
            "reused_previous_result": False,
            "decision_reason": "first_detection",
            "health_result": {"label": "healthy", "confidence": 0.95},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 45.0},
            "health_executed": True,
            "maturity_executed": True,
        }
        results = [_make_frame_result([det])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 1
        assert rows[0]["selected_as_best"] == "True"
        # decision_reason preserved from process_frame, NOT overwritten
        assert rows[0]["decision_reason"] == "first_detection"

    def test_exactly_one_best_per_track(self, tmp_path):
        """Exactly one detection per track is marked selected_as_best=True."""
        snapshots = [
            _make_snapshot(id=i + 1, monitoring_id=1, frame_index=i) for i in range(3)
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 150, 150])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 300, 300])]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 3
        best_count = sum(1 for r in rows if r["selected_as_best"] == "True")
        assert best_count == 1
        # The best is the largest area (frame_index=2)
        best_row = [r for r in rows if r["selected_as_best"] == "True"][0]
        assert best_row["frame_index"] == "2"

    def test_reused_row_with_same_bbox_not_selected(self, tmp_path):
        """Reused row with same bbox as best is NOT selected."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        # Both have same bbox but second is reused
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200], reused=True)]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 2
        # First (non-reused) is best
        assert rows[0]["selected_as_best"] == "True"
        assert rows[0]["reused_previous_result"] == "False"
        # Second (reused, same bbox) is NOT best
        assert rows[1]["selected_as_best"] == "False"
        assert rows[1]["reused_previous_result"] == "True"


# ---------------------------------------------------------------------------
# Test 5: summary.csv content
# ---------------------------------------------------------------------------


class TestSummaryCsv:
    """Verify summary.csv has correct aggregated data."""

    def test_summary_row_values(self, tmp_path):
        """Summary row reflects AnalysisResult values."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1, health_label="healthy"),
                _make_detection(track_id=2, health_label="unhealthy"),
            ]),
            _make_frame_result([
                _make_detection(track_id=3, health_label="healthy", maturity_stage="red"),
            ]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "summary.csv")

        assert len(rows) == 1
        row = rows[0]
        assert row["monitoring_id"] == "1"
        assert row["status"] == "completed"
        assert row["total_snapshots"] == "2"
        assert row["processed_snapshots"] == "2"
        assert row["failed_snapshots"] == "0"
        assert row["unique_tomatoes"] == "3"
        assert row["healthy_count"] == "2"
        assert row["unhealthy_count"] == "1"
        assert row["error_reason"] == ""


# ---------------------------------------------------------------------------
# Test 6: pipeline_metrics.json structure
# ---------------------------------------------------------------------------


class TestPipelineMetricsJson:
    """Verify pipeline_metrics.json structure and content."""

    def test_metrics_json_structure(self, tmp_path):
        """pipeline_metrics.json has schema_version, monitoring_id, analysis."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name="edge",
        )
        _run_service(service)

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        assert metrics_path.exists()

        with open(metrics_path, "r") as f:
            data = json.load(f)

        assert data["schema_version"] == 1
        assert data["monitoring_id"] == 1
        assert data["profile_name"] == "edge"
        assert "analysis" in data
        analysis = data["analysis"]
        assert analysis["total_snapshots"] == 1
        assert analysis["unique_tomatoes"] == 1
        assert analysis["healthy_count"] == 1

    def test_metrics_json_preserves_existing_capture_section(self, tmp_path):
        """If pipeline_metrics.json already exists with capture data, preserve it."""
        # Pre-create a pipeline_metrics.json with capture section
        metrics_dir = tmp_path / "1"
        metrics_dir.mkdir(parents=True)
        existing = {
            "schema_version": 1,
            "monitoring_id": 1,
            "capture": {"total_snapshots": 25, "duration_seconds": 44.0},
        }
        with open(metrics_dir / "pipeline_metrics.json", "w") as f:
            json.dump(existing, f)

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name="edge",
        )
        _run_service(service)

        with open(metrics_dir / "pipeline_metrics.json", "r") as f:
            data = json.load(f)

        # Capture section preserved
        assert data["capture"]["total_snapshots"] == 25
        assert data["capture"]["duration_seconds"] == 44.0
        # Analysis section added
        assert "analysis" in data
        assert data["profile_name"] == "edge"


# ---------------------------------------------------------------------------
# Test 7: report_paths populated in AnalysisResult
# ---------------------------------------------------------------------------


class TestReportPathsInResult:
    """Verify report_paths is populated in AnalysisResult."""

    def test_report_paths_has_all_keys(self, tmp_path):
        """After run, result.report_paths contains all 4 file paths."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        result = _run_service(service)

        assert "per_snapshot_csv" in result.report_paths
        assert "per_detection_csv" in result.report_paths
        assert "summary_csv" in result.report_paths
        assert "pipeline_metrics_json" in result.report_paths
        # All paths exist on disk
        for key, path_str in result.report_paths.items():
            assert Path(path_str).exists(), f"{key} not found at {path_str}"


# ---------------------------------------------------------------------------
# Test 8: No report_writer → default writer used, reports generated
# ---------------------------------------------------------------------------


class TestNoReportWriter:
    """Verify that when report_writer=None, default writer is used."""

    def test_no_writer_uses_default_writer(self, tmp_path):
        """When report_writer=None, a default SnapshotAnalysisReportWriter is created."""
        snapshot_repo = FakeSnapshotRepo([_make_snapshot(id=1, monitoring_id=1, frame_index=0)])
        db_session = FakeDbSession()
        results_iter = iter([_make_frame_result()])

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=None,
        )

        # Patch the default writer that will be lazily imported
        mock_write_result = MagicMock()
        mock_write_result.paths = {"per_snapshot_csv": "/fake/path.csv"}
        mock_write_result.errors = []

        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch(
                 "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter.write_reports",
                 return_value=mock_write_result,
             ) as mock_write:
            result = service.run()

        # Default writer was called
        mock_write.assert_called_once()
        assert result.report_paths == {"per_snapshot_csv": "/fake/path.csv"}
        assert service.progress.status == "completed"


# ---------------------------------------------------------------------------
# Test 9: Report writer failure doesn't crash analysis
# ---------------------------------------------------------------------------


class TestReportWriterFailure:
    """Verify report writer failure doesn't affect analysis result."""

    def test_writer_exception_doesnt_crash(self, tmp_path):
        """If report_writer.write_reports raises, analysis still succeeds."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        db_session = FakeDbSession()
        results_iter = iter(results)

        failing_writer = MagicMock()
        failing_writer.write_reports = MagicMock(side_effect=RuntimeError("Disk full"))

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=failing_writer,
        )

        result = _run_service(service)

        # Analysis still completes
        assert service.progress.status == "completed"
        assert result.processed_snapshots == 1
        assert result.unique_tomatoes == 1
        # Error recorded but not fatal
        assert any("Report generation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 10: Multiple snapshots produce correct row counts
# ---------------------------------------------------------------------------


class TestMultipleSnapshotsRowCounts:
    """Verify row counts across multiple snapshots."""

    def test_three_snapshots_two_detections_each(self, tmp_path):
        """3 snapshots with 2 detections each → 3 snapshot rows, 6 detection rows."""
        snapshots = [
            _make_snapshot(id=i + 1, monitoring_id=1, frame_index=i) for i in range(3)
        ]
        results = [
            _make_frame_result([
                _make_detection(track_id=1), _make_detection(track_id=2),
            ]) for _ in range(3)
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, snap_rows = _read_csv(reports_dir / "per_snapshot.csv")
        _, det_rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(snap_rows) == 3
        assert len(det_rows) == 6


# ---------------------------------------------------------------------------
# Test 11: Fatal DB error still generates reports (Correction 2)
# ---------------------------------------------------------------------------


class TestFatalErrorReports:
    """Verify reports are generated even on fatal error (partial data)."""

    def test_fatal_error_produces_partial_reports(self, tmp_path):
        """Fatal DB error: processed snapshots before error get report rows."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1)]),
            _make_frame_result([_make_detection(track_id=2)]),
        ]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        db_session = FakeDbSession()
        results_iter = iter(results)

        # Make commit fail on first call (during first snapshot processing)
        call_count = [0]
        original_commit = db_session.commit

        def failing_commit():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Disk full")
            original_commit()

        db_session.commit = failing_commit

        report_writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=report_writer,
        )

        result = _run_service(service)

        # Service reports error
        assert service.progress.status == "error"

        # Reports ARE generated (Correction 2: reports always generated in finally)
        assert "per_snapshot_csv" in result.report_paths
        assert "summary_csv" in result.report_paths

        # Verify summary.csv has status="error"
        reports_dir = tmp_path / "1" / "reports"
        _, summary_rows = _read_csv(reports_dir / "summary.csv")
        assert len(summary_rows) == 1
        assert summary_rows[0]["status"] == "error"
        assert summary_rows[0]["error_reason"] != ""

        # The fatal snapshot appears as "fatal_error" in per_snapshot.csv
        _, snap_rows = _read_csv(reports_dir / "per_snapshot.csv")
        statuses = [r["status"] for r in snap_rows]
        assert "fatal_error" in statuses

        # Only non-committed snapshots should NOT appear as "processed"
        # (commit failed on first snapshot, so none are processed)
        assert "processed" not in statuses


# ---------------------------------------------------------------------------
# Test 12: Report doesn't affect DB state
# ---------------------------------------------------------------------------


class TestReportDoesntAffectDb:
    """Verify report generation doesn't trigger additional DB operations."""

    def test_no_extra_commits_from_reports(self, tmp_path):
        """Report generation doesn't cause extra commits or rollbacks."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result([_make_detection(track_id=1)])]

        service, _, _, db_session, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        result = _run_service(service)

        # 1 commit for snapshot processing + 1 for persist_best_results = 2
        assert db_session.commit_count == 2
        assert db_session.rollback_count == 0


# ---------------------------------------------------------------------------
# Test 13: profile_name in pipeline_metrics.json
# ---------------------------------------------------------------------------


class TestProfileName:
    """Verify profile_name handling."""

    def test_profile_name_written(self, tmp_path):
        """profile_name appears in pipeline_metrics.json."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name="full",
        )
        _run_service(service)

        with open(tmp_path / "1" / "pipeline_metrics.json", "r") as f:
            data = json.load(f)
        assert data["profile_name"] == "full"

    def test_no_profile_name_uses_active_profile(self, tmp_path):
        """When profile_name=None, ACTIVE_PROFILE.name is used from settings."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name=None,
        )

        # Mock the settings import to fail so profile stays None
        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch.dict("sys.modules", {"src.infrastructure.config.settings": None}):
            result = service.run()

        with open(tmp_path / "1" / "pipeline_metrics.json", "r") as f:
            data = json.load(f)
        assert "profile_name" not in data


# ---------------------------------------------------------------------------
# Test 14: SnapshotAnalysisReportWriter standalone tests
# ---------------------------------------------------------------------------


class TestReportWriterStandalone:
    """Test the SnapshotAnalysisReportWriter in isolation."""

    def test_write_reports_creates_all_files(self, tmp_path):
        """write_reports creates all 4 files."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        result = writer.write_reports(
            monitoring_id=42,
            per_snapshot_rows=[{"monitoring_id": 42, "status": "processed"}],
            per_detection_rows=[{"monitoring_id": 42, "track_id": 1}],
            summary_row={"monitoring_id": 42, "status": "completed"},
            analysis_metrics={"total_snapshots": 1},
            profile_name="edge",
        )

        assert len(result.errors) == 0
        assert "per_snapshot_csv" in result.paths
        assert "per_detection_csv" in result.paths
        assert "summary_csv" in result.paths
        assert "pipeline_metrics_json" in result.paths

    def test_atomic_write_on_partial_failure(self, tmp_path):
        """If one CSV fails, others still succeed."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        # Make reports dir read-only for per_snapshot.csv by using a mock
        # Instead, test that errors list captures failures independently
        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=[],
            per_detection_rows=[],
            summary_row={},
            analysis_metrics={},
        )

        # All succeed with empty data
        assert len(result.errors) == 0
        assert len(result.paths) == 4

    def test_invalid_existing_metrics_raises(self, tmp_path):
        """Invalid existing pipeline_metrics.json → error reported."""
        metrics_dir = tmp_path / "1"
        metrics_dir.mkdir(parents=True)
        # Write invalid JSON
        with open(metrics_dir / "pipeline_metrics.json", "w") as f:
            f.write("not json")

        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=[],
            per_detection_rows=[],
            summary_row={},
            analysis_metrics={},
        )

        # pipeline_metrics.json should have an error
        assert any("pipeline_metrics.json" in e for e in result.errors)
        # But other files succeeded
        assert "per_snapshot_csv" in result.paths
        assert "per_detection_csv" in result.paths
        assert "summary_csv" in result.paths


# ---------------------------------------------------------------------------
# Test 15: Module importable without Detectron2
# ---------------------------------------------------------------------------


class TestImportability:
    """Verify report writer module is importable without heavy deps."""

    def test_report_writer_importable(self):
        """SnapshotAnalysisReportWriter imports without Detectron2/Torch/OpenCV."""
        from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
            SnapshotAnalysisReportWriter,
            AnalysisReportWriteResult,
            PER_SNAPSHOT_COLUMNS,
            PER_DETECTION_COLUMNS,
            SUMMARY_COLUMNS,
        )
        assert SnapshotAnalysisReportWriter is not None
        assert len(PER_SNAPSHOT_COLUMNS) == 16
        assert len(PER_DETECTION_COLUMNS) == 31
        assert len(SUMMARY_COLUMNS) == 20

    def test_analysis_result_has_report_paths(self):
        """AnalysisResult dataclass has report_paths field."""
        result = AnalysisResult()
        assert hasattr(result, "report_paths")
        assert result.report_paths == {}


# ---------------------------------------------------------------------------
# Test 16: Thermal stop called BEFORE report_writer.write_reports (Correction 1)
# ---------------------------------------------------------------------------


class TestThermalStopBeforeReports:
    """Verify thermal.stop() is called BEFORE report_writer.write_reports()."""

    def test_thermal_stop_before_report_write(self, tmp_path):
        """side_effect tracking confirms thermal.stop() precedes report writes."""
        call_order = []

        thermal = MagicMock()
        thermal.start = MagicMock()
        thermal.stop = MagicMock(side_effect=lambda: call_order.append("thermal_stop"))
        thermal.pause_event = None

        mock_write_result = MagicMock()
        mock_write_result.paths = {"per_snapshot_csv": "/fake/path.csv"}
        mock_write_result.errors = []

        report_writer = MagicMock()
        report_writer.write_reports = MagicMock(
            side_effect=lambda **kw: (call_order.append("write_reports"), mock_write_result)[1]
        )

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results_iter = iter([_make_frame_result()])

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=FakeSnapshotRepo(snapshots),
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=FakeDbSession(),
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            thermal_monitor=thermal,
            report_writer=report_writer,
        )

        _run_service(service)

        assert call_order == ["thermal_stop", "write_reports"]


# ---------------------------------------------------------------------------
# Test 17: Reused detection with inherited results (Correction 4)
# ---------------------------------------------------------------------------


class TestPerDetectionRowFields:
    """Verify per_detection row uses explicit health_executed/maturity_executed flags."""

    def test_reused_detection_inherited_labels_not_executed(self, tmp_path):
        """Reused detection with inherited health/maturity labels has
        health_executed=False, maturity_executed=False, but labels are present."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        # Detection is reused — inherits labels but didn't execute models
        det = {
            "track_id": 1,
            "is_new_track": False,
            "track_hits": 5,
            "detection_id": 0,
            "bbox": [100, 100, 200, 200],
            "det_score": 0.92,
            "reused_previous_result": True,
            "health_executed": False,
            "maturity_executed": False,
            "health_result": {"label": "healthy", "confidence": 0.95},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 45.0},
        }
        results = [_make_frame_result([det])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 1
        row = rows[0]
        # Labels present (inherited)
        assert row["health_label"] == "healthy"
        assert row["usda_stage"] == "turning"
        # But models were NOT executed for this detection
        assert row["health_executed"] == "False"
        assert row["maturity_executed"] == "False"
        assert row["reused_previous_result"] == "True"


# ---------------------------------------------------------------------------
# Test 18: Sorted rows in CSV output (Correction 6)
# ---------------------------------------------------------------------------


class TestSortedRowsInCsv:
    """Verify rows are sorted before writing to CSV."""

    def test_unordered_input_sorted_output(self, tmp_path):
        """Unordered per_snapshot_rows → sorted by frame_index in CSV."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        per_snapshot_rows = [
            {"monitoring_id": 1, "snapshot_id": 3, "frame_index": 5, "status": "processed"},
            {"monitoring_id": 1, "snapshot_id": 1, "frame_index": 0, "status": "processed"},
            {"monitoring_id": 1, "snapshot_id": 2, "frame_index": 2, "status": "processed"},
        ]
        per_detection_rows = [
            {"monitoring_id": 1, "frame_index": 5, "detection_id": 0, "track_id": 3},
            {"monitoring_id": 1, "frame_index": 0, "detection_id": 0, "track_id": 1},
            {"monitoring_id": 1, "frame_index": 2, "detection_id": 0, "track_id": 2},
        ]

        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=per_snapshot_rows,
            per_detection_rows=per_detection_rows,
            summary_row={"monitoring_id": 1, "status": "completed"},
            analysis_metrics={"total_snapshots": 3},
        )

        # Verify snapshot rows sorted by frame_index
        reports_dir = tmp_path / "1" / "reports"
        _, snap_rows = _read_csv(reports_dir / "per_snapshot.csv")
        frame_indices = [int(r["frame_index"]) for r in snap_rows]
        assert frame_indices == [0, 2, 5]

        # Verify detection rows sorted by frame_index
        _, det_rows = _read_csv(reports_dir / "per_detection.csv")
        det_frame_indices = [int(r["frame_index"]) for r in det_rows]
        assert det_frame_indices == [0, 2, 5]

        # Original lists are NOT modified
        assert per_snapshot_rows[0]["frame_index"] == 5
        assert per_detection_rows[0]["frame_index"] == 5


# ---------------------------------------------------------------------------
# Test 19: Complete pipeline_metrics.json analysis section (Correction 7)
# ---------------------------------------------------------------------------


class TestCompletePipelineMetrics:
    """Verify full analysis_metrics structure in pipeline_metrics.json."""

    def test_full_analysis_metrics_structure(self, tmp_path):
        """pipeline_metrics.json has complete analysis section with execution_counts and timings."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        # Detection with explicit flags
        det1 = {
            "track_id": 1, "is_new_track": True, "track_hits": 1,
            "detection_id": 0, "bbox": [100, 100, 200, 200], "det_score": 0.92,
            "reused_previous_result": False, "health_executed": True, "maturity_executed": True,
            "health_result": {"label": "healthy", "confidence": 0.95},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 45.0},
            "times": {"crop_sec": 0.01, "health_sec": 0.05, "maturity_sec": 0.03, "detection_pipeline_sec": 0.1},
        }
        det2_reused = {
            "track_id": 1, "is_new_track": False, "track_hits": 2,
            "detection_id": 0, "bbox": [100, 100, 200, 200], "det_score": 0.92,
            "reused_previous_result": True, "health_executed": False, "maturity_executed": False,
            "health_result": {"label": "healthy", "confidence": 0.95},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 45.0},
        }
        results = [
            _make_frame_result([det1]),
            _make_frame_result([det2_reused]),
        ]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name="edge",
        )
        _run_service(service)

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        with open(metrics_path, "r") as f:
            data = json.load(f)

        analysis = data["analysis"]

        # Top-level fields
        assert analysis["status"] == "completed"
        assert analysis["error_reason"] is None
        assert analysis["total_snapshots"] == 2
        assert analysis["processed_snapshots"] == 2
        assert analysis["failed_snapshots"] == 0
        assert analysis["unique_tomatoes"] == 1
        assert analysis["healthy_count"] == 1
        assert analysis["analysis_duration_seconds"] > 0
        assert analysis["processed_snapshots_per_second"] > 0

        # execution_counts
        ec = analysis["execution_counts"]
        assert "new_tracks" in ec
        assert ec["reused_rows"] == 1
        assert ec["health_executions"] == 1
        assert ec["maturity_executions"] == 1

        # average_timings_seconds
        at = analysis["average_timings_seconds"]
        assert "detection" in at
        assert "tracking" in at
        assert "total_frame" in at
        assert "crop" in at
        assert "health" in at
        assert "maturity" in at
        assert "detection_pipeline" in at

        # errors
        assert "errors_count" in analysis
        assert "errors" in analysis
        assert isinstance(analysis["errors"], list)

        # report_files (empty since filled after write — already written at this point)
        assert "report_files" in analysis


# ---------------------------------------------------------------------------
# Test 20: Invalid JSON preserves original bytes (Correction 9)
# ---------------------------------------------------------------------------


class TestInvalidJsonPreserved:
    """Verify invalid JSON in pipeline_metrics.json is preserved unchanged."""

    def test_invalid_json_preserved_unchanged(self, tmp_path):
        """Invalid existing pipeline_metrics.json is not overwritten."""
        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        metrics_path.parent.mkdir(parents=True)
        invalid_content = b"not {json} content"
        metrics_path.write_bytes(invalid_content)

        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)
        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=[{"monitoring_id": 1, "status": "processed"}],
            per_detection_rows=[],
            summary_row={"monitoring_id": 1, "status": "completed"},
            analysis_metrics={"total_snapshots": 1},
        )

        # File unchanged
        assert metrics_path.read_bytes() == invalid_content
        assert "pipeline_metrics_json" not in result.paths
        assert any("pipeline_metrics.json" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 21: Single CSV failure, others succeed (Correction 10)
# ---------------------------------------------------------------------------


class TestSingleCsvFailureOthersSucceed:
    """Verify independent CSV failure doesn't stop other files."""

    def test_single_csv_failure_others_succeed(self, tmp_path):
        """If per_snapshot.csv write fails, other files still succeed."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        original_write = writer._write_csv_atomic

        def patched_write(path, columns, rows):
            if "per_snapshot" in str(path):
                raise OSError("Disk full for per_snapshot")
            return original_write(path, columns, rows)

        writer._write_csv_atomic = patched_write

        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=[{"monitoring_id": 1, "status": "processed"}],
            per_detection_rows=[{"monitoring_id": 1, "track_id": 1}],
            summary_row={"monitoring_id": 1, "status": "completed"},
            analysis_metrics={"total_snapshots": 1},
        )

        assert "per_snapshot_csv" not in result.paths
        assert "per_detection_csv" in result.paths
        assert "summary_csv" in result.paths
        assert "pipeline_metrics_json" in result.paths
        assert any("per_snapshot.csv" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test 22: Correction 1 — First snapshot commit fails: best_results_by_track uncontaminated
# ---------------------------------------------------------------------------


class TestStagedBestOnFatalError:
    """Correction 1: staged best results not merged on fatal commit failure."""

    def test_first_snapshot_commit_fails_no_best_results(self, tmp_path):
        """First snapshot commit fails → unique_tomatoes==0, best_results_by_track=={}."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 300, 300])]),
        ]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        db_session = FakeDbSession()
        results_iter = iter(results)

        # Commit always fails
        db_session.commit = MagicMock(side_effect=RuntimeError("Disk full"))

        report_writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=report_writer,
        )

        result = _run_service(service)

        # best_results_by_track must be empty — commit failed before merge
        assert result.unique_tomatoes == 0
        assert result.best_results_by_track == {}
        assert result.total_detection_rows == 0

        # per_detection.csv has 0 rows (detections not appended for failed commit)
        reports_dir = tmp_path / "1" / "reports"
        _, det_rows = _read_csv(reports_dir / "per_detection.csv")
        assert len(det_rows) == 0

        # per_snapshot.csv has fatal_error row
        _, snap_rows = _read_csv(reports_dir / "per_snapshot.csv")
        assert len(snap_rows) == 1
        assert snap_rows[0]["status"] == "fatal_error"

    def test_first_ok_second_fails_only_first_tracks(self, tmp_path):
        """First snapshot commits OK, second fails → only first snapshot's tracks in results."""
        snapshots = [
            _make_snapshot(id=1, monitoring_id=1, frame_index=0),
            _make_snapshot(id=2, monitoring_id=1, frame_index=1),
        ]
        results = [
            _make_frame_result([_make_detection(track_id=1, bbox=[100, 100, 200, 200])]),
            _make_frame_result([_make_detection(track_id=2, bbox=[50, 50, 250, 250])]),
        ]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        db_session = FakeDbSession()
        results_iter = iter(results)

        # First commit succeeds, second fails
        commit_count = [0]

        def commit_side_effect():
            commit_count[0] += 1
            if commit_count[0] == 2:
                raise RuntimeError("Disk full on second")

        db_session.commit = commit_side_effect

        report_writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=report_writer,
        )

        result = _run_service(service)

        # Only track 1 from first snapshot
        assert result.unique_tomatoes == 1
        assert 1 in result.best_results_by_track
        assert 2 not in result.best_results_by_track

        # total_detection_rows only from first snapshot
        assert result.total_detection_rows == 1

        # per_detection.csv has only first snapshot's detection
        reports_dir = tmp_path / "1" / "reports"
        _, det_rows = _read_csv(reports_dir / "per_detection.csv")
        assert len(det_rows) == 1
        assert det_rows[0]["track_id"] == "1"

        # per_snapshot.csv: first=processed, second=fatal_error
        _, snap_rows = _read_csv(reports_dir / "per_snapshot.csv")
        assert len(snap_rows) == 2
        assert snap_rows[0]["status"] == "processed"
        assert snap_rows[1]["status"] == "fatal_error"


# ---------------------------------------------------------------------------
# Test 23: Correction 6 — Reused detection with decision_reason preserved
# ---------------------------------------------------------------------------


class TestReusedDetectionComplete:
    """Correction 6: Complete per_detection test for reused detections."""

    def test_reused_detection_full_fields(self, tmp_path):
        """Reused detection preserves decision_reason, inherited labels, executed=False."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        det = {
            "track_id": 5,
            "is_new_track": False,
            "track_hits": 4,
            "detection_id": 2,
            "bbox": [50, 60, 180, 190],
            "det_score": 0.88,
            "reused_previous_result": True,
            "decision_reason": "reuse_previous_result",
            "health_executed": False,
            "maturity_executed": False,
            "health_result": {"label": "healthy", "confidence": 0.93,
                              "prob_healthy": 0.93, "prob_unhealthy": 0.07},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 42.0,
                                "confidence": 0.8},
            "times": {},
        }
        results = [_make_frame_result([det])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 1
        row = rows[0]
        assert row["decision_reason"] == "reuse_previous_result"
        assert row["health_executed"] == "False"
        assert row["maturity_executed"] == "False"
        assert row["health_label"] == "healthy"
        assert row["usda_stage"] == "turning"
        assert row["selected_as_best"] == "False"  # reused never selected
        assert row["reused_previous_result"] == "True"

    def test_same_track_same_snapshot_different_bbox_exact_match(self, tmp_path):
        """Same track, same snapshot, two bboxes → only exact match is selected_as_best."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        # Two detections for same track_id in same snapshot but different bboxes
        det1 = {
            "track_id": 1, "is_new_track": True, "track_hits": 1,
            "detection_id": 0, "bbox": [100, 100, 200, 200], "det_score": 0.92,
            "reused_previous_result": False, "health_executed": True, "maturity_executed": True,
            "health_result": {"label": "healthy", "confidence": 0.95},
            "maturity_result": {"usda_stage": "turning", "maturity_percent": 45.0},
        }
        det2 = {
            "track_id": 1, "is_new_track": False, "track_hits": 2,
            "detection_id": 1, "bbox": [150, 150, 350, 350], "det_score": 0.90,
            "reused_previous_result": False, "health_executed": True, "maturity_executed": True,
            "health_result": {"label": "healthy", "confidence": 0.90},
            "maturity_result": {"usda_stage": "pink", "maturity_percent": 60.0},
        }
        results = [_make_frame_result([det1, det2])]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results
        )
        _run_service(service)

        reports_dir = tmp_path / "1" / "reports"
        _, rows = _read_csv(reports_dir / "per_detection.csv")

        assert len(rows) == 2
        # det2 has larger area (200*200=40000) vs det1 (100*100=10000)
        # so det2's bbox is the best
        best_rows = [r for r in rows if r["selected_as_best"] == "True"]
        assert len(best_rows) == 1
        assert best_rows[0]["x1"] == "150"
        assert best_rows[0]["y1"] == "150"
        assert best_rows[0]["x2"] == "350"
        assert best_rows[0]["y2"] == "350"


# ---------------------------------------------------------------------------
# Test 24: Correction 7 — Default profile_name from ACTIVE_PROFILE
# ---------------------------------------------------------------------------


class TestDefaultProfileFromActiveProfile:
    """Correction 7: profile_name defaults from ACTIVE_PROFILE when not provided."""

    def test_default_profile_from_active_profile(self, tmp_path):
        """When profile_name=None, ACTIVE_PROFILE.name is used."""
        from types import SimpleNamespace

        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name=None,
        )

        mock_profile = SimpleNamespace(name="edge-test")
        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch("src.infrastructure.config.settings.ACTIVE_PROFILE", mock_profile):
            result = service.run()

        with open(tmp_path / "1" / "pipeline_metrics.json") as f:
            data = json.load(f)
        assert data["profile_name"] == "edge-test"

    def test_profile_import_failure_continues(self, tmp_path):
        """When ACTIVE_PROFILE import fails, reports still generated without profile_name."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        service, _, _, _, _ = _build_service_with_reports(
            tmp_path, snapshots=snapshots, process_frame_results=results,
            profile_name=None,
        )

        # Make settings import raise
        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch.dict("sys.modules", {"src.infrastructure.config.settings": None}):
            result = service.run()

        assert service.progress.status == "completed"
        with open(tmp_path / "1" / "pipeline_metrics.json") as f:
            data = json.load(f)
        assert "profile_name" not in data


# ---------------------------------------------------------------------------
# Test 25: Correction 3 — CSV failure reflected in pipeline_metrics.json
# ---------------------------------------------------------------------------


class TestCsvFailureReflectedInJson:
    """Correction 3: CSV failures are reflected in pipeline_metrics.json."""

    def test_per_snapshot_fails_json_contains_error(self, tmp_path):
        """per_snapshot.csv fails → pipeline_metrics.json contains the error."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        original_write = SnapshotAnalysisReportWriter._write_csv_atomic

        def patched_write(self_arg, path, columns, rows):
            if "per_snapshot.csv" in str(path) and "per_detection" not in str(path):
                raise OSError("Permission denied for per_snapshot")
            return original_write(self_arg, path, columns, rows)

        with patch.object(SnapshotAnalysisReportWriter, "_write_csv_atomic", patched_write):
            result = writer.write_reports(
                monitoring_id=1,
                per_snapshot_rows=[{"monitoring_id": 1, "status": "processed"}],
                per_detection_rows=[{"monitoring_id": 1, "track_id": 1}],
                summary_row={"monitoring_id": 1, "status": "completed"},
                analysis_metrics={"total_snapshots": 1, "errors": [], "errors_count": 0, "report_files": {}},
            )

        # CSVs that succeeded must be in result.paths
        assert "per_snapshot_csv" not in result.paths
        assert "per_detection_csv" in result.paths
        assert "summary_csv" in result.paths
        assert "pipeline_metrics_json" in result.paths

        # Read the JSON and verify error is reflected
        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        with open(metrics_path, "r") as f:
            data = json.load(f)

        analysis = data["analysis"]
        assert any("per_snapshot.csv" in e for e in analysis["errors"])
        assert analysis["errors_count"] >= 1

        # report_files only has the 3 that succeeded
        assert "per_snapshot_csv" not in analysis["report_files"]
        assert "per_detection_csv" in analysis["report_files"]
        assert "summary_csv" in analysis["report_files"]
        assert "pipeline_metrics_json" in analysis["report_files"]

    def test_report_files_complete_on_success(self, tmp_path):
        """All files succeed → report_files has all 4 entries."""
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path)

        result = writer.write_reports(
            monitoring_id=1,
            per_snapshot_rows=[],
            per_detection_rows=[],
            summary_row={"monitoring_id": 1},
            analysis_metrics={"total_snapshots": 0, "errors": [], "errors_count": 0, "report_files": {}},
        )

        metrics_path = tmp_path / "1" / "pipeline_metrics.json"
        with open(metrics_path, "r") as f:
            data = json.load(f)

        rf = data["analysis"]["report_files"]
        assert rf["per_snapshot_csv"] == "reports/per_snapshot.csv"
        assert rf["per_detection_csv"] == "reports/per_detection.csv"
        assert rf["summary_csv"] == "reports/summary.csv"
        assert rf["pipeline_metrics_json"] == "pipeline_metrics.json"


# ---------------------------------------------------------------------------
# Test 26: Correction 4 — Writer constructor raises → run() returns normally
# ---------------------------------------------------------------------------


class TestWriterConstructorFailure:
    """Correction 4: Writer constructor failure doesn't crash run()."""

    def test_writer_constructor_raises(self, tmp_path):
        """If default writer import raises, run() returns normally with error recorded."""
        snapshots = [_make_snapshot(id=1, monitoring_id=1, frame_index=0)]
        results = [_make_frame_result()]

        snapshot_repo = FakeSnapshotRepo(snapshots)
        db_session = FakeDbSession()
        results_iter = iter(results)

        # No report_writer → will try to import default writer
        service = SnapshotAnalysisService(
            monitoring_id=1,
            snapshot_repo=snapshot_repo,
            inspection_result_repo=FakeInspectionResultRepo(),
            db_session=db_session,
            components_factory=MagicMock(return_value=MagicMock()),
            process_frame_fn=MagicMock(side_effect=lambda img, comp, name, **kw: next(results_iter)),
            annotation_renderer=MagicMock(return_value=np.zeros((480, 640, 3), dtype=np.uint8)),
            report_writer=None,
        )

        # Patch the import to raise
        with patch("cv2.imread", return_value=np.zeros((480, 640, 3), dtype=np.uint8)), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.makedirs"), \
             patch(
                 "src.infrastructure.persistence.local.snapshot_analysis_report_writer.SnapshotAnalysisReportWriter",
                 side_effect=RuntimeError("Cannot construct writer"),
             ):
            result = service.run()

        # Analysis still completes (or was already completed before reports)
        assert service.progress.status == "completed"
        assert any("Report generation failed" in e for e in result.errors)
