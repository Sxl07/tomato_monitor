from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[3]

APP_DIR = BASE_DIR / "app"
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
OUTPUTS_DIR = BASE_DIR / "outputs"

IMAGES_DIR = DATA_DIR / "images"
VIDEOS_DIR = DATA_DIR / "videos"

DETECTION_MODEL_PATH = MODELS_DIR / "modelo_d2" / "model.pth"
HEALTH_MODEL_B_PATH = MODELS_DIR / "health_model" / "model.pth"

DEVICE = "cpu"

RUN_MATURITY_ONLY_FOR_HEALTHY = True

# --- Camera Configuration ---
INPUT_SOURCE = "offline_video"  # Options: "offline_video", "live_camera"
CAMERA_ENABLED = False
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 5


# --- Execution Profiles ---


@dataclass(frozen=True)
class ExecutionProfile:
    """Configuration profile for monitoring execution.

    Defines all tunable parameters for edge vs full mode operation.
    Selected at startup via TOMATO_MONITOR_PROFILE environment variable.
    """

    name: str

    # Camera resolution
    camera_width: int
    camera_height: int
    camera_fps: int

    # Loop frequency control (time-based)
    capture_loop_fps: float
    min_seconds_between_snapshots: float
    max_seconds_without_snapshot: float

    # Scene Gate parameters
    gate_resolution: tuple[int, int]
    scene_gate_cooldown_frames: int
    scene_gate_timeout_frames: int
    scene_gate_orb_threshold: int
    scene_gate_hsv_threshold: float

    # Inference input size (independent of capture resolution)
    # inference_input_width/height: Recorded in pipeline_metrics.json for performance evaluation
    inference_input_width: int
    inference_input_height: int

    # Inference options
    skip_maturity: bool
    detection_score_threshold: float
    run_maturity_only_for_healthy: bool

    # Thermal management
    thermal_poll_interval_seconds: float
    thermal_warning_temp: float
    thermal_critical_temp: float
    thermal_resume_temp: float

    # Analysis phase parameters
    # analysis_skip_maturity: Recorded in pipeline_metrics.json for performance evaluation
    analysis_skip_maturity: bool
    analysis_thermal_pause_threshold: float
    analysis_thermal_resume_threshold: float

    # Memory budget
    memory_warning_rss_mb: int

    # --- Video-first recording (Spec 019) ---
    # video_first_enabled: enables the video-first flow for the profile.
    # recording_target_fps: NOMINAL fps requested from the camera/pipeline and
    #   used as configured_recording_fps by RaspberryCameraFrameSource(camera_mode
    #   ="video"), VideoRecordingWorker and VideoRecorder. It is kept aligned with
    #   camera_fps so the requested cadence and the MP4 container fps match. This
    #   is NOT the effective fps; effective_recording_fps is measured at runtime as
    #   frames_written / recording_duration_seconds.
    # video_codec_candidates: ordered codec fallback for VideoRecorder (tuple).
    video_first_enabled: bool
    recording_target_fps: float
    video_codec_candidates: tuple[str, ...]

    # --- Deferred sparse video analysis (Spec 019) ---
    # Gaps are expressed IN FRAMES, not seconds; there is no frame<->time
    # conversion here. These values are configurable and must be re-evaluated via
    # benchmarks over real videos:
    #   - FULL 5/12 is the documented LEGACY baseline (reference).
    #   - EDGE 1/4 was selected after the Raspberry monitoring 16 benchmark to
    #     prioritize coverage/recall: smaller gaps mean more frequent detection
    #     (a smaller min lets the detector be re-scheduled sooner and a smaller
    #     max forces it sooner), i.e. RetinaNet runs MORE often to preserve
    #     coverage/recall, at the cost of longer offline analysis time
    #     (analysis is offline, not real-time).
    # save_annotated_video: MUST be False in production; the primary artifact is
    #   monitoring.mp4. Annotated video is an optional/diagnostic capability only.
    sparse_min_frames_between_detections: int
    sparse_max_frames_without_detection: int
    sparse_use_scene_gate: bool
    sparse_enable_flow_propagation: bool
    save_annotated_video: bool


EDGE_PROFILE = ExecutionProfile(
    name="edge",
    camera_width=960,
    camera_height=720,
    camera_fps=5,
    capture_loop_fps=5.0,
    min_seconds_between_snapshots=1.0,
    max_seconds_without_snapshot=3.0,
    gate_resolution=(240, 240),
    scene_gate_cooldown_frames=30,
    scene_gate_timeout_frames=75,
    scene_gate_orb_threshold=35,
    scene_gate_hsv_threshold=0.38,
    inference_input_width=416,
    inference_input_height=312,
    skip_maturity=True,
    # Detector score threshold selected experimentally (threshold sweep on
    # Monitoring 21): 0.60 recovers true positives around 0.61–0.66 that 0.70
    # discarded, without going as low as 0.50. Re-evaluate after retraining.
    detection_score_threshold=0.60,
    run_maturity_only_for_healthy=True,
    thermal_poll_interval_seconds=5.0,
    thermal_warning_temp=72.0,
    thermal_critical_temp=78.0,
    thermal_resume_temp=65.0,
    analysis_skip_maturity=False,
    analysis_thermal_pause_threshold=78.0,  # pause at >= 78C
    analysis_thermal_resume_threshold=72.0,
    memory_warning_rss_mb=3000,
    # Video-first recording (nominal fps aligned with camera_fps).
    video_first_enabled=True,
    recording_target_fps=5.0,
    video_codec_candidates=("mp4v", "avc1"),
    # Deferred sparse analysis — EDGE uses smaller gaps (1/4) than FULL (5/12)
    # to prioritize recall in the field. Validated baseline after the Scene
    # Gate wiring fix (Raspberry monitoring 16, 155 frames @ 5 FPS): 1/4 ->
    # ~22.6% scheduled frames vs ~12.9% at 3/8.
    sparse_min_frames_between_detections=1,
    sparse_max_frames_without_detection=4,
    sparse_use_scene_gate=True,
    sparse_enable_flow_propagation=True,
    save_annotated_video=False,
)

FULL_PROFILE = ExecutionProfile(
    name="full",
    camera_width=640,
    camera_height=480,
    camera_fps=10,
    capture_loop_fps=5.0,
    min_seconds_between_snapshots=1.0,
    max_seconds_without_snapshot=3.0,
    gate_resolution=(240, 240),
    scene_gate_cooldown_frames=18,
    scene_gate_timeout_frames=45,
    scene_gate_orb_threshold=35,
    scene_gate_hsv_threshold=0.38,
    inference_input_width=640,
    inference_input_height=480,
    skip_maturity=False,
    detection_score_threshold=0.80,
    run_maturity_only_for_healthy=True,
    thermal_poll_interval_seconds=10.0,
    thermal_warning_temp=78.0,
    thermal_critical_temp=85.0,
    thermal_resume_temp=72.0,
    analysis_skip_maturity=False,
    analysis_thermal_pause_threshold=78.0,
    analysis_thermal_resume_threshold=72.0,
    memory_warning_rss_mb=4000,
    # Video-first recording (nominal fps aligned with camera_fps).
    video_first_enabled=True,
    recording_target_fps=10.0,
    video_codec_candidates=("mp4v", "avc1"),
    # Deferred sparse analysis — FULL uses the legacy baseline (5/12).
    sparse_min_frames_between_detections=5,
    sparse_max_frames_without_detection=12,
    sparse_use_scene_gate=True,
    sparse_enable_flow_propagation=True,
    save_annotated_video=False,
)

# --- Profile Selection via Environment Variable ---
import os
import logging as _logging

_profile_logger = _logging.getLogger(__name__)
_PROFILE_ENV_VAR = "TOMATO_MONITOR_PROFILE"
_profile_value = os.environ.get(_PROFILE_ENV_VAR, "").lower().strip()

if _profile_value == "full":
    ACTIVE_PROFILE = FULL_PROFILE
elif _profile_value == "edge" or _profile_value == "":
    # Empty or "edge" → use edge without warning
    ACTIVE_PROFILE = EDGE_PROFILE
else:
    _profile_logger.warning(
        f"Invalid {_PROFILE_ENV_VAR}='{_profile_value}'. "
        f"Valid values: 'edge', 'full'. Using 'edge' as default."
    )
    ACTIVE_PROFILE = EDGE_PROFILE

# --- Validation Limits ---
MAX_GREENHOUSE_NAME_LENGTH = 100
MAX_MODULE_NAME_LENGTH = 100
MAX_NOTES_LENGTH = 500
MAX_DIMENSION_METERS = 1000.0
MIN_DIMENSION_METERS = 0.0  # exclusive (must be > 0)
