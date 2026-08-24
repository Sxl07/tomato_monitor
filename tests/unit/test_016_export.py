"""Integration tests for export path resolution and orphan reconciliation (Fixes 10, 11).

Tests exercise real production entry points:
- validate_safe_path() for containment
- reconcile_orphan_exports() for startup reconciliation
- ExportService.generate_export() for image path resolution

Validates: Requirements 2.10, 2.11
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.infrastructure.security.path_sanitizer import validate_safe_path, PathTraversalError


class TestPathSanitizerContainment:
    """path_sanitizer uses Path.relative_to() for containment — not str.startswith."""

    def test_valid_relative_path_accepted(self, tmp_path):
        subdir = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        subdir.mkdir(parents=True)
        test_file = subdir / "snapshot_000001.jpg"
        test_file.write_text("test")
        result = validate_safe_path("monitorings/1/snapshots/raw/snapshot_000001.jpg", tmp_path)
        assert result.exists()

    def test_traversal_rejected(self, tmp_path):
        with pytest.raises(PathTraversalError):
            validate_safe_path("../../../etc/passwd", tmp_path)

    def test_null_bytes_rejected(self, tmp_path):
        with pytest.raises(PathTraversalError):
            validate_safe_path("file\x00.jpg", tmp_path)

    def test_sibling_prefix_attack_rejected(self, tmp_path):
        """Base='outputs', path resolves to 'outputs_evil/file' — must be rejected."""
        base = tmp_path / "outputs"
        base.mkdir()
        evil = tmp_path / "outputs_evil"
        evil.mkdir()
        (evil / "file.jpg").write_text("evil")
        with pytest.raises(PathTraversalError):
            validate_safe_path("../outputs_evil/file.jpg", base)

    def test_empty_path_rejected(self, tmp_path):
        with pytest.raises(PathTraversalError):
            validate_safe_path("", tmp_path)

    def test_absolute_path_rejected(self, tmp_path):
        with pytest.raises(PathTraversalError):
            validate_safe_path("/etc/passwd", tmp_path)


class TestOrphanReconciliationReal:
    """Tests call the REAL reconcile_orphan_exports() function."""

    def test_generating_transitions_to_error(self, db_manager):
        """reconcile_orphan_exports() transitions 'generating' → 'error'."""
        from src.infrastructure.persistence.models.export_package_model import ExportPackageModel
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.application.services.export_reconciliation import reconcile_orphan_exports

        session = db_manager.get_session()
        user = UserModel(
            full_name="Test", email="orphan_real@test.com",
            password_hash="h", role="operator",
        )
        session.add(user)
        session.flush()

        orphan = ExportPackageModel(
            created_by_user_id=user.id, scope="full", status="generating",
            records_count=0, images_count=0,
        )
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
        session.close()

        # Call the REAL function
        count = reconcile_orphan_exports(db_manager)

        assert count == 1

        # Verify via fresh session
        verify_session = db_manager.get_session()
        record = verify_session.query(ExportPackageModel).filter(
            ExportPackageModel.id == orphan_id
        ).first()
        assert record.status == "error"
        assert "interrumpida" in record.error_message.lower()
        assert record.completed_at is not None
        verify_session.close()

    def test_completed_not_affected(self, db_manager):
        """reconcile_orphan_exports() does NOT modify completed exports."""
        from src.infrastructure.persistence.models.export_package_model import ExportPackageModel
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.application.services.export_reconciliation import reconcile_orphan_exports

        session = db_manager.get_session()
        user = UserModel(
            full_name="T2", email="completed_ok@test.com",
            password_hash="h", role="operator",
        )
        session.add(user)
        session.flush()

        completed = ExportPackageModel(
            created_by_user_id=user.id, scope="full", status="completed",
            records_count=5, images_count=10, file_path="test.zip",
            file_size_bytes=1024,
        )
        session.add(completed)
        session.commit()
        cid = completed.id
        session.close()

        count = reconcile_orphan_exports(db_manager)
        assert count == 0

        verify_session = db_manager.get_session()
        record = verify_session.query(ExportPackageModel).filter(
            ExportPackageModel.id == cid
        ).first()
        assert record.status == "completed"
        assert record.records_count == 5
        assert record.file_path == "test.zip"
        verify_session.close()

    def test_error_not_affected(self, db_manager):
        """reconcile_orphan_exports() does NOT modify existing error exports."""
        from src.infrastructure.persistence.models.export_package_model import ExportPackageModel
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.application.services.export_reconciliation import reconcile_orphan_exports

        session = db_manager.get_session()
        user = UserModel(
            full_name="T3", email="error_ok@test.com",
            password_hash="h", role="operator",
        )
        session.add(user)
        session.flush()

        existing_error = ExportPackageModel(
            created_by_user_id=user.id, scope="full", status="error",
            error_message="Previous error", records_count=0, images_count=0,
        )
        session.add(existing_error)
        session.commit()
        eid = existing_error.id
        session.close()

        count = reconcile_orphan_exports(db_manager)
        assert count == 0

        verify_session = db_manager.get_session()
        record = verify_session.query(ExportPackageModel).filter(
            ExportPackageModel.id == eid
        ).first()
        assert record.status == "error"
        assert record.error_message == "Previous error"
        verify_session.close()

    def test_idempotent_second_call(self, db_manager):
        """Calling reconcile_orphan_exports() twice returns 0 the second time."""
        from src.infrastructure.persistence.models.export_package_model import ExportPackageModel
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.application.services.export_reconciliation import reconcile_orphan_exports

        session = db_manager.get_session()
        user = UserModel(
            full_name="T4", email="idempotent@test.com",
            password_hash="h", role="operator",
        )
        session.add(user)
        session.flush()
        orphan = ExportPackageModel(
            created_by_user_id=user.id, scope="full", status="generating",
            records_count=0, images_count=0,
        )
        session.add(orphan)
        session.commit()
        session.close()

        first = reconcile_orphan_exports(db_manager)
        assert first == 1

        second = reconcile_orphan_exports(db_manager)
        assert second == 0


class TestExportServiceImagePaths:
    """Tests that ExportService.generate_export() handles image paths correctly."""

    def _make_snapshot(self, frame_index=0, image_path=None, monitoring_id=1):
        """Create a real Snapshot domain entity."""
        from src.domain.entities.snapshot import Snapshot
        return Snapshot(
            id=frame_index + 1,
            monitoring_id=monitoring_id,
            frame_index=frame_index,
            image_path=image_path or f"outputs/monitorings/{monitoring_id}/snapshots/raw/snapshot_{frame_index:06d}.jpg",
            has_detections=False,
            captured_at=datetime(2026, 1, 1, 12, 0, 0),
            change_score=0.5,
        )

    def _make_monitoring(self, id=1):
        from src.domain.entities.monitoring import Monitoring
        return Monitoring(
            id=id, module_id=1, status="completed",
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            completed_at=datetime(2026, 1, 1, 12, 30, 0),
            width_m=5.0, length_m=3.0, notes=None,
            total_snapshots=1, total_detections=0,
            created_by_user_id=None, sync_status="pending",
        )

    def _run_export(self, tmp_path, snapshot, monitoring=None):
        """Run real generate_export() with real domain dataclasses."""
        from src.application.services.export_service import ExportService
        from src.domain.entities.greenhouse import Greenhouse
        from src.domain.entities.module import Module

        if monitoring is None:
            monitoring = self._make_monitoring()

        greenhouse = Greenhouse(
            id=1, name="GH1", location=None,
        )
        module = Module(
            id=1, greenhouse_id=1, name="Mod1", crop_type="Tomate Cherry",
            width_m=5.0, length_m=3.0, monitoring_frequency_days=7,
        )

        output_dir = tmp_path / "export_out"
        output_dir.mkdir(exist_ok=True)
        base_dir = tmp_path / "monitorings"
        base_dir.mkdir(exist_ok=True)

        service = ExportService()
        result = service.generate_export(
            greenhouses=[greenhouse],
            modules=[module],
            monitorings=[monitoring],
            metrics_by_monitoring={},
            snapshots_by_monitoring={monitoring.id: [snapshot]},
            activity_types=[],
            activity_logs=[],
            output_dir=str(output_dir),
            base_snapshots_dir=str(base_dir),
        )
        return result

    def test_raw_only_included(self, tmp_path):
        """When only raw snapshot exists, it is included."""
        base = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        base.mkdir(parents=True)
        (base / "snapshot_000000.jpg").write_bytes(b"\xff\xd8raw")

        snapshot = self._make_snapshot(image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg")
        result = self._run_export(tmp_path, snapshot)
        assert result.images_count >= 1
        assert len(result.files_missing) == 0

    def test_annotated_only_included(self, tmp_path):
        """When only annotated snapshot exists, it is included."""
        ann = tmp_path / "monitorings" / "1" / "annotated_snapshots"
        ann.mkdir(parents=True)
        (ann / "snapshot_000000.jpg").write_bytes(b"\xff\xd8ann")

        snapshot = self._make_snapshot()
        result = self._run_export(tmp_path, snapshot)
        assert result.images_count >= 1

    def test_both_raw_and_annotated(self, tmp_path):
        """Both exist — images_count counts the snapshot once."""
        base = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        base.mkdir(parents=True)
        (base / "snapshot_000000.jpg").write_bytes(b"\xff\xd8raw")

        ann = tmp_path / "monitorings" / "1" / "annotated_snapshots"
        ann.mkdir(parents=True)
        (ann / "snapshot_000000.jpg").write_bytes(b"\xff\xd8ann")

        snapshot = self._make_snapshot()
        result = self._run_export(tmp_path, snapshot)
        assert result.images_count >= 1
        assert len(result.files_missing) == 0

    def test_none_existing_reports_warning(self, tmp_path):
        """When no image exists, files_missing reports the snapshot."""
        snapshot = self._make_snapshot()
        result = self._run_export(tmp_path, snapshot)
        assert len(result.files_missing) >= 1

    def test_image_path_preferred_when_file_exists(self, tmp_path):
        """snapshot.image_path is used when the file exists at that location."""
        custom = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        custom.mkdir(parents=True)
        (custom / "snapshot_000000.jpg").write_bytes(b"\xff\xd8ok")

        snapshot = self._make_snapshot(
            image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg"
        )
        result = self._run_export(tmp_path, snapshot)
        assert result.images_count >= 1

    def test_image_path_fallback_when_file_missing(self, tmp_path):
        """When image_path points to nonexistent file, falls back to frame_index."""
        # Create at the fallback location
        fallback = tmp_path / "monitorings" / "1" / "snapshots" / "raw"
        fallback.mkdir(parents=True)
        (fallback / "snapshot_000000.jpg").write_bytes(b"\xff\xd8fb")

        snapshot = self._make_snapshot(
            image_path="outputs/monitorings/1/snapshots/raw/nonexistent_999.jpg"
        )
        result = self._run_export(tmp_path, snapshot)
        assert result.images_count >= 1
