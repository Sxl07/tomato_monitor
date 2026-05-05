from __future__ import annotations

import csv
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse

from config.settings import OUTPUTS_DIR


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _safe_session_dir(session_name: str) -> Path:
    session_dir = (OUTPUTS_DIR / "experiments" / session_name).resolve()
    experiments_dir = (OUTPUTS_DIR / "experiments").resolve()

    if not str(session_dir).startswith(str(experiments_dir)):
        raise HTTPException(status_code=400, detail="Nombre de sesión inválido.")

    if not session_dir.exists() or not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")

    return session_dir


def _read_summary_csv(summary_path: Path) -> dict | None:
    if not summary_path.exists():
        return None

    with open(summary_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return None

    return rows[0]


def _list_snapshot_files(session_dir: Path, limit: int = 20) -> list[str]:
    annotated_dir = session_dir / "detection_snapshots" / "annotated_frames"
    if not annotated_dir.exists():
        return []

    files = sorted(
        [p for p in annotated_dir.iterdir() if p.is_file()],
        key=lambda p: p.name
    )
    return [p.name for p in files[:limit]]


def _list_annotated_videos(session_dir: Path) -> list[str]:
    annotated_dir = session_dir / "annotated_video"
    if not annotated_dir.exists():
        return []

    files = sorted(
        [p for p in annotated_dir.iterdir() if p.is_file()],
        key=lambda p: p.name
    )
    return [p.name for p in files]


@router.get("/")
def list_sessions():
    experiments_dir = OUTPUTS_DIR / "experiments"
    sessions = []

    if experiments_dir.exists():
        for path in sorted(experiments_dir.iterdir(), key=lambda p: p.name.lower(), reverse=True):
            if path.is_dir():
                sessions.append(path.name)

    return {
        "status": "ok",
        "sessions": sessions,
    }


@router.get("/{session_name}")
def session_detail(request: Request, session_name: str):
    session_dir = _safe_session_dir(session_name)

    reports_dir = session_dir / "reports"
    summary = _read_summary_csv(reports_dir / "summary.csv")

    snapshot_files = _list_snapshot_files(session_dir)
    annotated_videos = _list_annotated_videos(session_dir)

    return templates.TemplateResponse(
        request,
        "session_detail.html",
        {
            "title": f"Sesión - {session_name}",
            "session_name": session_name,
            "summary": summary,
            "snapshot_files": snapshot_files,
            "annotated_videos": annotated_videos,
            "session_dir": str(session_dir),
            "reports_dir": str(reports_dir),
            "summary_csv": str(reports_dir / "summary.csv"),
            "per_frame_csv": str(reports_dir / "per_frame.csv"),
            "per_detection_csv": str(reports_dir / "per_detection.csv"),
        },
    )

@router.get("/{session_name}/snapshot/{filename}")
def get_snapshot(session_name: str, filename: str):
    session_dir = _safe_session_dir(session_name)
    snapshot_path = (session_dir / "detection_snapshots" / "annotated_frames" / filename).resolve()

    expected_parent = (session_dir / "detection_snapshots" / "annotated_frames").resolve()
    if not str(snapshot_path).startswith(str(expected_parent)):
        raise HTTPException(status_code=400, detail="Archivo inválido.")

    if not snapshot_path.exists() or not snapshot_path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot no encontrado.")

    return FileResponse(snapshot_path)