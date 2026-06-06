from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.dependencies import get_session_service


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _default_form() -> dict:
    return {
        "strategy_name": "sparse_flow_candidate",
        "video_index": 0,
        "min_frames_between_detections": 5,
        "max_frames_without_detection": 12,
        "use_scene_gate": True,
        "max_frames_to_process": "",
        "save_detection_snapshots": True,
        "save_detection_crops": True,
        "save_annotated_video": True,
    }


@router.get("/")
def home(request: Request):
    session_service = get_session_service()
    recent_sessions = session_service.list_recent_sessions(limit=10)
    session_names = [session.session_id for session in recent_sessions]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "Tomato Monitor",
            "sessions": session_names,
            "default_form": _default_form(),
            "message": None,
            "error_message": None,
            "summary": None,
            "generated_paths": None,
            "last_session_name": None,
        },
    )