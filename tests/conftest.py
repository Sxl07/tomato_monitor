"""Shared pytest fixtures for database testing.

Provides an in-memory SQLite engine, session factory, schema creation,
and per-test session with rollback isolation.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.persistence.database import DatabaseManager

# Import Base and all ORM models so create_all() registers them
from src.infrastructure.persistence.models import (  # noqa: F401
    Base,
    GreenhouseModel,
    ModuleModel,
    MonitoringModel,
    SnapshotModel,
    InspectionResultModel,
    MonitoringMetricsModel,
    UserModel,
    ActivityTypeModel,
    ActivityLogModel,
    ExportPackageModel,
)


@pytest.fixture(scope="session")
def engine():
    """Create an in-memory SQLite engine with foreign_keys enabled."""
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return eng


@pytest.fixture(scope="session")
def tables(engine):
    """Create all tables once per test session."""
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session_factory(engine, tables):
    """Provide a session factory bound to the in-memory engine."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def session(session_factory) -> Session:
    """Provide a session per test with rollback after each test for isolation."""
    sess = session_factory()
    yield sess
    sess.rollback()
    sess.close()


@pytest.fixture
def db_manager():
    """Provide an in-memory DatabaseManager with schema initialized."""
    manager = DatabaseManager(db_path=":memory:")
    manager.init_db()
    return manager


@pytest.fixture
def db_session(db_manager):
    """Provide a fresh SQLAlchemy session for testing."""
    session = db_manager.get_session()
    yield session
    session.close()
