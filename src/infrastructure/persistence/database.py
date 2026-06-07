"""Database connection management for SQLite persistence.

Provides centralized engine creation, session factory, PRAGMA configuration,
and idempotent schema initialization.
"""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.persistence.exceptions import DatabaseInitError
from src.infrastructure.persistence.models.base import Base


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
        """Create all tables using create_all(checkfirst=True).

        Idempotent — safe to call on every application startup.
        Raises DatabaseInitError if schema creation fails.
        """
        try:
            Base.metadata.create_all(self._engine, checkfirst=True)
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
