"""Targeted tests for repository recovery primitives (Spec 022, block D1).

Validates ONLY the recovery insert/lookup behavior of the SQLAlchemy
repositories, using a real file-backed SQLite DB (matching the pattern in
test_greenhouse_owner_migration.py).

Covered (representative subset: greenhouse, module, snapshot):
- find_by_remote_id finds the correct row.
- The remote UUID is stored in local.remote_id (NOT the local PK).
- The local PK stays an integer autoincrement value (int, != the UUID).
- A child insert receives the LOCAL parent id.
- A recovered insert has remote_sync_status == "synced", remote_sync_error is
  None, last_synced_at is not None (verified by reading the ORM model back).
- A second insert of the same remote_id raises RecoveredEntityAlreadyExistsError,
  does NOT create a duplicate, and does NOT modify the existing row.
- Greenhouse find_by_owner_and_name distinguishes owner A vs owner B.
- No update/merge method was added (no update_from_remote attribute).
"""

import os
import tempfile

import pytest

from src.domain.entities.activity_log import ActivityLog
from src.domain.entities.greenhouse import Greenhouse
from src.domain.entities.inspection_result import DetectionInspectionResult
from src.domain.entities.module import Module
from src.domain.entities.monitoring import Monitoring
from src.domain.entities.monitoring_metrics import MonitoringMetrics
from src.domain.entities.snapshot import Snapshot
from src.domain.exceptions import RecoveredEntityAlreadyExistsError
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
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
from src.infrastructure.persistence.repositories.sql_activity_log_repository import (
    SqlActivityLogRepository,
)
from src.infrastructure.persistence.repositories.sql_greenhouse_repository import (
    SqlGreenhouseRepository,
)
from src.infrastructure.persistence.repositories.sql_inspection_result_repository import (
    SqlInspectionResultRepository,
)
from src.infrastructure.persistence.repositories.sql_module_repository import (
    SqlModuleRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_metrics_repository import (
    SqlMonitoringMetricsRepository,
)
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)
from src.infrastructure.persistence.repositories.sql_snapshot_repository import (
    SqlSnapshotRepository,
)

_GH_REMOTE = "aaaaaaaa-1111-4222-8333-444444444444"
_MOD_REMOTE = "bbbbbbbb-1111-4222-8333-444444444444"
_SNAP_REMOTE = "cccccccc-1111-4222-8333-444444444444"
_MON_REMOTE = "dddddddd-1111-4222-8333-444444444444"
_METRICS_REMOTE = "eeeeeeee-1111-4222-8333-444444444444"
_INSP_REMOTE = "ffffffff-1111-4222-8333-444444444444"
_ACT_REMOTE = "11111111-1111-4222-8333-444444444444"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_manager():
    """Create a DatabaseManager backed by a temporary file DB and init it."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(db_path=db_path)
    manager.init_db()
    return manager, db_path


def _cleanup(engine, db_path):
    """Dispose the engine and remove the temp DB (Windows-safe)."""
    try:
        if engine is not None:
            engine.dispose()
    except Exception:
        pass
    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _make_user(session, email: str) -> int:
    user = UserModel(full_name="Operario", email=email, password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


def _assert_fresh_session_durability(
    manager, model_cls, local_id, remote_id, repo_cls
):
    """Prove the recovered row is durable WITHOUT the test committing.

    Opens a BRAND NEW session from the SAME manager (no external commit was
    issued by the caller) and confirms the row is readable both via the ORM
    model (get by local id) and via a fresh repo's find_by_remote_id.
    """
    fresh = manager.get_session()
    try:
        model = fresh.get(model_cls, local_id)
        assert model is not None, "row not durable in a fresh session"
        assert model.remote_id == remote_id

        fresh_repo = repo_cls(fresh)
        found = fresh_repo.find_by_remote_id(remote_id)
        assert found is not None
        assert found.id == local_id
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------


class TestGreenhouseRecovery:
    def test_insert_and_find_by_remote_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                repo = SqlGreenhouseRepository(session)

                created = repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_id, location="Zona A"),
                    remote_id=_GH_REMOTE,
                )

                # Local PK is an int, not the remote UUID.
                assert isinstance(created.id, int)
                assert created.id != _GH_REMOTE

                found = repo.find_by_remote_id(_GH_REMOTE)
                assert found is not None
                assert found.id == created.id
                assert found.name == "USB"

                # The remote UUID is stored in the ORM remote_id column.
                model = session.get(GreenhouseModel, created.id)
                assert model.remote_id == _GH_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                # Durability: readable from a fresh session with NO external commit.
                _assert_fresh_session_durability(
                    manager,
                    GreenhouseModel,
                    created.id,
                    _GH_REMOTE,
                    SqlGreenhouseRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises_and_does_not_modify(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                repo = SqlGreenhouseRepository(session)

                created = repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_id),
                    remote_id=_GH_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        Greenhouse(name="OTHER", owner_user_id=owner_id),
                        remote_id=_GH_REMOTE,
                    )
                session.rollback()

                # No duplicate row, and the original row is untouched.
                rows = (
                    session.query(GreenhouseModel)
                    .filter(GreenhouseModel.remote_id == _GH_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].id == created.id
                assert rows[0].name == "USB"
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_find_by_owner_and_name_distinguishes_owners(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_a = _make_user(session, "a@example.com")
                owner_b = _make_user(session, "b@example.com")
                repo = SqlGreenhouseRepository(session)

                gh_a = repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_a),
                    remote_id=_GH_REMOTE,
                )
                gh_b = repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_b),
                    remote_id=_MOD_REMOTE,  # any distinct remote id
                )

                found_a = repo.find_by_owner_and_name(owner_a, "USB")
                found_b = repo.find_by_owner_and_name(owner_b, "USB")

                assert found_a is not None and found_b is not None
                assert found_a.id == gh_a.id
                assert found_b.id == gh_b.id
                assert found_a.id != found_b.id
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_no_update_or_merge_method(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                repo = SqlGreenhouseRepository(session)
                assert not hasattr(repo, "update_from_remote")
                assert not hasattr(repo, "upsert_from_remote")
                assert not hasattr(repo, "merge_from_remote")
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# Module (child receives LOCAL parent id)
# ---------------------------------------------------------------------------


class TestModuleRecovery:
    def test_child_insert_uses_local_parent_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                gh_repo = SqlGreenhouseRepository(session)
                mod_repo = SqlModuleRepository(session)

                greenhouse = gh_repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_id),
                    remote_id=_GH_REMOTE,
                )

                module = mod_repo.insert_preserving_remote_id(
                    greenhouse.id,
                    Module(greenhouse_id=greenhouse.id, name="Modulo 1"),
                    remote_id=_MOD_REMOTE,
                )

                # The child row references the LOCAL greenhouse id, not a UUID.
                assert module.greenhouse_id == greenhouse.id
                assert isinstance(module.id, int)

                model = session.get(ModuleModel, module.id)
                assert model.greenhouse_id == greenhouse.id
                assert model.remote_id == _MOD_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                # Durability: readable from a fresh session with NO external commit.
                _assert_fresh_session_durability(
                    manager,
                    ModuleModel,
                    module.id,
                    _MOD_REMOTE,
                    SqlModuleRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                gh_repo = SqlGreenhouseRepository(session)
                mod_repo = SqlModuleRepository(session)

                greenhouse = gh_repo.insert_preserving_remote_id(
                    Greenhouse(name="USB", owner_user_id=owner_id),
                    remote_id=_GH_REMOTE,
                )
                mod_repo.insert_preserving_remote_id(
                    greenhouse.id,
                    Module(greenhouse_id=greenhouse.id, name="Modulo 1"),
                    remote_id=_MOD_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    mod_repo.insert_preserving_remote_id(
                        greenhouse.id,
                        Module(greenhouse_id=greenhouse.id, name="Modulo 2"),
                        remote_id=_MOD_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(ModuleModel)
                    .filter(ModuleModel.remote_id == _MOD_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].name == "Modulo 1"
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# Snapshot (storage paths + local parent id)
# ---------------------------------------------------------------------------


class TestSnapshotRecovery:
    def _seed_monitoring(self, session, owner_id: int) -> int:
        """Create a greenhouse, module, and monitoring; return monitoring id."""
        gh_repo = SqlGreenhouseRepository(session)
        mod_repo = SqlModuleRepository(session)
        greenhouse = gh_repo.insert_preserving_remote_id(
            Greenhouse(name="USB", owner_user_id=owner_id),
            remote_id=_GH_REMOTE,
        )
        module = mod_repo.insert_preserving_remote_id(
            greenhouse.id,
            Module(greenhouse_id=greenhouse.id, name="Modulo 1"),
            remote_id=_MOD_REMOTE,
        )
        monitoring = MonitoringModel(module_id=module.id, status="completed")
        session.add(monitoring)
        session.commit()
        session.refresh(monitoring)
        return monitoring.id

    def test_insert_records_storage_paths_and_local_parent(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                monitoring_id = self._seed_monitoring(session, owner_id)
                repo = SqlSnapshotRepository(session)

                snapshot = repo.insert_preserving_remote_id(
                    monitoring_id,
                    Snapshot(
                        monitoring_id=monitoring_id,
                        image_path="monitorings/1/snapshots/raw/snap_0.jpg",
                        frame_index=0,
                        has_detections=True,
                    ),
                    remote_id=_SNAP_REMOTE,
                    raw_storage_path="remote/raw/snap_0.jpg",
                    annotated_storage_path="remote/annotated/snap_0.jpg",
                )

                assert snapshot.monitoring_id == monitoring_id
                assert isinstance(snapshot.id, int)
                assert snapshot.id != _SNAP_REMOTE

                found = repo.find_by_remote_id(_SNAP_REMOTE)
                assert found is not None and found.id == snapshot.id

                model = session.get(SnapshotModel, snapshot.id)
                assert model.monitoring_id == monitoring_id
                assert model.remote_id == _SNAP_REMOTE
                assert model.raw_storage_path == "remote/raw/snap_0.jpg"
                assert model.annotated_storage_path == "remote/annotated/snap_0.jpg"
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                # Durability: readable from a fresh session with NO external commit.
                _assert_fresh_session_durability(
                    manager,
                    SnapshotModel,
                    snapshot.id,
                    _SNAP_REMOTE,
                    SqlSnapshotRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                monitoring_id = self._seed_monitoring(session, owner_id)
                repo = SqlSnapshotRepository(session)

                repo.insert_preserving_remote_id(
                    monitoring_id,
                    Snapshot(
                        monitoring_id=monitoring_id,
                        image_path="monitorings/1/snapshots/raw/snap_0.jpg",
                        frame_index=0,
                    ),
                    remote_id=_SNAP_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        monitoring_id,
                        Snapshot(
                            monitoring_id=monitoring_id,
                            image_path="monitorings/1/snapshots/raw/snap_1.jpg",
                            frame_index=1,
                        ),
                        remote_id=_SNAP_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(SnapshotModel)
                    .filter(SnapshotModel.remote_id == _SNAP_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].frame_index == 0
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# Shared seeding helpers for the parent chain (greenhouse -> module [-> ...])
# ---------------------------------------------------------------------------


def _seed_greenhouse_module(session, owner_id: int) -> int:
    """Create a greenhouse + module via recovery inserts; return LOCAL module id."""
    gh_repo = SqlGreenhouseRepository(session)
    mod_repo = SqlModuleRepository(session)
    greenhouse = gh_repo.insert_preserving_remote_id(
        Greenhouse(name="USB", owner_user_id=owner_id),
        remote_id=_GH_REMOTE,
    )
    module = mod_repo.insert_preserving_remote_id(
        greenhouse.id,
        Module(greenhouse_id=greenhouse.id, name="Modulo 1"),
        remote_id=_MOD_REMOTE,
    )
    return module.id


def _seed_monitoring_row(session, module_id: int, status: str = "completed") -> int:
    """Create a monitoring row directly via ORM; return its LOCAL id."""
    monitoring = MonitoringModel(module_id=module_id, status=status)
    session.add(monitoring)
    session.commit()
    session.refresh(monitoring)
    return monitoring.id


def _seed_snapshot_row(session, monitoring_id: int, frame_index: int = 0) -> int:
    """Create a snapshot row directly via ORM; return its LOCAL id."""
    snapshot = SnapshotModel(
        monitoring_id=monitoring_id,
        image_path=f"monitorings/{monitoring_id}/snapshots/raw/snap_{frame_index}.jpg",
        frame_index=frame_index,
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot.id


# ---------------------------------------------------------------------------
# Monitoring (child receives LOCAL module id; created_by stays a LOCAL user id)
# ---------------------------------------------------------------------------


class TestMonitoringRecovery:
    def test_insert_and_find_by_remote_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                repo = SqlMonitoringRepository(session)

                created = repo.insert_preserving_remote_id(
                    module_id,
                    Monitoring(
                        module_id=module_id,
                        status="completed",
                        created_by_user_id=owner_id,
                        width_m=5.0,
                        length_m=2.0,
                    ),
                    remote_id=_MON_REMOTE,
                )

                # Local PK is an int, distinct from the remote UUID.
                assert isinstance(created.id, int)
                assert created.id != _MON_REMOTE
                # Parent FK uses the LOCAL module id.
                assert created.module_id == module_id
                # created_by stays the LOCAL user id (int), never a remote UUID.
                assert created.created_by_user_id == owner_id

                found = repo.find_by_remote_id(_MON_REMOTE)
                assert found is not None and found.id == created.id

                model = session.get(MonitoringModel, created.id)
                assert model.module_id == module_id
                assert model.created_by_user_id == owner_id
                assert model.remote_id == _MON_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                _assert_fresh_session_durability(
                    manager,
                    MonitoringModel,
                    created.id,
                    _MON_REMOTE,
                    SqlMonitoringRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                repo = SqlMonitoringRepository(session)

                created = repo.insert_preserving_remote_id(
                    module_id,
                    Monitoring(
                        module_id=module_id,
                        status="completed",
                        created_by_user_id=owner_id,
                    ),
                    remote_id=_MON_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        module_id,
                        Monitoring(
                            module_id=module_id,
                            status="completed",
                            created_by_user_id=owner_id,
                        ),
                        remote_id=_MON_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(MonitoringModel)
                    .filter(MonitoringModel.remote_id == _MON_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].id == created.id
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# MonitoringMetrics (1:1 with monitoring; previously flush-only, now commits)
# ---------------------------------------------------------------------------


def _make_metrics(monitoring_id: int) -> MonitoringMetrics:
    return MonitoringMetrics(
        monitoring_id=monitoring_id,
        total_tomatoes=10,
        healthy_count=8,
        unhealthy_count=2,
        pct_healthy=80.0,
        pct_unhealthy=20.0,
        snapshots_with_detections=3,
        pct_red=50.0,
    )


class TestMonitoringMetricsRecovery:
    def test_insert_and_find_by_remote_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                # Recovery does not validate monitoring status; any status works.
                monitoring_id = _seed_monitoring_row(
                    session, module_id, status="running"
                )
                repo = SqlMonitoringMetricsRepository(session)

                created = repo.insert_preserving_remote_id(
                    monitoring_id,
                    _make_metrics(monitoring_id),
                    remote_id=_METRICS_REMOTE,
                )

                assert isinstance(created.id, int)
                assert created.id != _METRICS_REMOTE
                # Parent FK uses the LOCAL monitoring id.
                assert created.monitoring_id == monitoring_id

                found = repo.find_by_remote_id(_METRICS_REMOTE)
                assert found is not None and found.id == created.id

                model = session.get(MonitoringMetricsModel, created.id)
                assert model.monitoring_id == monitoring_id
                assert model.remote_id == _METRICS_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                _assert_fresh_session_durability(
                    manager,
                    MonitoringMetricsModel,
                    created.id,
                    _METRICS_REMOTE,
                    SqlMonitoringMetricsRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises_and_commits_first_row(self):
        """Proves the commit + duplicate-guard path for a previously flush-only type.

        The first recovered insert commits by itself (no external commit here),
        the duplicate raises, and exactly one row survives after rollback.
        """
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                # Distinct monitorings honor the 1:1 unique on monitoring_id.
                mon_a = _seed_monitoring_row(session, module_id, status="completed")
                mon_b = _seed_monitoring_row(session, module_id, status="completed")
                repo = SqlMonitoringMetricsRepository(session)

                created = repo.insert_preserving_remote_id(
                    mon_a,
                    _make_metrics(mon_a),
                    remote_id=_METRICS_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        mon_b,
                        _make_metrics(mon_b),
                        remote_id=_METRICS_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(MonitoringMetricsModel)
                    .filter(MonitoringMetricsModel.remote_id == _METRICS_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].id == created.id
                assert rows[0].monitoring_id == mon_a
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# InspectionResult (child receives LOCAL snapshot id; previously flush-only)
# ---------------------------------------------------------------------------


def _make_inspection(snapshot_id: int, detection_index: int = 0) -> DetectionInspectionResult:
    return DetectionInspectionResult(
        snapshot_id=snapshot_id,
        detection_index=detection_index,
        bbox_x1=10,
        bbox_y1=20,
        bbox_x2=30,
        bbox_y2=40,
        detection_score=0.95,
        health_label="healthy",
        health_confidence=0.9,
        maturity_stage="red",
        maturity_percent=88.0,
    )


class TestInspectionResultRecovery:
    def test_insert_and_find_by_remote_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                monitoring_id = _seed_monitoring_row(session, module_id)
                snapshot_id = _seed_snapshot_row(session, monitoring_id)
                repo = SqlInspectionResultRepository(session)

                created = repo.insert_preserving_remote_id(
                    snapshot_id,
                    _make_inspection(snapshot_id),
                    remote_id=_INSP_REMOTE,
                )

                assert isinstance(created.id, int)
                assert created.id != _INSP_REMOTE
                # Parent FK uses the LOCAL snapshot id.
                assert created.snapshot_id == snapshot_id

                found = repo.find_by_remote_id(_INSP_REMOTE)
                assert found is not None and found.id == created.id

                model = session.get(InspectionResultModel, created.id)
                assert model.snapshot_id == snapshot_id
                assert model.remote_id == _INSP_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                _assert_fresh_session_durability(
                    manager,
                    InspectionResultModel,
                    created.id,
                    _INSP_REMOTE,
                    SqlInspectionResultRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises_and_commits_first_row(self):
        """Proves the commit + duplicate-guard path for a previously flush-only type."""
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                monitoring_id = _seed_monitoring_row(session, module_id)
                snapshot_id = _seed_snapshot_row(session, monitoring_id)
                repo = SqlInspectionResultRepository(session)

                created = repo.insert_preserving_remote_id(
                    snapshot_id,
                    _make_inspection(snapshot_id, detection_index=0),
                    remote_id=_INSP_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        snapshot_id,
                        _make_inspection(snapshot_id, detection_index=1),
                        remote_id=_INSP_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(InspectionResultModel)
                    .filter(InspectionResultModel.remote_id == _INSP_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].id == created.id
                assert rows[0].detection_index == 0
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# ActivityLog (LOCAL module/user/activity_type ids)
# ---------------------------------------------------------------------------


class TestActivityLogRecovery:
    def test_insert_and_find_by_remote_id(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                # init_db() seeds the global activity_types catalog; use a real
                # LOCAL activity_type id as the FK (do NOT map a remote code).
                activity_type_id = session.query(ActivityTypeModel).first().id
                repo = SqlActivityLogRepository(session)

                created = repo.insert_preserving_remote_id(
                    ActivityLog(
                        module_id=module_id,
                        activity_type_id=activity_type_id,
                        user_id=owner_id,
                        notes="Recuperado",
                    ),
                    remote_id=_ACT_REMOTE,
                )

                assert isinstance(created.id, int)
                assert created.id != _ACT_REMOTE
                # Parent FKs use LOCAL ids.
                assert created.module_id == module_id
                assert created.user_id == owner_id
                assert created.activity_type_id == activity_type_id

                found = repo.find_by_remote_id(_ACT_REMOTE)
                assert found is not None and found.id == created.id

                model = session.get(ActivityLogModel, created.id)
                assert model.module_id == module_id
                assert model.user_id == owner_id
                assert model.activity_type_id == activity_type_id
                assert model.remote_id == _ACT_REMOTE
                assert model.remote_sync_status == "synced"
                assert model.remote_sync_error is None
                assert model.last_synced_at is not None

                _assert_fresh_session_durability(
                    manager,
                    ActivityLogModel,
                    created.id,
                    _ACT_REMOTE,
                    SqlActivityLogRepository,
                )
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_duplicate_remote_id_raises(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                owner_id = _make_user(session, "a@example.com")
                module_id = _seed_greenhouse_module(session, owner_id)
                activity_type_id = session.query(ActivityTypeModel).first().id
                repo = SqlActivityLogRepository(session)

                created = repo.insert_preserving_remote_id(
                    ActivityLog(
                        module_id=module_id,
                        activity_type_id=activity_type_id,
                        user_id=owner_id,
                        notes="Primero",
                    ),
                    remote_id=_ACT_REMOTE,
                )

                with pytest.raises(RecoveredEntityAlreadyExistsError):
                    repo.insert_preserving_remote_id(
                        ActivityLog(
                            module_id=module_id,
                            activity_type_id=activity_type_id,
                            user_id=owner_id,
                            notes="Duplicado",
                        ),
                        remote_id=_ACT_REMOTE,
                    )
                session.rollback()

                rows = (
                    session.query(ActivityLogModel)
                    .filter(ActivityLogModel.remote_id == _ACT_REMOTE)
                    .all()
                )
                assert len(rows) == 1
                assert rows[0].id == created.id
                assert rows[0].notes == "Primero"
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)
