"""Integration tests for LocalCascadeRepository.execute_cascade (TX2).

Validates the transactional local cascade delete (Local_Cascade) against real
SQLite via DatabaseManager (PRAGMA foreign_keys=ON, ORM cascades behave like
production). Covers Req 14.3, 5.4, 5.5:

- Deleting each root type (monitoring, module, greenhouse) removes ALL
  dependent rows (zero dependents remain).
- On success the associated outbox entry becomes
  local_delete_status='completed' with deleted_at set (the passed UTC timestamp)
  within the same transaction (TX2).
- On failure during TX2 the whole transaction rolls back: the hierarchy is left
  intact AND the outbox entry stays 'prepared' with deleted_at=None.
- The cascade does NOT delete sibling entities.

All tests run offline: no network, hardware, or camera.
"""

from datetime import datetime

import pytest

from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
from src.infrastructure.persistence.models.deletion_outbox_model import (
    DeletionOutboxModel,
)
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.inspection_result_model import (
    InspectionResultModel,
)
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.monitoring_metrics_model import (
    MonitoringMetricsModel,
)
from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
from src.infrastructure.persistence.models.snapshot_model import SnapshotModel
from src.infrastructure.persistence.models.user_model import UserModel
from src.infrastructure.persistence.local_cascade_repository import (
    LocalCascadeRepository,
)


DELETED_AT = datetime(2024, 1, 2, 3, 4, 5)


@pytest.fixture
def db_manager():
    """In-memory DatabaseManager with schema created and reference data seeded.

    Uses ':memory:' which reuses a single underlying connection (SingletonThread
    pool) so all sessions created via get_session() share the same database.
    PRAGMA foreign_keys=ON is applied per connection by DatabaseManager.
    """
    manager = DatabaseManager(db_path=":memory:")
    manager.init_db()
    return manager


@pytest.fixture
def session_factory(db_manager):
    """Session factory over the shared in-memory database."""
    return db_manager.get_session


def _seed_user(session) -> int:
    """Create a user for activity log ownership; return its id."""
    user = UserModel(
        full_name="Operario Test",
        email="operario@example.com",
        password_hash="x",
        role="operator",
    )
    session.add(user)
    session.commit()
    return user.id


def _first_activity_type_id(session) -> int:
    """Return the id of a seeded activity type."""
    at = session.query(ActivityTypeModel).first()
    assert at is not None, "activity types should be seeded by init_db"
    return at.id


def _make_monitoring_with_children(session, module_id: int) -> int:
    """Create a monitoring with a snapshot (+inspection result) and metrics.

    Returns the monitoring id.
    """
    monitoring = MonitoringModel(
        module_id=module_id, status="completed", width_m=5.0, length_m=2.0
    )
    session.add(monitoring)
    session.commit()

    snapshot = SnapshotModel(
        monitoring_id=monitoring.id,
        image_path="monitorings/x/snapshots/raw/snapshot_0.jpg",
        frame_index=0,
        has_detections=True,
    )
    session.add(snapshot)
    session.commit()

    result = InspectionResultModel(
        snapshot_id=snapshot.id,
        detection_index=0,
        bbox_x1=1,
        bbox_y1=2,
        bbox_x2=3,
        bbox_y2=4,
        detection_score=0.9,
        health_label="healthy",
        health_confidence=0.95,
        maturity_stage="red",
        maturity_percent=88.0,
    )
    session.add(result)

    metrics = MonitoringMetricsModel(
        monitoring_id=monitoring.id,
        total_tomatoes=1,
        healthy_count=1,
        unhealthy_count=0,
        pct_healthy=100.0,
        pct_unhealthy=0.0,
        snapshots_with_detections=1,
    )
    session.add(metrics)
    session.commit()

    return monitoring.id


def _make_outbox(session, entity_type: str, entity_local_id: int) -> int:
    """Create a prepared outbox entry for the given root; return its id."""
    entry = DeletionOutboxModel(
        entity_type=entity_type,
        entity_local_id=entity_local_id,
        remote_table=f"{entity_type}s",
        status="pending",
        local_delete_status="prepared",
    )
    session.add(entry)
    session.commit()
    return entry.id


# --------------------------------------------------------------------------- #
# Success: dependents removed + outbox marked completed
# --------------------------------------------------------------------------- #


class TestCascadeMonitoring:
    """execute_cascade for a monitoring root."""

    def test_deletes_all_dependents_and_marks_outbox_completed(self, session_factory):
        session = session_factory()
        gh = GreenhouseModel(name="GH Mon")
        session.add(gh)
        session.commit()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod")
        session.add(module)
        session.commit()
        monitoring_id = _make_monitoring_with_children(session, module.id)
        outbox_id = _make_outbox(session, "monitoring", monitoring_id)
        module_id = module.id
        gh_id = gh.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade(
            "monitoring", monitoring_id, outbox_id, DELETED_AT
        )

        assert result.success is True
        assert result.error_message is None

        verify = session_factory()
        try:
            # Root and all dependents gone.
            assert verify.get(MonitoringModel, monitoring_id) is None
            assert (
                verify.query(SnapshotModel)
                .filter_by(monitoring_id=monitoring_id)
                .count()
                == 0
            )
            assert verify.query(InspectionResultModel).count() == 0
            assert (
                verify.query(MonitoringMetricsModel)
                .filter_by(monitoring_id=monitoring_id)
                .count()
                == 0
            )
            # Outbox marked completed with the passed timestamp (same TX2).
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "completed"
            assert entry.deleted_at == DELETED_AT
            # Parent module and greenhouse untouched.
            assert verify.get(ModuleModel, module_id) is not None
            assert verify.get(GreenhouseModel, gh_id) is not None
        finally:
            verify.close()


class TestCascadeModule:
    """execute_cascade for a module root."""

    def test_deletes_monitorings_descendants_and_activity_logs(self, session_factory):
        session = session_factory()
        user_id = _seed_user(session)
        activity_type_id = _first_activity_type_id(session)

        gh = GreenhouseModel(name="GH Module")
        session.add(gh)
        session.commit()
        module = ModuleModel(greenhouse_id=gh.id, name="Target Mod")
        session.add(module)
        session.commit()

        mon_id = _make_monitoring_with_children(session, module.id)

        # Activity log attached to the module.
        activity = ActivityLogModel(
            module_id=module.id,
            activity_type_id=activity_type_id,
            user_id=user_id,
            notes="riego",
        )
        session.add(activity)
        session.commit()

        outbox_id = _make_outbox(session, "module", module.id)
        module_id = module.id
        gh_id = gh.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade("module", module_id, outbox_id, DELETED_AT)

        assert result.success is True

        verify = session_factory()
        try:
            assert verify.get(ModuleModel, module_id) is None
            assert verify.get(MonitoringModel, mon_id) is None
            assert verify.query(SnapshotModel).count() == 0
            assert verify.query(InspectionResultModel).count() == 0
            assert verify.query(MonitoringMetricsModel).count() == 0
            assert (
                verify.query(ActivityLogModel).filter_by(module_id=module_id).count()
                == 0
            )
            # Parent greenhouse untouched.
            assert verify.get(GreenhouseModel, gh_id) is not None
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "completed"
            assert entry.deleted_at == DELETED_AT
        finally:
            verify.close()


class TestCascadeGreenhouse:
    """execute_cascade for a greenhouse root."""

    def test_deletes_all_modules_and_descendants(self, session_factory):
        session = session_factory()
        gh = GreenhouseModel(name="GH Root")
        session.add(gh)
        session.commit()
        mod_a = ModuleModel(greenhouse_id=gh.id, name="Mod A")
        mod_b = ModuleModel(greenhouse_id=gh.id, name="Mod B")
        session.add_all([mod_a, mod_b])
        session.commit()
        _make_monitoring_with_children(session, mod_a.id)
        _make_monitoring_with_children(session, mod_b.id)
        outbox_id = _make_outbox(session, "greenhouse", gh.id)
        gh_id = gh.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade("greenhouse", gh_id, outbox_id, DELETED_AT)

        assert result.success is True

        verify = session_factory()
        try:
            assert verify.get(GreenhouseModel, gh_id) is None
            assert (
                verify.query(ModuleModel).filter_by(greenhouse_id=gh_id).count() == 0
            )
            assert verify.query(MonitoringModel).count() == 0
            assert verify.query(SnapshotModel).count() == 0
            assert verify.query(InspectionResultModel).count() == 0
            assert verify.query(MonitoringMetricsModel).count() == 0
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "completed"
            assert entry.deleted_at == DELETED_AT
        finally:
            verify.close()


# --------------------------------------------------------------------------- #
# Siblings preserved
# --------------------------------------------------------------------------- #


class TestSiblingsPreserved:
    """The cascade must not delete sibling entities."""

    def test_deleting_one_module_leaves_siblings_and_parent_intact(
        self, session_factory
    ):
        session = session_factory()
        gh = GreenhouseModel(name="GH Siblings")
        session.add(gh)
        session.commit()
        target = ModuleModel(greenhouse_id=gh.id, name="Target")
        sibling = ModuleModel(greenhouse_id=gh.id, name="Sibling")
        session.add_all([target, sibling])
        session.commit()

        target_mon = _make_monitoring_with_children(session, target.id)
        sibling_mon = _make_monitoring_with_children(session, sibling.id)

        outbox_id = _make_outbox(session, "module", target.id)
        target_id = target.id
        sibling_id = sibling.id
        gh_id = gh.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade("module", target_id, outbox_id, DELETED_AT)

        assert result.success is True

        verify = session_factory()
        try:
            # Target module + its monitoring gone.
            assert verify.get(ModuleModel, target_id) is None
            assert verify.get(MonitoringModel, target_mon) is None
            # Sibling module, its monitoring, and the parent greenhouse survive.
            assert verify.get(ModuleModel, sibling_id) is not None
            assert verify.get(MonitoringModel, sibling_mon) is not None
            assert verify.get(GreenhouseModel, gh_id) is not None
            # Sibling's dependents survive.
            assert (
                verify.query(SnapshotModel)
                .filter_by(monitoring_id=sibling_mon)
                .count()
                == 1
            )
        finally:
            verify.close()


# --------------------------------------------------------------------------- #
# Failure: rollback leaves hierarchy intact and outbox not completed
# --------------------------------------------------------------------------- #


class TestRollbackOnFailure:
    """On TX2 failure, hierarchy stays intact and outbox stays prepared."""

    def test_unsupported_entity_type_does_not_touch_data(self, session_factory):
        session = session_factory()
        gh = GreenhouseModel(name="GH Bad Type")
        session.add(gh)
        session.commit()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod")
        session.add(module)
        session.commit()
        mon_id = _make_monitoring_with_children(session, module.id)
        outbox_id = _make_outbox(session, "module", module.id)
        module_id = module.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade("unknown_type", module_id, outbox_id, DELETED_AT)

        assert result.success is False
        assert result.error_message is not None

        verify = session_factory()
        try:
            assert verify.get(ModuleModel, module_id) is not None
            assert verify.get(MonitoringModel, mon_id) is not None
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "prepared"
            assert entry.deleted_at is None
        finally:
            verify.close()

    def test_missing_root_id_does_not_mark_completed(self, session_factory):
        session = session_factory()
        gh = GreenhouseModel(name="GH Missing Root")
        session.add(gh)
        session.commit()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod")
        session.add(module)
        session.commit()
        outbox_id = _make_outbox(session, "monitoring", 999999)
        module_id = module.id
        session.close()

        repo = LocalCascadeRepository(session_factory)
        result = repo.execute_cascade("monitoring", 999999, outbox_id, DELETED_AT)

        assert result.success is False
        assert result.error_message is not None

        verify = session_factory()
        try:
            # Nothing deleted.
            assert verify.get(ModuleModel, module_id) is not None
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "prepared"
            assert entry.deleted_at is None
        finally:
            verify.close()

    def test_exception_during_commit_rolls_back_whole_tx2(self, session_factory):
        session = session_factory()
        gh = GreenhouseModel(name="GH Rollback")
        session.add(gh)
        session.commit()
        module = ModuleModel(greenhouse_id=gh.id, name="Mod")
        session.add(module)
        session.commit()
        mon_id = _make_monitoring_with_children(session, module.id)
        outbox_id = _make_outbox(session, "monitoring", mon_id)
        session.close()

        # Factory that returns a session whose commit raises, forcing a TX2
        # failure AFTER delete + completed marking are staged. The repository
        # must roll back the whole transaction.
        def failing_factory():
            s = session_factory()
            original_commit = s.commit

            def boom():
                raise RuntimeError("simulated TX2 commit failure")

            s.commit = boom  # type: ignore[method-assign]
            # Keep a reference so rollback (real) still works.
            s._original_commit = original_commit  # type: ignore[attr-defined]
            return s

        repo = LocalCascadeRepository(failing_factory)
        result = repo.execute_cascade("monitoring", mon_id, outbox_id, DELETED_AT)

        assert result.success is False
        assert "simulated TX2 commit failure" in result.error_message

        verify = session_factory()
        try:
            # Hierarchy intact: monitoring and its dependents still present.
            assert verify.get(MonitoringModel, mon_id) is not None
            assert (
                verify.query(SnapshotModel).filter_by(monitoring_id=mon_id).count()
                == 1
            )
            assert verify.query(InspectionResultModel).count() == 1
            assert (
                verify.query(MonitoringMetricsModel)
                .filter_by(monitoring_id=mon_id)
                .count()
                == 1
            )
            # Outbox NOT marked completed.
            entry = verify.get(DeletionOutboxModel, outbox_id)
            assert entry.local_delete_status == "prepared"
            assert entry.deleted_at is None
        finally:
            verify.close()
