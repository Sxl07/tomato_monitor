"""Tests for RecoveryService (Spec 022, block D2).

Metadata-only hierarchical recovery from remote rows into local SQLite.

These tests use a real file-backed SQLite DB via DatabaseManager (matching the
pattern in tests/infrastructure/persistence/test_recovery_primitives.py), the
real Sql* repositories, and the real SyncStateRepository. A FakeRemoteReadPort
returns queued RemoteQueryResult objects per table and can force a failure on a
given table.
"""

import os
import tempfile

from src.application.interfaces.recovery_tombstone_port import TombstoneCheckResult
from src.application.interfaces.remote_read_port import RemoteQueryResult
from src.application.services.deletion_service import ENTITY_TYPE_TO_REMOTE_TABLE
from src.application.services.recovery_service import RecoveryService
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.recovery_tombstone_adapter import (
    SqlRecoveryTombstoneAdapter,
)
from src.infrastructure.persistence.recovery_unit_of_work import SqlRecoveryUnitOfWork
from src.infrastructure.persistence.models.deletion_outbox_model import (
    DeletionOutboxModel,
)
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.module_model import ModuleModel
from src.infrastructure.persistence.models.user_model import UserModel
from src.infrastructure.persistence.repositories.sql_activity_log_repository import (
    SqlActivityLogRepository,
)
from src.infrastructure.persistence.repositories.sql_activity_type_repository import (
    SqlActivityTypeRepository,
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
from src.infrastructure.persistence.sync_state_repository import SyncStateRepository


# ---------------------------------------------------------------------------
# Remote UUID constants
# ---------------------------------------------------------------------------

REMOTE_USER = "99999999-1111-4222-8333-444444444444"
GH_ID = "aaaaaaaa-1111-4222-8333-444444444444"
MOD_ID = "bbbbbbbb-1111-4222-8333-444444444444"
MON_ID = "dddddddd-1111-4222-8333-444444444444"
METRICS_ID = "eeeeeeee-1111-4222-8333-444444444444"
SNAP_ID = "cccccccc-1111-4222-8333-444444444444"
INSP_ID = "ffffffff-1111-4222-8333-444444444444"
ACT_ID = "11111111-1111-4222-8333-444444444444"


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


def _fresh_manager():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(db_path=db_path)
    manager.init_db()
    return manager, db_path


def _cleanup(engine, db_path):
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


def _make_user(session, email: str, remote_user_id) -> int:
    user = UserModel(
        full_name="Operario",
        email=email,
        password_hash="x",
        remote_user_id=remote_user_id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id


class FakeRemoteReadPort:
    """Returns queued RemoteQueryResult per table; can force a failure."""

    def __init__(self, rows_by_table=None, fail_table=None, fail_error_type="CONNECTIVITY"):
        self._rows_by_table = rows_by_table or {}
        self._fail_table = fail_table
        self._fail_error_type = fail_error_type
        self.calls = []

    def fetch_by_owner(self, access_token, table, owner_user_id, filters=None):
        self.calls.append(table)
        if table == self._fail_table:
            return RemoteQueryResult(
                success=False,
                error_type=self._fail_error_type,
                error_message="forced failure",
            )
        return RemoteQueryResult(success=True, rows=list(self._rows_by_table.get(table, [])))


class _NullSession:
    """No-op stand-in so existing tests can still call ``session.close()``.

    The refactored RecoveryService no longer holds a long-lived session; it
    opens a fresh per-row unit-of-work internally. The old harness returned a
    session that tests closed after ``execute_recovery``; this keeps that shape
    without holding any DB resource.
    """

    def close(self):
        return None


def _uow_factory(manager):
    """Return a callable producing a fresh SqlRecoveryUnitOfWork per row."""
    def factory():
        return SqlRecoveryUnitOfWork(manager.get_session)

    return factory


def _build_service(
    manager,
    remote_port,
    tombstone_guard=None,
    remote_download=None,
    recovery_files=None,
):
    """Build a RecoveryService wired to a real per-row UoW factory + sync state.

    By default the real ``SqlRecoveryTombstoneAdapter`` is used over the real
    (empty) deletion_outbox: with nothing seeded, no tombstone blocks, so all
    metadata-only D2 tests keep passing unchanged. Tests that need controlled
    outcomes pass a ``FakeTombstoneGuard``.

    D3.2: ``remote_download`` and ``recovery_files`` default to inert fakes (no
    file present, downloads report NOT_FOUND) so metadata-only tests that do not
    care about images are unaffected. Image-focused tests inject configured
    fakes.
    """
    sync_state = SyncStateRepository(session_factory=manager.get_session)
    guard = tombstone_guard or SqlRecoveryTombstoneAdapter(manager.get_session)
    return (
        RecoveryService(
            remote_read=remote_port,
            uow_factory=_uow_factory(manager),
            sync_state=sync_state,
            tombstone_guard=guard,
            remote_download=remote_download or FakeRemoteDownloadPort(),
            recovery_files=recovery_files or InMemoryRecoveryFiles(),
        ),
        _NullSession(),
    )


def _seed_tombstone(
    manager, entity_type, remote_id, status="pending", entity_local_id=1
):
    """Insert a durable deletion_outbox tombstone directly via a session.

    ``remote_table`` is derived from ENTITY_TYPE_TO_REMOTE_TABLE. ``remote_id``
    may be None (to prove a NULL remote_id never blocks a valid UUID). The
    local delete is marked completed and cleanup pending, matching a real
    post-cascade tombstone.
    """
    session = manager.get_session()
    try:
        session.add(
            DeletionOutboxModel(
                entity_type=entity_type,
                entity_local_id=entity_local_id,
                remote_table=ENTITY_TYPE_TO_REMOTE_TABLE[entity_type],
                remote_id=remote_id,
                status=status,
                local_delete_status="completed",
                cleanup_status="pending",
            )
        )
        session.commit()
    finally:
        session.close()


class FakeTombstoneGuard:
    """In-memory RecoveryTombstonePort for check-failure / injected-block tests.

    Configured per (entity_type, remote_id) identity. Default response is
    ``success=True, blocked=False``. Identities added via ``force_failure`` yield
    ``success=False`` (lookup could not be determined); identities added via
    ``force_blocked`` yield ``success=True, blocked=True`` with a given status.
    """

    def __init__(self):
        self._failures: set[tuple[str, str]] = set()
        self._blocked: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str]] = []

    def force_failure(self, entity_type, remote_id):
        self._failures.add((entity_type, remote_id))
        return self

    def force_blocked(self, entity_type, remote_id, status="pending"):
        self._blocked[(entity_type, remote_id)] = status
        return self

    def check(self, entity_type, remote_id):
        self.calls.append((entity_type, remote_id))
        key = (entity_type, remote_id)
        if key in self._failures:
            return TombstoneCheckResult(
                success=False, blocked=False, error_message="forced failure"
            )
        if key in self._blocked:
            return TombstoneCheckResult(
                success=True, blocked=True, status=self._blocked[key]
            )
        return TombstoneCheckResult(success=True, blocked=False)


# ---------------------------------------------------------------------------
# D3.2 image-download fakes
# ---------------------------------------------------------------------------


from src.application.interfaces.recovery_file_port import RecoveryFileResult
from src.application.interfaces.remote_download_port import RemoteDownloadResult


class FakeRemoteDownloadPort:
    """Configurable in-memory RemoteDownloadPort.

    By default every download reports NOT_FOUND. Map exact remote paths to a
    ``RemoteDownloadResult`` via ``set_result``. Records every requested path in
    ``calls``. Optionally records a boolean per call via ``active_probe`` (used
    to assert no local UoW is open during HTTP).
    """

    def __init__(self, active_probe=None):
        self._results: dict[str, RemoteDownloadResult] = {}
        self.calls: list[str] = []
        self.uow_active_flags: list[bool] = []
        self._active_probe = active_probe

    def set_result(self, remote_path, result):
        self._results[remote_path] = result
        return self

    def set_bytes(self, remote_path, content):
        return self.set_result(
            remote_path, RemoteDownloadResult(success=True, content=content)
        )

    def set_error(self, remote_path, error_type):
        return self.set_result(
            remote_path,
            RemoteDownloadResult(success=False, error_type=error_type),
        )

    def download_object(self, access_token, remote_path):
        self.calls.append(remote_path)
        if self._active_probe is not None:
            self.uow_active_flags.append(bool(self._active_probe()))
        return self._results.get(
            remote_path,
            RemoteDownloadResult(success=False, error_type="NOT_FOUND"),
        )


class InMemoryRecoveryFiles:
    """In-memory RecoveryFilePort. Stores written bytes by relative path.

    ``preexisting`` seeds relative paths that already exist locally. ``fail``
    forces write_atomic to fail for specific relative paths (to exercise the
    IMAGE_WRITE_FAILED branch).
    """

    def __init__(self, preexisting=None, fail=None):
        self.stored: dict[str, bytes] = dict(preexisting or {})
        self._fail: set[str] = set(fail or set())
        self.write_calls: list[str] = []

    def exists(self, relative_path):
        return relative_path in self.stored

    def write_atomic(self, relative_path, content):
        self.write_calls.append(relative_path)
        if relative_path in self._fail:
            return RecoveryFileResult(success=False, error_message="forced")
        if relative_path in self.stored:
            return RecoveryFileResult(success=True, already_exists=True)
        self.stored[relative_path] = bytes(content)
        return RecoveryFileResult(success=True)


# Deterministic remote/local path helpers mirroring the production contract.
def _remote_raw(mon_uuid, frame_index=0):
    return f"monitorings/{mon_uuid}/raw/snapshot_{frame_index:06d}.jpg"


def _remote_annotated(mon_uuid, frame_index=0):
    return f"monitorings/{mon_uuid}/annotated/snapshot_{frame_index:06d}.jpg"


def _local_raw(local_mon_id, frame_index=0):
    return (
        f"monitorings/{local_mon_id}/snapshots/raw/"
        f"snapshot_{frame_index:06d}.jpg"
    )


def _local_annotated(local_mon_id, frame_index=0):
    return (
        f"monitorings/{local_mon_id}/annotated_snapshots/"
        f"snapshot_{frame_index:06d}.jpg"
    )


# ---------------------------------------------------------------------------
# Full hierarchy row builders
# ---------------------------------------------------------------------------


def _full_hierarchy_rows(owner=REMOTE_USER, creator=REMOTE_USER):
    return {
        "greenhouses": [
            {
                "id": GH_ID,
                "name": "Invernadero 1",
                "location": "Zona A",
                "created_at": "2023-01-01T10:00:00Z",
                "updated_at": "2023-01-02T11:00:00Z",
                "owner_user_id": owner,
            }
        ],
        "modules": [
            {
                "id": MOD_ID,
                "greenhouse_id": GH_ID,
                "name": "Modulo 1",
                "crop_type": "Tomate Cherry",
                "width_m": 5.0,
                "length_m": 2.0,
                "monitoring_frequency_days": 7,
                "created_at": "2023-01-03T10:00:00Z",
                "updated_at": "2023-01-04T11:00:00Z",
            }
        ],
        "monitorings": [
            {
                "id": MON_ID,
                "module_id": MOD_ID,
                "status": "completed",
                "started_at": "2023-02-01T08:00:00Z",
                "completed_at": "2023-02-01T09:00:00Z",
                "width_m": 5.0,
                "length_m": 2.0,
                "notes": "n",
                "total_snapshots": 1,
                "total_detections": 3,
                "created_by_user_id": creator,
            }
        ],
        "monitoring_metrics": [
            {
                "id": METRICS_ID,
                "monitoring_id": MON_ID,
                "total_tomatoes": 3,
                "healthy_count": 2,
                "unhealthy_count": 1,
                "pct_healthy": 66.6,
                "pct_unhealthy": 33.3,
                "pct_red": 50.0,
                "snapshots_with_detections": 1,
                "computed_at": "2023-02-01T09:05:00Z",
            }
        ],
        "snapshots": [
            {
                "id": SNAP_ID,
                "monitoring_id": MON_ID,
                "local_image_path": "monitorings/1/snapshots/raw/snap_0.jpg",
                "raw_storage_path": "remote/raw/snap_0.jpg",
                "annotated_storage_path": "remote/annotated/snap_0.jpg",
                "captured_at": "2023-02-01T08:30:00Z",
                "frame_index": 0,
                "change_score": 0.9,
                "has_detections": True,
            }
        ],
        "inspection_results": [
            {
                "id": INSP_ID,
                "snapshot_id": SNAP_ID,
                "detection_index": 0,
                "bbox_x1": 10,
                "bbox_y1": 20,
                "bbox_x2": 30,
                "bbox_y2": 40,
                "detection_score": 0.95,
                "health_label": "healthy",
                "health_confidence": 0.9,
                "maturity_stage": "red",
                "maturity_percent": 88.0,
                "created_at": "2023-02-01T09:01:00Z",
            }
        ],
        "activity_logs": [
            {
                "id": ACT_ID,
                "module_id": MOD_ID,
                "user_id": REMOTE_USER,
                "activity_type_code": "riego",
                "product_name": None,
                "quantity": 10.0,
                "unit": "L",
                "notes": "riego matinal",
                "occurred_at": "2023-02-02T07:00:00Z",
                "created_at": "2023-02-02T07:05:00Z",
            }
        ],
    }


# ---------------------------------------------------------------------------
# (1) Full hierarchy import into empty DB
# ---------------------------------------------------------------------------


def test_full_hierarchy_imports_all_with_local_pks_and_preserved_uuids():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 7
        assert result.entities_reused == 0
        assert result.conflicts == 0
        assert result.entities_skipped == 0

        verify = manager.get_session()
        try:
            gh_repo = SqlGreenhouseRepository(verify)
            mod_repo = SqlModuleRepository(verify)
            mon_repo = SqlMonitoringRepository(verify)
            snap_repo = SqlSnapshotRepository(verify)

            gh = gh_repo.find_by_remote_id(GH_ID)
            assert gh is not None and isinstance(gh.id, int)
            assert gh.owner_user_id == local_user

            mod = mod_repo.find_by_remote_id(MOD_ID)
            assert mod is not None and mod.greenhouse_id == gh.id

            mon = mon_repo.find_by_remote_id(MON_ID)
            assert mon is not None and mon.module_id == mod.id
            assert mon.created_by_user_id == local_user

            snap = snap_repo.find_by_remote_id(SNAP_ID)
            assert snap is not None and snap.monitoring_id == mon.id
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (2) Second run: 0 new inserts, all reused
# ---------------------------------------------------------------------------


def test_second_run_reuses_everything_no_new_inserts():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()

        port1 = FakeRemoteReadPort(rows)
        service1, s1 = _build_service(manager, port1)
        try:
            first = service1.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s1.close()
        assert first.entities_recovered == 7

        port2 = FakeRemoteReadPort(rows)
        service2, s2 = _build_service(manager, port2)
        try:
            second = service2.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s2.close()

        assert second.success is True
        assert second.entities_recovered == 0
        assert second.entities_reused == 7
        assert second.conflicts == 0

        verify = manager.get_session()
        try:
            count = verify.query(GreenhouseModel).count()
            assert count == 1
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (3) Same remote_id, different remote field -> reuse, local preserved
# ---------------------------------------------------------------------------


def test_existing_remote_id_with_changed_field_reuses_and_preserves_local():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        port1 = FakeRemoteReadPort({"greenhouses": rows["greenhouses"]})
        service1, s1 = _build_service(manager, port1)
        try:
            service1.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s1.close()

        # Second run: same remote_id but a different name/location.
        changed = dict(rows["greenhouses"][0])
        changed["name"] = "Renamed"
        changed["location"] = "Zona Z"
        port2 = FakeRemoteReadPort({"greenhouses": [changed]})
        service2, s2 = _build_service(manager, port2)
        try:
            result = service2.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s2.close()

        assert result.entities_reused == 1
        assert result.entities_recovered == 0

        verify = manager.get_session()
        try:
            gh = SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID)
            assert gh.name == "Invernadero 1"  # local value preserved
            assert gh.location == "Zona A"
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (4) Greenhouse owner mismatch -> conflict, no insert, subtree not recovered
# ---------------------------------------------------------------------------


def test_greenhouse_owner_mismatch_conflicts_and_blocks_subtree():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows(owner="00000000-0000-4000-8000-000000000000")
        port = FakeRemoteReadPort(rows)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.conflicts >= 1
        # Greenhouse not inserted -> children can't resolve parent.
        assert any(e.code == "OWNER_MISMATCH" for e in result.errors)
        assert result.entities_recovered == 0

        verify = manager.get_session()
        try:
            assert verify.query(GreenhouseModel).count() == 0
            assert verify.query(ModuleModel).count() == 0
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (5) Child parent mismatch (same remote_id, different local parent)
# ---------------------------------------------------------------------------


def test_module_parent_mismatch_conflicts_no_reparent():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        # Pre-seed a module with MOD_ID under a DIFFERENT local greenhouse.
        gh_repo = SqlGreenhouseRepository(seed)
        mod_repo = SqlModuleRepository(seed)
        from src.domain.entities.greenhouse import Greenhouse
        from src.domain.entities.module import Module

        other_gh = gh_repo.insert_preserving_remote_id(
            Greenhouse(name="Other", owner_user_id=local_user),
            remote_id="12121212-1111-4222-8333-444444444444",
        )
        mod_repo.insert_preserving_remote_id(
            other_gh.id,
            Module(greenhouse_id=other_gh.id, name="Preexisting"),
            remote_id=MOD_ID,
        )
        seed.close()

        rows = _full_hierarchy_rows()
        # Only greenhouse + module phases matter here.
        port = FakeRemoteReadPort(
            {"greenhouses": rows["greenhouses"], "modules": rows["modules"]}
        )
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert any(e.code == "PARENT_MISMATCH" for e in result.errors)

        verify = manager.get_session()
        try:
            mod = SqlModuleRepository(verify).find_by_remote_id(MOD_ID)
            # Still points at the ORIGINAL local greenhouse, not reparented.
            assert mod.greenhouse_id == other_gh.id
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (6) Greenhouse natural key conflict
# ---------------------------------------------------------------------------


def test_greenhouse_natural_key_conflict_no_autolink_no_duplicate():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        # Local greenhouse with same name but NO remote_id.
        gh_model = GreenhouseModel(owner_user_id=local_user, name="Invernadero 1")
        seed.add(gh_model)
        seed.commit()
        seed.close()

        rows = _full_hierarchy_rows()
        port = FakeRemoteReadPort({"greenhouses": rows["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert any(e.code == "NATURAL_KEY_CONFLICT" for e in result.errors)
        assert result.entities_recovered == 0

        verify = manager.get_session()
        try:
            # No duplicate; the local row was NOT auto-linked to the remote id.
            all_gh = verify.query(GreenhouseModel).all()
            assert len(all_gh) == 1
            assert all_gh[0].remote_id is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (7) Module natural key conflict
# ---------------------------------------------------------------------------


def test_module_natural_key_conflict():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        # First: recover greenhouse + module normally.
        port1 = FakeRemoteReadPort(
            {"greenhouses": rows["greenhouses"], "modules": rows["modules"]}
        )
        service1, s1 = _build_service(manager, port1)
        try:
            service1.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s1.close()

        # Second: a DIFFERENT remote module id, same greenhouse + same name.
        dup_module = dict(rows["modules"][0])
        dup_module["id"] = "77777777-1111-4222-8333-444444444444"
        port2 = FakeRemoteReadPort(
            {"greenhouses": rows["greenhouses"], "modules": [dup_module]}
        )
        service2, s2 = _build_service(manager, port2)
        try:
            result = service2.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s2.close()

        assert any(e.code == "NATURAL_KEY_CONFLICT" for e in result.errors)

        verify = manager.get_session()
        try:
            assert verify.query(ModuleModel).count() == 1
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (8) Metrics 1:1 natural conflict
# ---------------------------------------------------------------------------


def test_metrics_one_to_one_natural_conflict():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        # First: full up to metrics.
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "monitoring_metrics": rows["monitoring_metrics"],
        }
        port1 = FakeRemoteReadPort(subset)
        service1, s1 = _build_service(manager, port1)
        try:
            service1.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s1.close()

        # Second: a DIFFERENT metrics remote id for the SAME monitoring.
        dup_metrics = dict(rows["monitoring_metrics"][0])
        dup_metrics["id"] = "88888888-1111-4222-8333-444444444444"
        subset2 = dict(subset)
        subset2["monitoring_metrics"] = [dup_metrics]
        port2 = FakeRemoteReadPort(subset2)
        service2, s2 = _build_service(manager, port2)
        try:
            result = service2.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s2.close()

        assert any(e.code == "NATURAL_KEY_CONFLICT" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (9) Initial remote failure -> success=False, SQLite unchanged
# ---------------------------------------------------------------------------


def test_initial_remote_failure_leaves_db_unchanged():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        port = FakeRemoteReadPort(
            _full_hierarchy_rows(), fail_table="greenhouses",
            fail_error_type="CONNECTIVITY",
        )
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is False
        assert any(e.code == "REMOTE_READ_CONNECTIVITY" for e in result.errors)
        assert result.entities_recovered == 0

        verify = manager.get_session()
        try:
            assert verify.query(GreenhouseModel).count() == 0
        finally:
            verify.close()
        # Only the first phase was attempted.
        assert port.calls == ["greenhouses"]
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (10) Later remote failure -> prior inserts survive, success=False
# ---------------------------------------------------------------------------


def test_later_remote_failure_preserves_prior_inserts():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        port = FakeRemoteReadPort(rows, fail_table="monitorings")
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is False
        # Greenhouse + module persisted before the failing phase.
        assert result.entities_recovered == 2

        verify = manager.get_session()
        try:
            assert verify.query(GreenhouseModel).count() == 1
            assert verify.query(ModuleModel).count() == 1
        finally:
            verify.close()
        # inspection_results / activity_logs phases never ran.
        assert "inspection_results" not in port.calls
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (11) User mapping: monitoring.created_by_user_id remote uuid -> local id
# ---------------------------------------------------------------------------


def test_monitoring_creator_maps_remote_uuid_to_local_user_id():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows(creator=REMOTE_USER)
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            mon = SqlMonitoringRepository(verify).find_by_remote_id(MON_ID)
            assert mon.created_by_user_id == local_user
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


def test_monitoring_creator_without_mapping_is_skipped():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows(creator="55555555-1111-4222-8333-444444444444")
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": rows["snapshots"],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert any(e.code == "USER_MAPPING_NOT_FOUND" for e in result.errors)
        verify = manager.get_session()
        try:
            assert SqlMonitoringRepository(verify).find_by_remote_id(MON_ID) is None
            # Descendant snapshot becomes PARENT_UNRESOLVED.
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is None
        finally:
            verify.close()
        assert any(e.code == "PARENT_UNRESOLVED" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (12) Activity type mapping code "riego" -> local seeded id
# ---------------------------------------------------------------------------


def test_activity_type_code_maps_to_local_seeded_id():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "activity_logs": rows["activity_logs"],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            at = SqlActivityTypeRepository(verify).get_by_code("riego")
            act = SqlActivityLogRepository(verify).find_by_remote_id(ACT_ID)
            assert act is not None
            assert act.activity_type_id == at.id
            assert act.user_id == local_user
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (13) Unknown activity type -> skipped, no new catalog row
# ---------------------------------------------------------------------------


def test_unknown_activity_type_skipped_no_new_catalog_row():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        act = dict(rows["activity_logs"][0])
        act["activity_type_code"] = "does_not_exist"
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "activity_logs": [act],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert any(e.code == "ACTIVITY_TYPE_NOT_FOUND" for e in result.errors)
        verify = manager.get_session()
        try:
            assert SqlActivityLogRepository(verify).find_by_remote_id(ACT_ID) is None
            assert SqlActivityTypeRepository(verify).get_by_code("does_not_exist") is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (14) Snapshot rebuilds the canonical LOCAL image_path (D3.2) and preserves
# the remote Storage object paths verbatim.
#
# D3.2 contract change: the remote ``local_image_path`` is informative only and
# NOT authoritative for this device; the new local image_path is rebuilt from
# the LOCAL monitoring id + frame_index.
# ---------------------------------------------------------------------------


def test_snapshot_rebuilds_local_image_path_and_preserves_storage_paths():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": rows["snapshots"],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            from src.infrastructure.persistence.models.snapshot_model import (
                SnapshotModel,
            )

            snap = SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID)
            assert snap is not None
            local_mon_id = snap.monitoring_id
            # Rebuilt canonical path uses the LOCAL monitoring id + zero-padded
            # frame index, NOT the remote local_image_path ("monitorings/1/...").
            assert snap.image_path == (
                f"outputs/monitorings/{local_mon_id}/snapshots/raw/"
                "snapshot_000000.jpg"
            )
            model = (
                verify.query(SnapshotModel)
                .filter(SnapshotModel.remote_id == SNAP_ID)
                .first()
            )
            # Remote Storage object paths are preserved verbatim.
            assert model.raw_storage_path == "remote/raw/snap_0.jpg"
            assert model.annotated_storage_path == "remote/annotated/snap_0.jpg"
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


def test_snapshot_ignores_remote_local_image_path_from_other_device():
    """A remote local_image_path with another device's monitoring id is ignored.

    The recovered Snapshot.image_path is rebuilt with THIS device's local
    monitoring id, never the remote integer (e.g. 999).
    """
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        snap = dict(rows["snapshots"][0])
        snap["local_image_path"] = (
            "outputs/monitorings/999/snapshots/raw/snapshot_000003.jpg"
        )
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": [snap],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            recovered = SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID)
            assert recovered is not None
            assert "999" not in recovered.image_path
            assert recovered.image_path == (
                f"outputs/monitorings/{recovered.monitoring_id}/snapshots/raw/"
                "snapshot_000000.jpg"
            )
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


def test_snapshot_recovered_even_when_remote_local_image_path_empty():
    """D3.2: an empty remote local_image_path no longer blocks recovery.

    Because the local path is rebuilt deterministically, the snapshot metadata
    is still recovered (the old INVALID_SNAPSHOT_PATH contract is removed).
    """
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        snap = dict(rows["snapshots"][0])
        snap["local_image_path"] = ""
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": [snap],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert not any(e.code == "INVALID_SNAPSHOT_PATH" for e in result.errors)
        verify = manager.get_session()
        try:
            recovered = SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID)
            assert recovered is not None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (15) Invalid remote id / parent uuid -> safe skip, no global crash
# ---------------------------------------------------------------------------


def test_invalid_remote_id_and_parent_are_safely_skipped():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        # Greenhouse with missing id.
        bad_gh = dict(rows["greenhouses"][0])
        bad_gh["id"] = ""
        # Module with malformed greenhouse_id FK.
        bad_mod = dict(rows["modules"][0])
        bad_mod["greenhouse_id"] = "not-a-uuid"
        subset = {"greenhouses": [bad_gh], "modules": [bad_mod]}
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        # No crash; both rows safely skipped with INVALID_REMOTE_ID.
        assert result.success is True
        assert result.entities_skipped == 2
        assert all(e.code == "INVALID_REMOTE_ID" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (16) Historical timestamps preserved on greenhouse/module
# ---------------------------------------------------------------------------


def test_historical_timestamps_preserved_on_greenhouse_and_module():
    from datetime import datetime

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        subset = {"greenhouses": rows["greenhouses"], "modules": rows["modules"]}
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            gh = (
                verify.query(GreenhouseModel)
                .filter(GreenhouseModel.remote_id == GH_ID)
                .first()
            )
            assert gh.created_at == datetime(2023, 1, 1, 10, 0, 0)
            assert gh.updated_at == datetime(2023, 1, 2, 11, 0, 0)

            mod = (
                verify.query(ModuleModel)
                .filter(ModuleModel.remote_id == MOD_ID)
                .first()
            )
            assert mod.created_at == datetime(2023, 1, 3, 10, 0, 0)
            assert mod.updated_at == datetime(2023, 1, 4, 11, 0, 0)
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (17) Strict UUID: 36 non-hex chars -> INVALID_REMOTE_ID skip, no crash
# ---------------------------------------------------------------------------


def test_strict_uuid_rejects_36_char_non_uuid_greenhouse():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        bad_gh = dict(rows["greenhouses"][0])
        bad_gh["id"] = "x" * 36  # 36 chars but not a UUID
        port = FakeRemoteReadPort({"greenhouses": [bad_gh]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 0
        assert any(e.code == "INVALID_REMOTE_ID" for e in result.errors)

        verify = manager.get_session()
        try:
            assert verify.query(GreenhouseModel).count() == 0
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (18) LOCAL_OWNER_MISMATCH: existing local greenhouse owned by another user
# ---------------------------------------------------------------------------


def test_local_owner_mismatch_conflicts_and_blocks_subtree():
    from src.domain.entities.greenhouse import Greenhouse

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        # User A is the recovering operator (mapped to REMOTE_USER).
        user_a = _make_user(seed, "a@example.com", REMOTE_USER)
        # User B owns a pre-existing local greenhouse carrying GH_ID.
        user_b = _make_user(seed, "b@example.com", "b0b0b0b0-1111-4222-8333-444444444444")
        gh_repo = SqlGreenhouseRepository(seed)
        gh_repo.insert_preserving_remote_id(
            Greenhouse(name="Owned by B", owner_user_id=user_b),
            remote_id=GH_ID,
        )
        seed.close()

        # Cloud greenhouse row is owned by A's remote id; plus a child module.
        rows = _full_hierarchy_rows(owner=REMOTE_USER)
        port = FakeRemoteReadPort(
            {"greenhouses": rows["greenhouses"], "modules": rows["modules"]}
        )
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, user_a)
        finally:
            session.close()

        assert result.success is True
        assert result.conflicts >= 1
        assert any(e.code == "LOCAL_OWNER_MISMATCH" for e in result.errors)
        # Greenhouse not in map -> child module cannot resolve its parent.
        assert any(
            e.code == "PARENT_UNRESOLVED" and e.entity_type == "modules"
            for e in result.errors
        )

        verify = manager.get_session()
        try:
            gh = SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID)
            # Greenhouse NOT modified / NOT reparented: still owned by B.
            assert gh.owner_user_id == user_b
            assert gh.name == "Owned by B"
            # No auto-linked module created.
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (19) Monitoring REUSE independent of user mapping
# ---------------------------------------------------------------------------


def test_monitoring_reuse_independent_of_user_mapping():
    from src.domain.entities.greenhouse import Greenhouse
    from src.domain.entities.module import Module
    from src.domain.entities.monitoring import Monitoring

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        gh_repo = SqlGreenhouseRepository(seed)
        mod_repo = SqlModuleRepository(seed)
        mon_repo = SqlMonitoringRepository(seed)

        gh = gh_repo.insert_preserving_remote_id(
            Greenhouse(name="Invernadero 1", owner_user_id=local_user),
            remote_id=GH_ID,
        )
        mod = mod_repo.insert_preserving_remote_id(
            gh.id,
            Module(greenhouse_id=gh.id, name="Modulo 1"),
            remote_id=MOD_ID,
        )
        mon_repo.insert_preserving_remote_id(
            mod.id,
            Monitoring(
                module_id=mod.id,
                status="completed",
                created_by_user_id=local_user,
            ),
            remote_id=MON_ID,
        )
        seed.close()

        # Monitoring row references a creator with NO local mapping. A snapshot
        # child depends on the monitoring being mapped by reuse.
        unmapped = "55555555-1111-4222-8333-444444444444"
        rows = _full_hierarchy_rows(creator=unmapped)
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": rows["snapshots"],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        # Monitoring reused (creator mapping never consulted).
        assert not any(e.code == "USER_MAPPING_NOT_FOUND" for e in result.errors)
        assert result.entities_reused >= 1  # gh + mod + mon reused

        verify = manager.get_session()
        try:
            mon = SqlMonitoringRepository(verify).find_by_remote_id(MON_ID)
            # Local monitoring unchanged: creator preserved.
            assert mon.created_by_user_id == local_user
            # Monitoring mapped -> child snapshot recovered.
            snap = SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID)
            assert snap is not None and snap.monitoring_id == mon.id
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (20) ActivityLog REUSE independent of user/type mapping
# ---------------------------------------------------------------------------


def test_activity_log_reuse_independent_of_mappings():
    from src.domain.entities.activity_log import ActivityLog
    from src.domain.entities.greenhouse import Greenhouse
    from src.domain.entities.module import Module

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        gh_repo = SqlGreenhouseRepository(seed)
        mod_repo = SqlModuleRepository(seed)
        act_repo = SqlActivityLogRepository(seed)
        riego = SqlActivityTypeRepository(seed).get_by_code("riego")

        gh = gh_repo.insert_preserving_remote_id(
            Greenhouse(name="Invernadero 1", owner_user_id=local_user),
            remote_id=GH_ID,
        )
        mod = mod_repo.insert_preserving_remote_id(
            gh.id,
            Module(greenhouse_id=gh.id, name="Modulo 1"),
            remote_id=MOD_ID,
        )
        act_repo.insert_preserving_remote_id(
            ActivityLog(
                module_id=mod.id,
                activity_type_id=riego.id,
                user_id=local_user,
                notes="seeded",
            ),
            remote_id=ACT_ID,
        )
        seed.close()

        # Activity row references an unmapped user and a nonexistent type code.
        rows = _full_hierarchy_rows()
        act = dict(rows["activity_logs"][0])
        act["user_id"] = "55555555-1111-4222-8333-444444444444"  # unmapped
        act["activity_type_code"] = "does_not_exist"  # nonexistent
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "activity_logs": [act],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert not any(e.code == "USER_MAPPING_NOT_FOUND" for e in result.errors)
        assert not any(e.code == "ACTIVITY_TYPE_NOT_FOUND" for e in result.errors)

        verify = manager.get_session()
        try:
            row = SqlActivityLogRepository(verify).find_by_remote_id(ACT_ID)
            # Local row unchanged: still the seeded user/type/notes.
            assert row.user_id == local_user
            assert row.activity_type_id == riego.id
            assert row.notes == "seeded"
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (21) Session/network boundary: no remote fetch inside an open local UoW
# ---------------------------------------------------------------------------


class _ScopeFlag:
    """Mutable holder tracking whether a local UoW scope is currently open."""

    def __init__(self):
        self.active = False


class _SpyUnitOfWork:
    """Wraps a real SqlRecoveryUnitOfWork, flipping a shared flag on enter/exit.

    The production UoW is NOT modified; the spy only observes its lifecycle.
    """

    def __init__(self, inner, flag: _ScopeFlag):
        self._inner = inner
        self._flag = flag

    def __enter__(self):
        self._inner.__enter__()
        self._flag.active = True
        return self._inner

    def __exit__(self, exc_type, exc, tb):
        self._flag.active = False
        return self._inner.__exit__(exc_type, exc, tb)


class _BoundaryRemoteReadPort(FakeRemoteReadPort):
    """FakeRemoteReadPort that asserts no local UoW is open on each fetch."""

    def __init__(self, flag: _ScopeFlag, rows_by_table=None):
        super().__init__(rows_by_table=rows_by_table)
        self._flag = flag

    def fetch_by_owner(self, access_token, table, owner_user_id, filters=None):
        assert self._flag.active is False, (
            f"Remote fetch of '{table}' happened inside an open local UoW."
        )
        return super().fetch_by_owner(access_token, table, owner_user_id, filters)


def test_no_remote_fetch_inside_open_local_unit_of_work():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        flag = _ScopeFlag()

        def spy_factory():
            return _SpyUnitOfWork(SqlRecoveryUnitOfWork(manager.get_session), flag)

        rows = _full_hierarchy_rows()
        # Deterministic Storage paths so a download job is actually created and
        # the probe can prove no UoW is open during the download HTTP call.
        det_snap = dict(rows["snapshots"][0])
        det_snap["raw_storage_path"] = _remote_raw(MON_ID)
        det_snap["annotated_storage_path"] = _remote_annotated(MON_ID)
        rows = dict(rows)
        rows["snapshots"] = [det_snap]
        sync_state = SyncStateRepository(session_factory=manager.get_session)

        # A download port that records the UoW-active flag on each HTTP call.
        download = FakeRemoteDownloadPort(active_probe=lambda: flag.active)
        download.set_bytes(_remote_raw(MON_ID), b"raw").set_bytes(
            _remote_annotated(MON_ID), b"annotated"
        )

        # First run: full multi-phase import.
        port1 = _BoundaryRemoteReadPort(flag, rows)
        service1 = RecoveryService(
            remote_read=port1, uow_factory=spy_factory, sync_state=sync_state,
            tombstone_guard=SqlRecoveryTombstoneAdapter(manager.get_session),
            remote_download=download,
            recovery_files=InMemoryRecoveryFiles(),
        )
        first = service1.execute_recovery("token", REMOTE_USER, local_user)
        assert first.success is True
        assert first.entities_recovered == 7
        assert flag.active is False  # closed after last row
        # Downloads happened (raw + annotated) and NONE inside an open UoW.
        assert len(download.calls) == 2
        assert download.uow_active_flags == [False, False]

        # Second run: reuse across multiple phases.
        port2 = _BoundaryRemoteReadPort(flag, rows)
        service2 = RecoveryService(
            remote_read=port2, uow_factory=spy_factory, sync_state=sync_state,
            tombstone_guard=SqlRecoveryTombstoneAdapter(manager.get_session),
            remote_download=FakeRemoteDownloadPort(),
            recovery_files=InMemoryRecoveryFiles(),
        )
        second = service2.execute_recovery("token", REMOTE_USER, local_user)
        assert second.success is True
        assert second.entities_reused == 7
        assert flag.active is False
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (22) Row-error isolation: bad row in a phase does not poison the next row
# ---------------------------------------------------------------------------


def test_row_error_isolation_within_phase_domain_error_per_row():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()

        # Row A: valid uuid/parent but monitoring_frequency_days = -1 raises in
        # Module.__post_init__ during handler processing (a pre-flush domain
        # error) -> INSERT_ERROR skip. (SQL-failed-session isolation is covered
        # separately by test_row_error_isolation_failed_sql_session_per_row.)
        bad_module = dict(rows["modules"][0])
        bad_module["id"] = "aa000000-1111-4222-8333-444444444444"
        bad_module["name"] = "Bad Module"
        bad_module["monitoring_frequency_days"] = -1

        # Row B: valid, distinct id/name -> recovers in its own fresh UoW.
        good_module = dict(rows["modules"][0])
        good_module["id"] = MOD_ID
        good_module["name"] = "Good Module"

        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": [bad_module, good_module],
        }
        port = FakeRemoteReadPort(subset)
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        # Remote read succeeded for every phase -> success stays True.
        assert result.success is True
        # Row A produced an INSERT_ERROR; row B still recovered.
        assert any(e.code == "INSERT_ERROR" for e in result.errors)

        verify = manager.get_session()
        try:
            # Greenhouse (1 row) + only the good module persisted.
            assert verify.query(ModuleModel).count() == 1
            good = SqlModuleRepository(verify).find_by_remote_id(MOD_ID)
            assert good is not None and good.name == "Good Module"
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# Row-error isolation with a REAL failed SQLAlchemy session (flush IntegrityError)
# ---------------------------------------------------------------------------


class _FailingModuleRepo:
    """Test-only wrapper: reads delegate to the real repo, but the recovery
    insert forces a REAL IntegrityError during ``session.flush()`` by adding an
    orphan ModuleModel whose greenhouse_id does not exist (FK violation with
    PRAGMA foreign_keys = ON). It does NOT roll back, so the UoW's Session is
    left in a failed state until the real SqlRecoveryUnitOfWork.__exit__ cleans
    it up. Production code is untouched.
    """

    def __init__(self, real_repo, session):
        self._real = real_repo
        self._session = session

    def find_by_remote_id(self, remote_id):
        return self._real.find_by_remote_id(remote_id)

    def get_by_greenhouse(self, greenhouse_id):
        return self._real.get_by_greenhouse(greenhouse_id)

    def insert_preserving_remote_id(self, greenhouse_id, entity, remote_id):
        # Insert an orphan row (greenhouse_id that cannot exist) and flush so
        # SQLite raises IntegrityError; leave the session failed (no rollback).
        orphan = ModuleModel(
            greenhouse_id=999999999,
            name=entity.name,
            crop_type=entity.crop_type,
            remote_id=remote_id,
            remote_sync_status="synced",
        )
        self._session.add(orphan)
        self._session.flush()  # -> sqlalchemy.exc.IntegrityError (FK violation)
        # Not reached; kept for interface completeness.
        return self._real.insert_preserving_remote_id(greenhouse_id, entity, remote_id)


class _FailingFirstModuleUoW:
    """Wraps a real SqlRecoveryUnitOfWork; for the FIRST module insert it swaps
    module_repo for a repo that fails on flush. Uses the real Session and the
    real __exit__ (defensive rollback + close)."""

    def __init__(self, real_uow, state):
        self._real = real_uow
        self._state = state  # {"module_uow_count": int, "session_ids": list}

    def __enter__(self):
        self._real.__enter__()
        # Record the real Session id for module-phase UoWs so the test can
        # prove rows A and B ran on DIFFERENT sessions.
        session = self._real._session  # test-only introspection
        self._state["session_ids"].append(id(session))
        self._state["module_uow_count"] += 1
        # Only the FIRST module UoW gets the failing repo wrapper.
        if self._state["module_uow_count"] == 1:
            self._real.module_repo = _FailingModuleRepo(
                self._real.module_repo, session
            )
        return self._real

    def __exit__(self, exc_type, exc, tb):
        return self._real.__exit__(exc_type, exc, tb)


def test_row_error_isolation_failed_sql_session_per_row():
    """A row whose flush() fails (real IntegrityError) leaves its Session in a
    failed state, but the next row recovers because it runs in a fresh UoW /
    Session. No PendingRollbackError leaks to row B."""
    import sqlalchemy.exc

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()

        # Row A: valid payload/uuid; its test UoW forces a flush IntegrityError.
        bad_module = dict(rows["modules"][0])
        bad_module["id"] = "aa000000-1111-4222-8333-444444444444"
        bad_module["name"] = "Bad Module"

        # Row B: fully valid, distinct id/name.
        good_module = dict(rows["modules"][0])
        good_module["id"] = MOD_ID
        good_module["name"] = "Good Module"

        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": [bad_module, good_module],
        }

        state = {"module_uow_count": 0, "session_ids": []}

        # Factory: greenhouse phase uses a plain real UoW; module phase uses the
        # failing-first wrapper. We distinguish by whether a module row is being
        # processed — simplest is to always wrap and let the wrapper decide
        # (module_uow_count only increments here, but greenhouse also enters).
        # To keep module counting accurate, only wrap module-phase UoWs by
        # tracking phase order: greenhouse first (1 row), then modules.
        phase_state = {"uow_created": 0}

        def factory():
            phase_state["uow_created"] += 1
            real = SqlRecoveryUnitOfWork(manager.get_session)
            # The first UoW created is the single greenhouse row; wrap only the
            # subsequent (module) UoWs with the failing-first behavior.
            if phase_state["uow_created"] == 1:
                return real
            return _FailingFirstModuleUoW(real, state)

        # Confirm the wrapper truly triggers a SQLAlchemy IntegrityError on flush
        # in isolation (sanity check of the mechanism, using a throwaway UoW).
        probe = SqlRecoveryUnitOfWork(manager.get_session)
        probe.__enter__()
        try:
            failing = _FailingModuleRepo(probe.module_repo, probe._session)
            from src.domain.entities.module import Module as _Module

            raised = False
            try:
                failing.insert_preserving_remote_id(
                    1, _Module(greenhouse_id=1, name="probe"), "probe-remote-id"
                )
            except sqlalchemy.exc.IntegrityError:
                raised = True
            assert raised is True
        finally:
            probe.__exit__(None, None, None)

        port = FakeRemoteReadPort(subset)
        sync_state = SyncStateRepository(session_factory=manager.get_session)
        service = RecoveryService(
            remote_read=port, uow_factory=factory, sync_state=sync_state,
            tombstone_guard=SqlRecoveryTombstoneAdapter(manager.get_session),
            remote_download=FakeRemoteDownloadPort(),
            recovery_files=InMemoryRecoveryFiles(),
        )

        result = service.execute_recovery("token", REMOTE_USER, local_user)

        # Remote reads succeeded for every phase -> success stays True.
        assert result.success is True
        # Row A produced an INSERT_ERROR (from the real failed flush).
        assert any(e.code == "INSERT_ERROR" for e in result.errors)

        # Two module UoWs were created, on DIFFERENT real Sessions.
        assert state["module_uow_count"] == 2
        assert len(state["session_ids"]) == 2
        assert state["session_ids"][0] != state["session_ids"][1]

        verify = manager.get_session()
        try:
            # Greenhouse recovered; only the good module persisted (bad rolled back).
            assert verify.query(GreenhouseModel).count() == 1
            assert verify.query(ModuleModel).count() == 1
            good = SqlModuleRepository(verify).find_by_remote_id(MOD_ID)
            assert good is not None and good.name == "Good Module"
            # The bad module's orphan row never survived.
            assert (
                SqlModuleRepository(verify).find_by_remote_id(
                    "aa000000-1111-4222-8333-444444444444"
                )
                is None
            )
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ===========================================================================
# Anti-resurrection tests (Spec 022, block D3.1)
#
# These use a real file-backed SQLite DB and the real SqlRecoveryTombstoneAdapter
# over the real deletion_outbox, seeding tombstones directly. A FakeTombstoneGuard
# is used only where a check FAILURE (undetermined lookup) must be forced.
#
# Direct-tombstone entity types are ONLY greenhouse | module | monitoring.
# snapshots / monitoring_metrics / inspection_results / activity_logs have NO
# direct tombstone type; they are blockable only via a tombstoned ancestor
# (blocked lineage). No snapshot/activity tombstone type is fabricated.
# ===========================================================================


def _codes_for(result, entity_type):
    return {e.code for e in result.errors if e.entity_type == entity_type}


# ---------------------------------------------------------------------------
# (D3.1-1) Direct greenhouse tombstone (pending) blocks the entire subtree
# ---------------------------------------------------------------------------


def test_greenhouse_tombstone_pending_blocks_whole_subtree():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="pending")

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 0
        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "greenhouses")
        # Every descendant table skipped as PARENT_TOMBSTONED.
        for table in (
            "modules", "monitorings", "monitoring_metrics",
            "snapshots", "inspection_results", "activity_logs",
        ):
            assert "PARENT_TOMBSTONED" in _codes_for(result, table), table

        verify = manager.get_session()
        try:
            assert verify.query(GreenhouseModel).count() == 0
            assert verify.query(ModuleModel).count() == 0
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-2/3/4) Blocking statuses: syncing / error block; synced does NOT
# ---------------------------------------------------------------------------


def test_greenhouse_tombstone_syncing_blocks():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="syncing")

        port = FakeRemoteReadPort({"greenhouses": _full_hierarchy_rows()["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 0
        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "greenhouses")
    finally:
        _cleanup(manager.engine, db_path)


def test_greenhouse_tombstone_error_blocks():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="error")

        port = FakeRemoteReadPort({"greenhouses": _full_hierarchy_rows()["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 0
        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "greenhouses")
    finally:
        _cleanup(manager.engine, db_path)


def test_greenhouse_tombstone_synced_does_not_block():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="synced")

        port = FakeRemoteReadPort({"greenhouses": _full_hierarchy_rows()["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 1
        assert "TOMBSTONE_BLOCKED" not in _codes_for(result, "greenhouses")

        verify = manager.get_session()
        try:
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is not None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-5) A tombstone with remote_id NULL never blocks a valid remote UUID
# ---------------------------------------------------------------------------


def test_null_remote_id_tombstone_does_not_block():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", None, status="pending")

        port = FakeRemoteReadPort({"greenhouses": _full_hierarchy_rows()["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 1

        verify = manager.get_session()
        try:
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is not None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-6) Exact identity: same UUID but different entity_type does NOT block
# ---------------------------------------------------------------------------


def test_tombstone_matches_entity_type_exactly():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        # A MODULE tombstone carrying the greenhouse's UUID must NOT block the
        # greenhouse (entity_type differs).
        _seed_tombstone(manager, "module", GH_ID, status="pending")

        port = FakeRemoteReadPort({"greenhouses": _full_hierarchy_rows()["greenhouses"]})
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert result.entities_recovered == 1
        assert "TOMBSTONE_BLOCKED" not in _codes_for(result, "greenhouses")
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-7) Module tombstone: greenhouse recovers, module subtree blocked
# ---------------------------------------------------------------------------


def test_module_tombstone_blocks_module_subtree_but_not_greenhouse():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "module", MOD_ID, status="pending")

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        # Greenhouse recovered.
        verify = manager.get_session()
        try:
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is not None
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is None
            assert SqlMonitoringRepository(verify).find_by_remote_id(MON_ID) is None
        finally:
            verify.close()

        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "modules")
        # Module descendants (monitoring/metrics/snapshot/inspection) blocked,
        # and the module's activity_log too (its parent is the module).
        for table in (
            "monitorings", "monitoring_metrics",
            "snapshots", "inspection_results", "activity_logs",
        ):
            assert "PARENT_TOMBSTONED" in _codes_for(result, table), table
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-8/9) Monitoring tombstone: blocks its metrics/snapshot/inspection
# lineage, while the module's activity_log still recovers.
#
# NOTE (test 9): there is NO direct snapshot tombstone type. The snapshot ->
# inspection_result lineage is blocked by tombstoning the parent MONITORING.
# This single test covers both the "monitoring tombstone" and the
# "snapshot subtree blocked" intents; no snapshot tombstone type is invented.
# ---------------------------------------------------------------------------


def test_monitoring_tombstone_blocks_snapshot_lineage_but_activity_recovers():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "monitoring", MON_ID, status="pending")

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            # Greenhouse + module recover.
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is not None
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is not None
            # Monitoring + its snapshot lineage blocked (not persisted).
            assert SqlMonitoringRepository(verify).find_by_remote_id(MON_ID) is None
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is None
            assert (
                SqlInspectionResultRepository(verify).find_by_remote_id(INSP_ID)
                is None
            )
            # The module's activity_log recovers (parent is module, not monitoring).
            assert SqlActivityLogRepository(verify).find_by_remote_id(ACT_ID) is not None
        finally:
            verify.close()

        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "monitorings")
        for table in ("monitoring_metrics", "snapshots", "inspection_results"):
            assert "PARENT_TOMBSTONED" in _codes_for(result, table), table
        # activity_logs recovered -> no tombstone/lineage skip for it.
        assert "PARENT_TOMBSTONED" not in _codes_for(result, "activity_logs")
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-10) activity_log has no direct tombstone type.
#
# activity_log is only blockable via its parent MODULE (covered by
# test_module_tombstone_blocks_module_subtree_but_not_greenhouse). There is no
# "activity_log" entity_type in deletion_outbox, so no dedicated direct-tombstone
# test is possible without fabricating an unsupported entity_type. Documented
# here intentionally; nothing to assert.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (D3.1-11) Two independent greenhouses: only the tombstoned one is blocked
# ---------------------------------------------------------------------------


GH_B = "a2a2a2a2-1111-4222-8333-444444444444"
MOD_B = "b2b2b2b2-1111-4222-8333-444444444444"


def _two_greenhouse_rows():
    base = _full_hierarchy_rows()
    gh_b = dict(base["greenhouses"][0])
    gh_b["id"] = GH_B
    gh_b["name"] = "Invernadero 2"
    mod_b = dict(base["modules"][0])
    mod_b["id"] = MOD_B
    mod_b["greenhouse_id"] = GH_B
    mod_b["name"] = "Modulo B"
    return {
        "greenhouses": [base["greenhouses"][0], gh_b],
        "modules": [base["modules"][0], mod_b],
    }


def test_two_independent_greenhouses_only_tombstoned_blocked():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="pending")

        port = FakeRemoteReadPort(_two_greenhouse_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        verify = manager.get_session()
        try:
            # GH_A blocked; its module blocked.
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is None
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is None
            # GH_B recovers fully with its module.
            gh_b = SqlGreenhouseRepository(verify).find_by_remote_id(GH_B)
            assert gh_b is not None
            mod_b = SqlModuleRepository(verify).find_by_remote_id(MOD_B)
            assert mod_b is not None and mod_b.greenhouse_id == gh_b.id
        finally:
            verify.close()

        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "greenhouses")
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-12) Tombstone check FAILURE on one row: fail-safe skip, run continues
# ---------------------------------------------------------------------------


def test_tombstone_check_failure_skips_row_and_run_continues():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        guard = FakeTombstoneGuard().force_failure("greenhouse", GH_ID)

        port = FakeRemoteReadPort(_two_greenhouse_rows())
        service, session = _build_service(manager, port, tombstone_guard=guard)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        # A failed lookup does NOT abort the whole run.
        assert result.success is True
        assert "TOMBSTONE_CHECK_FAILED" in _codes_for(result, "greenhouses")

        verify = manager.get_session()
        try:
            # GH_A not mapped/inserted; GH_B recovers.
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID) is None
            assert SqlGreenhouseRepository(verify).find_by_remote_id(GH_B) is not None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-13) A parent check FAILURE blocks its descendants (via blocked lineage)
# ---------------------------------------------------------------------------


def test_parent_check_failure_blocks_descendants():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        guard = FakeTombstoneGuard().force_failure("greenhouse", GH_ID)

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port, tombstone_guard=guard)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        assert "TOMBSTONE_CHECK_FAILED" in _codes_for(result, "greenhouses")
        # Module blocked via blocked lineage, no reparent, not persisted.
        assert "PARENT_TOMBSTONED" in _codes_for(result, "modules")

        verify = manager.get_session()
        try:
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-14) Existing local row + same remote_id + blocking tombstone -> NO reuse
# ---------------------------------------------------------------------------


def test_existing_local_row_not_resurrected_when_tombstoned():
    from src.domain.entities.greenhouse import Greenhouse

    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        # Stale local greenhouse already carrying the remote id.
        SqlGreenhouseRepository(seed).insert_preserving_remote_id(
            Greenhouse(name="Stale local", owner_user_id=local_user),
            remote_id=GH_ID,
        )
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="pending")

        port = FakeRemoteReadPort(_full_hierarchy_rows())
        service, session = _build_service(manager, port)
        try:
            result = service.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            session.close()

        assert result.success is True
        # The guard sits BEFORE find_by_remote_id: no reuse despite a stale row.
        assert result.entities_reused == 0
        assert "TOMBSTONE_BLOCKED" in _codes_for(result, "greenhouses")
        # Not mapped -> child module blocked.
        assert "PARENT_TOMBSTONED" in _codes_for(result, "modules")

        verify = manager.get_session()
        try:
            gh = SqlGreenhouseRepository(verify).find_by_remote_id(GH_ID)
            assert gh is not None and gh.name == "Stale local"  # unchanged
            assert SqlModuleRepository(verify).find_by_remote_id(MOD_ID) is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# (D3.1-15) Idempotent: a second run with the tombstone present never resurrects
# ---------------------------------------------------------------------------


def test_second_run_with_tombstone_still_blocks_stable_counts():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "greenhouse", GH_ID, status="pending")

        rows = _full_hierarchy_rows()

        port1 = FakeRemoteReadPort(rows)
        service1, s1 = _build_service(manager, port1)
        try:
            first = service1.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s1.close()

        port2 = FakeRemoteReadPort(rows)
        service2, s2 = _build_service(manager, port2)
        try:
            second = service2.execute_recovery("token", REMOTE_USER, local_user)
        finally:
            s2.close()

        assert first.success is True and second.success is True
        assert first.entities_recovered == 0 and second.entities_recovered == 0
        assert "TOMBSTONE_BLOCKED" in _codes_for(first, "greenhouses")
        assert "TOMBSTONE_BLOCKED" in _codes_for(second, "greenhouses")

        verify = manager.get_session()
        try:
            # Never resurrected across either run.
            assert verify.query(GreenhouseModel).count() == 0
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# ===========================================================================
# Snapshot image recovery tests (Spec 022, block D3.2)
#
# These exercise physical download of raw/annotated snapshot images AFTER the
# snapshot metadata phase closes. Downloads and local writes are driven by
# FakeRemoteDownloadPort + InMemoryRecoveryFiles; no real HTTP or filesystem.
# ===========================================================================


def _rows_with_storage(raw=True, annotated=True, frame_index=0):
    """Full-hierarchy rows whose snapshot declares deterministic Storage paths.

    The default fixture uses non-deterministic remote paths; D3.2 requires the
    stored path to equal the deterministic path, so tests build it explicitly.
    """
    rows = _full_hierarchy_rows()
    snap = dict(rows["snapshots"][0])
    snap["frame_index"] = frame_index
    snap["raw_storage_path"] = _remote_raw(MON_ID, frame_index) if raw else None
    snap["annotated_storage_path"] = (
        _remote_annotated(MON_ID, frame_index) if annotated else None
    )
    rows = dict(rows)
    rows["snapshots"] = [snap]
    return rows


def _subset_through_inspection(rows):
    """Recovery subset covering the hierarchy down to inspection_results."""
    return {
        "greenhouses": rows["greenhouses"],
        "modules": rows["modules"],
        "monitorings": rows["monitorings"],
        "monitoring_metrics": rows["monitoring_metrics"],
        "snapshots": rows["snapshots"],
        "inspection_results": rows["inspection_results"],
    }


def _run_with_images(manager, rows, download, files, local_user):
    port = FakeRemoteReadPort(rows)
    service, session = _build_service(
        manager, port, remote_download=download, recovery_files=files
    )
    try:
        return service.execute_recovery("token", REMOTE_USER, local_user)
    finally:
        session.close()


def _local_mon_id(manager):
    verify = manager.get_session()
    try:
        return SqlMonitoringRepository(verify).find_by_remote_id(MON_ID).id
    finally:
        verify.close()


# (1) Full hierarchy + raw + annotated: metadata + both images downloaded.
def test_full_hierarchy_downloads_raw_and_annotated():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage()
        download = FakeRemoteDownloadPort()
        download.set_bytes(_remote_raw(MON_ID), b"RAW").set_bytes(
            _remote_annotated(MON_ID), b"ANN"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.success is True
        assert result.entities_recovered == 6  # gh, mod, mon, metrics, snap, insp
        assert result.images_downloaded == 2
        assert result.images_skipped == 0
        assert result.images_failed == 0

        local_mon = _local_mon_id(manager)
        assert files.stored[_local_raw(local_mon)] == b"RAW"
        assert files.stored[_local_annotated(local_mon)] == b"ANN"
    finally:
        _cleanup(manager.engine, db_path)


# (3) Raw only: one download.
def test_raw_only_single_download():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        download = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID), b"RAW")
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, {**_subset_through_inspection(rows)}, download, files, local_user
        )

        assert result.images_downloaded == 1
        assert result.images_failed == 0
        local_mon = _local_mon_id(manager)
        assert _local_raw(local_mon) in files.stored
        assert _local_annotated(local_mon) not in files.stored
    finally:
        _cleanup(manager.engine, db_path)


# (4) Annotated only: one download.
def test_annotated_only_single_download():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=False, annotated=True)
        download = FakeRemoteDownloadPort().set_bytes(
            _remote_annotated(MON_ID), b"ANN"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_downloaded == 1
        local_mon = _local_mon_id(manager)
        assert _local_annotated(local_mon) in files.stored
    finally:
        _cleanup(manager.engine, db_path)


# (5) Both Storage paths NULL: zero expected images, no download.
def test_no_storage_paths_zero_expected_images():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=False, annotated=False)
        download = FakeRemoteDownloadPort()
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_downloaded == 0
        assert result.images_skipped == 0
        assert result.images_failed == 0
        assert download.calls == []
    finally:
        _cleanup(manager.engine, db_path)


# (6) Raw 404: skipped, metadata survives.
def test_raw_not_found_is_skipped_metadata_survives():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        download = FakeRemoteDownloadPort().set_error(_remote_raw(MON_ID), "NOT_FOUND")
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_skipped == 1
        assert result.images_downloaded == 0
        assert result.images_failed == 0
        assert any(e.code == "IMAGE_NOT_FOUND" for e in result.errors)
        # Snapshot metadata survived.
        verify = manager.get_session()
        try:
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is not None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# (7) Annotated 404 independent of raw success.
def test_annotated_not_found_independent_of_raw():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=True)
        download = FakeRemoteDownloadPort()
        download.set_bytes(_remote_raw(MON_ID), b"RAW").set_error(
            _remote_annotated(MON_ID), "NOT_FOUND"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_downloaded == 1
        assert result.images_skipped == 1
        assert result.images_failed == 0
    finally:
        _cleanup(manager.engine, db_path)


# (8) Network failure counts as failed; the next object is still processed.
def test_network_failure_counts_failed_next_still_processed():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=True)
        download = FakeRemoteDownloadPort()
        download.set_error(_remote_raw(MON_ID), "CONNECTIVITY").set_bytes(
            _remote_annotated(MON_ID), b"ANN"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_failed == 1
        assert result.images_downloaded == 1
        assert any(e.code == "IMAGE_DOWNLOAD_CONNECTIVITY" for e in result.errors)
        local_mon = _local_mon_id(manager)
        assert _local_annotated(local_mon) in files.stored
    finally:
        _cleanup(manager.engine, db_path)


# (9) RLS_DENIED: failed, no file created.
def test_rls_denied_fails_and_writes_nothing():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        download = FakeRemoteDownloadPort().set_error(_remote_raw(MON_ID), "RLS_DENIED")
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_failed == 1
        assert result.images_downloaded == 0
        assert files.stored == {}
        assert any(e.code == "IMAGE_DOWNLOAD_RLS_DENIED" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# (10) Invalid raw remote path: no HTTP, failed.
def test_invalid_raw_remote_path_no_http():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        rows["snapshots"][0]["raw_storage_path"] = "monitorings/x/raw/whatever.jpg"
        download = FakeRemoteDownloadPort()
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_failed == 1
        assert download.calls == []  # never attempted HTTP
        assert any(e.code == "IMAGE_REMOTE_PATH_INVALID" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# (11) Path points to another monitoring UUID: no HTTP, failed.
def test_remote_path_for_other_monitoring_uuid_no_http():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        other_uuid = "12121212-1111-4222-8333-444444444444"
        rows["snapshots"][0]["raw_storage_path"] = _remote_raw(other_uuid)
        download = FakeRemoteDownloadPort()
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_failed == 1
        assert download.calls == []
        assert any(e.code == "IMAGE_REMOTE_PATH_INVALID" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# (12) Already-local raw: skipped, no HTTP for raw.
def test_already_local_raw_is_skipped_no_http():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        # Pre-seed the eventual local raw path. Local monitoring id starts at 1.
        rows = _rows_with_storage(raw=True, annotated=False)
        files = InMemoryRecoveryFiles(preexisting={_local_raw(1): b"already"})
        download = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID), b"RAW")

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        # Confirm local monitoring id is indeed 1 (single seeded monitoring).
        assert _local_mon_id(manager) == 1
        assert result.images_skipped == 1
        assert result.images_downloaded == 0
        assert download.calls == []
        assert files.stored[_local_raw(1)] == b"already"  # untouched
    finally:
        _cleanup(manager.engine, db_path)


# (13) Rerun: first downloads, second skips existing, no duplicate/truncate.
def test_rerun_skips_existing_images():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=True)
        files = InMemoryRecoveryFiles()
        download1 = FakeRemoteDownloadPort()
        download1.set_bytes(_remote_raw(MON_ID), b"RAW").set_bytes(
            _remote_annotated(MON_ID), b"ANN"
        )

        first = _run_with_images(
            manager, _subset_through_inspection(rows), download1, files, local_user
        )
        assert first.images_downloaded == 2

        # Second run: same files present -> all skipped, no new HTTP.
        download2 = FakeRemoteDownloadPort()
        download2.set_bytes(_remote_raw(MON_ID), b"CHANGED").set_bytes(
            _remote_annotated(MON_ID), b"CHANGED"
        )
        second = _run_with_images(
            manager, _subset_through_inspection(rows), download2, files, local_user
        )
        assert second.images_downloaded == 0
        assert second.images_skipped == 2
        assert download2.calls == []
        local_mon = _local_mon_id(manager)
        # Original bytes preserved (not overwritten/truncated).
        assert files.stored[_local_raw(local_mon)] == b"RAW"
        assert files.stored[_local_annotated(local_mon)] == b"ANN"
    finally:
        _cleanup(manager.engine, db_path)


# (14) Tombstoned monitoring: no snapshot metadata AND zero download calls.
def test_tombstoned_monitoring_triggers_zero_downloads():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        _seed_tombstone(manager, "monitoring", MON_ID, status="pending")

        rows = _rows_with_storage(raw=True, annotated=True)
        download = FakeRemoteDownloadPort()
        download.set_bytes(_remote_raw(MON_ID), b"RAW").set_bytes(
            _remote_annotated(MON_ID), b"ANN"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.success is True
        # No snapshot persisted, and NO download attempted for the blocked subtree.
        verify = manager.get_session()
        try:
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is None
        finally:
            verify.close()
        assert download.calls == []
        assert result.images_downloaded == 0
        assert result.images_failed == 0
        assert result.images_skipped == 0
    finally:
        _cleanup(manager.engine, db_path)


# (15) Metadata insert succeeds but image download fails: snapshot remains and
# the inspection_result still recovers.
def test_image_download_failure_keeps_metadata_and_inspection():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        download = FakeRemoteDownloadPort().set_error(
            _remote_raw(MON_ID), "REMOTE_UNAVAILABLE"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert result.images_failed == 1
        verify = manager.get_session()
        try:
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is not None
            # inspection_result (child of snapshot) still recovered.
            assert (
                SqlInspectionResultRepository(verify).find_by_remote_id(INSP_ID)
                is not None
            )
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)


# Write failure branch: download succeeds but local atomic write fails.
def test_write_failure_counts_as_failed():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _rows_with_storage(raw=True, annotated=False)
        download = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID), b"RAW")
        files = InMemoryRecoveryFiles(fail={_local_raw(1)})

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        assert _local_mon_id(manager) == 1
        assert result.images_failed == 1
        assert result.images_downloaded == 0
        assert any(e.code == "IMAGE_WRITE_FAILED" for e in result.errors)
    finally:
        _cleanup(manager.engine, db_path)


# (17) Invariant: downloaded + skipped + failed == number of declared paths.
def test_image_counter_invariant_holds():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        # raw succeeds, annotated 404 -> 1 declared each = 2 expected objects.
        rows = _rows_with_storage(raw=True, annotated=True)
        download = FakeRemoteDownloadPort()
        download.set_bytes(_remote_raw(MON_ID), b"RAW").set_error(
            _remote_annotated(MON_ID), "NOT_FOUND"
        )
        files = InMemoryRecoveryFiles()

        result = _run_with_images(
            manager, _subset_through_inspection(rows), download, files, local_user
        )

        expected_objects = 2  # both raw and annotated declared (non-null)
        total = (
            result.images_downloaded + result.images_skipped + result.images_failed
        )
        assert total == expected_objects


    finally:
        _cleanup(manager.engine, db_path)


# (16) Independent snapshots: failure of one does not block the other.
def test_independent_snapshots_failure_does_not_block_other():
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        snap_a = dict(rows["snapshots"][0])
        snap_a["frame_index"] = 0
        snap_a["raw_storage_path"] = _remote_raw(MON_ID, 0)
        snap_a["annotated_storage_path"] = None
        snap_b = dict(rows["snapshots"][0])
        snap_b_id = "cccccccc-2222-4222-8333-444444444444"
        snap_b["id"] = snap_b_id
        snap_b["frame_index"] = 1
        snap_b["raw_storage_path"] = _remote_raw(MON_ID, 1)
        snap_b["annotated_storage_path"] = None
        rows = dict(rows)
        rows["snapshots"] = [snap_a, snap_b]

        download = FakeRemoteDownloadPort()
        download.set_error(_remote_raw(MON_ID, 0), "CONNECTIVITY").set_bytes(
            _remote_raw(MON_ID, 1), b"B"
        )
        files = InMemoryRecoveryFiles()

        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": rows["snapshots"],
        }
        result = _run_with_images(manager, subset, download, files, local_user)

        assert result.images_failed == 1
        assert result.images_downloaded == 1
        local_mon = _local_mon_id(manager)
        assert files.stored[_local_raw(local_mon, 1)] == b"B"
    finally:
        _cleanup(manager.engine, db_path)


# ===========================================================================
# D3.2 contract hardening: snapshot REUSE ignores cloud frame_index; NEW
# snapshot requires a strict non-negative integer frame_index.
# ===========================================================================


import pytest as _pytest


def test_reuse_ignores_corrupted_cloud_frame_index_uses_local():
    """A valid local snapshot is REUSED even if the cloud row later carries a
    corrupted frame_index; physical jobs use the LOCAL frame_index."""
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        # First run: recover snapshot with frame_index = 3 and download raw.
        rows1 = _full_hierarchy_rows()
        snap1 = dict(rows1["snapshots"][0])
        snap1["frame_index"] = 3
        snap1["raw_storage_path"] = _remote_raw(MON_ID, 3)
        snap1["annotated_storage_path"] = None
        rows1 = dict(rows1)
        rows1["snapshots"] = [snap1]
        subset1 = {
            "greenhouses": rows1["greenhouses"],
            "modules": rows1["modules"],
            "monitorings": rows1["monitorings"],
            "snapshots": rows1["snapshots"],
        }
        files = InMemoryRecoveryFiles()
        dl1 = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID, 3), b"RAW3")
        first = _run_with_images(manager, subset1, dl1, files, local_user)
        assert first.entities_recovered >= 1
        local_mon = _local_mon_id(manager)
        assert files.stored[_local_raw(local_mon, 3)] == b"RAW3"

        # Simulate the physical file going missing before the second run.
        del files.stored[_local_raw(local_mon, 3)]

        # Second run: same remote_id/parent, but cloud frame_index is corrupted.
        rows2 = _full_hierarchy_rows()
        snap2 = dict(rows2["snapshots"][0])
        snap2["frame_index"] = "corrupted"
        # Storage path still references the true (local) frame 3.
        snap2["raw_storage_path"] = _remote_raw(MON_ID, 3)
        snap2["annotated_storage_path"] = None
        rows2 = dict(rows2)
        rows2["snapshots"] = [snap2]
        subset2 = {
            "greenhouses": rows2["greenhouses"],
            "modules": rows2["modules"],
            "monitorings": rows2["monitorings"],
            "snapshots": rows2["snapshots"],
        }
        dl2 = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID, 3), b"RAW3b")
        second = _run_with_images(manager, subset2, dl2, files, local_user)

        # Snapshot REUSED; no invalid/insert errors from the corrupted field.
        assert second.entities_reused >= 1
        assert not any(e.code == "INVALID_FRAME_INDEX" for e in second.errors)
        assert not any(e.code == "INSERT_ERROR" for e in second.errors)

        verify = manager.get_session()
        try:
            snap = SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID)
            assert snap is not None
            assert snap.frame_index == 3  # local metadata unchanged
        finally:
            verify.close()

        # The physical job used the LOCAL frame_index (000003), not "corrupted".
        assert files.stored[_local_raw(local_mon, 3)] == b"RAW3b"
        assert dl2.calls == [_remote_raw(MON_ID, 3)]
    finally:
        _cleanup(manager.engine, db_path)


@_pytest.mark.parametrize("bad_value", [None, -1, "3", True])
def test_new_snapshot_invalid_frame_index_is_skipped(bad_value):
    """A MISSING snapshot with a non-int/negative frame_index is skipped with
    INVALID_FRAME_INDEX: no metadata, no HTTP, no filesystem write."""
    manager, db_path = _fresh_manager()
    try:
        seed = manager.get_session()
        local_user = _make_user(seed, "op@example.com", REMOTE_USER)
        seed.close()

        rows = _full_hierarchy_rows()
        snap = dict(rows["snapshots"][0])
        snap["frame_index"] = bad_value
        snap["raw_storage_path"] = _remote_raw(MON_ID, 0)
        snap["annotated_storage_path"] = None
        rows = dict(rows)
        rows["snapshots"] = [snap]
        subset = {
            "greenhouses": rows["greenhouses"],
            "modules": rows["modules"],
            "monitorings": rows["monitorings"],
            "snapshots": rows["snapshots"],
        }
        download = FakeRemoteDownloadPort().set_bytes(_remote_raw(MON_ID, 0), b"X")
        files = InMemoryRecoveryFiles()

        result = _run_with_images(manager, subset, download, files, local_user)

        assert any(e.code == "INVALID_FRAME_INDEX" for e in result.errors)
        assert download.calls == []
        assert files.stored == {}
        verify = manager.get_session()
        try:
            assert SqlSnapshotRepository(verify).find_by_remote_id(SNAP_ID) is None
        finally:
            verify.close()
    finally:
        _cleanup(manager.engine, db_path)
