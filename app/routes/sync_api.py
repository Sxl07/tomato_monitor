"""API routes for manual remote synchronization.

POST /api/sync/trigger — Execute manual sync to Supabase.
GET  /api/sync/status  — Return sync readiness and progress info.

Ownership model:
- This route is the SOLE owner of SyncRuntimeState.try_acquire()/release().
- RemoteSyncService only calls update_progress() — never acquire/release.
- The ephemeral JWT lives only within the trigger request scope.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.dependencies import (
    get_deletion_outbox_repository,
    get_monitoring_runtime_registry,
    get_sync_runtime_state,
    get_sync_state_repository,
    require_current_user_api,
)
from src.domain.entities.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class SyncTriggerRequest(BaseModel):
    """Request body for POST /api/sync/trigger."""

    password: str


class SyncTriggerResponse(BaseModel):
    """Response body for POST /api/sync/trigger on success."""

    success: bool
    entities_synced: int
    entities_failed: int
    images_uploaded: int
    images_failed: int
    deletions_synced: int
    deletions_failed: int
    errors: list[str]
    duration_seconds: float


class SyncStatusResponse(BaseModel):
    """Response body for GET /api/sync/status."""

    supabase_configured: bool
    user_has_remote_id: bool
    is_syncing: bool
    pending_count: int
    synced_count: int
    error_count: int
    last_sync_at: Optional[str]
    current_progress: Optional[dict]


# ---------------------------------------------------------------------------
# POST /api/sync/trigger
# ---------------------------------------------------------------------------


@router.post("/trigger", response_model=SyncTriggerResponse)
async def trigger_sync(
    body: SyncTriggerRequest,
    request: Request,
    current_user: User = Depends(require_current_user_api),
    runtime_state=Depends(get_sync_runtime_state),
    registry=Depends(get_monitoring_runtime_registry),
    sync_state_repo=Depends(get_sync_state_repository),
    deletion_outbox_repo=Depends(get_deletion_outbox_repository),
):
    """Trigger a manual sync operation to Supabase.

    Validates preconditions in order:
    1. User has remote_user_id configured.
    2. No active monitoring (running/analyzing) in progress.
    3. No other sync operation in progress (mutual exclusion).
    4. Re-authenticates with Supabase to obtain ephemeral JWT.
    5. Validates remote identity matches local user.
    6. Executes RemoteSyncService in threadpool.
    7. Always releases runtime state in finally block.
    """
    # 1. Validate remote_user_id
    if not current_user.remote_user_id:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta no tiene un identificador remoto configurado. "
            "Registra tu cuenta en el sistema remoto primero.",
        )

    # 2. Check for active monitoring
    if registry.has_any_live_thread():
        raise HTTPException(
            status_code=409,
            detail="Finaliza el monitoreo en curso antes de sincronizar.",
        )

    # 3. Attempt to acquire sync lock (also blocked while a recovery runs)
    if not runtime_state.try_acquire():
        active = runtime_state.get_active_operation()
        if active == "recovery":
            detail = "Hay una recuperación de datos en curso."
        else:
            detail = "Sincronización en curso, espera a que finalice."
        raise HTTPException(status_code=409, detail=detail)

    # From here, we MUST release in finally
    result_dict: Optional[dict] = None
    try:
        logger.info("Remote sync started for user_id=%s", current_user.id)

        # 4. Validate Supabase is configured
        config = getattr(request.app.state, "supabase_config", None)
        if config is None:
            raise HTTPException(
                status_code=400,
                detail="La sincronización remota no está configurada en este dispositivo.",
            )

        # 5. Re-authenticate with Supabase for ephemeral JWT
        from src.infrastructure.supabase.supabase_auth_adapter import SupabaseAuthAdapter

        auth_adapter = SupabaseAuthAdapter(config)
        auth_result = await run_in_threadpool(
            auth_adapter.sign_in,
            current_user.email,
            body.password,
        )

        if not auth_result.success:
            # Map remote auth errors to appropriate HTTP responses
            logger.warning(
                "Remote sync auth failed for user_id=%s: error_type=%s",
                current_user.id,
                auth_result.error_type,
            )
            error_msg = _map_auth_error(auth_result.error_type, auth_result.error_message)
            raise HTTPException(status_code=401, detail=error_msg)

        access_token = auth_result.access_token

        # 6. Validate remote identity matches local user
        if auth_result.user_id != current_user.remote_user_id:
            logger.warning(
                "Remote identity mismatch for user_id=%s",
                current_user.id,
            )
            raise HTTPException(
                status_code=403,
                detail="La identidad remota no coincide con tu cuenta local.",
            )

        # 7. Build RemoteSyncService and execute
        from src.infrastructure.supabase.supabase_data_adapter import SupabaseDataAdapter
        from src.infrastructure.supabase.supabase_storage_adapter import SupabaseStorageAdapter
        from src.application.services.remote_sync_service import RemoteSyncService

        data_adapter = SupabaseDataAdapter(config)
        storage_adapter = SupabaseStorageAdapter(config)

        sync_service = RemoteSyncService(
            remote_data=data_adapter,
            remote_storage=storage_adapter,
            sync_state=sync_state_repo,
            runtime_state=runtime_state,
            deletion_outbox=deletion_outbox_repo,
        )

        sync_result = await run_in_threadpool(
            sync_service.execute_sync,
            access_token,
            current_user.remote_user_id,
        )

        logger.info(
            "Remote sync completed for user_id=%s: success=%s "
            "entities_synced=%d entities_failed=%d "
            "images_uploaded=%d images_failed=%d "
            "errors_count=%d duration_seconds=%.1f",
            current_user.id,
            sync_result.success,
            sync_result.entities_synced,
            sync_result.entities_failed,
            sync_result.images_uploaded,
            sync_result.images_failed,
            len(sync_result.errors),
            sync_result.duration_seconds,
        )

        # Build result dict for runtime state
        result_dict = {
            "success": sync_result.success,
            "entities_synced": sync_result.entities_synced,
            "entities_failed": sync_result.entities_failed,
            "images_uploaded": sync_result.images_uploaded,
            "images_failed": sync_result.images_failed,
            "deletions_synced": sync_result.deletions_synced,
            "deletions_failed": sync_result.deletions_failed,
            "duration_seconds": sync_result.duration_seconds,
        }

        return SyncTriggerResponse(
            success=sync_result.success,
            entities_synced=sync_result.entities_synced,
            entities_failed=sync_result.entities_failed,
            images_uploaded=sync_result.images_uploaded,
            images_failed=sync_result.images_failed,
            deletions_synced=sync_result.deletions_synced,
            deletions_failed=sync_result.deletions_failed,
            errors=sync_result.errors,
            duration_seconds=sync_result.duration_seconds,
        )

    except HTTPException:
        # Re-raise HTTP exceptions without wrapping
        raise
    except Exception as exc:
        logger.error(
            "Remote sync failed unexpectedly for user_id=%s: %s",
            current_user.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Ocurrió un error inesperado durante la sincronización.",
        )
    finally:
        runtime_state.release(result_dict)


# ---------------------------------------------------------------------------
# GET /api/sync/status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SyncStatusResponse)
async def get_sync_status(
    request: Request,
    current_user: User = Depends(require_current_user_api),
    runtime_state=Depends(get_sync_runtime_state),
    sync_state_repo=Depends(get_sync_state_repository),
    deletion_outbox_repo=Depends(get_deletion_outbox_repository),
):
    """Return current sync status, readiness indicators, and progress."""
    # Supabase configuration check
    config = getattr(request.app.state, "supabase_config", None)
    supabase_configured = config is not None and config.is_configured

    # User remote id check
    user_has_remote_id = bool(current_user.remote_user_id)

    # Runtime state snapshot
    runtime_status = runtime_state.get_status()
    is_syncing = runtime_status["is_syncing"]

    # Build current_progress only if syncing
    current_progress = None
    if is_syncing:
        current_progress = {
            "phase": runtime_status["phase"],
            "processed": runtime_status["processed"],
            "total": runtime_status["total"],
        }

    # Counts from persistent state
    counts = sync_state_repo.get_sync_status_counts(current_user.id)

    # Include Deletion_Outbox entries actually eligible for propagation
    # (get_pending_for_propagation already filters local_delete_status=='completed'
    # AND status in pending/error/syncing). We do NOT sum the whole durable
    # synced outbox history, which would grow unbounded.
    pending_deletions = deletion_outbox_repo.get_pending_for_propagation(
    current_user.id
)
    deletion_pending_count = len(pending_deletions)
    deletion_error_count = sum(
        1 for entry in pending_deletions if entry.status == "error"
    )

    # Format last_sync_at as ISO string with explicit UTC
    last_sync_at_str = None
    if counts.last_sync_at is not None:
        from datetime import timezone

        dt = counts.last_sync_at
        # Naive datetimes are UTC by project convention
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        # Use Z suffix for UTC
        last_sync_at_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return SyncStatusResponse(
        supabase_configured=supabase_configured,
        user_has_remote_id=user_has_remote_id,
        is_syncing=is_syncing,
        pending_count=counts.pending_count + deletion_pending_count,
        synced_count=counts.synced_count,
        error_count=counts.error_count + deletion_error_count,
        last_sync_at=last_sync_at_str,
        current_progress=current_progress,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_auth_error(error_type: Optional[str], error_message: Optional[str]) -> str:
    """Map remote auth error to a user-facing message."""
    if error_type == "INVALID_CREDENTIALS":
        return "Contraseña incorrecta. Verifica tu contraseña e intenta de nuevo."
    if error_type == "CONNECTIVITY":
        return "No se pudo conectar al servidor remoto. Verifica tu conexión a internet."
    if error_type == "REMOTE_UNAVAILABLE":
        return "El servidor remoto no está disponible. Intenta más tarde."
    if error_type == "RATE_LIMITED":
        return "Demasiados intentos. Espera unos minutos antes de reintentar."
    if error_type == "AUTH_FORBIDDEN":
        return "Tu cuenta remota está deshabilitada o no tiene permisos."
    # Generic fallback
    return error_message or "Error de autenticación remota."
