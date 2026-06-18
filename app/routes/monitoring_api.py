"""API endpoints for monitoring UX: camera preview, status, activity log, last snapshot."""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from src.application.services.camera_service import CameraService, CameraStatus
from src.application.services.log_service import LogService
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories import SqlSnapshotRepository

router = APIRouter(prefix="/api", tags=["monitoring-api"])


@router.get("/camera/preview")
async def camera_preview(request: Request):
    """Return a single JPEG frame from the camera."""
    camera_service = request.app.state.camera_service
    result = camera_service.check_availability()
    if result.status != CameraStatus.AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={"error": "Cámara no disponible", "reason": result.reason},
        )
    frame_bytes = camera_service.capture_preview_frame()
    if frame_bytes is None:
        return JSONResponse(
            status_code=503,
            content={"error": "No se pudo capturar la imagen"},
        )
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/camera/status")
async def camera_status(request: Request):
    """Return camera availability status as JSON."""
    camera_service = request.app.state.camera_service
    result = camera_service.check_availability()
    return {"status": result.status.value, "reason": result.reason}


@router.get("/monitoring/{monitoring_id}/log")
async def monitoring_log(
    monitoring_id: int,
    request: Request,
    since: Optional[str] = Query(None),
):
    """Return log entries for a monitoring session as a JSON array.

    Supports optional `?since=ISO8601` query param for incremental polling.
    Returns empty array if no entries exist (not an error).
    """
    log_service: LogService = request.app.state.log_service

    parsed_since: Optional[datetime] = None
    if since is not None:
        try:
            parsed_since = datetime.fromisoformat(since)
        except (ValueError, TypeError):
            # If the timestamp can't be parsed, ignore the filter
            parsed_since = None

    entries = log_service.get_entries(monitoring_id, since=parsed_since)
    return [
        {
            "timestamp": entry.timestamp.isoformat(),
            "level": entry.level.value,
            "source": entry.source,
            "message": entry.message,
        }
        for entry in entries
    ]


@router.get("/monitoring/{monitoring_id}/last-snapshot")
async def last_snapshot(
    monitoring_id: int,
    request: Request,
):
    """Return the most recent snapshot image as JPEG.

    Reads snapshots for the monitoring from the database, picks the one
    with the highest frame_index, and serves the image file.
    Returns 404 if no snapshots exist or the image file is missing on disk.
    """
    db_manager: DatabaseManager = request.app.state.db_manager
    session = db_manager.get_session()
    try:
        snapshot_repo = SqlSnapshotRepository(session=session)
        snapshots = snapshot_repo.get_by_monitoring(monitoring_id)

        if not snapshots:
            return JSONResponse(
                status_code=404,
                content={"error": "No hay snapshots disponibles"},
            )

        # get_by_monitoring returns ordered by captured_at desc, but we want
        # the highest frame_index for reliability
        most_recent = max(snapshots, key=lambda s: s.frame_index)

        image_path = Path(most_recent.image_path)
        if not image_path.is_absolute():
            # Resolve relative paths from project root
            image_path = Path.cwd() / image_path

        if not image_path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": "No hay snapshots disponibles"},
            )

        image_bytes = image_path.read_bytes()
        return Response(content=image_bytes, media_type="image/jpeg")
    finally:
        session.close()
