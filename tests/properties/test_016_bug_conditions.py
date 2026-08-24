"""Bug condition exploration tests for Spec 016 — Post-Raspberry Stabilization.

These tests encode EXPECTED behavior (the fix targets). They are designed to
FAIL on the unfixed codebase, confirming the bugs exist. Once fixes are applied,
these tests should PASS.

**DO NOT fix the tests or the code when they fail.**

Testing framework: pytest + hypothesis
Validates: Requirements 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 1.10, 1.11, 1.14
"""

import pytest
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Bug Condition 1: EDGE profile analysis_thermal_pause_threshold == 78.0
# Currently 72.0 — will FAIL
# Validates: Requirements 1.5
# ---------------------------------------------------------------------------


class TestEdgeThermalThreshold:
    """EDGE profile should pause analysis at 78°C, not 72°C."""

    def test_edge_analysis_thermal_pause_threshold_is_78(self):
        """**Validates: Requirements 1.5**

        The EDGE profile's analysis_thermal_pause_threshold should be 78.0°C.
        At 72°C the SoC operates normally during inference — pausing there
        causes excessive interruptions.
        """
        from src.infrastructure.config.settings import EDGE_PROFILE

        assert EDGE_PROFILE.analysis_thermal_pause_threshold == 78.0, (
            f"EDGE analysis_thermal_pause_threshold is "
            f"{EDGE_PROFILE.analysis_thermal_pause_threshold}, expected 78.0"
        )

    def test_edge_analysis_thermal_resume_threshold_is_72(self):
        """**Validates: Requirements 1.5**

        The EDGE profile's analysis_thermal_resume_threshold should be 72.0°C.
        """
        from src.infrastructure.config.settings import EDGE_PROFILE

        assert EDGE_PROFILE.analysis_thermal_resume_threshold == 72.0, (
            f"EDGE analysis_thermal_resume_threshold is "
            f"{EDGE_PROFILE.analysis_thermal_resume_threshold}, expected 72.0"
        )


# ---------------------------------------------------------------------------
# Bug Condition 2: LogService timestamp has tzinfo
# Currently naive (datetime.utcnow()) — will FAIL
# Validates: Requirements 1.7
# ---------------------------------------------------------------------------


class TestLogServiceTimezone:
    """LogService entries should have timezone-aware timestamps."""

    def test_log_entry_timestamp_has_tzinfo(self):
        """**Validates: Requirements 1.7**

        LogService.add_entry() should produce a LogEntry with a timezone-aware
        timestamp (tzinfo is not None). Currently uses datetime.utcnow() which
        produces naive datetimes.
        """
        from src.application.services.log_service import LogService, LogLevel

        service = LogService()
        entry = service.add_entry(
            monitoring_id=1,
            level=LogLevel.INFO,
            source="test",
            message="Test message",
        )

        assert entry.timestamp.tzinfo is not None, (
            f"LogEntry.timestamp.tzinfo is None — timestamp is naive. "
            f"Got: {entry.timestamp!r}"
        )


# ---------------------------------------------------------------------------
# Bug Condition 3: to_bogota() utility converts UTC naive to America/Bogota
# Function doesn't exist yet — will FAIL/ERROR
# Validates: Requirements 1.6
# ---------------------------------------------------------------------------


class TestToBogotaUtility:
    """Centralized timezone utility to_bogota() should exist and work."""

    def test_to_bogota_converts_utc_naive_to_bogota(self):
        """**Validates: Requirements 1.6**

        to_bogota() should accept a naive UTC datetime and return it
        converted to America/Bogota (UTC-5, no DST).
        """
        from src.application.utils.timezone import to_bogota

        # 2024-01-15 02:17:00 UTC → 2024-01-14 21:17:00 Bogota
        utc_naive = datetime(2024, 1, 15, 2, 17, 0)
        result = to_bogota(utc_naive)

        assert result is not None
        assert result.hour == 21, f"Expected hour 21, got {result.hour}"
        assert result.day == 14, f"Expected day 14, got {result.day}"

    def test_to_bogota_handles_none(self):
        """**Validates: Requirements 1.6**

        to_bogota(None) should return None gracefully.
        """
        from src.application.utils.timezone import to_bogota

        result = to_bogota(None)
        assert result is None


# ---------------------------------------------------------------------------
# Bug Condition 4: bogota_to_utc() converts local 21:17 to UTC 02:17
# Function doesn't exist yet — will FAIL/ERROR
# Validates: Requirements 1.14
# ---------------------------------------------------------------------------


class TestBogotaToUtcUtility:
    """Centralized timezone utility bogota_to_utc() should exist and work."""

    def test_bogota_to_utc_converts_local_to_utc(self):
        """**Validates: Requirements 1.14**

        bogota_to_utc() should interpret a naive datetime as America/Bogota
        and convert to UTC. 21:17 Bogota = 02:17 UTC (next day).
        """
        from src.application.utils.timezone import bogota_to_utc

        # 2024-01-14 21:17:00 Bogota → 2024-01-15 02:17:00 UTC
        local_naive = datetime(2024, 1, 14, 21, 17, 0)
        result = bogota_to_utc(local_naive)

        assert result is not None
        assert result.hour == 2, f"Expected hour 2, got {result.hour}"
        assert result.day == 15, f"Expected day 15, got {result.day}"
        # Should be timezone-aware UTC
        # bogota_to_utc returns naive UTC for SQLite persistence


# ---------------------------------------------------------------------------
# Bug Condition 5: Orphan export reconciliation transitions "generating" → "error"
# No reconciliation mechanism at startup — will FAIL
# Validates: Requirements 1.11
# ---------------------------------------------------------------------------


class TestOrphanExportReconciliation:
    """Application startup should reconcile orphan 'generating' exports."""

    def test_generating_exports_reconciled_at_startup(self, db_manager):
        """**Validates: Requirements 1.11**

        After application startup (init_db + reconciliation), any ExportPackage
        records stuck in 'generating' status should be transitioned to 'error'.
        """
        from src.infrastructure.persistence.models.export_package_model import (
            ExportPackageModel,
        )
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.application.services.export_reconciliation import reconcile_orphan_exports

        session = db_manager.get_session()

        # Create a test user for the FK
        user = UserModel(
            full_name="Test User",
            email="orphan_test@example.com",
            password_hash="hash123",
            role="operator",
        )
        session.add(user)
        session.flush()

        # Create an orphan export stuck in "generating"
        orphan = ExportPackageModel(
            created_by_user_id=user.id,
            scope="full",
            status="generating",
            records_count=0,
            images_count=0,
        )
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
        session.close()

        # Call the REAL reconciliation function (same one called by lifespan)
        count = reconcile_orphan_exports(db_manager)
        assert count == 1

        verify_session = db_manager.get_session()
        record = (
            verify_session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == orphan_id)
            .first()
        )

        assert record.status == "error", (
            f"Orphan export remains in status='{record.status}'. "
            f"Expected 'error' after startup reconciliation."
        )
        verify_session.close()


# ---------------------------------------------------------------------------
# Bug Condition 6: Dashboard does NOT contain "Acceso rápido"
# Currently present — will FAIL
# Validates: Requirements 1.1
# ---------------------------------------------------------------------------


class TestDashboardNoAccesoRapido:
    """Dashboard should not show the redundant 'Acceso rápido' section."""

    def test_dashboard_does_not_contain_acceso_rapido(self, authenticated_client):
        """**Validates: Requirements 1.1**

        The dashboard rendered HTML should NOT contain the text 'Acceso rápido'
        because it duplicates the bottom navigation bar.
        """
        response = authenticated_client.get("/")
        assert response.status_code == 200

        html_content = response.text
        assert "Acceso rápido" not in html_content, (
            "Dashboard still contains 'Acceso rápido' section. "
            "This should be removed — it duplicates the bottom nav."
        )


# ---------------------------------------------------------------------------
# Bug Condition 7: validate_optional_dimensions("", "") returns valid (None, None)
# Function doesn't exist yet — will FAIL/ERROR
# Validates: Requirements 1.4
# ---------------------------------------------------------------------------


class TestValidateOptionalDimensions:
    """Optional dimension validation should accept both-empty as valid."""

    def test_both_empty_returns_none_none(self):
        """**Validates: Requirements 1.4**

        validate_optional_dimensions("", "") should return (None, None)
        indicating valid empty dimensions. Currently the function does not exist.
        """
        from src.application.validators import validate_optional_dimensions

        result = validate_optional_dimensions("", "")
        assert result == (None, None), (
            f"Expected (None, None) for both-empty dimensions, got {result}"
        )

    def test_both_filled_valid_returns_floats(self):
        """**Validates: Requirements 1.4**

        validate_optional_dimensions("5.0", "3.0") should return (5.0, 3.0).
        """
        from src.application.validators import validate_optional_dimensions

        result = validate_optional_dimensions("5.0", "3.0")
        assert result == (5.0, 3.0), f"Expected (5.0, 3.0), got {result}"

    def test_one_filled_one_empty_raises_error(self):
        """**Validates: Requirements 1.4**

        validate_optional_dimensions("5.0", "") should raise ValidationError
        because both must be provided or both empty.
        """
        from src.application.validators import validate_optional_dimensions, ValidationError

        with pytest.raises(ValidationError):
            validate_optional_dimensions("5.0", "")


# ---------------------------------------------------------------------------
# Bug Condition 8: SQLite migration preserves existing monitoring data
# Migration doesn't exist yet — will FAIL/ERROR
# Validates: Requirements 1.4 (end-to-end)
# ---------------------------------------------------------------------------


class TestSqliteDimensionMigration:
    """SQLite migration for nullable dimensions should preserve existing data."""

    def test_migration_preserves_monitoring_with_dimensions(self):
        """**Validates: Requirements 1.4**

        After running the migration that makes width_m/length_m nullable,
        existing monitoring records with non-null dimensions must be preserved.
        """
        from sqlalchemy import create_engine, event, text
        from sqlalchemy.orm import sessionmaker

        # Create a fresh in-memory DB with the OLD schema (NOT NULL)
        engine = create_engine("sqlite:///:memory:", echo=False)

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        with engine.connect() as conn:
            # Create old schema with NOT NULL dimensions
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
                    width_m REAL,
                    length_m REAL,
                    monitoring_frequency_days INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
                    captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
                    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Insert test data
            conn.execute(text(
                "INSERT INTO greenhouses (name) VALUES ('Test GH')"
            ))
            conn.execute(text(
                "INSERT INTO modules (greenhouse_id, name) VALUES (1, 'Mod 1')"
            ))
            conn.execute(text("""
                INSERT INTO monitorings (module_id, status, width_m, length_m, total_snapshots, total_detections)
                VALUES (1, 'completed', 5.0, 3.0, 10, 45)
            """))
            conn.execute(text("""
                INSERT INTO snapshots (monitoring_id, image_path, frame_index, has_detections)
                VALUES (1, 'outputs/monitorings/1/snapshots/raw/snapshot_000001.jpg', 1, 1)
            """))
            conn.execute(text("""
                INSERT INTO monitoring_metrics (monitoring_id, total_tomatoes, healthy_count, unhealthy_count,
                    pct_healthy, pct_unhealthy, snapshots_with_detections)
                VALUES (1, 45, 35, 10, 77.8, 22.2, 8)
            """))
            conn.commit()

        # Now attempt to run the migration that makes dimensions nullable
        # This function does NOT exist yet — will cause ImportError or AttributeError
        from src.infrastructure.persistence.database import _migrate_dimensions_nullable

        _migrate_dimensions_nullable(engine)

        # Verify data is preserved
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT width_m, length_m, total_snapshots, total_detections FROM monitorings WHERE id = 1"
            )).fetchone()

            assert row is not None, "Monitoring record lost after migration!"
            assert row[0] == 5.0, f"width_m changed: expected 5.0, got {row[0]}"
            assert row[1] == 3.0, f"length_m changed: expected 3.0, got {row[1]}"
            assert row[2] == 10, f"total_snapshots changed: expected 10, got {row[2]}"
            assert row[3] == 45, f"total_detections changed: expected 45, got {row[3]}"

            # Verify FK integrity preserved
            snapshot = conn.execute(text(
                "SELECT monitoring_id FROM snapshots WHERE id = 1"
            )).fetchone()
            assert snapshot[0] == 1, "Snapshot FK broken after migration"

            metrics = conn.execute(text(
                "SELECT monitoring_id FROM monitoring_metrics WHERE id = 1"
            )).fetchone()
            assert metrics[0] == 1, "Metrics FK broken after migration"

            # Verify nullable now works
            conn.execute(text("""
                INSERT INTO monitorings (module_id, status, width_m, length_m, total_snapshots, total_detections)
                VALUES (1, 'completed', NULL, NULL, 0, 0)
            """))
            conn.commit()

            null_row = conn.execute(text(
                "SELECT width_m, length_m FROM monitorings WHERE id = 2"
            )).fetchone()
            assert null_row[0] is None, "width_m should be nullable after migration"
            assert null_row[1] is None, "length_m should be nullable after migration"


# ---------------------------------------------------------------------------
# Bug Condition 9: history_service _ACTIVITY_ICONS does NOT contain emoji chars
# Currently uses emojis — will FAIL
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------


class TestHistoryServiceNoEmojis:
    """History service activity icons should NOT use emoji Unicode characters."""

    def test_activity_icons_no_emoji_unicode(self):
        """**Validates: Requirements 1.2**

        _ACTIVITY_ICONS values should be semantic string keys (e.g., 'watering',
        'harvest'), NOT Unicode emoji characters that may not render on
        Raspberry Pi OS without emoji fonts.
        """
        from src.application.services.history_service import _ACTIVITY_ICONS
        import re

        # Common emoji Unicode ranges
        emoji_pattern = re.compile(
            "["
            "\U0001F300-\U0001F9FF"  # Misc Symbols, Emoticons, etc.
            "\U00002702-\U000027B0"  # Dingbats
            "\U0000FE00-\U0000FE0F"  # Variation Selectors
            "\U0000200D"             # Zero Width Joiner
            "\U00002600-\U000026FF"  # Misc symbols
            "\U0000231A-\U0000231B"  # Watch/Hourglass
            "\U00002328"             # Keyboard
            "\U000023CF"             # Eject
            "\U000023E9-\U000023F3"  # Various controls
            "\U000023F8-\U000023FA"  # Various controls
            "\U0000270A-\U0000270D"  # Fist/Pen etc.
            "\U00002764"             # Heart
            "\U0000FE0F"             # Variation Selector-16
            "\U0001FA70-\U0001FAFF"  # Symbols/Pictographs Extended-A
            "\U00002702-\U000027B0"  # Dingbats
            "]+",
            flags=re.UNICODE,
        )

        icons_with_emojis = {}
        for key, value in _ACTIVITY_ICONS.items():
            if emoji_pattern.search(value):
                icons_with_emojis[key] = value

        assert len(icons_with_emojis) == 0, (
            f"_ACTIVITY_ICONS contains emoji characters that won't render on "
            f"Raspberry Pi OS: {icons_with_emojis}"
        )
