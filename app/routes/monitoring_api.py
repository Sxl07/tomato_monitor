"""API endpoints for monitoring UX: camera preview, status, activity log, last snapshot."""
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.dependencies import (
    get_live_preview_manager,
    require_current_user_api,
    require_monitoring_owner,
)
from src.application.services.camera_service import CameraService, CameraStatus
from src.application.services.live_preview_manager import STREAM_STOPPED
from src.application.services.log_service import LogService
from src.infrastructure.config.settings import ACTIVE_PROFILE
from src.infrastructure.persistence.database import DatabaseManager
from src.infrastructure.persistence.repositories import SqlSnapshotRepository

router = APIRouter(prefix="/api", tags=["monitoring-api"])

#: MJPEG multipart boundary and media type (Spec 023 preview streams).
_MJPEG_BOUNDARY = "frame"
_MJPEG_MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY}"


def _mjpeg_part(jpeg: bytes) -> bytes:
    """Build one multipart/x-mixed-replace part from JPEG bytes."""
    return (
        b"--" + _MJPEG_BOUNDARY.encode("ascii") + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode("ascii") + b"\r\n"
        b"\r\n" + jpeg + b"\r\n"
    )


@router.get("/camera/preview")
async def camera_preview(request: Request, user=Depends(require_current_user_api)):
    """Return a single JPEG frame from the camera.

    Uses capture_preview_frame() which does a single open-capture-close cycle.
    Does NOT call check_availability() separately to avoid double camera access.
    """
    camera_service: CameraService = request.app.state.camera_service
    frame_bytes = camera_service.capture_preview_frame()
    if frame_bytes is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Cámara no disponible", "reason": "No se pudo capturar la imagen."},
        )
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/camera/preview-stream")
def camera_preview_stream(
    request: Request,
    user=Depends(require_current_user_api),
    manager=Depends(get_live_preview_manager),
):
    """Fluid MJPEG preview BEFORE a monitoring starts (Spec 023).

    Synchronous handler (Starlette runs it in a threadpool) so the blocking
    preflight wait and the generator never touch the event loop. Returns 503
    BEFORE the response headers when the camera cannot start or no frame is
    produced in time; once a 200 StreamingResponse is returned, headers are
    already sent and cannot be downgraded.
    """
    sub = manager.subscribe()
    if sub is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "La cámara no está disponible en este momento.",
            },
        )
    token, generation = sub
    first = manager.wait_for_preview(generation, 0, 2.0)
    if first is None or first is STREAM_STOPPED:
        manager.unsubscribe(token)
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "No se pudo obtener imagen de la cámara.",
            },
        )

    def _generate():
        try:
            last_sequence, jpeg = first
            yield _mjpeg_part(jpeg)  # reuse the preflight frame as the first
            while True:
                item = manager.wait_for_preview(generation, last_sequence, 1.0)
                if item is STREAM_STOPPED:
                    break
                if item is None:
                    continue  # timeout without a new frame; keep waiting
                last_sequence, jpeg = item
                yield _mjpeg_part(jpeg)
        finally:
            manager.unsubscribe(token)

    return StreamingResponse(_generate(), media_type=_MJPEG_MEDIA_TYPE)


@router.get("/monitoring/{monitoring_id}/preview")
async def monitoring_preview(
    monitoring_id: int,
    request: Request,
    _owned_monitoring=Depends(require_monitoring_owner),
):
    """Return the last recorded frame during a video-first recording as JPEG.

    Ownership (Spec 023): access requires the authenticated user to own the
    monitoring (require_monitoring_owner already enforces authentication AND
    ownership, returning 404 for a foreign or non-existent id). Auth is not run
    twice.

    Single camera owner (Requirement 7): the VideoRecordingWorker owns the
    camera during recording. This endpoint NEVER opens the camera, never calls
    capture_single_frame(), never constructs a second RaspberryCameraFrameSource
    / Picamera2, and never re-acquires the camera lock. It only reads a
    thread-safe COPY of the worker's last frame via
    MonitoringRuntimeRegistry.get_worker(monitoring_id).get_last_frame().

    Returns 503 (not an error, no camera opened) when there is no active
    recording worker or no frame has been captured yet.
    """
    registry = getattr(request.app.state, "monitoring_runtime_registry", None)
    worker = registry.get_worker(monitoring_id) if registry is not None else None

    # Only a video-first recording worker exposes get_last_frame(). If there is
    # no such worker (no active recording), report unavailable WITHOUT touching
    # the camera — the caller must not fall back to opening a second source.
    get_last_frame = getattr(worker, "get_last_frame", None)
    if worker is None or not callable(get_last_frame):
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "No hay una grabación activa para este monitoreo.",
            },
        )

    frame = get_last_frame()  # thread-safe COPY from the recording worker
    if frame is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "Aún no hay imagen grabada.",
            },
        )

    frame_bytes = CameraService.encode_frame_jpeg(frame)
    if frame_bytes is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "No se pudo generar la imagen.",
            },
        )
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/monitoring/{monitoring_id}/preview-stream")
def monitoring_preview_stream(
    monitoring_id: int,
    request: Request,
    _owned_monitoring=Depends(require_monitoring_owner),
):
    """Fluid MJPEG preview DURING a video-first recording (Spec 023).

    Ownership enforced by require_monitoring_owner (404 for foreign/missing).
    Synchronous handler (threadpool): the JPEG encode and the small waits never
    run on the event loop. NEVER opens the camera — it only reads thread-safe
    snapshots from the recording worker via the registry. Returns 503 BEFORE the
    headers when there is no worker / no first frame within a bounded wait.
    """
    registry = getattr(request.app.state, "monitoring_runtime_registry", None)
    worker = registry.get_worker(monitoring_id) if registry is not None else None
    snapshot_fn = getattr(worker, "get_preview_frame_snapshot", None)

    # Bounded preflight: wait up to ~2s for the first snapshot so the UI does not
    # fail just because it arrived a few ms before the first captured frame.
    frame_interval = 1.0 / float(ACTIVE_PROFILE.camera_stream_fps)
    deadline = time.monotonic() + 2.0
    first_snapshot = None
    while time.monotonic() < deadline:
        if worker is None or not callable(snapshot_fn):
            # Re-resolve in case the worker registered slightly later.
            worker = registry.get_worker(monitoring_id) if registry is not None else None
            snapshot_fn = getattr(worker, "get_preview_frame_snapshot", None)
        if callable(snapshot_fn):
            snap = snapshot_fn()
            if snap is not None:
                first_snapshot = snap
                break
        time.sleep(frame_interval)

    if first_snapshot is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Vista previa no disponible",
                "reason": "No hay una grabación activa para este monitoreo.",
            },
        )

    initial_worker = worker

    def _generate():
        last_sequence, frame = first_snapshot
        jpeg = CameraService.encode_frame_jpeg(frame)
        if jpeg is not None:
            yield _mjpeg_part(jpeg)
        while True:
            # Re-resolve the worker each iteration: if the registry unregistered
            # or REPLACED it (new session), this stream must end naturally rather
            # than keep serving stale frames from the old object. Never opens the
            # camera / touches the FrameSource.
            current_worker = (
                registry.get_worker(monitoring_id) if registry is not None else None
            )
            if current_worker is None or current_worker is not initial_worker:
                break
            current_snapshot_fn = getattr(
                current_worker, "get_preview_frame_snapshot", None
            )
            if not callable(current_snapshot_fn):
                break
            snap = current_snapshot_fn()
            if snap is None:
                break  # worker gone / no frame -> end the stream
            sequence, frame = snap
            if sequence <= last_sequence:
                time.sleep(frame_interval)  # no new frame yet; no busy-loop
                continue
            last_sequence = sequence
            jpeg = CameraService.encode_frame_jpeg(frame)
            if jpeg is not None:
                yield _mjpeg_part(jpeg)

    return StreamingResponse(_generate(), media_type=_MJPEG_MEDIA_TYPE)


@router.get("/camera/status")
async def camera_status(request: Request, user=Depends(require_current_user_api)):
    """Return camera availability status as JSON.

    Lightweight check — uses is_available() which only instantiates
    Picamera2 briefly to detect hardware, not a full open+capture cycle.
    """
    camera_service: CameraService = request.app.state.camera_service
    result = camera_service.check_availability()
    return {"status": result.status.value, "reason": result.reason}


@router.get("/camera/preview-diagnostics")
async def camera_preview_diagnostics(
    request: Request,
    user=Depends(require_current_user_api),
    manager=Depends(get_live_preview_manager),
):
    """Runtime diagnostics of the pre-monitoring live preview (Spec 023, Task 11).

    Read-only: only calls ``LivePreviewManager.diagnostics()``. It NEVER opens
    the camera, never creates a subscriber and never constructs a FrameSource.
    """
    d = manager.diagnostics()
    return {
        "camera_frames_produced": d.get("camera_frames_produced", 0),
        "preview_frames_encoded": d.get("preview_frames_encoded", 0),
        "active_subscribers": d.get("active_subscribers", 0),
        "camera_capture_elapsed_seconds": d.get("camera_capture_elapsed_seconds", 0.0),
        "effective_camera_stream_fps": d.get("effective_camera_stream_fps", 0.0),
        "generation": d.get("generation", 0),
        "state": {
            "capture_state": d.get("capture_state"),
            "subscriptions_suspended": d.get("subscriptions_suspended"),
        },
    }


@router.get("/monitoring/{monitoring_id}/log")
async def monitoring_log(
    monitoring_id: int,
    request: Request,
    since: Optional[str] = Query(None),
    user=Depends(require_current_user_api),
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
            # Serialize at full precision (microseconds + UTC offset) so the
            # value round-trips through datetime.fromisoformat when the client
            # echoes it back as ?since. Truncating to whole seconds (the old
            # strftime("%Y-%m-%dT%H:%M:%SZ")) dropped microseconds, so the
            # strict ">" filter in LogService kept re-returning the last entry
            # on every poll.
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
    user=Depends(require_current_user_api),
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
