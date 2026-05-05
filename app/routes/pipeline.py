from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates

from app.dependencies import get_pipeline_service, get_session_repository, get_session_service
from src.application.dto.inspection_request import InspectionRequest
from src.domain.entities.inspection_session import InspectionSession


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post("/run")
def run_pipeline(
    request: Request,
    strategy_name: str = Form(...),
    video_index: int = Form(...),
    min_frames_between_detections: int = Form(...),
    max_frames_without_detection: int = Form(...),
    use_scene_gate: bool = Form(False),
    max_frames_to_process: str = Form(""),
    save_detection_snapshots: bool = Form(False),
    save_detection_crops: bool = Form(False),
    save_annotated_video: bool = Form(False),
):
    session_service = get_session_service()

    submitted = {
        "strategy_name": strategy_name,
        "video_index": video_index,
        "min_frames_between_detections": min_frames_between_detections,
        "max_frames_without_detection": max_frames_without_detection,
        "use_scene_gate": use_scene_gate,
        "max_frames_to_process": max_frames_to_process,
        "save_detection_snapshots": save_detection_snapshots,
        "save_detection_crops": save_detection_crops,
        "save_annotated_video": save_annotated_video,
    }

    try:
        parsed_max_frames = None
        if str(max_frames_to_process).strip():
            parsed_max_frames = int(str(max_frames_to_process).strip())

        inspection_request = InspectionRequest(
            strategy_name=strategy_name,
            video_index=video_index,
            min_frames_between_detections=min_frames_between_detections,
            max_frames_without_detection=max_frames_without_detection,
            use_scene_gate=use_scene_gate,
            max_frames_to_process=parsed_max_frames,
            save_detection_snapshots=save_detection_snapshots,
            save_detection_crops=save_detection_crops,
            save_annotated_video=save_annotated_video,
        )

        pipeline_service = get_pipeline_service()
        result = pipeline_service.run_video_inspection(inspection_request)

        session_repo = get_session_repository()
        session = InspectionSession(
            session_id=result.session_name,
            strategy_name=strategy_name,
            source_video=f"video_index:{video_index}",
            started_at=datetime.utcnow(),
            status="completed",
            completed_at=datetime.utcnow(),
            parameters=inspection_request.__dict__,
        )
        session_repo.save(session)

        recent_sessions = session_service.list_recent_sessions(limit=10)
        session_names = [s.session_id for s in recent_sessions]

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Tomato Monitor",
                "sessions": session_names,
                "default_form": submitted,
                "message": "Pipeline ejecutado correctamente.",
                "error_message": None,
                "summary": result.summary.to_dict(),
                "generated_paths": result.generated_paths,
                "last_session_name": result.session_name,
            },
        )

    except Exception as exc:
        recent_sessions = session_service.list_recent_sessions(limit=10)
        session_names = [s.session_id for s in recent_sessions]

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Tomato Monitor",
                "sessions": session_names,
                "default_form": submitted,
                "message": None,
                "error_message": f"{type(exc).__name__}: {exc}",
                "summary": None,
                "generated_paths": None,
                "last_session_name": None,
            },
        )