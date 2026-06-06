from __future__ import annotations

from pathlib import Path
import torch


# =========================================================
# RUTAS BASE DEL PROYECTO
# =========================================================
# tomato_monitor/
CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONFIG_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
MODELS_DIR = PROJECT_ROOT / "models"
APP_DIR = PROJECT_ROOT / "app"
PIPELINE_CORE_DIR = PROJECT_ROOT / "pipeline_core"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# =========================================================
# ENTRADAS PRINCIPALES
# =========================================================
IMAGES_DIR = DATA_DIR / "images"
VIDEOS_DIR = DATA_DIR / "videos"


# =========================================================
# MODELOS
# =========================================================
DETECTION_MODEL_PATH = MODELS_DIR / "modelo_d2" / "model.pth"
HEALTH_MODEL_B_PATH = MODELS_DIR / "health_model" / "model.pth"


# =========================================================
# DISPOSITIVO
# =========================================================
DEVICE = "cpu"
USE_CUDA = False


# =========================================================
# OUTPUTS GENERALES
# =========================================================
PIPELINE_OUTPUT_DIR = OUTPUTS_DIR / "pipeline_v1"
HEALTH_OUTPUT_DIR = OUTPUTS_DIR / "health"
MATURITY_OUTPUT_DIR = OUTPUTS_DIR / "maturity"
CAPTURE_OUTPUT_DIR = OUTPUTS_DIR / "capture"
REPORTS_OUTPUT_DIR = OUTPUTS_DIR / "reports"
TEMP_OUTPUT_DIR = OUTPUTS_DIR / "temp"


# =========================================================
# NOMBRES DE CLASES
# =========================================================
THING_CLASSES = ["cherry_tomato"]


# =========================================================
# APP VISUAL
# =========================================================
APP_HOST = "127.0.0.1"
APP_PORT = 8000
APP_DEBUG = True


# =========================================================
# BANDERAS DEL PROYECTO
# =========================================================
ENABLE_TRACKING = False          # Sprint 0 y 1: todavía no integrado
ENABLE_HEALTH = True
ENABLE_MATURITY = True
ENABLE_CAPTURE_LOGIC = True

# En Sprint 1 puede servir para decidir si la madurez se ejecuta
# solo para frutos saludables
RUN_MATURITY_ONLY_FOR_HEALTHY = True