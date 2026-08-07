"""Tests for activity type seeding and schema migration logic.

Validates:
- seed_activity_types inserts all 12 types
- seed is idempotent (running twice doesn't duplicate)
- _migrate_add_columns adds missing columns to existing tables
"""

import pytest
from sqlalchemy import create_engine, event, text, Integer, String, Float, DateTime
from sqlalchemy import Column, ForeignKey
from sqlalchemy.orm import sessionmaker, Session

from src.infrastructure.persistence.database import (
    DatabaseManager,
    seed_activity_types,
    _migrate_add_columns,
    INITIAL_ACTIVITY_TYPES,
)
from src.infrastructure.persistence.models.activity_type_model import ActivityTypeModel
from src.infrastructure.persistence.models.base import Base


class TestSeedActivityTypes:
    """Tests for the activity type seed function."""

    def test_seeds_all_12_types(self, db_session):
        """After init_db, all 12 initial activity types should exist."""
        # db_manager fixture already calls init_db which seeds
        types = db_session.query(ActivityTypeModel).all()
        assert len(types) == 12

    def test_seed_codes_match_catalog(self, db_session):
        """All expected codes should be present."""
        types = db_session.query(ActivityTypeModel).all()
        codes = {t.code for t in types}
        expected_codes = {row[0] for row in INITIAL_ACTIVITY_TYPES}
        assert codes == expected_codes

    def test_seed_is_idempotent(self, db_session):
        """Running seed twice should not create duplicates."""
        # Already seeded by init_db
        seed_activity_types(db_session)
        types = db_session.query(ActivityTypeModel).all()
        assert len(types) == 12

    def test_seed_specific_type_properties(self, db_session):
        """Verify specific activity type properties."""
        fertilizacion = (
            db_session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.code == "fertilizacion")
            .first()
        )
        assert fertilizacion is not None
        assert fertilizacion.name == "Fertilización"
        assert fertilizacion.category == "nutrición"
        assert fertilizacion.requires_product is True
        assert fertilizacion.allows_quantity is True
        assert fertilizacion.default_unit == "L"

        poda = (
            db_session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.code == "poda")
            .first()
        )
        assert poda is not None
        assert poda.requires_product is False
        assert poda.allows_quantity is False
        assert poda.default_unit is None


class TestSchemaMigration:
    """Tests for the non-destructive column migration logic."""

    def test_migrate_adds_missing_columns_to_modules(self):
        """Simulate old schema missing monitoring_frequency_days, then migrate."""
        engine = create_engine("sqlite:///:memory:", echo=False)

        @event.listens_for(engine, "connect")
        def set_pragma(dbapi_conn, conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        # Create old-style modules table WITHOUT monitoring_frequency_days
        with engine.connect() as conn:
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
                    width_m FLOAT NOT NULL,
                    length_m FLOAT NOT NULL,
                    notes TEXT,
                    total_snapshots INTEGER NOT NULL DEFAULT 0,
                    total_detections INTEGER NOT NULL DEFAULT 0
                )
            """))
            conn.commit()

        # Run migration
        _migrate_add_columns(engine)

        # Verify columns exist
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(modules)"))
            module_cols = {row[1] for row in result.fetchall()}
            assert "monitoring_frequency_days" in module_cols

            result = conn.execute(text("PRAGMA table_info(monitorings)"))
            monitoring_cols = {row[1] for row in result.fetchall()}
            assert "created_by_user_id" in monitoring_cols
            assert "sync_status" in monitoring_cols

    def test_migrate_is_idempotent(self):
        """Running migration twice should not fail."""
        engine = create_engine("sqlite:///:memory:", echo=False)

        @event.listens_for(engine, "connect")
        def set_pragma(dbapi_conn, conn_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        # Create tables with all columns already present
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE greenhouses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE
                )
            """))
            conn.execute(text("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name VARCHAR(150) NOT NULL,
                    email VARCHAR(200) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    greenhouse_id INTEGER NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    monitoring_frequency_days INTEGER
                )
            """))
            conn.execute(text("""
                CREATE TABLE monitorings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'initializing',
                    width_m FLOAT NOT NULL,
                    length_m FLOAT NOT NULL,
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    total_snapshots INTEGER NOT NULL DEFAULT 0,
                    total_detections INTEGER NOT NULL DEFAULT 0,
                    created_by_user_id INTEGER,
                    sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                )
            """))
            conn.commit()

        # Should not raise
        _migrate_add_columns(engine)
        _migrate_add_columns(engine)

    def test_full_init_db_creates_all_tables(self):
        """DatabaseManager.init_db() creates all tables and seeds data."""
        manager = DatabaseManager(db_path=":memory:")
        manager.init_db()

        session = manager.get_session()
        try:
            # Check activity types are seeded
            types = session.query(ActivityTypeModel).all()
            assert len(types) == 12

            # Check new tables exist by querying them
            from src.infrastructure.persistence.models.user_model import UserModel
            from src.infrastructure.persistence.models.export_package_model import ExportPackageModel
            from src.infrastructure.persistence.models.activity_log_model import ActivityLogModel

            users = session.query(UserModel).all()
            assert users == []

            exports = session.query(ExportPackageModel).all()
            assert exports == []

            logs = session.query(ActivityLogModel).all()
            assert logs == []
        finally:
            session.close()
