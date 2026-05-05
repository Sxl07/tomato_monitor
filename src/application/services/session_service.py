from __future__ import annotations

import csv
from pathlib import Path

from src.application.dto.inspection_summary import InspectionSummaryDTO
from src.application.dto.session_detail import SessionDetailDTO
from src.application.use_cases.get_session_detail import GetSessionDetailUseCase
from src.application.use_cases.list_sessions import ListSessionsUseCase
from src.domain.repositories.artifact_repository import ArtifactRepository
from src.domain.repositories.session_repository import SessionRepository


class SessionService:
    def __init__(
        self,
        session_repository: SessionRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self.session_repository = session_repository
        self.artifact_repository = artifact_repository
        self.list_sessions_use_case = ListSessionsUseCase(session_repository)
        self.get_session_detail_use_case = GetSessionDetailUseCase(artifact_repository)

    def list_recent_sessions(self, limit: int = 10) -> list:
        return self.list_sessions_use_case.execute(limit=limit)

    def read_summary_csv(self, summary_path: Path) -> InspectionSummaryDTO | None:
        if not summary_path.exists():
            return None

        with open(summary_path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            return None

        return InspectionSummaryDTO.from_dict(rows[0])

    def get_session_detail(self, session_name: str) -> SessionDetailDTO:
        session_dirs = self.artifact_repository.ensure_session_dirs(session_name)
        summary = self.read_summary_csv(session_dirs["reports_dir"] / "summary.csv")

        return self.get_session_detail_use_case.execute(
            session_name=session_name,
            summary_dto=summary,
        )