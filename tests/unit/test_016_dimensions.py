"""Integration tests for optional dimensions (Fix 4).

Tests cover:
- validate_optional_dimensions() (unit)
- DatabaseManager.init_db() with old schema (integration)
- Full startup cycle via init_db() (idempotency, FK integrity)

Validates: Requirements 2.4, 3.3, 3.11
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, text

from src.application.validators import (
    validate_optional_dimensions,
    validate_dimensions,
    ValidationError,
)


class TestValidateOptionalDimensions:
    """Tests for the optional dimension validator."""

    def test_both_empty_returns_none_none(self):
        assert validate_optional_dimensions("", "") == (None, None)

    def test_both_spaces_returns_none_none(self):
        assert validate_optional_dimensions("   ", "   ") == (None, None)

    def test_both_filled_valid(self):
        assert validate_optional_dimensions("5.0", "3.0") == (5.0, 3.0)

    def test_only_width_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_optional_dimensions("5.0", "")
        assert "ambas dimensiones" in exc_info.value.message

    def test_only_length_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_optional_dimensions("", "3.0")
        assert "ambas dimensiones" in exc_info.value.message

    def test_negative_width_raises(self):
        with pytest.raises(ValidationError):
            validate_optional_dimensions("-1.0", "3.0")

    def test_negative_length_raises(self):
        with pytest.raises(ValidationError):
            validate_optional_dimensions("5.0", "-1.0")

    def test_zero_raises(self):
        with pytest.raises(ValidationError):
            validate_optional_dimensions("0", "3.0")

    @given(
        width=st.floats(min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False),
        length=st.floats(min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_all_positive_pairs_accepted(self, width, length):
        w, l = validate_optional_dimensions(f"{width:.4f}", f"{length:.4f}")
        assert w is not None
        assert l is not None
        assert abs(w - width) < 0.01
        assert abs(l - length) < 0.01


class TestDatabaseManagerInitDbMigration:
    """Integration: DatabaseManager.init_db() migrates old NOT NULL dimensions.

    Creates a temporary SQLite with the OLD schema (width_m NOT NULL),
    inserts data, then calls init_db() and verifies migration + data integrity.
    """

    def test_init_db_migrates_old_schema_and_preserves_data(self, tmp_path):
        """Full init_db() cycle on a DB with old NOT NULL columns."""
        from src.infrastructure.persistence.database import DatabaseManager

        db_file = tmp_path / "test_migrate.db"

        # 1. Create OLD schema manually (simulating pre-migration DB)
        old_engine = create_engine(f"sqlite:///{db_file}", echo=False)

        @event.listens_for(old_engine, "connect")
        def _pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        with old_engine.connect() as conn:
            # Minimal schema that matches old production (pre-migration)
            conn.execute(text("""
                CREATE TABLE greenhouses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    location VARCHAR(200),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    greenhouse_id INTEGER NOT NULL REFERENCES greenhouses(id),
                    name VARCHAR(100) NOT NULL,
                    crop_type VARCHAR(100) NOT NULL DEFAULT 'Tomate Cherry',
                    width_m REAL,
                    length_m REAL,
                    monitoring_frequency_days INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
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
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login_at DATETIME
                )
            """))
            conn.execute(text("""
                CREATE TABLE monitorings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL REFERENCES modules(id),
                    status VARCHAR(20) NOT NULL DEFAULT 'initializing',
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    width_m REAL NOT NULL,
                    length_m REAL NOT NULL,
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
                    captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    image_path VARCHAR(500) NOT NULL,
                    frame_index INTEGER NOT NULL,
                    change_score REAL,
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
                    pct_healthy REAL NOT NULL,
                    pct_unhealthy REAL NOT NULL,
                    pct_green REAL NOT NULL DEFAULT 0,
                    pct_breaker REAL NOT NULL DEFAULT 0,
                    pct_turning REAL NOT NULL DEFAULT 0,
                    pct_pink REAL NOT NULL DEFAULT 0,
                    pct_light_red REAL NOT NULL DEFAULT 0,
                    pct_red REAL NOT NULL DEFAULT 0,
                    snapshots_with_detections INTEGER NOT NULL,
                    computed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # 2. Insert test data
            conn.execute(text("INSERT INTO greenhouses (name) VALUES ('GH1')"))
            conn.execute(text("INSERT INTO modules (greenhouse_id, name) VALUES (1, 'Mod1')"))
            conn.execute(text("""
                INSERT INTO monitorings (module_id, status, width_m, length_m, total_snapshots, total_detections)
                VALUES (1, 'completed', 5.0, 3.0, 10, 45)
            """))
            conn.execute(text("""
                INSERT INTO snapshots (monitoring_id, image_path, frame_index, has_detections)
                VALUES (1, 'outputs/monitorings/1/snapshots/raw/snapshot_000001.jpg', 1, 1)
            """))
            conn.execute(text("""
                INSERT INTO monitoring_metrics (
                    monitoring_id, total_tomatoes, healthy_count, unhealthy_count,
                    pct_healthy, pct_unhealthy, snapshots_with_detections
                ) VALUES (1, 45, 35, 10, 77.8, 22.2, 8)
            """))
            conn.commit()
        old_engine.dispose()

        # 3. Run DatabaseManager.init_db() — this triggers the real migration path
        dm = DatabaseManager(db_path=str(db_file))
        dm.init_db()

        # 4. Verify: PRAGMA table_info shows width_m as nullable
        with dm.engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(monitorings)"))
            cols = {row[1]: row for row in result.fetchall()}
            # row[3] = notnull flag: 0 means nullable
            assert cols["width_m"][3] == 0, "width_m should be nullable after migration"
            assert cols["length_m"][3] == 0, "length_m should be nullable after migration"

        # 5. Verify data survived
        with dm.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT width_m, length_m, total_snapshots, total_detections FROM monitorings WHERE id=1"
            )).fetchone()
            assert row[0] == 5.0
            assert row[1] == 3.0
            assert row[2] == 10
            assert row[3] == 45

            # FK integrity: snapshot still references monitoring
            snap = conn.execute(text("SELECT monitoring_id FROM snapshots WHERE id=1")).fetchone()
            assert snap[0] == 1

            # FK integrity: metrics still references monitoring
            met = conn.execute(text("SELECT monitoring_id FROM monitoring_metrics WHERE id=1")).fetchone()
            assert met[0] == 1

            # foreign_key_check is clean
            fk_violations = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
            assert fk_violations == []

            # foreign_keys is ON
            fk_status = conn.execute(text("PRAGMA foreign_keys")).fetchone()
            assert fk_status[0] == 1

        # 6. Idempotency: second init_db() does not error
        dm.init_db()

        # 7. NULL dimensions now accepted
        with dm.engine.connect() as conn:
            conn.execute(text("INSERT INTO modules (greenhouse_id, name) VALUES (1, 'Mod2')"))
            conn.execute(text("""
                INSERT INTO monitorings (module_id, status, width_m, length_m, total_snapshots, total_detections)
                VALUES (2, 'completed', NULL, NULL, 0, 0)
            """))
            conn.commit()
            null_row = conn.execute(text(
                "SELECT width_m, length_m FROM monitorings WHERE module_id=2"
            )).fetchone()
            assert null_row[0] is None
            assert null_row[1] is None

    def test_fresh_install_works_without_migration(self, tmp_path):
        """On a fresh DB with no pre-existing tables, init_db() creates nullable schema."""
        from src.infrastructure.persistence.database import DatabaseManager

        db_file = tmp_path / "fresh.db"
        dm = DatabaseManager(db_path=str(db_file))
        dm.init_db()

        with dm.engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(monitorings)"))
            cols = {row[1]: row for row in result.fetchall()}
            assert cols["width_m"][3] == 0, "width_m should be nullable on fresh install"
            assert cols["length_m"][3] == 0, "length_m should be nullable on fresh install"
