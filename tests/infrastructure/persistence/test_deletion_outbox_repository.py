"""Durability tests for DeletionOutboxRepository (Deletion_Outbox).

Validates:
- enqueue persists the entry + child Storage-path rows + local-artifact rows
  with local_delete_status='prepared', status='pending', deleted_at=None, and
  commits synchronously (visible from a fresh session).
- RESTART durability (Req 14.2): after enqueue, disposing the engine and
  building a NEW DatabaseManager/engine/session_factory over the SAME db file
  shows the entry persisted, retaining remote_id and the Storage paths BEFORE
  any Local_Cascade.
- Idempotent re-enqueue by (entity_type, entity_local_id) returns the existing
  non-completed entry without creating a duplicate row.
- mark_local_completed(outbox_id, deleted_at) sets local_delete_status=
  'completed' AND deleted_at; get_pending_for_propagation returns only
  completed + retryable (pending/error/syncing) entries ordered by created_at
  ASC and excludes prepared/failed and synced.
- Allowed remote-status set {pending, syncing, synced, error} (Req 4.4): the
  ORM/DB layer does not enforce a CHECK constraint, so instead we assert the
  repository only ever writes allowed values through its public API.

All tests run offline: no network, no hardware, no camera. A file-based temp
SQLite (tmp_path) is used so a process "restart" can be simulated by disposing
and recreating the engine/session_factory over the same DB file. The production
DatabaseManager is used, which applies PRAGMA foreign_keys = ON on every
connection.

Spec 021 — Monitoring Data Lifecycle and Remote Deletion Consistency.
Requirements: 14.2, 4.4, 4.6
"""

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntryInput,
    OutboxLocalArtifactInput,
    OutboxStoragePathInput,
)
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.deletion_outbox_repository import (
    DeletionOutboxRepository,
)
from src.infrastructure.persistence.models.deletion_outbox_model import (
    DeletionOutboxLocalArtifactModel,
    DeletionOutboxModel,
    DeletionOutboxStoragePathModel,
)

# Allowed remote-propagation status set (Req 4.4).
ALLOWED_REMOTE_STATUSES = {"pending", "syncing", "synced", "error"}
UUID_ROOT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _utcnow():
    """Return current UTC time as a timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_file(tmp_path):
    """Path to a file-based temp SQLite DB (enables restart simulation)."""
    return str(tmp_path / "outbox_durability.db")


@pytest.fixture
def manager(db_file):
    """A DatabaseManager over the temp file with schema initialized.

    Uses the production DatabaseManager so PRAGMA foreign_keys = ON is applied
    on every connection, matching production behaviour.
    """
    mgr = DatabaseManager(db_path=db_file)
    mgr.init_db()
    return mgr


# Local owner used by the outbox entries in these tests (FK -> users.id).
OWNER_UID = 1


@pytest.fixture(autouse=True)
def _seed_owner_user(manager):
    """Seed user id=1 so outbox.owner_user_id FK is satisfiable (Spec 022)."""
    from src.infrastructure.persistence.models.user_model import UserModel

    session = manager.get_session()
    try:
        if session.query(UserModel).filter(UserModel.id == OWNER_UID).first() is None:
            session.add(UserModel(
                id=OWNER_UID,
                full_name="Owner",
                email="owner@example.com",
                password_hash="x",
                role="operator",
                is_active=True,
            ))
            session.commit()
    finally:
        session.close()


@pytest.fixture
def repo(manager):
    """DeletionOutboxRepository bound to the temp-file session factory."""
    return DeletionOutboxRepository(manager.get_session)


def _sample_input(
    entity_type="monitoring",
    entity_local_id=1,
    remote_table="monitorings",
    remote_id=UUID_ROOT,
    owner_user_id=OWNER_UID,
):
    """Build a DeletionOutboxEntryInput with two Storage paths and one artifact."""
    return DeletionOutboxEntryInput(
        entity_type=entity_type,
        entity_local_id=entity_local_id,
        remote_table=remote_table,
        remote_id=remote_id,
        owner_user_id=owner_user_id,
        storage_paths=[
            OutboxStoragePathInput(
                storage_path="monitorings/uuid-1/raw/snapshot_000001.jpg"
            ),
            OutboxStoragePathInput(
                storage_path="monitorings/uuid-1/annotated/snapshot_000001.jpg"
            ),
        ],
        local_artifacts=[
            OutboxLocalArtifactInput(relative_path="monitorings/1"),
        ],
    )


def _reopen_manager(db_file):
    """Simulate a process restart over the SAME db file.

    Returns a brand-new DatabaseManager (new engine + new session_factory)
    pointing at the same file. Callers should dispose the previous engine first.
    """
    return DatabaseManager(db_path=db_file)


# ---------------------------------------------------------------------------
# enqueue: persistence + prepared/pending/None defaults + synchronous commit
# ---------------------------------------------------------------------------


class TestEnqueuePersistence:
    """enqueue creates the entry + child rows durably with correct defaults."""

    def test_enqueue_returns_prepared_pending_entry(self, repo):
        entry = repo.enqueue(_sample_input())

        assert entry.id is not None
        assert entry.entity_type == "monitoring"
        assert entry.entity_local_id == 1
        assert entry.remote_table == "monitorings"
        assert entry.remote_id == UUID_ROOT
        assert entry.status == "pending"
        assert entry.local_delete_status == "prepared"
        assert entry.deleted_at is None
        assert entry.retry_count == 0

    def test_enqueue_persists_child_storage_and_artifact_rows(self, repo, manager):
        entry = repo.enqueue(_sample_input())

        # Read back from a fresh session (proves synchronous commit).
        session = manager.get_session()
        try:
            model = session.get(DeletionOutboxModel, entry.id)
            assert model is not None
            assert model.local_delete_status == "prepared"
            assert model.status == "pending"
            assert model.deleted_at is None

            paths = (
                session.query(DeletionOutboxStoragePathModel)
                .filter(DeletionOutboxStoragePathModel.outbox_id == entry.id)
                .all()
            )
            assert len(paths) == 2
            assert all(p.status == "pending" for p in paths)
            assert {p.storage_path for p in paths} == {
                "monitorings/uuid-1/raw/snapshot_000001.jpg",
                "monitorings/uuid-1/annotated/snapshot_000001.jpg",
            }

            artifacts = (
                session.query(DeletionOutboxLocalArtifactModel)
                .filter(DeletionOutboxLocalArtifactModel.outbox_id == entry.id)
                .all()
            )
            assert len(artifacts) == 1
            assert artifacts[0].relative_path == "monitorings/1"
            assert artifacts[0].status == "pending"
            assert artifacts[0].last_error is None
        finally:
            session.close()

    def test_enqueue_commit_is_synchronous(self, repo, manager):
        """The row exists in the DB immediately after enqueue returns."""
        entry = repo.enqueue(_sample_input())

        session = manager.get_session()
        try:
            count = session.query(DeletionOutboxModel).count()
            assert count == 1
            assert session.get(DeletionOutboxModel, entry.id) is not None
        finally:
            session.close()


# ---------------------------------------------------------------------------
# RESTART durability (Req 14.2)
# ---------------------------------------------------------------------------


class TestRestartDurability:
    """Entry + remote_id + Storage paths survive a simulated restart."""

    def test_entry_survives_engine_dispose_and_reopen(self, manager, db_file):
        repo = DeletionOutboxRepository(manager.get_session)
        entry = repo.enqueue(_sample_input())
        original_id = entry.id

        # Simulate a process restart: dispose the current engine and build a
        # brand-new DatabaseManager over the SAME db file.
        manager.engine.dispose()
        reopened = _reopen_manager(db_file)

        session = reopened.get_session()
        try:
            model = session.get(DeletionOutboxModel, original_id)
            assert model is not None, "Outbox entry must survive restart (Req 14.2)"
            # remote_id is retained BEFORE any Local_Cascade.
            assert model.remote_id == UUID_ROOT
            assert model.remote_table == "monitorings"
            # Still prepared (no Local_Cascade has run yet).
            assert model.local_delete_status == "prepared"
            assert model.deleted_at is None

            # Storage paths are retained too.
            paths = (
                session.query(DeletionOutboxStoragePathModel)
                .filter(DeletionOutboxStoragePathModel.outbox_id == original_id)
                .all()
            )
            assert {p.storage_path for p in paths} == {
                "monitorings/uuid-1/raw/snapshot_000001.jpg",
                "monitorings/uuid-1/annotated/snapshot_000001.jpg",
            }

            artifacts = (
                session.query(DeletionOutboxLocalArtifactModel)
                .filter(DeletionOutboxLocalArtifactModel.outbox_id == original_id)
                .all()
            )
            assert [a.relative_path for a in artifacts] == ["monitorings/1"]
        finally:
            session.close()
            reopened.engine.dispose()

    def test_repository_reads_entry_after_restart(self, manager, db_file):
        """A NEW repository over a reopened DB still finds the pending entry."""
        repo = DeletionOutboxRepository(manager.get_session)
        repo.enqueue(_sample_input())

        manager.engine.dispose()
        reopened = _reopen_manager(db_file)
        new_repo = DeletionOutboxRepository(reopened.get_session)

        # Not completed yet -> not eligible for propagation.
        try:
            assert new_repo.get_pending_for_propagation(OWNER_UID) == []
            # But re-enqueue is idempotent against the persisted prepared entry.
            again = new_repo.enqueue(_sample_input())
            assert again.local_delete_status == "prepared"
            session = reopened.get_session()
            try:
                assert session.query(DeletionOutboxModel).count() == 1
            finally:
                session.close()
        finally:
            reopened.engine.dispose()


# ---------------------------------------------------------------------------
# Idempotent re-enqueue (Req 4.6)
# ---------------------------------------------------------------------------


class TestIdempotentReEnqueue:
    """Re-enqueue by (entity_type, entity_local_id) reuses the existing entry."""

    def test_re_enqueue_returns_existing_prepared_entry(self, repo, manager):
        first = repo.enqueue(_sample_input())
        second = repo.enqueue(_sample_input())

        assert second.id == first.id

        session = manager.get_session()
        try:
            assert session.query(DeletionOutboxModel).count() == 1
        finally:
            session.close()

    def test_re_enqueue_reuses_failed_entry(self, repo, manager):
        first = repo.enqueue(_sample_input())
        repo.mark_local_failed(first.id)

        second = repo.enqueue(_sample_input())
        assert second.id == first.id
        assert second.local_delete_status == "failed"

        session = manager.get_session()
        try:
            assert session.query(DeletionOutboxModel).count() == 1
        finally:
            session.close()

    def test_different_entity_creates_new_entry(self, repo, manager):
        repo.enqueue(_sample_input(entity_local_id=1))
        repo.enqueue(_sample_input(entity_local_id=2))

        session = manager.get_session()
        try:
            assert session.query(DeletionOutboxModel).count() == 2
        finally:
            session.close()

    def test_completed_entry_is_not_reused_for_re_enqueue(self, repo, manager):
        """A completed entry no longer blocks a new prepared entry."""
        first = repo.enqueue(_sample_input())
        repo.mark_local_completed(first.id, deleted_at=_utcnow())

        second = repo.enqueue(_sample_input())
        assert second.id != first.id
        assert second.local_delete_status == "prepared"

        session = manager.get_session()
        try:
            assert session.query(DeletionOutboxModel).count() == 2
        finally:
            session.close()


# ---------------------------------------------------------------------------
# mark_local_completed + get_pending_for_propagation gating/ordering
# ---------------------------------------------------------------------------


class TestLocalCompletedAndPropagationGating:
    """mark_local_completed sets fields; propagation query gates + orders."""

    def test_mark_local_completed_sets_status_and_deleted_at(self, repo, manager):
        entry = repo.enqueue(_sample_input())
        deleted_at = _utcnow()

        repo.mark_local_completed(entry.id, deleted_at=deleted_at)

        session = manager.get_session()
        try:
            model = session.get(DeletionOutboxModel, entry.id)
            assert model.local_delete_status == "completed"
            assert model.deleted_at == deleted_at
        finally:
            session.close()

    def test_prepared_entry_excluded_from_propagation(self, repo):
        repo.enqueue(_sample_input())
        assert repo.get_pending_for_propagation(OWNER_UID) == []

    def test_failed_entry_excluded_from_propagation(self, repo):
        entry = repo.enqueue(_sample_input())
        repo.mark_local_failed(entry.id)
        assert repo.get_pending_for_propagation(OWNER_UID) == []

    def test_completed_pending_entry_included(self, repo):
        entry = repo.enqueue(_sample_input())
        repo.mark_local_completed(entry.id, deleted_at=_utcnow())

        pending = repo.get_pending_for_propagation(OWNER_UID)
        assert [e.id for e in pending] == [entry.id]

    def test_completed_syncing_and_error_are_retryable(self, repo):
        e_syncing = repo.enqueue(_sample_input(entity_local_id=10))
        repo.mark_local_completed(e_syncing.id, deleted_at=_utcnow())
        repo.mark_syncing(e_syncing.id)

        e_error = repo.enqueue(_sample_input(entity_local_id=11))
        repo.mark_local_completed(e_error.id, deleted_at=_utcnow())
        repo.mark_error(e_error.id, "boom")

        pending_ids = {e.id for e in repo.get_pending_for_propagation(OWNER_UID)}
        assert e_syncing.id in pending_ids
        assert e_error.id in pending_ids

    def test_completed_synced_entry_excluded(self, repo):
        entry = repo.enqueue(_sample_input())
        repo.mark_local_completed(entry.id, deleted_at=_utcnow())
        repo.mark_synced(entry.id)
        assert repo.get_pending_for_propagation(OWNER_UID) == []

    def test_propagation_ordered_by_created_at_asc(self, repo, manager):
        first = repo.enqueue(_sample_input(entity_local_id=1))
        # Ensure distinct created_at ordering even at coarse clock resolution.
        time.sleep(0.01)
        second = repo.enqueue(_sample_input(entity_local_id=2))

        # Force a deterministic ordering gap by rewriting created_at directly.
        session = manager.get_session()
        try:
            m1 = session.get(DeletionOutboxModel, first.id)
            m2 = session.get(DeletionOutboxModel, second.id)
            m1.created_at = datetime(2024, 1, 1, 0, 0, 0)
            m2.created_at = datetime(2024, 1, 2, 0, 0, 0)
            session.commit()
        finally:
            session.close()

        repo.mark_local_completed(first.id, deleted_at=_utcnow())
        repo.mark_local_completed(second.id, deleted_at=_utcnow())

        pending = repo.get_pending_for_propagation(OWNER_UID)
        assert [e.id for e in pending] == [first.id, second.id]


# ---------------------------------------------------------------------------
# Allowed remote-status set (Req 4.4)
# ---------------------------------------------------------------------------


class TestAllowedRemoteStatusSet:
    """Req 4.4: invalid statuses are rejected at BOTH the DB and API layers.

    The ORM models now declare CHECK constraints on the status columns, so the
    DB rejects out-of-set values (raw INSERT -> IntegrityError). The repository
    additionally validates caller-provided statuses in its public setters,
    raising ValueError BEFORE any commit. Every repository transition writes a
    value inside {pending, syncing, synced, error}.
    """

    def test_model_enforces_check_constraint(self, manager):
        """A CHECK constraint exists and the DB rejects an invalid status."""
        table = DeletionOutboxModel.__table__
        check_names = {
            c.name
            for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }
        assert "ck_deletion_outbox_status" in check_names

        # Raw DB INSERT with an out-of-set status must be rejected.
        session = manager.get_session()
        try:
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO deletion_outbox "
                        "(entity_type, entity_local_id, remote_table, status, "
                        "local_delete_status, cleanup_status, retry_count, created_at) "
                        "VALUES ('monitoring', 999, 'monitorings', 'bogus', "
                        "'prepared', 'pending', 0, :ts)"
                    ),
                    {"ts": _utcnow()},
                )
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_storage_path_check_constraint_rejects_invalid_status(self, repo, manager):
        """Raw DB INSERT of an invalid storage-path status -> IntegrityError."""
        entry = repo.enqueue(_sample_input())
        session = manager.get_session()
        try:
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO deletion_outbox_storage_path "
                        "(outbox_id, storage_path, status) "
                        "VALUES (:oid, 'p/x.jpg', 'bogus')"
                    ),
                    {"oid": entry.id},
                )
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_local_artifact_check_constraint_rejects_invalid_status(self, repo, manager):
        """Raw DB INSERT of an invalid local-artifact status -> IntegrityError."""
        entry = repo.enqueue(_sample_input())
        session = manager.get_session()
        try:
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO deletion_outbox_local_artifact "
                        "(outbox_id, relative_path, status) "
                        "VALUES (:oid, 'monitorings/1', 'bogus')"
                    ),
                    {"oid": entry.id},
                )
                session.commit()
        finally:
            session.rollback()
            session.close()

    def test_mark_storage_path_status_invalid_raises_value_error(self, repo, manager):
        """Invalid storage-path status via API -> ValueError, no state change."""
        entry = repo.enqueue(_sample_input())
        session = manager.get_session()
        try:
            path = (
                session.query(DeletionOutboxStoragePathModel)
                .filter(DeletionOutboxStoragePathModel.outbox_id == entry.id)
                .first()
            )
            path_id = path.id
        finally:
            session.close()

        with pytest.raises(ValueError):
            repo.mark_storage_path_status(path_id, "bogus")

        # Row status unchanged (no commit happened).
        session = manager.get_session()
        try:
            assert (
                session.get(DeletionOutboxStoragePathModel, path_id).status
                == "pending"
            )
        finally:
            session.close()

    def test_mark_local_artifact_status_invalid_raises_value_error(self, repo, manager):
        """Invalid local-artifact status via API -> ValueError, no state change."""
        entry = repo.enqueue(_sample_input())
        session = manager.get_session()
        try:
            artifact = (
                session.query(DeletionOutboxLocalArtifactModel)
                .filter(DeletionOutboxLocalArtifactModel.outbox_id == entry.id)
                .first()
            )
            artifact_id = artifact.id
        finally:
            session.close()

        with pytest.raises(ValueError):
            repo.mark_local_artifact_status(artifact_id, "bogus")

        session = manager.get_session()
        try:
            assert (
                session.get(DeletionOutboxLocalArtifactModel, artifact_id).status
                == "pending"
            )
        finally:
            session.close()

    def test_valid_child_statuses_still_persist(self, repo, manager):
        """Valid storage-path/local-artifact statuses persist through the API."""
        entry = repo.enqueue(_sample_input())
        session = manager.get_session()
        try:
            path_id = (
                session.query(DeletionOutboxStoragePathModel)
                .filter(DeletionOutboxStoragePathModel.outbox_id == entry.id)
                .first()
                .id
            )
            artifact_id = (
                session.query(DeletionOutboxLocalArtifactModel)
                .filter(DeletionOutboxLocalArtifactModel.outbox_id == entry.id)
                .first()
                .id
            )
        finally:
            session.close()

        repo.mark_storage_path_status(path_id, "removed")
        repo.mark_storage_path_status(path_id, "error")
        repo.mark_local_artifact_status(artifact_id, "done")
        repo.mark_local_artifact_status(artifact_id, "error", last_error="boom")

        session = manager.get_session()
        try:
            assert (
                session.get(DeletionOutboxStoragePathModel, path_id).status == "error"
            )
            artifact = session.get(DeletionOutboxLocalArtifactModel, artifact_id)
            assert artifact.status == "error"
            assert artifact.last_error == "boom"
        finally:
            session.close()

    def test_repository_transitions_only_write_allowed_values(self, repo, manager):
        entry = repo.enqueue(_sample_input())
        repo.mark_local_completed(entry.id, deleted_at=_utcnow())

        repo.mark_syncing(entry.id)
        assert _read_status(manager, entry.id) == "syncing"

        repo.mark_error(entry.id, "transient failure")
        assert _read_status(manager, entry.id) == "error"

        repo.mark_synced(entry.id)
        assert _read_status(manager, entry.id) == "synced"

        # Every value written by the repository is within the allowed set.
        assert {"pending", "syncing", "error", "synced"} <= ALLOWED_REMOTE_STATUSES

    def test_all_written_statuses_are_within_allowed_set(self, repo, manager):
        """Sweep the repository's remote-status API and check the invariant."""
        entry = repo.enqueue(_sample_input())
        assert _read_status(manager, entry.id) in ALLOWED_REMOTE_STATUSES

        repo.mark_local_completed(entry.id, deleted_at=_utcnow())
        for transition in (repo.mark_syncing, repo.mark_synced):
            transition(entry.id)
            assert _read_status(manager, entry.id) in ALLOWED_REMOTE_STATUSES

        repo.mark_error(entry.id, "err")
        assert _read_status(manager, entry.id) in ALLOWED_REMOTE_STATUSES


def _read_status(manager, outbox_id):
    session = manager.get_session()
    try:
        return session.get(DeletionOutboxModel, outbox_id).status
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Spec 022 — deletion_outbox.owner_user_id migration
# ---------------------------------------------------------------------------


class TestDeletionOutboxOwnerMigration:
    """Additive, idempotent owner_user_id column on deletion_outbox."""

    def _legacy_engine(self, db_file):
        """Create a legacy deletion_outbox table WITHOUT owner_user_id."""
        from sqlalchemy import create_engine, event

        engine = create_engine(f"sqlite:///{db_file}", echo=False)

        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            cur.close()

        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name VARCHAR(150) NOT NULL,
                    email VARCHAR(200) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE deletion_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type VARCHAR(20) NOT NULL,
                    entity_local_id INTEGER NOT NULL,
                    remote_id VARCHAR(36),
                    remote_table VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL,
                    status VARCHAR(10) NOT NULL DEFAULT 'pending',
                    local_delete_status VARCHAR(12) NOT NULL DEFAULT 'prepared',
                    last_error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    cleanup_status VARCHAR(12) NOT NULL DEFAULT 'pending',
                    deleted_at DATETIME
                )
            """))
            conn.execute(text(
                "INSERT INTO deletion_outbox "
                "(id, entity_type, entity_local_id, remote_table, created_at, "
                " status, local_delete_status, cleanup_status, retry_count) "
                "VALUES (1, 'monitoring', 5, 'monitorings', '2025-06-01 12:00:00', "
                "'pending', 'completed', 'pending', 0)"
            ))
            conn.commit()
        return engine

    def test_migration_adds_owner_column_legacy_null_and_idempotent(self, tmp_path):
        from src.infrastructure.persistence.database import (
            _migrate_add_deletion_outbox_owner,
        )

        db_file = str(tmp_path / "outbox_owner_migration.db")
        engine = self._legacy_engine(db_file)
        try:
            _migrate_add_deletion_outbox_owner(engine)
            # Re-run must be a no-op.
            _migrate_add_deletion_outbox_owner(engine)

            with engine.connect() as conn:
                cols = [r[1] for r in conn.execute(
                    text("PRAGMA table_info(deletion_outbox)")
                ).fetchall()]
                # Exactly one owner_user_id column added.
                assert cols.count("owner_user_id") == 1

                # Legacy row preserved with owner_user_id NULL (never inferred).
                row = conn.execute(text(
                    "SELECT entity_type, entity_local_id, owner_user_id "
                    "FROM deletion_outbox WHERE id = 1"
                )).fetchone()
                assert row == ("monitoring", 5, None)
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Spec 022 microfix — safe owner reconciliation on prepared/failed re-enqueue
# ---------------------------------------------------------------------------


OWNER_A = OWNER_UID   # == 1, already seeded by _seed_owner_user fixture
OWNER_B = 2           # a second user; seeded within each test that needs it


def _seed_owner_b(manager):
    """Seed user id=2 so OWNER_B FK is satisfiable."""
    from src.infrastructure.persistence.models.user_model import UserModel

    session = manager.get_session()
    try:
        if session.query(UserModel).filter(UserModel.id == OWNER_B).first() is None:
            session.add(UserModel(
                id=OWNER_B,
                full_name="Owner B",
                email="ownerb@example.com",
                password_hash="x",
                role="operator",
                is_active=True,
            ))
            session.commit()
    finally:
        session.close()


class TestEnqueueOwnerReconciliation:
    """Spec 022 microfix: safe owner reconciliation when reusing prepared/failed."""

    @pytest.mark.parametrize("local_delete_status", ["prepared", "failed"])
    def test_legacy_null_owner_updated_to_known_owner(
        self, repo, local_delete_status
    ):
        """Case A: NULL→known owner — backfills the owner on the same outbox row."""
        # Enqueue without owner (legacy / no owner known at TX1 time).
        first = repo.enqueue(_sample_input(owner_user_id=None))
        assert first.owner_user_id is None

        # Simulate the failure path so the entry matches prepared|failed.
        if local_delete_status == "failed":
            repo.mark_local_failed(first.id)

        # Count rows before retry.
        session = repo._session_factory()
        try:
            count_before = session.query(DeletionOutboxModel).count()
        finally:
            session.close()

        # Re-enqueue with the now-known owner (e.g. from build_deletion_payload).
        second = repo.enqueue(_sample_input(owner_user_id=OWNER_A))

        session = repo._session_factory()
        try:
            count_after = session.query(DeletionOutboxModel).count()
            row = session.get(DeletionOutboxModel, first.id)
        finally:
            session.close()

        # Same outbox entry reused (same id, no duplicate row).
        assert second.id == first.id
        assert count_after == count_before

        # Owner is now set.
        assert second.owner_user_id == OWNER_A
        assert row.owner_user_id == OWNER_A

        # Remote_id, remote_table and other metadata are untouched.
        assert second.remote_id == first.remote_id
        assert second.remote_table == first.remote_table

    @pytest.mark.parametrize("local_delete_status", ["prepared", "failed"])
    def test_known_owner_not_erased_by_null_retry(
        self, repo, local_delete_status
    ):
        """Case B: known→NULL — existing owner is preserved; not erased."""
        first = repo.enqueue(_sample_input(owner_user_id=OWNER_A))
        if local_delete_status == "failed":
            repo.mark_local_failed(first.id)

        # Retry payload has no owner (e.g. older code path or unresolvable root).
        second = repo.enqueue(_sample_input(owner_user_id=None))

        assert second.id == first.id
        assert second.owner_user_id == OWNER_A  # preserved

        session = repo._session_factory()
        try:
            row = session.get(DeletionOutboxModel, first.id)
        finally:
            session.close()
        assert row.owner_user_id == OWNER_A

    @pytest.mark.parametrize("local_delete_status", ["prepared", "failed"])
    def test_same_owner_reused_unchanged(self, repo, local_delete_status):
        """Case C: same owner — normal reuse, no unintended changes."""
        first = repo.enqueue(_sample_input(owner_user_id=OWNER_A))
        if local_delete_status == "failed":
            repo.mark_local_failed(first.id)

        second = repo.enqueue(_sample_input(owner_user_id=OWNER_A))

        assert second.id == first.id
        assert second.owner_user_id == OWNER_A

    @pytest.mark.parametrize("local_delete_status", ["prepared", "failed"])
    def test_owner_conflict_raises_and_preserves_original(
        self, repo, manager, local_delete_status
    ):
        """Case D: owner conflict — ValueError; existing outbox unchanged."""
        _seed_owner_b(manager)

        first = repo.enqueue(_sample_input(owner_user_id=OWNER_A))
        if local_delete_status == "failed":
            repo.mark_local_failed(first.id)

        original_retry = first.retry_count

        with pytest.raises(ValueError, match="conflict"):
            repo.enqueue(_sample_input(owner_user_id=OWNER_B))

        # No duplicate row created.
        session = repo._session_factory()
        try:
            count = session.query(DeletionOutboxModel).count()
            row = session.get(DeletionOutboxModel, first.id)
        finally:
            session.close()

        assert count == 1
        assert row.owner_user_id == OWNER_A  # not changed
        assert row.retry_count == original_retry  # metadata untouched

    def test_propagation_eligibility_after_owner_backfill(self, repo):
        """After backfill, completed entry appears in correct user's queue only."""
        # Enqueue legacy (no owner), then retry with owner.
        first = repo.enqueue(_sample_input(owner_user_id=None))
        repo.enqueue(_sample_input(owner_user_id=OWNER_A))  # backfills owner

        # Complete the local deletion.
        repo.mark_local_completed(first.id, deleted_at=_utcnow())

        # OWNER_A's propagation queue contains this entry.
        pending_a = repo.get_pending_for_propagation(OWNER_A)
        assert any(e.id == first.id for e in pending_a)

        # Another user's queue is empty.
        pending_other = repo.get_pending_for_propagation(99)
        assert not any(e.id == first.id for e in pending_other)
