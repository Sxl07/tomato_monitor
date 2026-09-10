"""API route for manual user-scoped cloud recovery (Spec 022, block E).

POST /api/recovery/trigger — Re-authenticate against Supabase and rebuild the
local hierarchy (metadata + snapshot images) for the current operator.

Design constraints:
- Recovery is MANUAL. There is intentionally NO GET /api/recovery/status, no
  polling, no background task, and no startup recovery. The HTTP request stays
  open while recovery runs in the threadpool.
- Mutual exclusion with sync is enforced via the shared SyncRuntimeState: at
  most one manual cloud operation (sync OR recovery) runs at a time.
- The Supabase JWT is ephemeral: obtained via sign_in for this request, used
  during execution, and discarded. It is never persisted (no SQLite, cookie,
  filesystem) and never logged.
"""

from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.dependencies import (
    get_monitoring_runtime_registry,
    get_recovery_service,
    get_sync_runtime_state,
    release_request_db_session,
    require_current_user_api,
)
from src.domain.entities.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class RecoveryTriggerRequest(BaseModel):
    """Request body for POST /api/recovery/trigger.

    Only the password is accepted; the email is taken from the authenticated
    local user (never from the client) to re-authenticate against Supabase.
    """

    password: str


# ---------------------------------------------------------------------------
# POST /api/recovery/trigger
# ---------------------------------------------------------------------------


@router.post("/trigger")
async def trigger_recovery(
    body: RecoveryTriggerRequest,
    request: Request,
    current_user: User = Depends(require_current_user_api),
    runtime_state=Depends(get_sync_runtime_state),
    registry=Depends(get_monitoring_runtime_registry),
):
    """Trigger a manual cloud recovery for the authenticated operator.

    Precondition order:
    1. Authenticated local user (require_current_user_api) with a valid id.
    2. Local user has remote_user_id (else 400; no remote auth attempted).
    3. Supabase configured (else same 400 as sync).
    4. Acquire the recovery lock (mutual exclusion; else 409).
    5. Guard: no active capture/analysis (else 409).
    6. Re-authenticate with Supabase (email from user + request password).
    7. Validate auth success and exact remote-identity match.
    8. Validate access_token present.
    9. RECHECK the capture/analysis guard (narrow the re-auth window).
    10. Execute RecoveryService in the threadpool.
    11. Always release the recovery lock in finally.
    """
    # 1. Local identity precondition.
    if not current_user.id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # 2. Remote link precondition — no sign_in without a remote_user_id.
    if not current_user.remote_user_id:
        raise HTTPException(
            status_code=400,
            detail="El usuario actual no está vinculado a una cuenta remota.",
        )

    # current_user is a fully-materialized domain entity; the request-scoped
    # auth session is no longer needed. Release it NOW so no SQLite read
    # transaction stays open across the upcoming remote network calls and the
    # recovery inserts (which use their own sessions). D2 session discipline.
    release_request_db_session(request)

    # 3. Supabase configuration (consistent with /api/sync/trigger).
    config = getattr(request.app.state, "supabase_config", None)
    if config is None:
        raise HTTPException(
            status_code=400,
            detail="La sincronización remota no está configurada en este dispositivo.",
        )

    # 4. Acquire the recovery lock BEFORE any sign_in. Blocked by sync too.
    if not runtime_state.try_acquire_recovery():
        active = runtime_state.get_active_operation()
        if active == "sync":
            detail = "Hay una sincronización en curso."
        else:
            detail = "Hay una recuperación de datos en curso."
        raise HTTPException(status_code=409, detail=detail)

    # From here we MUST release the recovery lock in finally.
    try:
        logger.info("Cloud recovery started for user_id=%s", current_user.id)

        # 5. Monitoring/capture/analysis guard (first check).
        if registry.has_any_live_thread():
            raise HTTPException(
                status_code=409,
                detail="Finaliza la captura o el análisis en curso antes de "
                "recuperar datos.",
            )

        # 6. Re-authenticate with Supabase for an ephemeral JWT. Recovery does
        # NOT use HybridAuthService (no local fallback allowed).
        from src.infrastructure.supabase.supabase_auth_adapter import (
            SupabaseAuthAdapter,
        )

        auth_adapter = SupabaseAuthAdapter(config)
        auth_result = await run_in_threadpool(
            auth_adapter.sign_in,
            current_user.email,
            body.password,
        )

        # 7. Validate auth success.
        if not auth_result.success:
            logger.warning(
                "Cloud recovery auth failed for user_id=%s: error_type=%s",
                current_user.id,
                auth_result.error_type,
            )
            status_code, error_msg = _map_auth_error(
                auth_result.error_type, auth_result.error_message
            )
            raise HTTPException(status_code=status_code, detail=error_msg)

        # 7b. Exact remote-identity match (no overwrite, no email-only trust).
        if (
            auth_result.user_id is None
            or auth_result.user_id != current_user.remote_user_id
        ):
            logger.warning(
                "Cloud recovery remote identity mismatch for user_id=%s",
                current_user.id,
            )
            raise HTTPException(
                status_code=403,
                detail="La identidad remota autenticada no coincide con el "
                "usuario local.",
            )

        # 8. Access token must be a non-empty, non-whitespace string; fail
        # closed otherwise. The original token is passed through unchanged.
        access_token = auth_result.access_token
        if not isinstance(access_token, str) or not access_token.strip():
            logger.error(
                "Cloud recovery auth succeeded without access_token for "
                "user_id=%s",
                current_user.id,
            )
            raise HTTPException(
                status_code=502,
                detail="No se recibió un token de acceso remoto válido.",
            )

        # 9. RECHECK the monitoring guard to narrow the re-auth window.
        if registry.has_any_live_thread():
            raise HTTPException(
                status_code=409,
                detail="Finaliza la captura o el análisis en curso antes de "
                "recuperar datos.",
            )

        # 10. Build the productive RecoveryService and execute in threadpool.
        recovery_service = get_recovery_service(request)
        result = await run_in_threadpool(
            recovery_service.execute_recovery,
            access_token,
            current_user.remote_user_id,
            current_user.id,
        )

        logger.info(
            "Cloud recovery completed for user_id=%s: success=%s "
            "recovered=%d reused=%d skipped=%d conflicts=%d "
            "images_downloaded=%d images_skipped=%d images_failed=%d "
            "errors_count=%d",
            current_user.id,
            result.success,
            result.entities_recovered,
            result.entities_reused,
            result.entities_skipped,
            result.conflicts,
            result.images_downloaded,
            result.images_skipped,
            result.images_failed,
            len(result.errors),
        )

        # 11. Return HTTP 200 even when result.success is False: a partial
        # recovery (durable inserts + a later remote read failure) is a valid,
        # informative outcome, not a transport error.
        return {
            "success": result.success,
            "entities_recovered": result.entities_recovered,
            "entities_reused": result.entities_reused,
            "entities_skipped": result.entities_skipped,
            "conflicts": result.conflicts,
            "images_downloaded": result.images_downloaded,
            "images_skipped": result.images_skipped,
            "images_failed": result.images_failed,
            "errors": [dataclasses.asdict(issue) for issue in result.errors],
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed with a safe log
        logger.error(
            "Cloud recovery failed unexpectedly for user_id=%s: %s",
            current_user.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Ocurrió un error inesperado durante la recuperación.",
        )
    finally:
        runtime_state.release_recovery()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_auth_error(error_type, error_message) -> tuple[int, str]:
    """Map a remote auth error to an (HTTP status, message) pair.

    Mirrors the sync auth-error semantics: all auth failures surface as 401 with
    a safe, user-facing Spanish message. A wrong password never falls back to
    local authentication.
    """
    if error_type == "INVALID_CREDENTIALS":
        return 401, "Contraseña incorrecta. Verifica tu contraseña e intenta de nuevo."
    if error_type == "CONNECTIVITY":
        return 401, "No se pudo conectar al servidor remoto. Verifica tu conexión a internet."
    if error_type == "REMOTE_UNAVAILABLE":
        return 401, "El servidor remoto no está disponible. Intenta más tarde."
    if error_type == "RATE_LIMITED":
        return 401, "Demasiados intentos. Espera unos minutos antes de reintentar."
    if error_type == "AUTH_FORBIDDEN":
        return 401, "Tu cuenta remota está deshabilitada o no tiene permisos."
    return 401, error_message or "Error de autenticación remota."
