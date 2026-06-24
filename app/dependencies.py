from __future__ import annotations

from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from src.infrastructure.config.settings import DETECTION_MODEL_PATH, OUTPUTS_DIR

from src.application.services.camera_service import CameraService
from src.application.services.log_service import LogService
from src.application.services.model_service import ModelService
from src.application.services.pipeline_service import PipelineService
from src.application.services.session_service import SessionService
from src.infrastructure.persistence.local.csv_inspection_repository import CsvInspectionRepository
from src.infrastructure.persistence.local.csv_session_repository import CsvSessionRepository
from src.infrastructure.persistence.local.file_artifact_repository import FileArtifactRepository
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories import (
    SqlGreenhouseRepository,
    SqlModuleRepository,
    SqlMonitoringRepository,
    SqlSnapshotRepository,
    SqlInspectionResultRepository,
    SqlMonitoringMetricsRepository,
)


# ---------------------------------------------------------------------------
# Existing CSV-based dependencies (still used by the video pipeline)
# ---------------------------------------------------------------------------


def get_session_repository() -> CsvSessionRepository:
    return CsvSessionRepository(OUTPUTS_DIR / "meta")


def get_inspection_repository() -> CsvInspectionRepository:
    return CsvInspectionRepository(OUTPUTS_DIR / "experiments")


def get_artifact_repository() -> FileArtifactRepository:
    return FileArtifactRepository(OUTPUTS_DIR / "experiments")


def get_pipeline_service() -> PipelineService:
    return PipelineService(
        artifact_repository=get_artifact_repository(),
    )


def get_session_service() -> SessionService:
    return SessionService(
        session_repository=get_session_repository(),
        artifact_repository=get_artifact_repository(),
    )


# ---------------------------------------------------------------------------
# New SQLite-based dependencies (used by agricultural data routes)
# ---------------------------------------------------------------------------


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Provide a transactional SQLAlchemy session scoped to a single request.

    Commits on success, rolls back on exception, and always closes the session.
    """
    db_manager: DatabaseManager = request.app.state.db_manager
    session = db_manager.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_greenhouse_repository(request: Request) -> SqlGreenhouseRepository:
    """Provide a SqlGreenhouseRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlGreenhouseRepository(session=db_manager.get_session())


def get_module_repository(request: Request) -> SqlModuleRepository:
    """Provide a SqlModuleRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlModuleRepository(session=db_manager.get_session())


def get_monitoring_repository(request: Request) -> SqlMonitoringRepository:
    """Provide a SqlMonitoringRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlMonitoringRepository(session=db_manager.get_session())


def get_snapshot_repository(request: Request) -> SqlSnapshotRepository:
    """Provide a SqlSnapshotRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlSnapshotRepository(session=db_manager.get_session())


def get_inspection_result_repository(request: Request) -> SqlInspectionResultRepository:
    """Provide a SqlInspectionResultRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlInspectionResultRepository(session=db_manager.get_session())


def get_monitoring_metrics_repository(request: Request) -> SqlMonitoringMetricsRepository:
    """Provide a SqlMonitoringMetricsRepository backed by the app's DatabaseManager."""
    db_manager: DatabaseManager = request.app.state.db_manager
    return SqlMonitoringMetricsRepository(session=db_manager.get_session())


# ---------------------------------------------------------------------------
# Composite service dependencies
# ---------------------------------------------------------------------------


def get_monitoring_service(request: Request) -> "MonitoringService":
    """Provide MonitoringService with all repositories from DatabaseManager."""
    from src.application.services.monitoring_service import MonitoringService

    db_manager: DatabaseManager = request.app.state.db_manager
    session = db_manager.get_session()
    registry = request.app.state.monitoring_runtime_registry
    return MonitoringService(
        monitoring_repo=SqlMonitoringRepository(session),
        snapshot_repo=SqlSnapshotRepository(session),
        inspection_result_repo=SqlInspectionResultRepository(session),
        metrics_repo=SqlMonitoringMetricsRepository(session),
        module_repo=SqlModuleRepository(session),
        runtime_registry=registry,
    )


# ---------------------------------------------------------------------------
# New SQLite-based dependencies (used by agricultural data routes)
# ---------------------------------------------------------------------------

# IMPORTANT: Use a scoped session that is created once per request,
# committed on success, rolled back on error, and always closed.
# All repositories within the same request share the SAME session
# to avoid "database is locked" errors with SQLite.


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Provide a transactional SQLAlchemy session scoped to a single request.

    Commits on success, rolls back on exception, and always closes the session.
    Use this as a dependency in route handlers that need direct session access.
    """
    db_manager: DatabaseManager = request.app.state.db_manager
    session = db_manager.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_request_session(request: Request) -> Session:
    """Get or create a scoped session for the current request.

    Stores the session in request.state so all dependencies within the same
    request share a single session. This prevents SQLite "database is locked" errors.
    """
    if not hasattr(request.state, "_db_session") or request.state._db_session is None:
        db_manager: DatabaseManager = request.app.state.db_manager
        request.state._db_session = db_manager.get_session()
    return request.state._db_session


def get_greenhouse_repository(request: Request) -> SqlGreenhouseRepository:
    """Provide a SqlGreenhouseRepository using the request-scoped session."""
    return SqlGreenhouseRepository(session=_get_request_session(request))


def get_module_repository(request: Request) -> SqlModuleRepository:
    """Provide a SqlModuleRepository using the request-scoped session."""
    return SqlModuleRepository(session=_get_request_session(request))


def get_monitoring_repository(request: Request) -> SqlMonitoringRepository:
    """Provide a SqlMonitoringRepository using the request-scoped session."""
    return SqlMonitoringRepository(session=_get_request_session(request))


def get_snapshot_repository(request: Request) -> SqlSnapshotRepository:
    """Provide a SqlSnapshotRepository using the request-scoped session."""
    return SqlSnapshotRepository(session=_get_request_session(request))


def get_inspection_result_repository(request: Request) -> SqlInspectionResultRepository:
    """Provide a SqlInspectionResultRepository using the request-scoped session."""
    return SqlInspectionResultRepository(session=_get_request_session(request))


def get_monitoring_metrics_repository(request: Request) -> SqlMonitoringMetricsRepository:
    """Provide a SqlMonitoringMetricsRepository using the request-scoped session."""
    return SqlMonitoringMetricsRepository(session=_get_request_session(request))


# ---------------------------------------------------------------------------
# Composite service dependencies
# ---------------------------------------------------------------------------


def get_monitoring_service(request: Request) -> "MonitoringService":
    """Provide MonitoringService with all repositories sharing one session.

    IMPORTANT: Injects the shared runtime_registry from app.state so that
    worker/thread state persists across requests.
    """
    from src.application.services.monitoring_service import MonitoringService

    session = _get_request_session(request)
    registry = request.app.state.monitoring_runtime_registry
    return MonitoringService(
        monitoring_repo=SqlMonitoringRepository(session),
        snapshot_repo=SqlSnapshotRepository(session),
        inspection_result_repo=SqlInspectionResultRepository(session),
        metrics_repo=SqlMonitoringMetricsRepository(session),
        module_repo=SqlModuleRepository(session),
        runtime_registry=registry,
    )


# ---------------------------------------------------------------------------
# Monitoring UX service dependencies
# ---------------------------------------------------------------------------


def get_log_service(request: Request) -> LogService:
    """Provide the singleton LogService stored on app.state at startup."""
    return request.app.state.log_service


def get_camera_service() -> CameraService:
    """Provide a CameraService instance (stateless, per-request)."""
    return CameraService()


def get_model_service() -> ModelService:
    """Provide a ModelService configured with the detection model path."""
    return ModelService(model_path=DETECTION_MODEL_PATH)
