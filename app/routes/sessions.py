from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_artifact_repository, get_session_service


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _safe_snapshot_path(session_name: str, filename: str) -> Path:
    artifact_repository = get_artifact_repository()
    session_dirs = artifact_repository.get_session_dirs(session_name)

    snapshot_path = (session_dirs["snapshot_annotated_dir"] / filename).resolve()
    expected_parent = session_dirs["snapshot_annotated_dir"].resolve()

    if not str(snapshot_path).startswith(str(expected_parent)):
        raise HTTPException(status_code=400, detail="Archivo inválido.")

    if not snapshot_path.exists() or not snapshot_path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot no encontrado.")

    return snapshot_path


@router.get("/")
def list_sessions():
    session_service = get_session_service()
    sessions = session_service.list_recent_sessions(limit=100)

    return {
        "status": "ok",
        "sessions": [s.session_id for s in sessions],
    }


@router.get("/{session_name}")
def session_detail(request: Request, session_name: str):
    session_service = get_session_service()

    try:
        detail = session_service.get_session_detail(session_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")

    return templates.TemplateResponse(
        request,
        "session_detail.html",
        {
            "title": f"Sesión - {detail.session_name}",
            "session_name": detail.session_name,
            "summary": detail.summary.to_dict() if detail.summary else None,
            "snapshot_files": detail.snapshot_files,
            "annotated_videos": detail.annotated_videos,
            "session_dir": detail.session_dir,
            "reports_dir": detail.reports_dir,
            "summary_csv": detail.summary_csv,
            "per_frame_csv": detail.per_frame_csv,
            "per_detection_csv": detail.per_detection_csv,
        },
    )


@router.post("/{session_name}/delete")
def delete_session(session_name: str):
    session_service = get_session_service()
    session_service.delete_session(session_name)
    return RedirectResponse(url="/", status_code=303)


@router.get("/{session_name}/snapshot/{filename}")
def get_snapshot(session_name: str, filename: str):
    snapshot_path = _safe_snapshot_path(session_name, filename)
    return FileResponse(snapshot_path)