"""FastAPI routes for monitoring session control.

Provides endpoints to start, pause, resume, abort, complete, and query
monitoring sessions. Each route delegates to MonitoringService and maps
domain exceptions to appropriate HTTP status codes.

Requirements: 9.1-9.10
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.application.dtos.monitoring_dtos import (
    ErrorResponse,
    MonitoringResponse,
    MonitoringStatusResponse,
    StartMonitoringRequest,
)
from app.dependencies import get_monitoring_service
from src.application.services.monitoring_service import (
    ActiveSessionError,
    MonitoringNotFoundError,
    MonitoringService,
)
from src.domain.exceptions import InvalidTransitionError, ParentNotFoundError

logger = logging.getLogger(__name__)


def _safe_nonneg_int(value, default=0) -> int:
    """Safely convert value to non-negative int, returning default on failure."""
    try:
        v = int(value)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _safe_nonneg_float(value, default=0.0) -> float:
    """Safely convert value to non-negative float, returning default on failure."""
    try:
        v = float(value)
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def _safe_temperature(value) -> float | None:
    """Safely convert value to a non-negative temperature, or None."""
    try:
        v = float(value)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.post("/start", response_model=MonitoringResponse, status_code=201)
async def start_monitoring(
    request: StartMonitoringRequest,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringResponse:
    """Start a new monitoring session for a module.

    Validates module exists, enforces one-active-session-per-module,
    creates the session, and spawns the background worker.
    """
    try:
        monitoring = service.start_session(
            module_id=request.module_id,
            width_m=request.width_m,
            length_m=request.length_m,
            notes=request.notes,
        )
        return MonitoringResponse.model_validate(monitoring)
    except ParentNotFoundError as e:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )
    except ActiveSessionError as e:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )


@router.post("/{monitoring_id}/pause", response_model=MonitoringResponse)
async def pause_monitoring(
    monitoring_id: int,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringResponse:
    """Pause an active monitoring session.

    Transitions the session from 'running' to 'paused' status.
    """
    try:
        monitoring = service.pause_session(monitoring_id)
        return MonitoringResponse.model_validate(monitoring)
    except MonitoringNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )
    except InvalidTransitionError as e:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                detail=str(e),
                current_status=e.current_state,
            ).model_dump(),
        )


@router.post("/{monitoring_id}/resume", response_model=MonitoringResponse)
async def resume_monitoring(
    monitoring_id: int,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringResponse:
    """Resume a paused monitoring session.

    Transitions the session from 'paused' to 'running' status.
    """
    try:
        monitoring = service.resume_session(monitoring_id)
        return MonitoringResponse.model_validate(monitoring)
    except MonitoringNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )
    except InvalidTransitionError as e:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                detail=str(e),
                current_status=e.current_state,
            ).model_dump(),
        )


@router.post("/{monitoring_id}/abort", response_model=MonitoringResponse)
async def abort_monitoring(
    monitoring_id: int,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringResponse:
    """Abort a monitoring session and preserve partial results.

    Transitions the session from any non-terminal state to 'aborted'.
    """
    try:
        monitoring = service.abort_session(monitoring_id)
        return MonitoringResponse.model_validate(monitoring)
    except MonitoringNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )
    except InvalidTransitionError as e:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                detail=str(e),
                current_status=e.current_state,
            ).model_dump(),
        )


@router.post("/{monitoring_id}/complete", response_model=MonitoringResponse)
async def complete_monitoring(
    monitoring_id: int,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringResponse:
    """Signal monitoring traversal is complete and finalize the session.

    Transitions the session from 'running' to 'finishing', computes
    metrics, and then transitions to 'completed'.
    """
    try:
        monitoring = service.complete_session(monitoring_id)
        return MonitoringResponse.model_validate(monitoring)
    except MonitoringNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )
    except InvalidTransitionError as e:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                detail=str(e),
                current_status=e.current_state,
            ).model_dump(),
        )


@router.get("/{monitoring_id}/status", response_model=MonitoringStatusResponse)
async def get_monitoring_status(
    monitoring_id: int,
    request: Request,
    service: MonitoringService = Depends(get_monitoring_service),
) -> MonitoringStatusResponse:
    """Get the current status, counters, and configuration of a monitoring session.

    Includes analysis_processed and analysis_total read from the in-memory
    runtime registry (not persisted). Defaults to 0/0 when no analysis
    runtime is available.
    """
    try:
        monitoring = service.get_status(monitoring_id)
    except MonitoringNotFoundError as e:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                detail=str(e),
                current_status=None,
            ).model_dump(),
        )

    # Read analysis progress from runtime registry (best-effort)
    analysis_processed = 0
    analysis_total = 0
    thermal_paused = False
    thermal_current_temperature_c = None
    thermal_peak_temperature_c = 0.0
    thermal_pause_count = 0
    thermal_pause_duration_seconds = 0.0

    try:
        registry = getattr(request.app.state, "monitoring_runtime_registry", None)
        if registry is not None:
            runtime = registry.get_worker(monitoring_id)
            if runtime is not None:
                progress = getattr(runtime, "progress", None)
                if progress is not None:
                    analysis_processed = _safe_nonneg_int(
                        getattr(progress, "processed_snapshots", 0) or 0
                    )
                    analysis_total = _safe_nonneg_int(
                        getattr(progress, "total_snapshots", 0) or 0
                    )
                    thermal_paused = bool(
                        getattr(progress, "thermal_paused", False)
                    )
                    thermal_current_temperature_c = _safe_temperature(
                        getattr(progress, "thermal_current_temperature_c", None)
                    )
                    thermal_peak_temperature_c = _safe_nonneg_float(
                        getattr(progress, "thermal_peak_temperature_c", 0.0) or 0.0
                    )
                    thermal_pause_count = _safe_nonneg_int(
                        getattr(progress, "thermal_pause_count", 0) or 0
                    )
                    thermal_pause_duration_seconds = _safe_nonneg_float(
                        getattr(progress, "thermal_pause_duration_seconds", 0.0) or 0.0
                    )
    except Exception as e:
        logger.warning(
            f"Failed to read analysis progress for monitoring {monitoring_id}: {e}"
        )
        analysis_processed = 0
        analysis_total = 0
        thermal_paused = False
        thermal_current_temperature_c = None
        thermal_peak_temperature_c = 0.0
        thermal_pause_count = 0
        thermal_pause_duration_seconds = 0.0

    # Determine pause_reason and temperature for response
    pause_reason = None
    temperature = None
    if thermal_paused:
        pause_reason = "Pausado por temperatura. Esperando que la Raspberry Pi se enfríe para continuar el análisis."
    if thermal_current_temperature_c is not None:
        temperature = thermal_current_temperature_c
    elif thermal_peak_temperature_c > 0:
        temperature = thermal_peak_temperature_c

    response = MonitoringStatusResponse.model_validate(monitoring)
    return response.model_copy(
        update={
            "analysis_processed": analysis_processed,
            "analysis_total": analysis_total,
            "temperature": temperature,
            "pause_reason": pause_reason,
            "analysis_thermal_paused": thermal_paused,
            "analysis_peak_temperature_c": thermal_peak_temperature_c,
            "analysis_thermal_pause_count": thermal_pause_count,
            "analysis_thermal_pause_duration_seconds": thermal_pause_duration_seconds,
        }
    )
