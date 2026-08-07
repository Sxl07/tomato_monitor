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
from app.dependencies import _AuthRedirectException
from src.application.services.log_service import LogService
from src.infrastructure.config.logging_config import configure_logging
from src.infrastructure.persistence.database import DatabaseManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logging and database on startup."""
    configure_logging()
    db_manager = DatabaseManager()
    db_manager.init_db()
    app.state.db_manager = db_manager
    app.state.log_service = LogService()

    from src.application.services.camera_service import CameraService
    app.state.camera_service = CameraService()

    from src.application.services.monitoring_runtime_registry import MonitoringRuntimeRegistry
    app.state.monitoring_runtime_registry = MonitoringRuntimeRegistry()

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
app.include_router(ui_router)
app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
app.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
app.include_router(monitoring_router)
app.include_router(agricultural_router)
app.include_router(monitoring_api_router)