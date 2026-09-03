"""Migration + round-trip tests for monitorings.video_path (Spec 019, Task 6.5).

Uses REAL temporary SQLite databases (no PRAGMA/ALTER mocking).

Scenarios:
    A. Existing pre-019 DB WITHOUT video_path -> init_db() adds the column via
       ALTER TABLE, preserving existing rows.
    B. Idempotency: a second init_db() is a no-op (one video_path column, data intact).
    C. Fresh install: init_db() creates monitorings already with video_path.
    D. Repository round-trip: None <-> NULL, relative path via create() and via
       update_video_path(), and clearing back to None.

The legacy schema in scenario A/B uses NULLABLE width_m/length_m (post-Spec016
state) so the historical dimensions migration is NOT triggered — this isolates
the video_path migration.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text

from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories.sql_monitoring_repository import (
    SqlMonitoringRepository,
)
from src.domain.entities.monitoring import Monitoring


REL_PATH = "outputs/monitorings/123/video/monitoring.mp4"


#: Known sentinel values for the legacy monitoring row's remote-sync columns.
LEGACY_REMOTE_ID = "legacy-remote-001"
LEGACY_REMOTE_SYNC_STATUS = "synced"
LEGACY_LAST_SYNCED_AT = "2026-01-15 08:30:00"


def _build_legacy_db_without_video_path(db_path: str) -> None:
    """Create the CURRENT pre-019 SQLite schema with ONLY video_path missing.

    Reflects the current ORM columns exactly (post-Spec016 nullable dimensions +
    Spec017 remote-sync columns on greenhouses/modules/monitorings, and
    monitoring_frequency_days on modules), so that running init_db() isolates the
    video_path migration: neither _migrate_dimensions_nullable nor
    _migrate_add_sync_columns nor the modules column migration should have work to
    do. The only thing missing is monitorings.video_path.
    """
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name VARCHAR(150) NOT NULL,
                email VARCHAR(200) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'operator',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # greenhouses: current schema incl. Spec017 remote-sync columns.
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
        # modules: current schema incl. monitoring_frequency_days + remote-sync columns.
        conn.execute(text("""
            CREATE TABLE modules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                greenhouse_id INTEGER NOT NULL REFERENCES greenhouses(id),
                name VARCHAR(100) NOT NULL,
                crop_type VARCHAR(100) NOT NULL DEFAULT 'Tomate Cherry',
                width_m FLOAT,
                length_m FLOAT,
                monitoring_frequency_days INTEGER,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                remote_id VARCHAR(36),
                remote_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                last_synced_at DATETIME,
                remote_sync_error TEXT
            )
        """))
        # monitorings: current schema (nullable dimensions, user + sync_status +
        # Spec017 remote-sync columns) with ONLY video_path missing.
        conn.execute(text("""
            CREATE TABLE monitorings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id INTEGER NOT NULL REFERENCES modules(id),
                status VARCHAR(20) NOT NULL DEFAULT 'initializing',
                started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                width_m FLOAT,
                length_m FLOAT,
                notes TEXT,
                total_snapshots INTEGER NOT NULL DEFAULT 0,
                total_detections INTEGER NOT NULL DEFAULT 0,
                created_by_user_id INTEGER REFERENCES users(id),
                sync_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                remote_id VARCHAR(36),
                remote_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                last_synced_at DATETIME,
                remote_sync_error TEXT
            )
        """))

        # Minimal parent rows + one recognizable legacy monitoring row carrying
        # known remote-sync sentinel values.
        conn.execute(text("INSERT INTO greenhouses (id, name) VALUES (1, 'GH Hist')"))
        conn.execute(text(
            "INSERT INTO modules (id, greenhouse_id, name) VALUES (1, 1, 'Mod1')"
        ))
        conn.execute(text(
            "INSERT INTO monitorings "
            "(id, module_id, status, notes, total_snapshots, total_detections, "
            " remote_id, remote_sync_status, last_synced_at, remote_sync_error) "
            "VALUES (1, 1, 'completed', 'legacy-row', 12, 34, "
            " :remote_id, :remote_sync_status, :last_synced_at, NULL)"
        ), {
            "remote_id": LEGACY_REMOTE_ID,
            "remote_sync_status": LEGACY_REMOTE_SYNC_STATUS,
            "last_synced_at": LEGACY_LAST_SYNCED_AT,
        })
        conn.commit()

    engine.dispose()


def _monitoring_columns(engine) -> list[str]:
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(monitorings)"))
        return [row[1] for row in result.fetchall()]


def _column_info(engine, column: str):
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(monitorings)"))
        for row in result.fetchall():
            if row[1] == column:
                # (cid, name, type, notnull, dflt_value, pk)
                return row
    return None


# --------------------------------------------------------------------------- #
# A. Existing DB without video_path -> migrated, rows preserved
# --------------------------------------------------------------------------- #

class TestExistingDatabaseMigration:
    def test_migration_adds_column_and_preserves_row(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        _build_legacy_db_without_video_path(db_path)

        # Pre-condition: the ONLY missing column is video_path; the Spec017
        # remote-sync columns already exist, and dimensions are already nullable.
        pre_engine = create_engine(f"sqlite:///{db_path}")
        pre_cols = _monitoring_columns(pre_engine)
        assert "video_path" not in pre_cols
        assert "remote_id" in pre_cols
        assert "remote_sync_status" in pre_cols
        assert "last_synced_at" in pre_cols
        assert "remote_sync_error" in pre_cols
        # width_m/length_m already nullable (notnull flag == 0).
        assert _column_info(pre_engine, "width_m")[3] == 0
        assert _column_info(pre_engine, "length_m")[3] == 0
        pre_engine.dispose()

        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        # video_path column now exists, nullable, VARCHAR(500)-compatible.
        info = _column_info(manager.engine, "video_path")
        assert info is not None
        assert "VARCHAR" in str(info[2]).upper() or "CHAR" in str(info[2]).upper()
        assert info[3] == 0  # notnull flag == 0 -> nullable

        # Legacy row preserved intact, incl. remote-sync sentinels, video_path NULL.
        with manager.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, module_id, status, notes, total_snapshots, "
                "total_detections, remote_id, remote_sync_status, "
                "last_synced_at, remote_sync_error, video_path "
                "FROM monitorings WHERE id = 1"
            )).fetchone()
        assert row is not None
        assert row[0] == 1          # id
        assert row[1] == 1          # module_id
        assert row[2] == "completed"
        assert row[3] == "legacy-row"
        assert row[4] == 12         # total_snapshots
        assert row[5] == 34         # total_detections
        assert row[6] == LEGACY_REMOTE_ID
        assert row[7] == LEGACY_REMOTE_SYNC_STATUS
        assert str(row[8]) == LEGACY_LAST_SYNCED_AT
        assert row[9] is None       # remote_sync_error
        assert row[10] is None      # video_path NULL


# --------------------------------------------------------------------------- #
# B. Idempotency
# --------------------------------------------------------------------------- #

class TestIdempotency:
    def test_second_init_db_is_noop(self, tmp_path):
        db_path = str(tmp_path / "legacy_idem.db")
        _build_legacy_db_without_video_path(db_path)

        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        manager.init_db()  # must not raise

        cols = _monitoring_columns(manager.engine)
        assert cols.count("video_path") == 1  # exactly one column

        with manager.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, notes, total_snapshots, video_path, "
                "remote_id, remote_sync_status, last_synced_at "
                "FROM monitorings WHERE id = 1"
            )).fetchone()
        assert row[0] == 1
        assert row[1] == "legacy-row"
        assert row[2] == 12
        assert row[3] is None                       # video_path
        assert row[4] == LEGACY_REMOTE_ID           # remote sync sentinels preserved
        assert row[5] == LEGACY_REMOTE_SYNC_STATUS
        assert str(row[6]) == LEGACY_LAST_SYNCED_AT


# --------------------------------------------------------------------------- #
# C. Fresh install
# --------------------------------------------------------------------------- #

class TestFreshInstall:
    def test_fresh_db_has_video_path(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        cols = _monitoring_columns(manager.engine)
        assert "video_path" in cols
        info = _column_info(manager.engine, "video_path")
        assert info[3] == 0  # nullable


# --------------------------------------------------------------------------- #
# D. Repository round-trip
# --------------------------------------------------------------------------- #

class TestRepositoryRoundTrip:
    def _seed_module(self, manager) -> int:
        """Create a greenhouse + module via ORM, return module id."""
        from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
        from src.infrastructure.persistence.models.module_model import ModuleModel

        session = manager.get_session()
        try:
            gh = GreenhouseModel(name="GH RoundTrip", location="Test")
            session.add(gh)
            session.flush()
            module = ModuleModel(greenhouse_id=gh.id, name="Mod RoundTrip")
            session.add(module)
            session.flush()
            session.commit()
            return module.id
        finally:
            session.close()

    def test_create_with_none_maps_to_null_and_back(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rt_none.db"))
        manager.init_db()
        module_id = self._seed_module(manager)

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            created = repo.create(module_id, Monitoring(module_id=module_id, video_path=None))
            fetched = repo.get_by_id(created.id)
            assert fetched is not None
            assert fetched.video_path is None
        finally:
            session.close()

    def test_create_with_relative_path(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rt_create.db"))
        manager.init_db()
        module_id = self._seed_module(manager)

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            created = repo.create(
                module_id,
                Monitoring(module_id=module_id, video_path=REL_PATH),
            )
            fetched = repo.get_by_id(created.id)
            assert fetched.video_path == REL_PATH  # stored/read as-is (relative)
        finally:
            session.close()

    def test_update_video_path_sets_and_clears(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rt_update.db"))
        manager.init_db()
        module_id = self._seed_module(manager)

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            created = repo.create(module_id, Monitoring(module_id=module_id, video_path=None))
            assert repo.get_by_id(created.id).video_path is None

            updated = repo.update_video_path(created.id, REL_PATH)
            assert updated.video_path == REL_PATH
            assert repo.get_by_id(created.id).video_path == REL_PATH

            cleared = repo.update_video_path(created.id, None)
            assert cleared.video_path is None
            assert repo.get_by_id(created.id).video_path is None
        finally:
            session.close()

    def test_update_video_path_missing_id_raises(self, tmp_path):
        manager = DatabaseManager(db_path=str(tmp_path / "rt_missing.db"))
        manager.init_db()

        session = manager.get_session()
        try:
            repo = SqlMonitoringRepository(session)
            import pytest

            with pytest.raises(ValueError):
                repo.update_video_path(99999, REL_PATH)
        finally:
            session.close()
