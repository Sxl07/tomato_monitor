"""Database connection management for SQLite persistence.

Provides centralized engine creation, session factory, PRAGMA configuration,
idempotent schema initialization, activity type seeding, and migration helper.
"""

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.persistence.exceptions import DatabaseInitError
from src.infrastructure.persistence.models.base import Base

# Import the models package so all ORM models attach to Base.metadata before
# create_all() runs. Without this, table creation would depend on import
# ordering elsewhere. Importing the package registers every model, including
# the deletion outbox tables (deletion_outbox, deletion_outbox_storage_path,
# deletion_outbox_local_artifact). Migration-safe: create_all(checkfirst=True)
# only creates missing tables and never alters existing ones.
import src.infrastructure.persistence.models  # noqa: F401


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




def _migrate_dimensions_nullable(engine) -> None:
    """Migrate width_m/length_m from NOT NULL to nullable.

    SQLite doesn't support ALTER COLUMN. Strategy:
    1. PRAGMA table_info -> detect if width_m is NOT NULL
    2. If already nullable -> no-op (idempotent)
    3. Disable foreign_keys via raw DBAPI, VERIFY OFF
    4. BEGIN transaction
    5. Create monitorings_new with correct schema (nullable width_m/length_m)
    6. Copy data with EXPLICIT column list
    7. DROP old monitorings
    8. RENAME monitorings_new -> monitorings
    9. PRAGMA foreign_key_check -> if violations: ROLLBACK + ERROR
    10. COMMIT (only if FK check passes)
    11. Re-enable foreign_keys via raw DBAPI, VERIFY ON
    """
    with engine.connect() as conn:
        # 1. Check current schema
        result = conn.execute(text("PRAGMA table_info(monitorings)"))
        columns_info = result.fetchall()

        if not columns_info:
            return  # Table doesn't exist yet (fresh install handles via create_all)

        columns = {row[1]: row for row in columns_info}

        width_col = columns.get("width_m")
        if width_col is None:
            return  # Table doesn't have the column yet

        # row[3] is 'notnull' flag (1 = NOT NULL, 0 = nullable)
        if width_col[3] == 0:
            return  # Already nullable, nothing to do

        # 2. Get explicit column list for safe INSERT
        column_names = [row[1] for row in columns_info]
        col_list = ", ".join(column_names)

        # 3. Close any open transaction; FK OFF must be outside transaction
        conn.commit()

        # Use raw DBAPI connection for reliable PRAGMA control
        raw_conn = conn.connection.dbapi_connection

        raw_conn.execute("PRAGMA foreign_keys = OFF")

        # VERIFY FK is actually disabled
        cursor = raw_conn.execute("PRAGMA foreign_keys")
        fk_status = cursor.fetchone()[0]
        if fk_status != 0:
            raise DatabaseInitError(
                cause="Failed to disable foreign_keys for migration",
                original=None,
            )

        try:
            # 4-8. All destructive ops in one transaction
            raw_conn.execute("BEGIN")

            raw_conn.execute("""
                CREATE TABLE monitorings_new (
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
            """)

            raw_conn.execute(f"""
                INSERT INTO monitorings_new ({col_list})
                SELECT {col_list} FROM monitorings
            """)

            raw_conn.execute("DROP TABLE monitorings")
            raw_conn.execute("ALTER TABLE monitorings_new RENAME TO monitorings")

            # 9. FK check BEFORE commit -- can still ROLLBACK
            violations = raw_conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raw_conn.execute("ROLLBACK")
                raise DatabaseInitError(
                    cause=f"FK integrity violation after migration: {violations}",
                    original=None,
                )

            # 10. All good -- commit
            raw_conn.execute("COMMIT")

        except DatabaseInitError:
            raise
        except Exception as e:
            try:
                raw_conn.execute("ROLLBACK")
            except Exception:
                pass
            raise DatabaseInitError(
                cause=f"Dimensions migration failed: {e}",
                original=e,
            )
        finally:
            # 11. Re-enable foreign_keys and VERIFY
            raw_conn.execute("PRAGMA foreign_keys = ON")
            cursor = raw_conn.execute("PRAGMA foreign_keys")
            fk_status = cursor.fetchone()[0]
            if fk_status != 1:
                raise DatabaseInitError(
                    cause="Failed to re-enable foreign_keys after migration",
                    original=None,
                )

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
        if "video_path" not in monitoring_columns:
            conn.execute(
                text("ALTER TABLE monitorings ADD COLUMN video_path VARCHAR(500)")
            )

        conn.commit()


def _migrate_add_sync_columns(engine) -> None:
    """Non-destructive migration: add remote sync metadata columns.

    Adds remote_id, remote_sync_status, last_synced_at, and remote_sync_error
    to all syncable tables. Snapshot additionally receives raw_storage_path
    and annotated_storage_path.

    Uses PRAGMA table_info to detect existing columns. Idempotent — safe to
    call on every startup. Does nothing if columns already exist.

    Spec 017 — Supabase Remote Sync.
    """
    # Column definitions: (column_name, ddl_type)
    _COMMON_SYNC_COLUMNS = [
        ("remote_id", "VARCHAR(36)"),
        ("remote_sync_status", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("last_synced_at", "DATETIME"),
        ("remote_sync_error", "TEXT"),
    ]

    _SNAPSHOT_EXTRA_COLUMNS = [
        ("raw_storage_path", "VARCHAR(500)"),
        ("annotated_storage_path", "VARCHAR(500)"),
    ]

    _TABLE_COLUMNS = {
        "greenhouses": _COMMON_SYNC_COLUMNS,
        "modules": _COMMON_SYNC_COLUMNS,
        "monitorings": _COMMON_SYNC_COLUMNS,
        "snapshots": _COMMON_SYNC_COLUMNS + _SNAPSHOT_EXTRA_COLUMNS,
        "monitoring_metrics": _COMMON_SYNC_COLUMNS,
        "inspection_results": _COMMON_SYNC_COLUMNS,
        "activity_logs": _COMMON_SYNC_COLUMNS,
    }

    with engine.connect() as conn:
        for table_name, columns_to_add in _TABLE_COLUMNS.items():
            # Get existing columns for this table
            result = conn.execute(text(f"PRAGMA table_info({table_name})"))
            existing_columns = {row[1] for row in result.fetchall()}

            # Skip if table doesn't exist (empty set from PRAGMA)
            if not existing_columns:
                continue

            for col_name, col_ddl in columns_to_add:
                if col_name not in existing_columns:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_ddl}"
                        )
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
            _migrate_dimensions_nullable(self._engine)
            _migrate_add_columns(self._engine)
            _migrate_add_sync_columns(self._engine)
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
