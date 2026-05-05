from __future__ import annotations

import csv
import shutil
from pathlib import Path

from src.domain.repositories.artifact_repository import ArtifactRepository


class FileArtifactRepository(ArtifactRepository):
    def __init__(self, experiments_dir: Path) -> None:
        self.experiments_dir = Path(experiments_dir)
        self.experiments_dir.mkdir(parents=True, exist_ok=True)

    def _build_session_dirs(self, session_id: str) -> dict[str, Path]:
        session_dir = self.experiments_dir / session_id
        reports_dir = session_dir / "reports"
        annotated_dir = session_dir / "annotated_video"
        snapshots_dir = session_dir / "detection_snapshots"
        snapshot_annotated_dir = snapshots_dir / "annotated_frames"
        snapshot_raw_dir = snapshots_dir / "raw_frames"
        snapshot_crops_dir = snapshots_dir / "crops"

        return {
            "session_dir": session_dir,
            "reports_dir": reports_dir,
            "annotated_dir": annotated_dir,
            "snapshots_dir": snapshots_dir,
            "snapshot_annotated_dir": snapshot_annotated_dir,
            "snapshot_raw_dir": snapshot_raw_dir,
            "snapshot_crops_dir": snapshot_crops_dir,
        }

    def create_session_dirs(self, session_id: str) -> dict[str, Path]:
        dirs = self._build_session_dirs(session_id)
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        return dirs

    def get_session_dirs(self, session_id: str) -> dict[str, Path]:
        dirs = self._build_session_dirs(session_id)
        if not dirs["session_dir"].exists() or not dirs["session_dir"].is_dir():
            raise FileNotFoundError(f"Sesión no encontrada: {session_id}")
        return dirs

    def delete_session_artifacts(self, session_id: str) -> None:
        dirs = self._build_session_dirs(session_id)
        if dirs["session_dir"].exists():
            shutil.rmtree(dirs["session_dir"])

    def save_summary(self, session_id: str, summary: dict) -> Path:
        dirs = self.create_session_dirs(session_id)
        output_path = dirs["reports_dir"] / "summary.csv"
        self._write_csv([summary], output_path)
        return output_path

    def save_per_frame_rows(self, session_id: str, rows: list[dict]) -> Path:
        dirs = self.create_session_dirs(session_id)
        output_path = dirs["reports_dir"] / "per_frame.csv"
        self._write_csv(rows, output_path)
        return output_path

    def save_per_detection_rows(self, session_id: str, rows: list[dict]) -> Path:
        dirs = self.create_session_dirs(session_id)
        output_path = dirs["reports_dir"] / "per_detection.csv"
        self._write_csv(rows, output_path)
        return output_path

    def list_snapshots(self, session_id: str, limit: int = 20) -> list[str]:
        dirs = self.get_session_dirs(session_id)
        snapshot_dir = dirs["snapshot_annotated_dir"]
        if not snapshot_dir.exists():
            return []

        files = sorted(
            [p for p in snapshot_dir.iterdir() if p.is_file()],
            key=lambda p: p.name,
        )
        return [p.name for p in files[:limit]]

    def list_annotated_videos(self, session_id: str) -> list[str]:
        dirs = self.get_session_dirs(session_id)
        annotated_dir = dirs["annotated_dir"]
        if not annotated_dir.exists():
            return []

        files = sorted(
            [p for p in annotated_dir.iterdir() if p.is_file()],
            key=lambda p: p.name,
        )
        return [p.name for p in files]

    def _write_csv(self, rows: list[dict], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not rows:
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                f.write("")
            return

        fieldnames = list(rows[0].keys())

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)