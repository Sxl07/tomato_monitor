from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.templating import Jinja2Templates

from config.settings import OUTPUTS_DIR
from scripts.run_pipeline_video import run_video_pipeline


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


def _build_session_name(
    strategy_name: str,
    min_frames_between_detections: int,
    max_frames_without_detection: int,
    use_scene_gate: bool,
) -> str:
    return (
        f"{strategy_name}"
        f"_min{min_frames_between_detections}"
        f"_max{max_frames_without_detection}"
        f"_gate{int(use_scene_gate)}"
        f"_flow1"
    )


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

        session_name = _build_session_name(
            strategy_name=strategy_name,
            min_frames_between_detections=min_frames_between_detections,
            max_frames_without_detection=max_frames_without_detection,
            use_scene_gate=use_scene_gate,
        )

        summary = run_video_pipeline(
            strategy_name=session_name,
            enable_sparse_detection=True,
            enable_flow_propagation=True,
            use_scene_gate=use_scene_gate,
            min_frames_between_detections=min_frames_between_detections,
            max_frames_without_detection=max_frames_without_detection,
            force_detect_on_first_frame=True,
            save_detection_snapshots=save_detection_snapshots,
            save_detection_crops=save_detection_crops,
            save_annotated_video=save_annotated_video,
            video_index=video_index,
            max_frames_to_process=parsed_max_frames,
            verbose=True,
        )

        session_dir = OUTPUTS_DIR / "experiments" / session_name
        reports_dir = session_dir / "reports"
        annotated_dir = session_dir / "annotated_video"

        generated_paths = {
            "session_dir": str(session_dir),
            "reports_dir": str(reports_dir),
            "summary_csv": str(reports_dir / "summary.csv"),
            "per_frame_csv": str(reports_dir / "per_frame.csv"),
            "per_detection_csv": str(reports_dir / "per_detection.csv"),
            "annotated_dir": str(annotated_dir),
        }

        message = "Pipeline ejecutado correctamente."

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Tomato Monitor",
                "sessions": _load_recent_sessions(),
                "default_form": submitted,
                "message": message,
                "error_message": None,
                "summary": summary,
                "generated_paths": generated_paths,
                "last_session_name": session_name,
            },
        )

    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Tomato Monitor",
                "sessions": _load_recent_sessions(),
                "default_form": submitted,
                "message": None,
                "error_message": f"{type(exc).__name__}: {exc}",
                "summary": None,
                "generated_paths": None,
                "last_session_name": None,
            },
        )