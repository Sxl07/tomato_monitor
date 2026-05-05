from __future__ import annotations

from src.application.dto.session_detail import SessionDetailDTO
from src.domain.repositories.artifact_repository import ArtifactRepository


class GetSessionDetailUseCase:
    def __init__(self, artifact_repository: ArtifactRepository) -> None:
        self.artifact_repository = artifact_repository

    def execute(self, session_name: str, summary_dto) -> SessionDetailDTO:
        session_dirs = self.artifact_repository.ensure_session_dirs(session_name)

        return SessionDetailDTO(
            session_name=session_name,
            summary=summary_dto,
            session_dir=str(session_dirs["session_dir"]),
            reports_dir=str(session_dirs["reports_dir"]),
            summary_csv=str(session_dirs["reports_dir"] / "summary.csv"),
            per_frame_csv=str(session_dirs["reports_dir"] / "per_frame.csv"),
            per_detection_csv=str(session_dirs["reports_dir"] / "per_detection.csv"),
            snapshot_files=self.artifact_repository.list_snapshots(session_name),
            annotated_videos=self.artifact_repository.list_annotated_videos(session_name),
        )