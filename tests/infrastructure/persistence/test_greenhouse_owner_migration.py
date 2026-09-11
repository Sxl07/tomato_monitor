"""Targeted tests for greenhouse per-owner ownership (Spec 022, Task 1.3).

Validates ONLY:
- owner_user_id is nullable for legacy rows
- invalid owner_user_id FK is rejected
- UNIQUE(owner_user_id, name): same name under two distinct owners is allowed
- UNIQUE(owner_user_id, name): same name under the same owner is rejected
- the constraint-recreation migration preserves existing data
- re-running init_db/migration does not duplicate columns, constraints or rows
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError

from src.infrastructure.persistence.database import (
    DatabaseManager,
    _migrate_greenhouse_owner,
)
from src.infrastructure.persistence.exceptions import DatabaseInitError
from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
from src.infrastructure.persistence.models.user_model import UserModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_manager():
    """Create a DatabaseManager backed by a temporary file DB and init it.

    A file-backed DB is used (not :memory:) so the transactional table-rebuild
    migration, which opens multiple connections, operates on the same database.
    Returns (manager, db_path); caller is responsible for cleanup.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(db_path=db_path)
    manager.init_db()
    return manager, db_path


def _cleanup(engine, db_path):
    """Dispose the engine (release the file handle) and remove the temp DB.

    On Windows the SQLite file stays locked until the engine's connection pool
    is disposed, so disposal must happen before os.remove.
    """
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


def _make_user(session, email: str) -> UserModel:
    user = UserModel(
        full_name="Operario",
        email=email,
        password_hash="x",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Model-level ownership / uniqueness tests (fresh schema via init_db)
# ---------------------------------------------------------------------------


class TestGreenhouseOwnership:
    def test_owner_user_id_nullable_for_legacy(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                gh = GreenhouseModel(name="Legacy", owner_user_id=None)
                session.add(gh)
                session.commit()
                session.refresh(gh)
                assert gh.owner_user_id is None
                assert gh.id is not None
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_invalid_owner_fk_rejected(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                # 999 does not correspond to any users.id -> FK violation.
                gh = GreenhouseModel(name="Huerfano", owner_user_id=999)
                session.add(gh)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_same_name_different_owners_allowed(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                user_a = _make_user(session, "a@example.com")
                user_b = _make_user(session, "b@example.com")

                session.add(GreenhouseModel(name="USB", owner_user_id=user_a.id))
                session.commit()
                session.add(GreenhouseModel(name="USB", owner_user_id=user_b.id))
                session.commit()

                rows = (
                    session.query(GreenhouseModel)
                    .filter(GreenhouseModel.name == "USB")
                    .all()
                )
                assert len(rows) == 2
                assert {r.owner_user_id for r in rows} == {user_a.id, user_b.id}
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)

    def test_same_name_same_owner_rejected(self):
        manager, db_path = _fresh_manager()
        try:
            session = manager.get_session()
            try:
                user_a = _make_user(session, "a@example.com")

                session.add(GreenhouseModel(name="USB", owner_user_id=user_a.id))
                session.commit()

                session.add(GreenhouseModel(name="USB", owner_user_id=user_a.id))
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
            finally:
                session.close()
        finally:
            _cleanup(manager.engine, db_path)


# ---------------------------------------------------------------------------
# Constraint-recreation migration on a legacy database
# ---------------------------------------------------------------------------


class TestGreenhouseOwnerMigration:
    def _build_legacy_engine(self, db_path):
        """Create a legacy `greenhouses` schema: global UNIQUE(name), no owner."""
        engine = create_engine(f"sqlite:///{db_path}", echo=False)

        @event.listens_for(engine, "connect")
        def set_pragma(dbapi_conn, conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

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
                CREATE TABLE greenhouses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    location VARCHAR(200),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    remote_id VARCHAR(36),
                    remote_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    last_synced_at DATETIME,
                    remote_sync_error TEXT
                )
            """))
            # A child table with an FK into greenhouses, to prove FKs survive.
            conn.execute(text("""
                CREATE TABLE modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    greenhouse_id INTEGER NOT NULL REFERENCES greenhouses(id),
                    name VARCHAR(100) NOT NULL
                )
            """))
            conn.execute(text(
                "INSERT INTO greenhouses (id, name, location) "
                "VALUES (1, 'Inv-1', 'Zona A'), (2, 'Inv-2', 'Zona B')"
            ))
            conn.execute(text(
                "INSERT INTO modules (id, greenhouse_id, name) "
                "VALUES (10, 1, 'Mod-1'), (11, 2, 'Mod-2')"
            ))
            conn.commit()
        return engine

    def test_migration_preserves_existing_data(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = None
        try:
            engine = self._build_legacy_engine(db_path)

            _migrate_greenhouse_owner(engine)

            with engine.connect() as conn:
                # owner_user_id column added
                cols = {r[1] for r in conn.execute(
                    text("PRAGMA table_info(greenhouses)")
                ).fetchall()}
                assert "owner_user_id" in cols

                # Rows preserved with ids, names, locations, and NULL owner
                rows = conn.execute(text(
                    "SELECT id, name, location, owner_user_id "
                    "FROM greenhouses ORDER BY id"
                )).fetchall()
                assert rows == [
                    (1, "Inv-1", "Zona A", None),
                    (2, "Inv-2", "Zona B", None),
                ]

                # Child FKs preserved
                mods = conn.execute(text(
                    "SELECT id, greenhouse_id, name FROM modules ORDER BY id"
                )).fetchall()
                assert mods == [(10, 1, "Mod-1"), (11, 2, "Mod-2")]

                # Composite unique present, global name-only unique gone
                index_list = conn.execute(
                    text("PRAGMA index_list(greenhouses)")
                ).fetchall()
                has_composite = False
                has_global_name_unique = False
                for idx in index_list:
                    if not bool(idx[2]):
                        continue
                    idx_cols = [c[2] for c in conn.execute(
                        text(f"PRAGMA index_info({idx[1]})")
                    ).fetchall()]
                    # Detect by columns: SQLite backs the table-level UNIQUE
                    # with an auto-generated index name (sqlite_autoindex_*).
                    if idx_cols == ["owner_user_id", "name"]:
                        has_composite = True
                    elif idx_cols == ["name"]:
                        has_global_name_unique = True
                assert has_composite is True
                assert has_global_name_unique is False
        finally:
            _cleanup(engine, db_path)

    def test_migration_is_idempotent_no_duplicates(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = None
        try:
            engine = self._build_legacy_engine(db_path)

            _migrate_greenhouse_owner(engine)
            # Second run must be a no-op (no error, no duplicate work).
            _migrate_greenhouse_owner(engine)
            # Third run to be thorough.
            _migrate_greenhouse_owner(engine)

            with engine.connect() as conn:
                # Exactly one owner_user_id column.
                col_names = [r[1] for r in conn.execute(
                    text("PRAGMA table_info(greenhouses)")
                ).fetchall()]
                assert col_names.count("owner_user_id") == 1

                # Exactly one composite unique index.
                index_list = conn.execute(
                    text("PRAGMA index_list(greenhouses)")
                ).fetchall()
                composite_count = 0
                for idx in index_list:
                    if not bool(idx[2]):
                        continue
                    idx_cols = [c[2] for c in conn.execute(
                        text(f"PRAGMA index_info({idx[1]})")
                    ).fetchall()]
                    if idx_cols == ["owner_user_id", "name"]:
                        composite_count += 1
                assert composite_count == 1

                # Rows not duplicated.
                count = conn.execute(
                    text("SELECT COUNT(*) FROM greenhouses")
                ).fetchone()[0]
                assert count == 2
        finally:
            _cleanup(engine, db_path)

    def test_rebuild_failure_leaves_legacy_schema_intact(self):
        """Force a failure DURING the rebuild; legacy schema must stay intact.

        A controlled FK violation is injected before migrating: an orphan
        `modules` row referencing a non-existent greenhouse is inserted with FK
        enforcement disabled. During the rebuild, ``PRAGMA foreign_key_check``
        detects the orphan and triggers ROLLBACK. After the error we assert the
        legacy schema is fully intact: `owner_user_id` was NOT added, the global
        UNIQUE(name) survives, and the original rows are unchanged.
        """
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = None
        try:
            engine = self._build_legacy_engine(db_path)

            # Inject an orphan child row with FK checks OFF so it persists but
            # will be flagged by foreign_key_check during the rebuild.
            with engine.connect() as conn:
                raw = conn.connection.dbapi_connection
                raw.execute("PRAGMA foreign_keys = OFF")
                raw.execute(
                    "INSERT INTO modules (id, greenhouse_id, name) "
                    "VALUES (99, 4242, 'Orphan')"
                )
                raw.execute("COMMIT")
                raw.execute("PRAGMA foreign_keys = ON")

            # Migration must fail because of the FK violation.
            with pytest.raises(DatabaseInitError):
                _migrate_greenhouse_owner(engine)

            # Legacy schema must remain fully intact.
            with engine.connect() as conn:
                cols = [r[1] for r in conn.execute(
                    text("PRAGMA table_info(greenhouses)")
                ).fetchall()]
                # owner_user_id must NOT have been added.
                assert "owner_user_id" not in cols

                # Global UNIQUE(name) must still be present; composite absent.
                index_list = conn.execute(
                    text("PRAGMA index_list(greenhouses)")
                ).fetchall()
                has_composite = False
                has_global_name_unique = False
                for idx in index_list:
                    if not bool(idx[2]):
                        continue
                    idx_cols = [c[2] for c in conn.execute(
                        text(f"PRAGMA index_info({idx[1]})")
                    ).fetchall()]
                    if idx_cols == ["owner_user_id", "name"]:
                        has_composite = True
                    elif idx_cols == ["name"]:
                        has_global_name_unique = True
                assert has_global_name_unique is True
                assert has_composite is False

                # Original greenhouse rows unchanged.
                rows = conn.execute(text(
                    "SELECT id, name, location FROM greenhouses ORDER BY id"
                )).fetchall()
                assert rows == [
                    (1, "Inv-1", "Zona A"),
                    (2, "Inv-2", "Zona B"),
                ]

                # No leftover temp table from the aborted rebuild.
                tables = {r[0] for r in conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )).fetchall()}
                assert "greenhouses_new" not in tables
        finally:
            _cleanup(engine, db_path)

    def test_full_init_db_rerun_is_noop(self):
        """A second init_db over a migrated file DB is a no-op (no duplicates)."""
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        manager = None
        try:
            manager = DatabaseManager(db_path=db_path)
            manager.init_db()
            manager.init_db()  # rerun

            with manager.engine.connect() as conn:
                col_names = [r[1] for r in conn.execute(
                    text("PRAGMA table_info(greenhouses)")
                ).fetchall()]
                assert col_names.count("owner_user_id") == 1

                index_list = conn.execute(
                    text("PRAGMA index_list(greenhouses)")
                ).fetchall()
                composite_count = 0
                for idx in index_list:
                    if not bool(idx[2]):
                        continue
                    idx_cols = [c[2] for c in conn.execute(
                        text(f"PRAGMA index_info({idx[1]})")
                    ).fetchall()]
                    if idx_cols == ["owner_user_id", "name"]:
                        composite_count += 1
                assert composite_count == 1
        finally:
            _cleanup(manager.engine if manager else None, db_path)
