from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates as _Templates
from starlette.middleware.base import BaseHTTPMiddleware
import logging as _logging

from fastapi.responses import RedirectResponse as _RedirectResponse

from app.routes.auth import router as auth_router
from app.routes.ui import router as ui_router
from app.routes.pipeline import router as pipeline_router
from app.routes.sessions import router as sessions_router
from app.routes.monitoring import router as monitoring_router
from app.routes.agricultural_ui import router as agricultural_router
from app.routes.monitoring_api import router as monitoring_api_router
from app.routes.sync_api import router as sync_api_router
from app.dependencies import _AuthRedirectException
from src.application.services.log_service import LogService
from src.infrastructure.config.logging_config import configure_logging
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.supabase.supabase_config import load_supabase_config


def _recover_abrupt_recordings(db_manager, runtime_registry) -> None:
    """Wire startup recovery to MonitoringService (Task 11.1 + hotfix).

    Builds a MonitoringService with SQL repositories on a fresh session and
    delegates to recover_abrupt_recordings() (leftover video promotion) and
    reconcile_orphaned_sessions_on_startup() (mark restart-orphaned sessions,
    e.g. stuck in 'analyzing', as 'error' while preserving the video). No
    recovery logic lives here — this is pure wiring. Best-effort: never
    raises, never blocks startup.
    """
    from src.application.services.monitoring_service import MonitoringService
    from src.infrastructure.persistence.repositories.sql_monitoring_repository import SqlMonitoringRepository
    from src.infrastructure.persistence.repositories.sql_snapshot_repository import SqlSnapshotRepository
    from src.infrastructure.persistence.repositories.sql_inspection_result_repository import SqlInspectionResultRepository
    from src.infrastructure.persistence.repositories.sql_monitoring_metrics_repository import SqlMonitoringMetricsRepository
    from src.infrastructure.persistence.repositories.sql_module_repository import SqlModuleRepository

    session = db_manager.get_session()
    try:
        service = MonitoringService(
            monitoring_repo=SqlMonitoringRepository(session=session),
            snapshot_repo=SqlSnapshotRepository(session=session),
            inspection_result_repo=SqlInspectionResultRepository(session=session),
            metrics_repo=SqlMonitoringMetricsRepository(session=session),
            module_repo=SqlModuleRepository(session=session),
            runtime_registry=runtime_registry,
        )
        # Promote any leftover recording temp files first (filesystem only).
        service.recover_abrupt_recordings()
        # Then reconcile sessions left in a non-terminal status by the restart
        # (e.g. stuck in 'analyzing' with no worker) to 'error'. Preserves
        # monitoring.mp4 and does NOT auto-reprocess.
        service.reconcile_orphaned_sessions_on_startup()
        session.commit()
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        _recovery_logger = _logging.getLogger("app.recovery")
        _recovery_logger.warning(f"Startup recovery failed: {e}")
    finally:
        session.close()


def _run_deferred_cleanup(db_manager) -> None:
    """Wire the deferred physical cleanup (Spec 021, Task 11) at startup.

    Builds a CleanupService with a DeletionOutboxRepository over a fresh session
    factory and runs one pass. Pure wiring — best-effort: never raises, never
    blocks startup, no network, does not touch vision/camera/inference.
    """
    try:
        from src.application.services.cleanup_service import CleanupService
        from src.infrastructure.config.settings import (
            OUTPUTS_DIR,
            RETENTION_WINDOW_HOURS,
        )
        from src.infrastructure.persistence.deletion_outbox_repository import (
            DeletionOutboxRepository,
        )
        from src.infrastructure.security.path_sanitizer import validate_safe_path

        outbox = DeletionOutboxRepository(session_factory=db_manager.get_session)
        service = CleanupService(
            deletion_outbox=outbox,
            outputs_dir=OUTPUTS_DIR,
            retention_window_hours=RETENTION_WINDOW_HOURS,
            validate_safe_path=validate_safe_path,
        )
        service.run()
    except Exception as e:  # pragma: no cover - defensive, must not block startup
        _cleanup_logger = _logging.getLogger("app.cleanup")
        _cleanup_logger.warning(f"Deferred cleanup failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logging and database on startup."""
    configure_logging()
    app.state.supabase_config = load_supabase_config()
    db_manager = DatabaseManager()
    db_manager.init_db()
    app.state.db_manager = db_manager
    app.state.log_service = LogService()

    # Reconcile orphan exports stuck in 'generating' from interrupted process
    from src.application.services.export_reconciliation import reconcile_orphan_exports
    reconcile_orphan_exports(db_manager)

    from src.application.services.camera_service import CameraService
    app.state.camera_service = CameraService()

    from src.application.services.monitoring_runtime_registry import MonitoringRuntimeRegistry
    app.state.monitoring_runtime_registry = MonitoringRuntimeRegistry()

    # Recover leftover recordings from an abrupt termination (Task 11.1).
    # Delegates entirely to MonitoringService.recover_abrupt_recordings();
    # best-effort — must never crash startup.
    _recover_abrupt_recordings(db_manager, app.state.monitoring_runtime_registry)

    from src.application.services.sync_runtime_state import SyncRuntimeState
    app.state.sync_runtime_state = SyncRuntimeState()

    # Bootstrap admin user from environment (idempotent)
    from src.application.services.auth_service import maybe_bootstrap_admin
    from src.infrastructure.persistence.repositories.sql_user_repository import SqlUserRepository
    bootstrap_session = db_manager.get_session()
    try:
        maybe_bootstrap_admin(SqlUserRepository(session=bootstrap_session))
        bootstrap_session.commit()
    except Exception as e:
        bootstrap_session.rollback()
        _bootstrap_logger = _logging.getLogger("app.bootstrap")
        _bootstrap_logger.warning(f"Bootstrap admin failed: {e}")
    finally:
        bootstrap_session.close()

    # Deferred physical cleanup of local artifacts (Spec 021, Task 11).
    # Lightweight, best-effort filesystem operation — never blocks/crashes
    # startup, never touches vision/camera/inference, no network.
    _run_deferred_cleanup(db_manager)

    yield
    # No persistent camera to release — preview uses single-frame capture


class DBSessionMiddleware(BaseHTTPMiddleware):
    """Middleware that commits and closes the DB session after each request.

    If a request-scoped session was created (stored in request.state._db_session),
    this middleware commits it on success or rolls back on error, then closes it.
    This prevents SQLite "database is locked" errors from dangling sessions.
    """

    async def dispatch(self, request: Request, call_next):
        response = None
        try:
            response = await call_next(request)
        except Exception:
            # Roll back on unhandled exception
            session = getattr(request.state, "_db_session", None)
            if session is not None:
                session.rollback()
                session.close()
                request.state._db_session = None
            raise
        else:
            # Commit on success
            session = getattr(request.state, "_db_session", None)
            if session is not None:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
                finally:
                    session.close()
                    request.state._db_session = None
        return response


app = FastAPI(
    title="Tomato Monitor",
    description="Mini app de pruebas para visión por computador en tomate cherry",
    version="0.1.0",
    lifespan=lifespan,
)

# Add DB session middleware BEFORE mounting routes
app.add_middleware(DBSessionMiddleware)

# --- Global Exception Handler ---
_error_templates = _Templates(directory="app/templates")
_error_logger = _logging.getLogger("app.error_handler")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions, log with traceback, return generic error page."""
    _error_logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        str(exc),
        exc_info=True,
    )
    return _error_templates.TemplateResponse(
        request,
        "error.html",
        {"message": "Ocurrió un error inesperado. Intenta de nuevo."},
        status_code=500,
    )


@app.exception_handler(_AuthRedirectException)
async def auth_redirect_handler(request: Request, exc: _AuthRedirectException):
    """Redirect unauthenticated HTML requests to the login page."""
    return _RedirectResponse(url=f"/login?next={exc.next_path}", status_code=302)


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/snapshots", StaticFiles(directory="outputs/monitorings", check_dir=False), name="snapshots")

app.include_router(auth_router)
app.include_router(agricultural_router)
app.include_router(ui_router)
app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
app.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
app.include_router(monitoring_router)
app.include_router(monitoring_api_router)
app.include_router(sync_api_router)