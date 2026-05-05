from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from config.settings import OUTPUTS_DIR


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _load_recent_sessions(limit: int = 10) -> list[str]:
    experiments_dir = OUTPUTS_DIR / "experiments"

    sessions = []
    if experiments_dir.exists():
        for path in sorted(experiments_dir.iterdir(), key=lambda p: p.name.lower(), reverse=True):
            if path.is_dir():
                sessions.append(path.name)

    return sessions[:limit]


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
    sessions = _load_recent_sessions()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "Tomato Monitor",
            "sessions": sessions,
            "default_form": _default_form(),
            "message": None,
            "error_message": None,
            "summary": None,
            "generated_paths": None,
            "last_session_name": None,
        },
    )