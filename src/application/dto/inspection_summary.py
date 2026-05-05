from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InspectionSummaryDTO:
    strategy_name: str
    video_name: str
    total_frames: int
    detector_runs: int
    detector_skips: int
    detector_run_ratio: float
    avg_total_frame_sec: float
    effective_fps: float
    avg_detector_frame_sec: float
    avg_skipped_frame_sec: float
    avg_propagation_sec: float
    unique_tracks_detected: int
    total_health_executions: int
    total_maturity_executions: int
    total_propagated_rows: int
    reason_first_frame: int = 0
    reason_scene_gate: int = 0
    reason_scene_gate_blocked: int = 0
    reason_max_gap_force: int = 0
    reason_min_gap_ready: int = 0
    reason_cooldown: int = 0
    reason_full_detection: int = 0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "video_name": self.video_name,
            "total_frames": self.total_frames,
            "detector_runs": self.detector_runs,
            "detector_skips": self.detector_skips,
            "detector_run_ratio": self.detector_run_ratio,
            "avg_total_frame_sec": self.avg_total_frame_sec,
            "effective_fps": self.effective_fps,
            "avg_detector_frame_sec": self.avg_detector_frame_sec,
            "avg_skipped_frame_sec": self.avg_skipped_frame_sec,
            "avg_propagation_sec": self.avg_propagation_sec,
            "unique_tracks_detected": self.unique_tracks_detected,
            "total_health_executions": self.total_health_executions,
            "total_maturity_executions": self.total_maturity_executions,
            "total_propagated_rows": self.total_propagated_rows,
            "reason_first_frame": self.reason_first_frame,
            "reason_scene_gate": self.reason_scene_gate,
            "reason_scene_gate_blocked": self.reason_scene_gate_blocked,
            "reason_max_gap_force": self.reason_max_gap_force,
            "reason_min_gap_ready": self.reason_min_gap_ready,
            "reason_cooldown": self.reason_cooldown,
            "reason_full_detection": self.reason_full_detection,
        }

    @staticmethod
    def from_dict(data: dict) -> "InspectionSummaryDTO":
        return InspectionSummaryDTO(
            strategy_name=str(data.get("strategy_name", "")),
            video_name=str(data.get("video_name", "")),
            total_frames=int(data.get("total_frames", 0)),
            detector_runs=int(data.get("detector_runs", 0)),
            detector_skips=int(data.get("detector_skips", 0)),
            detector_run_ratio=float(data.get("detector_run_ratio", 0.0)),
            avg_total_frame_sec=float(data.get("avg_total_frame_sec", 0.0)),
            effective_fps=float(data.get("effective_fps", 0.0)),
            avg_detector_frame_sec=float(data.get("avg_detector_frame_sec", 0.0)),
            avg_skipped_frame_sec=float(data.get("avg_skipped_frame_sec", 0.0)),
            avg_propagation_sec=float(data.get("avg_propagation_sec", 0.0)),
            unique_tracks_detected=int(data.get("unique_tracks_detected", 0)),
            total_health_executions=int(data.get("total_health_executions", 0)),
            total_maturity_executions=int(data.get("total_maturity_executions", 0)),
            total_propagated_rows=int(data.get("total_propagated_rows", 0)),
            reason_first_frame=int(data.get("reason_first_frame", 0)),
            reason_scene_gate=int(data.get("reason_scene_gate", 0)),
            reason_scene_gate_blocked=int(data.get("reason_scene_gate_blocked", 0)),
            reason_max_gap_force=int(data.get("reason_max_gap_force", 0)),
            reason_min_gap_ready=int(data.get("reason_min_gap_ready", 0)),
            reason_cooldown=int(data.get("reason_cooldown", 0)),
            reason_full_detection=int(data.get("reason_full_detection", 0)),
        )