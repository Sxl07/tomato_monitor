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


def _migrate_add_deletion_outbox_owner(engine) -> None:
    """Add ``deletion_outbox.owner_user_id`` if missing (Spec 022, additive).

    Local-only, additive nullable FK column (``REFERENCES users(id)``). Uses the
    same non-destructive ``ALTER TABLE ADD COLUMN`` pattern as the other add-
    column migrations: PRAGMA table_info detects existing columns, so re-running
    ``init_db`` is a no-op and existing rows are left untouched (legacy rows keep
    ``owner_user_id = NULL``; ownership is never inferred or backfilled).
    """
    with engine.connect() as conn:
        info = conn.execute(text("PRAGMA table_info(deletion_outbox)")).fetchall()
        if not info:
            return  # Table not present yet (fresh install builds final schema).
        existing_columns = {row[1] for row in info}
        if "owner_user_id" not in existing_columns:
            conn.execute(
                text(
                    "ALTER TABLE deletion_outbox "
                    "ADD COLUMN owner_user_id INTEGER REFERENCES users(id)"
                )
            )
            conn.commit()


def _migrate_greenhouse_owner(engine) -> None:
    """Migrate ``greenhouses`` to per-owner ownership (Spec 022, Task 1.2).

    Brings an EXISTING legacy ``greenhouses`` table to the target schema in a
    SINGLE atomic transaction that BOTH adds the nullable ``owner_user_id``
    column (FK -> users.id) AND replaces the global ``UNIQUE(name)`` constraint
    with the composite ``UNIQUE(owner_user_id, name)``.

    SQLite cannot add a FK column and drop a column-level UNIQUE in place, so a
    transactional table rebuild is used (same pattern as
    ``_migrate_dimensions_nullable``): detect state, then FK OFF, BEGIN, create
    the new table with the composite unique, copy rows with an EXPLICIT column
    list restricted to columns present in BOTH source and target (preserving
    ids, all common columns and FK values), DROP old, RENAME new,
    ``PRAGMA foreign_key_check`` before COMMIT, then FK ON.

    Legacy rows get ``owner_user_id = NULL``: ownership is only assigned with
    persisted, unambiguous, deterministic evidence, which the ``greenhouses``
    table does not carry, so NULL is the correct legacy value (never inferred
    from "a single user exists"). When the source lacks ``owner_user_id`` it is
    simply not copied and defaults to NULL in the rebuilt table.

    Idempotency: the rebuild is skipped entirely when the table already has
    ``owner_user_id`` AND the composite unique AND no global unique on ``name``.
    Re-running after a completed migration is a no-op.

    Note: down-migration restoring a global ``UNIQUE(name)`` is intentionally
    NOT provided, since after migration legitimately duplicated names may exist
    across different owners.

    Atomicity: the legacy -> target transformation (adding ``owner_user_id`` AND
    replacing the global ``UNIQUE(name)`` with ``UNIQUE(owner_user_id, name)``)
    happens in a SINGLE table rebuild inside ONE transaction. There is no
    separate ``ALTER TABLE ADD COLUMN`` + COMMIT before the rebuild, so a failure
    during the rebuild can never leave ``owner_user_id`` added while the
    constraint stays unmigrated. On any failure the transaction is rolled back
    and the original schema (including the global ``UNIQUE(name)`` and the
    absence of ``owner_user_id``) remains fully intact.
    """
    # --- Detect the real schema state (idempotency) --------------------------
    with engine.connect() as conn:
        info = conn.execute(text("PRAGMA table_info(greenhouses)")).fetchall()
        if not info:
            return  # Fresh install: create_all() already built the final schema.

        existing_columns = [row[1] for row in info]
        has_owner_column = "owner_user_id" in existing_columns

        index_list = conn.execute(text("PRAGMA index_list(greenhouses)")).fetchall()
        has_composite = False
        has_global_name_unique = False
        for idx in index_list:
            if not bool(idx[2]):  # not a UNIQUE index
                continue
            # Detect by the indexed COLUMNS, not the index name: SQLite backs a
            # column-level / table-level UNIQUE with an auto-generated index
            # name (sqlite_autoindex_*), so the constraint name is not reliable.
            idx_cols = [
                c[2]
                for c in conn.execute(
                    text(f"PRAGMA index_info({idx[1]})")
                ).fetchall()
            ]
            if idx_cols == ["owner_user_id", "name"]:
                has_composite = True
            elif idx_cols == ["name"]:
                has_global_name_unique = True

        # Target state already reached -> no-op.
        if has_owner_column and has_composite and not has_global_name_unique:
            return

        # Columns present in the SOURCE that also exist in the TARGET schema.
        # If the source lacks owner_user_id, it is simply not copied and thus
        # defaults to NULL in the rebuilt table.
        target_columns = [
            "id",
            "owner_user_id",
            "name",
            "location",
            "created_at",
            "updated_at",
            "remote_id",
            "remote_sync_status",
            "last_synced_at",
            "remote_sync_error",
        ]
        copy_columns = [c for c in target_columns if c in existing_columns]
        col_list = ", ".join(copy_columns)

    # --- Atomic table rebuild: single transaction ----------------------------
    with engine.connect() as conn:
        conn.commit()  # ensure no implicit transaction is open before FK OFF
        raw_conn = conn.connection.dbapi_connection

        raw_conn.execute("PRAGMA foreign_keys = OFF")
        cursor = raw_conn.execute("PRAGMA foreign_keys")
        if cursor.fetchone()[0] != 0:
            raise DatabaseInitError(
                cause="Failed to disable foreign_keys for greenhouse owner migration",
                original=None,
            )

        try:
            raw_conn.execute("BEGIN")

            raw_conn.execute("""
                CREATE TABLE greenhouses_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER REFERENCES users(id),
                    name VARCHAR(100) NOT NULL,
                    location VARCHAR(200),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    remote_id VARCHAR(36),
                    remote_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    last_synced_at DATETIME,
                    remote_sync_error TEXT,
                    CONSTRAINT uq_greenhouse_owner_name UNIQUE (owner_user_id, name)
                )
            """)

            raw_conn.execute(f"""
                INSERT INTO greenhouses_new ({col_list})
                SELECT {col_list} FROM greenhouses
            """)

            raw_conn.execute("DROP TABLE greenhouses")
            raw_conn.execute("ALTER TABLE greenhouses_new RENAME TO greenhouses")

            violations = raw_conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raw_conn.execute("ROLLBACK")
                raise DatabaseInitError(
                    cause=(
                        "FK integrity violation after greenhouse owner migration: "
                        f"{violations}"
                    ),
                    original=None,
                )

            raw_conn.execute("COMMIT")

        except DatabaseInitError:
            raise
        except Exception as e:
            try:
                raw_conn.execute("ROLLBACK")
            except Exception:
                pass
            raise DatabaseInitError(
                cause=f"Greenhouse owner migration failed: {e}",
                original=e,
            )
        finally:
            raw_conn.execute("PRAGMA foreign_keys = ON")
            cursor = raw_conn.execute("PRAGMA foreign_keys")
            if cursor.fetchone()[0] != 1:
                raise DatabaseInitError(
                    cause="Failed to re-enable foreign_keys after greenhouse owner migration",
                    original=None,
                )


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
            _migrate_add_deletion_outbox_owner(self._engine)
            _migrate_greenhouse_owner(self._engine)
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
