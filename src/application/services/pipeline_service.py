from __future__ import annotations

from src.application.dto.inspection_request import InspectionRequest
from src.application.use_cases.run_video_inspection import (
    RunVideoInspectionResult,
    RunVideoInspectionUseCase,
)
from src.domain.repositories.artifact_repository import ArtifactRepository


class PipelineService:
    def __init__(self, artifact_repository: ArtifactRepository) -> None:
        self.use_case = RunVideoInspectionUseCase(artifact_repository)

    def run_video_inspection(
        self,
        request: InspectionRequest,
    ) -> RunVideoInspectionResult:
        from src.infrastructure.vision.video_inspection_runner import run_video_inspection

        return self.use_case.execute(
            request=request,
            runner=run_video_inspection,
        )