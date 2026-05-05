from __future__ import annotations

from dataclasses import dataclass

from src.application.dto.inspection_request import InspectionRequest
from src.application.dto.inspection_summary import InspectionSummaryDTO
from src.domain.repositories.artifact_repository import ArtifactRepository


@dataclass
class RunVideoInspectionResult:
    session_name: str
    summary: InspectionSummaryDTO
    generated_paths: dict[str, str]


class RunVideoInspectionUseCase:
    def __init__(self, artifact_repository: ArtifactRepository) -> None:
        self.artifact_repository = artifact_repository

    def build_session_name(self, request: InspectionRequest) -> str:
        return (
            f"{request.strategy_name}"
            f"_min{request.min_frames_between_detections}"
            f"_max{request.max_frames_without_detection}"
            f"_gate{int(request.use_scene_gate)}"
            f"_flow1"
        )

    def execute(
        self,
        request: InspectionRequest,
        runner,
    ) -> RunVideoInspectionResult:
        session_name = self.build_session_name(request)

        summary_dict = runner(
            strategy_name=session_name,
            enable_sparse_detection=True,
            enable_flow_propagation=True,
            use_scene_gate=request.use_scene_gate,
            min_frames_between_detections=request.min_frames_between_detections,
            max_frames_without_detection=request.max_frames_without_detection,
            force_detect_on_first_frame=True,
            save_detection_snapshots=request.save_detection_snapshots,
            save_detection_crops=request.save_detection_crops,
            save_annotated_video=request.save_annotated_video,
            video_index=request.video_index,
            max_frames_to_process=request.max_frames_to_process,
            verbose=True,
        )

        summary = InspectionSummaryDTO.from_dict(summary_dict)

        session_dir = self.artifact_repository.ensure_session_dirs(session_name)

        generated_paths = {
            "session_dir": str(session_dir["session_dir"]),
            "reports_dir": str(session_dir["reports_dir"]),
            "summary_csv": str(session_dir["reports_dir"] / "summary.csv"),
            "per_frame_csv": str(session_dir["reports_dir"] / "per_frame.csv"),
            "per_detection_csv": str(session_dir["reports_dir"] / "per_detection.csv"),
            "annotated_dir": str(session_dir["annotated_dir"]),
        }

        return RunVideoInspectionResult(
            session_name=session_name,
            summary=summary,
            generated_paths=generated_paths,
        )