"""Unit tests for ExportService ZIP generation."""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.application.services.export_service import ExportService, _entity_to_dict
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.entities.snapshot import Snapshot
from src.domain.entities.activity_type import ActivityType
from src.domain.entities.activity_log import ActivityLog


@pytest.fixture
def sample_greenhouses():
    return [
        Greenhouse(name="Invernadero 1", id=1, location="Zona A",
                   created_at=datetime(2024, 1, 1, 10, 0, 0)),
    ]


@pytest.fixture
def sample_modules():
    return [
        Module(greenhouse_id=1, name="Módulo 1", id=1, crop_type="Tomate Cherry",
               width_m=5.0, length_m=2.0, monitoring_frequency_days=7),
    ]


@pytest.fixture
def sample_monitorings():
    return [
        Monitoring(module_id=1, width_m=5.0, length_m=2.0, id=1,
                   status="completed", total_snapshots=2, total_detections=5,
                   started_at=datetime(2024, 1, 15, 8, 0, 0),
                   completed_at=datetime(2024, 1, 15, 8, 30, 0)),
    ]


@pytest.fixture
def sample_metrics():
    return {
        1: MonitoringMetrics(
            monitoring_id=1, total_tomatoes=5, healthy_count=4,
            unhealthy_count=1, pct_healthy=80.0, pct_unhealthy=20.0,
            snapshots_with_detections=2, id=1,
            computed_at=datetime(2024, 1, 15, 8, 30, 0),
        ),
    }


@pytest.fixture
def sample_snapshots():
    return {
        1: [
            Snapshot(monitoring_id=1, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg",
                     frame_index=0, id=1, has_detections=True),
            Snapshot(monitoring_id=1, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000001.jpg",
                     frame_index=1, id=2, has_detections=True),
        ],
    }


@pytest.fixture
def sample_activity_types():
    return [
        ActivityType(code="riego", name="Riego", category="mantenimiento", id=1),
    ]


@pytest.fixture
def sample_activity_logs():
    return [
        ActivityLog(module_id=1, activity_type_id=1, user_id=1, id=1,
                    occurred_at=datetime(2024, 1, 10, 9, 0, 0)),
    ]


class TestExportServiceGeneratesValidZip:
    """Test that ExportService generates a valid ZIP with correct structure."""

    def test_generates_zip_with_manifest(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """ZIP contains manifest.json with correct structure."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        assert result.status == "completed"
        assert result.file_path.endswith(".zip")
        assert result.file_size_bytes > 0

        with zipfile.ZipFile(result.file_path, "r") as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["app_name"] == "Tomato Monitor"
            assert manifest["counts"]["greenhouses"] == 1
            assert manifest["counts"]["modules"] == 1
            assert manifest["counts"]["monitorings"] == 1

    def test_includes_data_json_files(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """ZIP contains all expected data/*.json files."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        with zipfile.ZipFile(result.file_path, "r") as zf:
            names = zf.namelist()
            assert "data/greenhouses.json" in names
            assert "data/modules.json" in names
            assert "data/monitorings.json" in names
            assert "data/metrics.json" in names
            assert "data/snapshots.json" in names
            assert "data/activity_types.json" in names
            assert "data/activity_logs.json" in names

    def test_copies_existing_snapshot_files(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """ZIP includes snapshot images that exist on disk."""
        # Create fake snapshot files
        mon_dir = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        mon_dir.mkdir(parents=True)
        (mon_dir / "snapshot_000000.jpg").write_bytes(b"\xff\xd8\xff\xe0fake_jpg_0")
        (mon_dir / "snapshot_000001.jpg").write_bytes(b"\xff\xd8\xff\xe0fake_jpg_1")

        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path / "output"),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        assert result.status == "completed"
        assert result.images_count == 2
        assert result.files_missing == []

        with zipfile.ZipFile(result.file_path, "r") as zf:
            names = zf.namelist()
            assert "images/monitoring_1/raw/snapshot_000000.jpg" in names
            assert "images/monitoring_1/raw/snapshot_000001.jpg" in names

    def test_prefers_annotated_over_raw(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_activity_types,
        sample_activity_logs,
    ):
        """ZIP includes annotated snapshots when available."""
        # Create annotated and raw files
        annotated_dir = tmp_path / "monitorings" / "1" / "annotated_snapshots"
        annotated_dir.mkdir(parents=True)
        (annotated_dir / "snapshot_000000.jpg").write_bytes(b"annotated_data")

        raw_dir = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        (raw_dir / "snapshot_000000.jpg").write_bytes(b"raw_data")

        snapshots = {
            1: [Snapshot(monitoring_id=1, image_path="x", frame_index=0, id=1)],
        }

        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring={},
            snapshots_by_monitoring=snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path / "output"),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        assert result.images_count == 1  # counted once
        assert result.files_included == 2  # both files written

        with zipfile.ZipFile(result.file_path, "r") as zf:
            names = zf.namelist()
            assert "images/monitoring_1/annotated/snapshot_000000.jpg" in names
            assert "images/monitoring_1/raw/snapshot_000000.jpg" in names

    def test_handles_missing_snapshots_gracefully(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """Missing snapshot files are tracked in manifest warnings."""
        # Don't create any snapshot files
        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        assert result.status == "completed"
        assert result.images_count == 0
        assert len(result.files_missing) == 2
        assert result.manifest["warnings"]

    def test_counts_records_correctly(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """Records count includes all data entities."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        # 1 greenhouse + 1 module + 1 monitoring + 1 metrics + 2 snapshots + 1 activity_type + 1 activity_log
        expected = 1 + 1 + 1 + 1 + 2 + 1 + 1
        assert result.records_count == expected

    def test_serializes_datetime_as_iso(self, tmp_path):
        """Datetime fields are serialized as ISO 8601 strings."""
        dt = datetime(2024, 6, 15, 14, 30, 0)
        gh = Greenhouse(name="Test", id=1, created_at=dt)

        service = ExportService()
        result = service.generate_export(
            greenhouses=[gh],
            modules=[],
            monitorings=[],
            metrics_by_monitoring={},
            snapshots_by_monitoring={},
            activity_types=[],
            activity_logs=[],
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "mon"),
        )

        with zipfile.ZipFile(result.file_path, "r") as zf:
            data = json.loads(zf.read("data/greenhouses.json"))
            assert data[0]["created_at"] == "2024-06-15T14:30:00"

    def test_empty_data_generates_valid_zip(self, tmp_path):
        """Empty data still generates a valid ZIP with manifest."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=[],
            modules=[],
            monitorings=[],
            metrics_by_monitoring={},
            snapshots_by_monitoring={},
            activity_types=[],
            activity_logs=[],
            output_dir=str(tmp_path),
            base_snapshots_dir=str(tmp_path / "mon"),
        )

        assert result.status == "completed"
        assert result.records_count == 0
        assert result.images_count == 0

        with zipfile.ZipFile(result.file_path, "r") as zf:
            assert "manifest.json" in zf.namelist()

    def test_includes_pipeline_metrics_when_available(
        self, tmp_path, sample_greenhouses, sample_modules,
        sample_monitorings, sample_metrics, sample_snapshots,
        sample_activity_types, sample_activity_logs,
    ):
        """ZIP includes pipeline_metrics.json from reports directory."""
        reports_dir = tmp_path / "monitorings" / "1" / "reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "pipeline_metrics.json").write_text(
            json.dumps({"fps": 2.5, "total_time_s": 120}), encoding="utf-8"
        )

        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses,
            modules=sample_modules,
            monitorings=sample_monitorings,
            metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots,
            activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path / "output"),
            base_snapshots_dir=str(tmp_path / "monitorings"),
        )

        with zipfile.ZipFile(result.file_path, "r") as zf:
            assert "reports/monitoring_1/pipeline_metrics.json" in zf.namelist()


class TestExportFilenameCollision:
    """Regression tests: distinct packages must not share a file_path (Spec 025)."""

    def _generate(self, service, tmp_path, export_package=None):
        return service.generate_export(
            greenhouses=[], modules=[], monitorings=[],
            metrics_by_monitoring={}, snapshots_by_monitoring={},
            activity_types=[], activity_logs=[],
            output_dir=str(tmp_path), base_snapshots_dir=str(tmp_path / "mon"),
            export_package=export_package,
        )

    def test_distinct_ids_same_second_do_not_collide(self, tmp_path, monkeypatch):
        """Two packages with distinct ids at the SAME timestamp get distinct paths."""
        import src.application.services.export_service as export_module
        from src.domain.entities.export_package import ExportPackage

        fixed = datetime(2026, 9, 17, 6, 30, 0, tzinfo=timezone.utc)

        class _FixedDatetime:
            @staticmethod
            def now(tz=None):
                return fixed

        # Force an identical timestamp for both generations.
        monkeypatch.setattr(export_module, "datetime", _FixedDatetime)

        service = ExportService()
        pkg1 = ExportPackage(created_by_user_id=1, scope="full", id=101, status="generating")
        pkg2 = ExportPackage(created_by_user_id=1, scope="full", id=102, status="generating")

        result1 = self._generate(service, tmp_path, export_package=pkg1)
        result2 = self._generate(service, tmp_path, export_package=pkg2)

        assert result1.file_path != result2.file_path
        assert Path(result1.file_path).exists()
        assert Path(result2.file_path).exists()
        assert "101" in Path(result1.file_path).name
        assert "102" in Path(result2.file_path).name

    def test_fallback_without_package_uses_microseconds(self, tmp_path, monkeypatch):
        """Without a package id, the filename uses a microsecond-resolution
        timestamp (not seconds-only), so near-simultaneous generations do not
        collide. No sleeps required."""
        import src.application.services.export_service as export_module

        # Two calls in the same second but different microseconds.
        times = iter([
            datetime(2026, 9, 17, 6, 30, 0, 111111, tzinfo=timezone.utc),
            datetime(2026, 9, 17, 6, 30, 0, 222222, tzinfo=timezone.utc),
        ])

        class _SeqDatetime:
            @staticmethod
            def now(tz=None):
                return next(times)

        monkeypatch.setattr(export_module, "datetime", _SeqDatetime)

        service = ExportService()
        result1 = self._generate(service, tmp_path, export_package=None)
        result2 = self._generate(service, tmp_path, export_package=None)

        # Filename shape: tomato_monitor_export_YYYYmmdd_HHMMSS_ffffff.zip
        name1 = Path(result1.file_path).name
        assert name1.startswith("tomato_monitor_export_")
        stem = name1[len("tomato_monitor_export_"):].removesuffix(".zip")
        assert len(stem.split("_")) == 3  # date, HHMMSS, microseconds
        assert stem.endswith("111111")

        assert result1.file_path != result2.file_path
        assert Path(result1.file_path).exists()
        assert Path(result2.file_path).exists()


class TestEntityToDict:
    """Test the _entity_to_dict helper."""

    def test_excludes_specified_fields(self):
        """Fields in exclude list are not included."""
        from src.domain.entities.user import User

        user = User(
            id=1,
            full_name="Test",
            email="test@test.com",
            password_hash="secret_hash_value",
            role="operator",
        )
        result = _entity_to_dict(user, exclude_fields=["password_hash"])
        assert "password_hash" not in result
        assert result["full_name"] == "Test"
        assert result["email"] == "test@test.com"

    def test_handles_none_values(self):
        """None values are preserved as None."""
        gh = Greenhouse(name="Test", id=1, location=None)
        result = _entity_to_dict(gh)
        assert result["location"] is None

    def test_handles_datetime(self):
        """Datetime values are converted to ISO strings."""
        dt = datetime(2024, 3, 15, 10, 30, 0)
        gh = Greenhouse(name="Test", id=1, created_at=dt)
        result = _entity_to_dict(gh)
        assert result["created_at"] == "2024-03-15T10:30:00"

    def test_includes_export_package_json(self, tmp_path, sample_greenhouses, sample_modules, sample_monitorings, sample_metrics, sample_snapshots, sample_activity_types, sample_activity_logs):
        """ZIP contains data/export_package.json."""
        from src.domain.entities.export_package import ExportPackage
        pkg = ExportPackage(created_by_user_id=1, scope="full", id=5, status="generating")

        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses, modules=sample_modules,
            monitorings=sample_monitorings, metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots, activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path), base_snapshots_dir=str(tmp_path / "monitorings"),
            export_package=pkg,
        )
        with zipfile.ZipFile(result.file_path, "r") as zf:
            assert "data/export_package.json" in zf.namelist()
            pkg_data = json.loads(zf.read("data/export_package.json"))
            assert pkg_data["scope"] == "full"
            assert "file_path" not in pkg_data  # excluded for safety

    def test_manifest_has_required_metadata(self, tmp_path):
        """Manifest contains export_id, included_sections, and notes."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=[], modules=[], monitorings=[],
            metrics_by_monitoring={}, snapshots_by_monitoring={},
            activity_types=[], activity_logs=[],
            output_dir=str(tmp_path), base_snapshots_dir=str(tmp_path / "mon"),
        )
        manifest = result.manifest
        assert "export_id" in manifest
        assert "included_sections" in manifest
        assert "notes" in manifest
        assert manifest["counts"]["files_missing"] == 0

    def test_manifest_does_not_include_absolute_paths(self, tmp_path, sample_greenhouses, sample_modules, sample_monitorings, sample_metrics, sample_snapshots, sample_activity_types, sample_activity_logs):
        """Manifest text does not contain the tmp_path (absolute path)."""
        service = ExportService()
        result = service.generate_export(
            greenhouses=sample_greenhouses, modules=sample_modules,
            monitorings=sample_monitorings, metrics_by_monitoring=sample_metrics,
            snapshots_by_monitoring=sample_snapshots, activity_types=sample_activity_types,
            activity_logs=sample_activity_logs,
            output_dir=str(tmp_path), base_snapshots_dir=str(tmp_path / "monitorings"),
        )
        with zipfile.ZipFile(result.file_path, "r") as zf:
            manifest_text = zf.read("manifest.json").decode("utf-8")
            # tmp_path is unique per test — should not appear in manifest
            assert str(tmp_path) not in manifest_text
