from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import torch


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
    """

    name: str

    # Camera resolution
    camera_width: int
    camera_height: int
    camera_fps: int

    # Inference input size (independent of capture resolution)
    inference_input_width: int
    inference_input_height: int

    # Inference options
    skip_maturity: bool
    detection_score_threshold: float
    run_maturity_only_for_healthy: bool

    # Scene Gate parameters
    scene_gate_cooldown_frames: int
    scene_gate_timeout_frames: int
    scene_gate_orb_threshold: int
    scene_gate_hsv_threshold: float

    # Thermal management
    thermal_poll_interval_seconds: float
    thermal_warning_temp: float
    thermal_critical_temp: float
    thermal_resume_temp: float

    # Memory budget
    memory_warning_rss_mb: int


EDGE_PROFILE = ExecutionProfile(
    name="edge",
    camera_width=480,
    camera_height=360,
    camera_fps=5,
    inference_input_width=416,
    inference_input_height=312,
    skip_maturity=True,
    detection_score_threshold=0.85,
    run_maturity_only_for_healthy=True,
    scene_gate_cooldown_frames=30,
    scene_gate_timeout_frames=75,
    scene_gate_orb_threshold=35,
    scene_gate_hsv_threshold=0.38,
    thermal_poll_interval_seconds=5.0,
    thermal_warning_temp=72.0,
    thermal_critical_temp=78.0,
    thermal_resume_temp=65.0,
    memory_warning_rss_mb=3000,
)

FULL_PROFILE = ExecutionProfile(
    name="full",
    camera_width=640,
    camera_height=480,
    camera_fps=5,
    inference_input_width=640,
    inference_input_height=480,
    skip_maturity=False,
    detection_score_threshold=0.80,
    run_maturity_only_for_healthy=True,
    scene_gate_cooldown_frames=18,
    scene_gate_timeout_frames=45,
    scene_gate_orb_threshold=35,
    scene_gate_hsv_threshold=0.38,
    thermal_poll_interval_seconds=10.0,
    thermal_warning_temp=78.0,
    thermal_critical_temp=85.0,
    thermal_resume_temp=72.0,
    memory_warning_rss_mb=4000,
)

# Default profile for the current environment
ACTIVE_PROFILE = EDGE_PROFILE

# --- Validation Limits ---
MAX_GREENHOUSE_NAME_LENGTH = 100
MAX_MODULE_NAME_LENGTH = 100
MAX_NOTES_LENGTH = 500
MAX_DIMENSION_METERS = 1000.0
MIN_DIMENSION_METERS = 0.0  # exclusive (must be > 0)
