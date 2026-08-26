"""Tests for remote sync metadata migration (_migrate_add_sync_columns).

Validates:
- Helper adds all sync columns to 7 tables
- Snapshot receives additional storage path columns
- Historical data is preserved (row counts, IDs, values, FKs)
- Historical rows get remote_sync_status='pending', others NULL
- Pre-existing sync_status is preserved (Monitoring, ActivityLog)
- Snapshot.image_path is preserved
- Migration is idempotent (second run is no-op)
- Partial migration state is handled gracefully
- Missing tables are skipped without error
- Full DatabaseManager.init_db() works on historical schema
- Fresh database has all columns
- PRAGMA integrity_check and foreign_key_check pass

Spec 017 — Supabase Remote Sync.
Requirements: 10.1, 10.2, 16.1, 16.3, 16.4, 26.1–26.5
"""

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.infrastructure.persistence.database import (
    DatabaseManager,
    _migrate_add_sync_columns,
    INITIAL_ACTIVITY_TYPES,
)
from src.infrastructure.persistence.models.base import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def historical_engine():
    """Create an in-memory engine with a historical schema (pre-Spec 017).

    Includes all 7 syncable tables plus users and activity_types for FK integrity.
    Tables have NO remote sync columns.
    """
    engine = create_engine("sqlite:///:memory:", echo=False)

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
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'operator',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                remote_user_id VARCHAR(100),
                sync_status VARCHAR(20) NOT NULL DEFAULT 'local_only',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE activity_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code VARCHAR(50) NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL,
                category VARCHAR(50) NOT NULL,
                requires_product BOOLEAN NOT NULL DEFAULT 0,
                allows_quantity BOOLEAN NOT NULL DEFAULT 0,
                default_unit VARCHAR(20),
                is_active BOOLEAN NOT NULL DEFAULT 1
            )
        """))
        conn.execute(text("""
            CREATE TABLE greenhouses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL UNIQUE,
                location VARCHAR(200),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
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
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
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
                sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
            )
        """))
        conn.execute(text("""
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitoring_id INTEGER NOT NULL REFERENCES monitorings(id),
                captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                image_path VARCHAR(500) NOT NULL,
                frame_index INTEGER NOT NULL,
                change_score FLOAT,
                has_detections BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE monitoring_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitoring_id INTEGER NOT NULL UNIQUE REFERENCES monitorings(id),
                total_tomatoes INTEGER NOT NULL,
                healthy_count INTEGER NOT NULL,
                unhealthy_count INTEGER NOT NULL,
                pct_healthy FLOAT NOT NULL,
                pct_unhealthy FLOAT NOT NULL,
                pct_green FLOAT NOT NULL DEFAULT 0,
                pct_breaker FLOAT NOT NULL DEFAULT 0,
                pct_turning FLOAT NOT NULL DEFAULT 0,
                pct_pink FLOAT NOT NULL DEFAULT 0,
                pct_light_red FLOAT NOT NULL DEFAULT 0,
                pct_red FLOAT NOT NULL DEFAULT 0,
                snapshots_with_detections INTEGER NOT NULL,
                computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE inspection_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                detection_index INTEGER NOT NULL,
                bbox_x1 INTEGER NOT NULL,
                bbox_y1 INTEGER NOT NULL,
                bbox_x2 INTEGER NOT NULL,
                bbox_y2 INTEGER NOT NULL,
                detection_score FLOAT NOT NULL,
                health_label VARCHAR(20) NOT NULL,
                health_confidence FLOAT NOT NULL,
                maturity_stage VARCHAR(20),
                maturity_percent FLOAT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_id INTEGER NOT NULL REFERENCES modules(id),
                activity_type_id INTEGER NOT NULL REFERENCES activity_types(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                product_name VARCHAR(150),
                quantity FLOAT,
                unit VARCHAR(20),
                notes TEXT,
                occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
            )
        """))
        conn.commit()

    return engine


@pytest.fixture
def seeded_historical_engine(historical_engine):
    """Historical engine with related rows inserted across all 7 tables."""
    engine = historical_engine
    with engine.connect() as conn:
        # User
        conn.execute(text(
            "INSERT INTO users (id, full_name, email, password_hash) "
            "VALUES (1, 'Operador Test', 'op@test.com', 'hash123')"
        ))
        # Activity type
        conn.execute(text(
            "INSERT INTO activity_types (id, code, name, category) "
            "VALUES (1, 'riego', 'Riego', 'mantenimiento')"
        ))
        # Greenhouse
        conn.execute(text(
            "INSERT INTO greenhouses (id, name, location) "
            "VALUES (1, 'Invernadero 1', 'Zona Norte')"
        ))
        # Module
        conn.execute(text(
            "INSERT INTO modules (id, greenhouse_id, name, crop_type, width_m, length_m) "
            "VALUES (1, 1, 'Modulo A', 'Tomate Cherry', 5.0, 2.0)"
        ))
        # Monitoring with existing sync_status='exported'
        conn.execute(text(
            "INSERT INTO monitorings (id, module_id, status, width_m, length_m, "
            "total_snapshots, total_detections, created_by_user_id, sync_status) "
            "VALUES (1, 1, 'completed', 5.0, 2.0, 3, 10, 1, 'exported')"
        ))
        # Snapshot
        conn.execute(text(
            "INSERT INTO snapshots (id, monitoring_id, image_path, frame_index, has_detections) "
            "VALUES (1, 1, 'data/images/example.jpg', 0, 1)"
        ))
        # Monitoring metrics
        conn.execute(text(
            "INSERT INTO monitoring_metrics (id, monitoring_id, total_tomatoes, "
            "healthy_count, unhealthy_count, pct_healthy, pct_unhealthy, "
            "snapshots_with_detections) "
            "VALUES (1, 1, 10, 8, 2, 80.0, 20.0, 1)"
        ))
        # Inspection result
        conn.execute(text(
            "INSERT INTO inspection_results (id, snapshot_id, detection_index, "
            "bbox_x1, bbox_y1, bbox_x2, bbox_y2, detection_score, "
            "health_label, health_confidence, maturity_stage, maturity_percent) "
            "VALUES (1, 1, 0, 10, 20, 50, 60, 0.95, 'healthy', 0.92, 'red', 85.0)"
        ))
        # Activity log with sync_status='exported'
        conn.execute(text(
            "INSERT INTO activity_logs (id, module_id, activity_type_id, user_id, "
            "notes, sync_status) "
            "VALUES (1, 1, 1, 1, 'Riego matutino', 'exported')"
        ))
        conn.commit()

    return engine


# ---------------------------------------------------------------------------
# Helper: get column set for a table
# ---------------------------------------------------------------------------


def _get_columns(conn, table_name):
    """Return set of column names for a table."""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    return {row[1] for row in result.fetchall()}


def _get_column_info(conn, table_name):
    """Return dict of column_name -> (cid, name, type, notnull, dflt_value, pk)."""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    return {row[1]: row for row in result.fetchall()}


# ===========================================================================
# 6. Helper adds all sync columns
# ===========================================================================


class TestHelperAddsAllColumns:
    """_migrate_add_sync_columns adds the correct columns to all 7 tables."""

    _SYNC_COLS = {"remote_id", "remote_sync_status", "last_synced_at", "remote_sync_error"}

    def test_greenhouses_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "greenhouses")
            assert self._SYNC_COLS.issubset(cols)

    def test_modules_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "modules")
            assert self._SYNC_COLS.issubset(cols)

    def test_monitorings_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "monitorings")
            assert self._SYNC_COLS.issubset(cols)

    def test_snapshots_gets_sync_and_storage_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "snapshots")
            assert self._SYNC_COLS.issubset(cols)
            assert "raw_storage_path" in cols
            assert "annotated_storage_path" in cols

    def test_monitoring_metrics_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "monitoring_metrics")
            assert self._SYNC_COLS.issubset(cols)

    def test_inspection_results_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "inspection_results")
            assert self._SYNC_COLS.issubset(cols)

    def test_activity_logs_gets_sync_columns(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            cols = _get_columns(conn, "activity_logs")
            assert self._SYNC_COLS.issubset(cols)

    def test_remote_sync_status_is_not_null_with_default(self, historical_engine):
        _migrate_add_sync_columns(historical_engine)
        with historical_engine.connect() as conn:
            info = _get_column_info(conn, "greenhouses")
            col = info["remote_sync_status"]
            # col[3] = notnull flag, col[4] = default value
            assert col[3] == 1, "remote_sync_status should be NOT NULL"
            assert "'pending'" in str(col[4]), f"Expected default 'pending', got {col[4]}"


# ===========================================================================
# 7-8. Historical data preservation and remote state
# ===========================================================================


class TestHistoricalDataPreservation:
    """Migration preserves all historical rows, IDs, values, and FKs."""

    def test_row_counts_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            tables = [
                "greenhouses", "modules", "monitorings", "snapshots",
                "monitoring_metrics", "inspection_results", "activity_logs",
            ]
            for table in tables:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()[0]
                assert count == 1, f"{table} should have 1 row, got {count}"

    def test_ids_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            for table in ["greenhouses", "modules", "monitorings", "snapshots",
                          "monitoring_metrics", "inspection_results", "activity_logs"]:
                row = conn.execute(text(f"SELECT id FROM {table}")).fetchone()
                assert row[0] == 1, f"{table}.id should be 1"

    def test_greenhouse_values_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT name, location FROM greenhouses WHERE id=1"
            )).fetchone()
            assert row[0] == "Invernadero 1"
            assert row[1] == "Zona Norte"

    def test_monitoring_metrics_values_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT total_tomatoes, healthy_count, pct_healthy, pct_unhealthy "
                "FROM monitoring_metrics WHERE id=1"
            )).fetchone()
            assert row[0] == 10
            assert row[1] == 8
            assert row[2] == 80.0
            assert row[3] == 20.0

    def test_inspection_result_values_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT bbox_x1, bbox_y1, bbox_x2, bbox_y2, detection_score, "
                "health_label, maturity_stage, maturity_percent "
                "FROM inspection_results WHERE id=1"
            )).fetchone()
            assert row[0] == 10
            assert row[1] == 20
            assert row[2] == 50
            assert row[3] == 60
            assert row[4] == 0.95
            assert row[5] == "healthy"
            assert row[6] == "red"
            assert row[7] == 85.0

    def test_fk_relationships_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            # module -> greenhouse
            row = conn.execute(text("SELECT greenhouse_id FROM modules WHERE id=1")).fetchone()
            assert row[0] == 1
            # monitoring -> module
            row = conn.execute(text("SELECT module_id FROM monitorings WHERE id=1")).fetchone()
            assert row[0] == 1
            # snapshot -> monitoring
            row = conn.execute(text("SELECT monitoring_id FROM snapshots WHERE id=1")).fetchone()
            assert row[0] == 1
            # metrics -> monitoring
            row = conn.execute(text("SELECT monitoring_id FROM monitoring_metrics WHERE id=1")).fetchone()
            assert row[0] == 1
            # inspection_result -> snapshot
            row = conn.execute(text("SELECT snapshot_id FROM inspection_results WHERE id=1")).fetchone()
            assert row[0] == 1
            # activity_log -> module, user, activity_type
            row = conn.execute(text(
                "SELECT module_id, user_id, activity_type_id FROM activity_logs WHERE id=1"
            )).fetchone()
            assert row[0] == 1
            assert row[1] == 1
            assert row[2] == 1

    def test_historical_rows_get_pending_status(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            for table in ["greenhouses", "modules", "monitorings", "snapshots",
                          "monitoring_metrics", "inspection_results", "activity_logs"]:
                row = conn.execute(text(
                    f"SELECT remote_sync_status FROM {table} WHERE id=1"
                )).fetchone()
                assert row[0] == "pending", f"{table}.remote_sync_status should be 'pending'"

    def test_historical_rows_have_null_remote_fields(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            for table in ["greenhouses", "modules", "monitorings", "snapshots",
                          "monitoring_metrics", "inspection_results", "activity_logs"]:
                row = conn.execute(text(
                    f"SELECT remote_id, last_synced_at, remote_sync_error FROM {table} WHERE id=1"
                )).fetchone()
                assert row[0] is None, f"{table}.remote_id should be NULL"
                assert row[1] is None, f"{table}.last_synced_at should be NULL"
                assert row[2] is None, f"{table}.remote_sync_error should be NULL"


# ===========================================================================
# 9. Preserve pre-existing sync_status
# ===========================================================================


class TestPreserveLegacySyncStatus:
    """Migration preserves the pre-existing sync_status column semantics."""

    def test_monitoring_sync_status_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT sync_status, remote_sync_status FROM monitorings WHERE id=1"
            )).fetchone()
            assert row[0] == "exported", "Original sync_status must be preserved"
            assert row[1] == "pending", "New remote_sync_status must be 'pending'"

    def test_activity_log_sync_status_preserved(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT sync_status, remote_sync_status FROM activity_logs WHERE id=1"
            )).fetchone()
            assert row[0] == "exported", "Original sync_status must be preserved"
            assert row[1] == "pending", "New remote_sync_status must be 'pending'"


# ===========================================================================
# 10. Snapshot image_path preserved
# ===========================================================================


class TestSnapshotImagePathPreserved:
    """Snapshot.image_path is never modified; storage paths start as NULL."""

    def test_image_path_unchanged(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT image_path, raw_storage_path, annotated_storage_path "
                "FROM snapshots WHERE id=1"
            )).fetchone()
            assert row[0] == "data/images/example.jpg"
            assert row[1] is None
            assert row[2] is None


# ===========================================================================
# 11. Idempotency
# ===========================================================================


class TestIdempotency:
    """Running the migration twice is a safe no-op."""

    def test_second_run_does_not_fail(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)
        # Second run should not raise
        _migrate_add_sync_columns(engine)

    def test_no_duplicate_columns(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)
        with engine.connect() as conn:
            cols_before = _get_columns(conn, "greenhouses")

        _migrate_add_sync_columns(engine)
        with engine.connect() as conn:
            cols_after = _get_columns(conn, "greenhouses")

        assert cols_before == cols_after

    def test_data_unchanged_after_second_run(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT name, location, remote_sync_status, remote_id "
                "FROM greenhouses WHERE id=1"
            )).fetchone()
            assert row[0] == "Invernadero 1"
            assert row[1] == "Zona Norte"
            assert row[2] == "pending"
            assert row[3] is None

    def test_row_counts_unchanged_after_second_run(self, seeded_historical_engine):
        engine = seeded_historical_engine
        _migrate_add_sync_columns(engine)
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            for table in ["greenhouses", "modules", "monitorings", "snapshots",
                          "monitoring_metrics", "inspection_results", "activity_logs"]:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()[0]
                assert count == 1


# ===========================================================================
# 12. Partial migration recovery
# ===========================================================================


class TestPartialMigration:
    """Migration handles a partially-migrated table gracefully."""

    def test_adds_missing_columns_preserves_existing(self):
        """Simulate a table with only remote_id and remote_sync_status already present."""
        engine = create_engine("sqlite:///:memory:", echo=False)

        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE greenhouses (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    remote_id VARCHAR(36),
                    remote_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                )
            """))
            # Insert a row with existing remote state
            conn.execute(text(
                "INSERT INTO greenhouses (id, name, remote_id, remote_sync_status) "
                "VALUES (1, 'GH1', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'error')"
            ))
            # Create other tables as minimal stubs
            conn.execute(text("CREATE TABLE modules (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE monitorings (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE snapshots (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE monitoring_metrics (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE inspection_results (id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE activity_logs (id INTEGER PRIMARY KEY)"))
            conn.commit()

        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            cols = _get_columns(conn, "greenhouses")
            assert "last_synced_at" in cols
            assert "remote_sync_error" in cols

            # Existing values must NOT be overwritten
            row = conn.execute(text(
                "SELECT remote_id, remote_sync_status FROM greenhouses WHERE id=1"
            )).fetchone()
            assert row[0] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            assert row[1] == "error"


# ===========================================================================
# 13. Missing tables are skipped
# ===========================================================================


class TestMissingTablesSkipped:
    """Helper skips tables that don't exist without raising."""

    def test_only_existing_tables_migrated(self):
        engine = create_engine("sqlite:///:memory:", echo=False)

        # Only create greenhouses
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE greenhouses (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)"
            ))
            conn.commit()

        # Should not raise despite 6 missing tables
        _migrate_add_sync_columns(engine)

        with engine.connect() as conn:
            cols = _get_columns(conn, "greenhouses")
            assert "remote_id" in cols
            assert "remote_sync_status" in cols


# ===========================================================================
# 14. Full historical init_db (PRE-SPEC016 schema)
# ===========================================================================


class TestFullHistoricalInitDb:
    """DatabaseManager.init_db() migrates a full pre-Spec016 historical DB."""

    def test_full_migration_order(self, tmp_path):
        """Exercises: create_all → dimensions → add_columns → sync_columns → seed."""
        db_path = str(tmp_path / "historical.db")

        # Create a PRE-SPEC016 schema: width_m/length_m NOT NULL, no created_by_user_id
        pre_engine = create_engine(f"sqlite:///{db_path}", echo=False)

        @event.listens_for(pre_engine, "connect")
        def set_pragma(dbapi_conn, conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        with pre_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name VARCHAR(150) NOT NULL,
                    email VARCHAR(200) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'operator',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    remote_user_id VARCHAR(100),
                    sync_status VARCHAR(20) NOT NULL DEFAULT 'local_only',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE greenhouses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    location VARCHAR(200),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    greenhouse_id INTEGER NOT NULL REFERENCES greenhouses(id),
                    name VARCHAR(100) NOT NULL,
                    crop_type VARCHAR(100) NOT NULL DEFAULT 'Tomate Cherry',
                    width_m FLOAT,
                    length_m FLOAT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE monitorings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL REFERENCES modules(id),
                    status VARCHAR(20) NOT NULL DEFAULT 'initializing',
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    width_m FLOAT NOT NULL,
                    length_m FLOAT NOT NULL,
                    notes TEXT,
                    total_snapshots INTEGER NOT NULL DEFAULT 0,
                    total_detections INTEGER NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text("""
                CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitoring_id INTEGER NOT NULL REFERENCES monitorings(id),
                    captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    image_path VARCHAR(500) NOT NULL,
                    frame_index INTEGER NOT NULL,
                    change_score FLOAT,
                    has_detections BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text("""
                CREATE TABLE monitoring_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitoring_id INTEGER NOT NULL UNIQUE REFERENCES monitorings(id),
                    total_tomatoes INTEGER NOT NULL,
                    healthy_count INTEGER NOT NULL,
                    unhealthy_count INTEGER NOT NULL,
                    pct_healthy FLOAT NOT NULL,
                    pct_unhealthy FLOAT NOT NULL,
                    pct_green FLOAT NOT NULL DEFAULT 0,
                    pct_breaker FLOAT NOT NULL DEFAULT 0,
                    pct_turning FLOAT NOT NULL DEFAULT 0,
                    pct_pink FLOAT NOT NULL DEFAULT 0,
                    pct_light_red FLOAT NOT NULL DEFAULT 0,
                    pct_red FLOAT NOT NULL DEFAULT 0,
                    snapshots_with_detections INTEGER NOT NULL,
                    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE inspection_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                    detection_index INTEGER NOT NULL,
                    bbox_x1 INTEGER NOT NULL,
                    bbox_y1 INTEGER NOT NULL,
                    bbox_x2 INTEGER NOT NULL,
                    bbox_y2 INTEGER NOT NULL,
                    detection_score FLOAT NOT NULL,
                    health_label VARCHAR(20) NOT NULL,
                    health_confidence FLOAT NOT NULL,
                    maturity_stage VARCHAR(20),
                    maturity_percent FLOAT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Insert historical data
            conn.execute(text(
                "INSERT INTO users (id, full_name, email, password_hash) "
                "VALUES (1, 'Admin', 'admin@test.com', 'hash')"
            ))
            conn.execute(text(
                "INSERT INTO greenhouses (id, name) VALUES (1, 'Invernadero Hist')"
            ))
            conn.execute(text(
                "INSERT INTO modules (id, greenhouse_id, name) VALUES (1, 1, 'Mod1')"
            ))
            conn.execute(text(
                "INSERT INTO monitorings (id, module_id, status, width_m, length_m, "
                "total_snapshots, total_detections) "
                "VALUES (1, 1, 'completed', 4.0, 3.0, 2, 5)"
            ))
            conn.execute(text(
                "INSERT INTO snapshots (id, monitoring_id, image_path, frame_index) "
                "VALUES (1, 1, 'outputs/snap1.jpg', 0)"
            ))
            conn.execute(text(
                "INSERT INTO monitoring_metrics (id, monitoring_id, total_tomatoes, "
                "healthy_count, unhealthy_count, pct_healthy, pct_unhealthy, "
                "snapshots_with_detections) "
                "VALUES (1, 1, 5, 4, 1, 80.0, 20.0, 1)"
            ))
            conn.execute(text(
                "INSERT INTO inspection_results (id, snapshot_id, detection_index, "
                "bbox_x1, bbox_y1, bbox_x2, bbox_y2, detection_score, "
                "health_label, health_confidence) "
                "VALUES (1, 1, 0, 5, 10, 40, 50, 0.90, 'healthy', 0.88)"
            ))
            conn.commit()

        pre_engine.dispose()

        # Now run full init_db via DatabaseManager
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        with manager.engine.connect() as conn:
            # A. width_m nullable (dimensions migration ran)
            info = _get_column_info(conn, "monitorings")
            # After dimensions migration, width_m should be nullable (notnull=0)
            # Note: dimensions migration recreates the table, so it should be nullable
            assert info["width_m"][3] == 0, "width_m should be nullable after migration"
            assert info["length_m"][3] == 0, "length_m should be nullable after migration"

            # C. created_by_user_id exists (add_columns ran)
            assert "created_by_user_id" in info

            # D. sync_status exists (add_columns ran)
            assert "sync_status" in info

            # E. remote sync columns exist (sync migration ran)
            assert "remote_id" in info
            assert "remote_sync_status" in info
            assert "last_synced_at" in info
            assert "remote_sync_error" in info

            # F. historical rows preserved
            count = conn.execute(text("SELECT COUNT(*) FROM monitorings")).fetchone()[0]
            assert count == 1

            row = conn.execute(text(
                "SELECT status, width_m, length_m, total_detections FROM monitorings WHERE id=1"
            )).fetchone()
            assert row[0] == "completed"
            assert row[1] == 4.0
            assert row[2] == 3.0
            assert row[3] == 5

            # G. FK relationships preserved
            row = conn.execute(text("SELECT module_id FROM monitorings WHERE id=1")).fetchone()
            assert row[0] == 1
            row = conn.execute(text("SELECT monitoring_id FROM snapshots WHERE id=1")).fetchone()
            assert row[0] == 1

            # H. remote_sync_status == 'pending' for historical row
            row = conn.execute(text(
                "SELECT remote_sync_status FROM monitorings WHERE id=1"
            )).fetchone()
            assert row[0] == "pending"

            # Snapshot storage paths
            snap_cols = _get_columns(conn, "snapshots")
            assert "raw_storage_path" in snap_cols
            assert "annotated_storage_path" in snap_cols

            # I. Activity types seeded
            from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
            Session = sessionmaker(bind=manager.engine)
            sess = Session()
            types = sess.query(ActivityTypeModel).all()
            assert len(types) == 12
            sess.close()

            # Integrity checks
            result = conn.execute(text("PRAGMA integrity_check")).fetchone()
            assert result[0] == "ok"

            result = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
            assert result == []

            # Foreign keys enabled
            result = conn.execute(text("PRAGMA foreign_keys")).fetchone()
            assert result[0] == 1


# ===========================================================================
# 15. init_db idempotent (second call)
# ===========================================================================


class TestInitDbIdempotent:
    """Second call to init_db() on already-migrated DB is a safe no-op."""

    def test_second_init_db_no_error(self, tmp_path):
        db_path = str(tmp_path / "idempotent.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        # Second call must not raise
        manager.init_db()

    def test_second_init_db_preserves_data(self, tmp_path):
        db_path = str(tmp_path / "idempotent2.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        # Insert a row using ORM to respect Python-side defaults
        from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel
        Session = sessionmaker(bind=manager.engine)
        sess = Session()
        sess.add(GreenhouseModel(name="Test GH"))
        sess.commit()
        sess.close()

        # Second init_db
        manager.init_db()

        with manager.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM greenhouses")).fetchone()[0]
            assert count == 1

            row = conn.execute(text("SELECT name FROM greenhouses")).fetchone()
            assert row[0] == "Test GH"

    def test_second_init_db_no_duplicate_activity_types(self, tmp_path):
        db_path = str(tmp_path / "idempotent3.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()
        manager.init_db()

        from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
        Session = sessionmaker(bind=manager.engine)
        sess = Session()
        types = sess.query(ActivityTypeModel).all()
        assert len(types) == 12
        sess.close()


# ===========================================================================
# 16. Fresh database
# ===========================================================================


class TestFreshDatabase:
    """Fresh init_db() creates all tables with all columns from the start."""

    def test_fresh_db_has_all_sync_columns(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        sync_cols = {"remote_id", "remote_sync_status", "last_synced_at", "remote_sync_error"}

        with manager.engine.connect() as conn:
            for table in ["greenhouses", "modules", "monitorings", "snapshots",
                          "monitoring_metrics", "inspection_results", "activity_logs"]:
                cols = _get_columns(conn, table)
                assert sync_cols.issubset(cols), f"{table} missing sync columns"

            # Snapshot extras
            snap_cols = _get_columns(conn, "snapshots")
            assert "raw_storage_path" in snap_cols
            assert "annotated_storage_path" in snap_cols

    def test_fresh_db_seeds_activity_types(self, tmp_path):
        db_path = str(tmp_path / "fresh2.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
        Session = sessionmaker(bind=manager.engine)
        sess = Session()
        types = sess.query(ActivityTypeModel).all()
        assert len(types) == 12
        sess.close()

    def test_new_orm_record_defaults_to_pending(self, tmp_path):
        """A new ORM record gets remote_sync_status='pending' and NULL remote fields."""
        from src.infrastructure.persistence.models.greenhouse_model import GreenhouseModel

        db_path = str(tmp_path / "fresh_defaults.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        Session = sessionmaker(bind=manager.engine)
        sess = Session()
        gh = GreenhouseModel(name="Invernadero Nuevo")
        sess.add(gh)
        sess.commit()
        sess.refresh(gh)

        assert gh.remote_sync_status == "pending"
        assert gh.remote_id is None
        assert gh.last_synced_at is None
        assert gh.remote_sync_error is None
        sess.close()


# ===========================================================================
# 17-18. Integrity and FK checks
# ===========================================================================


class TestIntegrityChecks:
    """PRAGMA integrity_check and foreign_key_check pass after migration."""

    def test_integrity_after_migration(self, seeded_historical_engine):
        _migrate_add_sync_columns(seeded_historical_engine)

        with seeded_historical_engine.connect() as conn:
            result = conn.execute(text("PRAGMA integrity_check")).fetchone()
            assert result[0] == "ok"

    def test_foreign_key_check_after_migration(self, seeded_historical_engine):
        _migrate_add_sync_columns(seeded_historical_engine)

        with seeded_historical_engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
            assert result == []

    def test_foreign_keys_enabled_after_init_db(self, tmp_path):
        db_path = str(tmp_path / "fk_test.db")
        manager = DatabaseManager(db_path=db_path)
        manager.init_db()

        with manager.engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys")).fetchone()
            assert result[0] == 1
