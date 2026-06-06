# =========================================================
# DETECCIÓN
# =========================================================
DETECTION_SCORE_THRESHOLD = 0.80


# =========================================================
# SANIDAD (RESNET18)
# =========================================================
HEALTH_B_THRESHOLD = 0.70
HEALTH_IMAGE_SIZE = 224


# =========================================================
# CROP / CALIDAD MÍNIMA
# =========================================================
CROP_EXPAND_RATIO = 0.05
MIN_CROP_WIDTH = 80
MIN_CROP_HEIGHT = 80


# =========================================================
# MADUREZ - FILTRO DE EJECUCIÓN
# =========================================================
MATURITY_MIN_DET_SCORE = 0.80
MATURITY_MIN_CROP_WIDTH = 80
MATURITY_MIN_CROP_HEIGHT = 80


# =========================================================
# CAPTURA VISUAL INTELIGENTE
# =========================================================
MIN_FRAMES_BETWEEN_CAPTURES = 5
MAX_FRAMES_WITHOUT_CAPTURE = 12
SCENE_CHANGE_CONFIRM_FRAMES = 3

CENTER_CROP_RATIO = 0.70

ACCUMULATE_SCENE_CHANGE = True
ACCUMULATED_HIST_DIFF_THRESHOLD = 1.20
ACCUMULATED_ORB_CHANGE_THRESHOLD = 45.0
RESET_ACCUMULATORS_ON_CAPTURE = True
MIN_FRAMES_FOR_ACCUMULATED_TRIGGER = 32

ORB_MIN_MATCH_COUNT = 35
HSV_HIST_DIFF_THRESHOLD = 0.38
USE_HISTOGRAM_VALIDATION = True


# =========================================================
# DEDUPLICACIÓN / TRACKING SIMPLE BASE
# =========================================================
CENTER_DIST_THRESHOLD = 80.0
AREA_RATIO_THRESHOLD = 0.35
COLOR_DIST_THRESHOLD = 35.0


# =========================================================
# VISUALIZACIÓN
# =========================================================
BOX_COLOR_BGR = (255, 0, 255)   # magenta
TEXT_COLOR_BGR = (255, 255, 255)
HEALTHY_COLOR_BGR = (0, 170, 0)
UNHEALTHY_COLOR_BGR = (0, 0, 220)
MATURITY_LABEL_BG_BGR = (120, 60, 180)