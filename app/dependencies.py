from __future__ import annotations

from src.infrastructure.config.settings import OUTPUTS_DIR

from src.application.services.pipeline_service import PipelineService
from src.application.services.session_service import SessionService
from src.infrastructure.persistence.local.csv_inspection_repository import CsvInspectionRepository
from src.infrastructure.persistence.local.csv_session_repository import CsvSessionRepository
from src.infrastructure.persistence.local.file_artifact_repository import FileArtifactRepository


def get_session_repository() -> CsvSessionRepository:
    return CsvSessionRepository(OUTPUTS_DIR / "meta")


def get_inspection_repository() -> CsvInspectionRepository:
    return CsvInspectionRepository(OUTPUTS_DIR / "experiments")


def get_artifact_repository() -> FileArtifactRepository:
    return FileArtifactRepository(OUTPUTS_DIR / "experiments")


def get_pipeline_service() -> PipelineService:
    return PipelineService(
        artifact_repository=get_artifact_repository(),
    )


def get_session_service() -> SessionService:
    return SessionService(
        session_repository=get_session_repository(),
        artifact_repository=get_artifact_repository(),
    )