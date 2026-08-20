"""Database connection management for SQLite persistence.

Provides centralized engine creation, session factory, PRAGMA configuration,
idempotent schema initialization, activity type seeding, and migration helper.
"""

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.persistence.exceptions import DatabaseInitError
from src.infrastructure.persistence.models.base import Base


# Initial activity type catalog definition
INITIAL_ACTIVITY_TYPES = [
    ("riego", "Riego", "mantenimiento", False, True, "L"),
    ("fertilizacion", "Fertilización", "nutrición", True, True, "L"),
    ("fitosanitario", "Aplicación fitosanitaria", "protección", True, True, "mL"),
    ("monitoreo_plagas", "Monitoreo de plagas", "vigilancia", False, False, None),
    ("monitoreo_enfermedades", "Monitoreo de enfermedades", "vigilancia", False, False, None),
    ("poda", "Poda", "manejo_planta", False, False, None),
    ("deshoje", "Deshoje", "manejo_planta", False, False, None),
    ("tutorado", "Tutorado / amarre", "manejo_planta", False, False, None),
    ("limpieza", "Limpieza del módulo", "mantenimiento", False, False, None),
    ("cosecha", "Cosecha", "producción", False, True, "kg"),
    ("inspeccion_visual", "Inspección visual", "vigilancia", False, False, None),
    ("observacion_general", "Observación general", "general", False, False, None),
]


def seed_activity_types(session: Session) -> None:
    """Insert initial activity types if they don't already exist.

    Idempotent — checks by code before inserting.
    """
    from src.infrastructure.persistence.models.activity_type_model import (
        ActivityTypeModel,
    )

    for code, name, category, requires_product, allows_quantity, default_unit in INITIAL_ACTIVITY_TYPES:
        existing = (
            session.query(ActivityTypeModel)
            .filter(ActivityTypeModel.code == code)
            .first()
        )
        if existing is None:
            model = ActivityTypeModel(
                code=code,
                name=name,
                category=category,
                requires_product=requires_product,
                allows_quantity=allows_quantity,
                default_unit=default_unit,
                is_active=True,
            )
            session.add(model)
    session.commit()


def _migrate_add_columns(engine) -> None:
    """Non-destructive migration: add missing columns to existing tables.

    Uses PRAGMA table_info to detect if columns exist before altering.
    Safe to call on every startup — does nothing if columns already exist.
    """
    with engine.connect() as conn:
        # Check and add monitoring_frequency_days to modules
        result = conn.execute(text("PRAGMA table_info(modules)"))
        module_columns = {row[1] for row in result.fetchall()}
        if "monitoring_frequency_days" not in module_columns:
            conn.execute(
                text("ALTER TABLE modules ADD COLUMN monitoring_frequency_days INTEGER")
            )

        # Check and add columns to monitorings
        result = conn.execute(text("PRAGMA table_info(monitorings)"))
        monitoring_columns = {row[1] for row in result.fetchall()}
        if "created_by_user_id" not in monitoring_columns:
            conn.execute(
                text("ALTER TABLE monitorings ADD COLUMN created_by_user_id INTEGER REFERENCES users(id)")
            )
        if "sync_status" not in monitoring_columns:
            conn.execute(
                text("ALTER TABLE monitorings ADD COLUMN sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'")
            )

        conn.commit()


class DatabaseManager:
    """Manages SQLite database connection, session factory, and schema initialization."""

    def __init__(self, db_path: str = "data/tomato_monitor.db") -> None:
        """Initialize the database manager.

        Creates the data directory if needed and configures the SQLAlchemy engine.
        Raises DatabaseInitError on permission/disk failures.

        Args:
            db_path: Path to the SQLite database file, relative to the project root.
        """
        # Create data directory
        db_dir = os.path.dirname(db_path)
        if db_dir:
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                raise DatabaseInitError(
                    cause=f"Cannot create directory '{db_dir}': {e}",
                    original=e,
                )

        # Create engine
        self._engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
        )

        # Apply PRAGMAs on every connection
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()

        # Session factory
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
        )

    def init_db(self) -> None:
        """Create all tables, run migrations, and seed reference data.

        Idempotent — safe to call on every application startup.
        Raises DatabaseInitError if schema creation fails.
        """
        try:
            Base.metadata.create_all(self._engine, checkfirst=True)
            _migrate_add_columns(self._engine)
            session = self._session_factory()
            try:
                seed_activity_types(session)
            finally:
                session.close()
        except DatabaseInitError:
            raise
        except Exception as e:
            raise DatabaseInitError(
                cause=f"Schema creation failed: {e}",
                original=e,
            )

    def get_session(self) -> Session:
        """Create and return a new SQLAlchemy Session."""
        return self._session_factory()

    @property
    def engine(self):
        """Access the underlying engine (for testing/inspection)."""
        return self._engine
