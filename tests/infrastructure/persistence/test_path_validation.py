"""Unit tests for image path validation in SqlSnapshotRepository.

Tests the validation logic that rejects invalid image paths before database insertion.
Validates Requirements 9.1 and 9.4.
"""

import pytest

from src.domain.entities.snapshot import Snapshot
from src.domain.exceptions import InvalidImagePathError
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
from src.infrastructure.persistence.repositories.sql_snapshot_repository import (
    SqlSnapshotRepository,
)


@pytest.fixture
def db_session():
    """Provide an in-memory SQLite session with schema initialized."""
    manager = DatabaseManager(db_path=":memory:")
    manager.init_db()
    session = manager.get_session()
    yield session
    session.close()


@pytest.fixture
def monitoring_id(db_session):
    """Create parent entities and return a valid monitoring_id for snapshot tests."""
    greenhouse = GreenhouseModel(name="Test Greenhouse")
    db_session.add(greenhouse)
    db_session.flush()

    module = ModuleModel(
        greenhouse_id=greenhouse.id,
        name="Module 1",
        crop_type="Tomate Cherry",
    )
    db_session.add(module)
    db_session.flush()

    monitoring = MonitoringModel(
        module_id=module.id,
        status="running",
        width_m=5.0,
        length_m=2.0,
    )
    db_session.add(monitoring)
    db_session.flush()

    return monitoring.id


class TestValidRelativePaths:
    """Test that valid relative paths are accepted by the repository."""

    def test_typical_snapshot_path(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="outputs/monitorings/1/snapshots/snapshot_0.jpg",
            frame_index=0,
        )
        result = repo.create(monitoring_id, snapshot)
        assert result.id is not None
        assert result.image_path == "outputs/monitorings/1/snapshots/snapshot_0.jpg"

    def test_simple_relative_path(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="images/photo.jpg",
            frame_index=1,
        )
        result = repo.create(monitoring_id, snapshot)
        assert result.id is not None


class TestPathLengthValidation:
    """Test that paths exceeding 500 characters are rejected."""

    def test_path_exceeding_500_chars_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        long_path = "a" * 501
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path=long_path,
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "exceeds 500 characters"
        assert exc_info.value.path == long_path

    def test_path_exactly_500_chars_accepted(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        path_500 = "a" * 500
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path=path_500,
            frame_index=0,
        )
        result = repo.create(monitoring_id, snapshot)
        assert result.id is not None


class TestPathTraversalValidation:
    """Test that paths containing '../' are rejected."""

    def test_path_with_traversal_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="outputs/../etc/passwd",
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "contains path traversal sequence"

    def test_path_with_traversal_at_start_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="../secret/file.jpg",
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "contains path traversal sequence"


class TestAbsolutePathValidation:
    """Test that absolute paths are rejected."""

    def test_unix_absolute_path_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="/var/data/snapshot.jpg",
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "must be a relative path"

    def test_windows_drive_letter_path_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="C:\\Users\\farmer\\snapshot.jpg",
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "must be a relative path"

    def test_windows_drive_forward_slash_rejected(self, db_session, monitoring_id):
        repo = SqlSnapshotRepository(db_session)
        snapshot = Snapshot(
            monitoring_id=monitoring_id,
            image_path="D:/data/snapshot.jpg",
            frame_index=0,
        )
        with pytest.raises(InvalidImagePathError) as exc_info:
            repo.create(monitoring_id, snapshot)
        assert exc_info.value.reason == "must be a relative path"
