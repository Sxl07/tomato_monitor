from __future__ import annotations

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