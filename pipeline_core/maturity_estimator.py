
"""
maturity_estimator.py

Estimador colorimétrico de maduración para tomates recortados desde detecciones.
Diseñado para trabajar sobre un recorte individual (bounding box) y devolver:

- estado USDA estimado
- porcentaje continuo de madurez (0-100)
- confianza de la estimación
- razón de oclusión por hoja/fondo verde
- métricas colorimétricas útiles para depuración / calibración

Notas:
- Esta implementación usa reglas heurísticas robustas y calibrables.
- Los umbrales iniciales están pensados como punto de partida y deben ajustarse
  con una muestra etiquetada de tu propia cámara/entorno.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np


# =========================================================
# CONFIGURACIÓN CALIBRABLE
# =========================================================
MIN_VALID_FRUIT_PIXELS = 800

# Verde "vegetativo" (hoja/tallo) en HSV + Lab
HSV_GREEN_LOW = np.array([35, 35, 25], dtype=np.uint8)
HSV_GREEN_HIGH = np.array([95, 255, 255], dtype=np.uint8)

# Umbral Lab a* para diferenciar verde vs rojizo.
# En OpenCV Lab va de 0-255 con 128 ~ 0 real.
LAB_A_GREEN_MAX = 124  # <128 tiende a verde; más bajo = más estricto
LAB_A_RED_MIN = 132    # >128 tiende a rojo

# Umbrales de color por píxel sobre la superficie visible del fruto
# Hue OpenCV: 0-179; amarillo ~ 20-35, verde ~ 35-90, rojo cerca de 0/179
HSV_RED_1_LOW = np.array([0, 40, 20], dtype=np.uint8)
HSV_RED_1_HIGH = np.array([15, 255, 255], dtype=np.uint8)
HSV_RED_2_LOW = np.array([165, 40, 20], dtype=np.uint8)
HSV_RED_2_HIGH = np.array([179, 255, 255], dtype=np.uint8)

HSV_TRANSITION_LOW = np.array([8, 25, 20], dtype=np.uint8)
HSV_TRANSITION_HIGH = np.array([35, 255, 255], dtype=np.uint8)

# Rango conceptual del hue angle (grados, no OpenCV hue)
# Verde ~120°, sobremaduro ~40°
HUE_ANGLE_GREEN = 120.0
HUE_ANGLE_RED = 55.0

# Rango conceptual para a*/b*
A_OVER_B_GREEN = -0.30
A_OVER_B_RED = 1.20

# Penalización por oclusión
OCCLUSION_WARNING_THRESHOLD = 0.20
OCCLUSION_HIGH_THRESHOLD = 0.35

# Penalización por fondo/segmentación pobre
LOW_FRUIT_FILL_THRESHOLD = 0.35


# =========================================================
# MODELOS DE DATOS
# =========================================================
@dataclass
class MaturityMetrics:
    fruit_pixels: int
    crop_pixels: int
    fruit_fill_ratio: float
    occlusion_ratio: float
    pct_green_visible: float
    pct_transition_visible: float
    pct_red_visible: float
    pct_non_green_visible: float
    median_hue_angle_deg: float
    mean_hue_angle_deg: float
    median_a_star: float
    mean_a_star: float
    median_a_over_b: float
    mean_a_over_b: float


@dataclass
class MaturityEstimate:
    usda_stage: str
    maturity_percent: float
    confidence: float
    occlusion_ratio: float
    visible_fruit_ratio: float
    warning: Optional[str]
    metrics: MaturityMetrics

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["metrics"] = asdict(self.metrics)
        return payload


# =========================================================
# UTILIDADES COLORIMÉTRICAS
# =========================================================
def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _safe_div(a: np.ndarray, b: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    return a / np.where(np.abs(b) < eps, eps, b)


def _bgr_to_lab_float(image_bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    # OpenCV: L 0..255, a/b 0..255 con 128 ~ 0
    l = lab[:, :, 0] * (100.0 / 255.0)
    a = lab[:, :, 1] - 128.0
    b = lab[:, :, 2] - 128.0
    return l, a, b


def _compute_hue_angle_deg(a_star: np.ndarray, b_star: np.ndarray) -> np.ndarray:
    """
    Hue angle clásico en grados usando atan2(b*, a*), rango 0..360.
    """
    hue = np.degrees(np.arctan2(b_star, a_star))
    hue = np.where(hue < 0, hue + 360.0, hue)
    return hue


def _percent(mask: np.ndarray, valid_mask: np.ndarray) -> float:
    denom = int(valid_mask.sum())
    if denom == 0:
        return 0.0
    return float(mask[valid_mask].sum()) / denom


# =========================================================
# SEGMENTACIÓN
# =========================================================
def _initial_fruit_mask(image_bgr: np.ndarray) -> np.ndarray:
    """
    Máscara amplia del fruto visible:
    - excluye fondo negro o muy oscuro
    - prioriza regiones con suficiente saturación/valor o señal cromática
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    _, a_star, b_star = _bgr_to_lab_float(image_bgr)

    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    # Amplio: zonas no muy oscuras y con algo de saturación o cromaticidad
    chroma = np.sqrt(a_star ** 2 + b_star ** 2)

    mask = (
        (v > 25) &
        (
            (s > 20) |
            (chroma > 8.0)
        )
    )

    mask = (mask.astype(np.uint8) * 255)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    # Quedarse con el mayor componente conexo
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return np.zeros(mask.shape, dtype=bool)

    largest_idx = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    out = labels == largest_idx

    return out


def _vegetative_green_mask(image_bgr: np.ndarray) -> np.ndarray:
    """
    Marca verde vegetativo (hoja/tallo) con doble regla:
    - verde en HSV
    - a* con sesgo a verde
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)

    hsv_green = cv2.inRange(hsv, HSV_GREEN_LOW, HSV_GREEN_HIGH) > 0
    a_channel = lab[:, :, 1]
    lab_green = a_channel <= LAB_A_GREEN_MAX

    mask = hsv_green & lab_green

    kernel = np.ones((3, 3), np.uint8)
    mask_u8 = (mask.astype(np.uint8) * 255)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel, iterations=1)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_DILATE, kernel, iterations=1)

    return mask_u8 > 0


def _refine_visible_fruit_mask(image_bgr: np.ndarray, fruit_mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Estima hoja/tallo verde, pero NO lo resta directamente del fruto.
    En tomates verdes, restarlo causa falsos maduros.
    """
    leaf_mask = _vegetative_green_mask(image_bgr) & fruit_mask

    # Usar todo el fruto estimado para colorimetría
    visible_fruit_mask = fruit_mask.copy()

    return visible_fruit_mask, leaf_mask


# =========================================================
# CLASIFICACIÓN DE PÍXELES DEL FRUTO VISIBLE
# =========================================================
def _classify_visible_pixels(image_bgr: np.ndarray, visible_fruit_mask: np.ndarray) -> Dict[str, np.ndarray]:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    a_ch = lab[:, :, 1]

    red1 = cv2.inRange(hsv, HSV_RED_1_LOW, HSV_RED_1_HIGH) > 0
    red2 = cv2.inRange(hsv, HSV_RED_2_LOW, HSV_RED_2_HIGH) > 0
    red_hsv = red1 | red2
    red_lab = a_ch >= LAB_A_RED_MIN
    red_like = red_hsv & red_lab & visible_fruit_mask

    transition_like = (cv2.inRange(hsv, HSV_TRANSITION_LOW, HSV_TRANSITION_HIGH) > 0) & visible_fruit_mask
    # evitar contar rojo como transición
    transition_like = transition_like & (~red_like)

    # "verde visible" aquí significa todavía no maduro dentro del fruto visible,
    # no hoja. Se aproxima por lo que quedó fuera de rojo/transición dentro del fruto visible.
    green_visible = visible_fruit_mask & (~red_like) & (~transition_like)

    return {
        "red_like": red_like,
        "transition_like": transition_like,
        "green_visible": green_visible,
    }


# =========================================================
# MAPEO USDA + % CONTINUO
# =========================================================
def _usda_stage_from_percentages(pct_red: float, pct_non_green: float) -> str:
    """
    Basado en la lógica USDA por porcentaje de superficie con cambio de color.
    """
    if pct_red <= 0.01 and pct_non_green <= 0.05:
        return "green"
    if pct_non_green <= 0.10:
        return "breaker"
    if pct_non_green <= 0.30:
        return "turning"
    if pct_non_green <= 0.60:
        return "pink"
    if pct_non_green <= 0.90:
        return "light_red"
    return "red"


def _normalize_hue_angle_score(median_hue_angle_deg: float) -> float:
    """
    Convierte hue angle a score 0..1 donde:
    - ~120° => 0 (verde)
    - ~55°  => 1 (rojo aceptable)
    """
    span = max(1e-6, HUE_ANGLE_GREEN - HUE_ANGLE_RED)
    clamped_hue = min(max(median_hue_angle_deg, HUE_ANGLE_RED), HUE_ANGLE_GREEN)
    score = (HUE_ANGLE_GREEN - clamped_hue) / span
    return _clip01(score)


def _normalize_a_over_b_score(median_a_over_b: float) -> float:
    span = max(1e-6, A_OVER_B_RED - A_OVER_B_GREEN)
    score = (median_a_over_b - A_OVER_B_GREEN) / span
    return _clip01(score)


def _continuous_percent_from_stage(
    stage: str,
    pct_non_green: float,
    pct_red: float,
    hue_score: float,
    a_over_b_score: float,
) -> float:
    """
    Mapea a porcentaje continuo usando:
    - estado USDA por superficie
    - refinamiento con hue y a*/b*
    """
    stage_ranges = {
        "green": (0.0, 10.0),
        "breaker": (10.0, 20.0),
        "turning": (20.0, 40.0),
        "pink": (40.0, 60.0),
        "light_red": (60.0, 85.0),
        "red": (85.0, 100.0),
    }
    low, high = stage_ranges[stage]

    # score principal por superficie visible
    if stage == "green":
        surface_score = _clip01(pct_non_green / 0.10)
    elif stage == "breaker":
        surface_score = _clip01((pct_non_green - 0.00) / 0.10)
    elif stage == "turning":
        surface_score = _clip01((pct_non_green - 0.10) / 0.20)
    elif stage == "pink":
        surface_score = _clip01((pct_non_green - 0.30) / 0.30)
    elif stage == "light_red":
        # aquí conviene darle más peso al rojo
        mixed = 0.5 * _clip01((pct_non_green - 0.60) / 0.30) + 0.5 * _clip01((pct_red - 0.30) / 0.50)
        surface_score = _clip01(mixed)
    else:  # red
        mixed = 0.6 * _clip01((pct_red - 0.60) / 0.40) + 0.4 * max(hue_score, a_over_b_score)
        surface_score = _clip01(mixed)

    color_refinement = 0.5 * hue_score + 0.5 * a_over_b_score
    combined = 0.9 * surface_score + 0.1 * color_refinement
    return float(low + (high - low) * _clip01(combined))


def _confidence_score(
    fruit_pixels: int,
    crop_pixels: int,
    visible_fruit_pixels: int,
    occlusion_ratio: float,
    pct_red: float,
    pct_non_green: float,
) -> Tuple[float, Optional[str]]:
    """
    Confianza heurística:
    - baja si hay poco fruto útil
    - baja si hay mucha oclusión
    - baja si la segmentación útil ocupa muy poco del recorte
    """
    warning = None

    if fruit_pixels <= 0 or visible_fruit_pixels <= 0:
        return 0.0, "sin_superficie_util"

    fruit_fill_ratio = fruit_pixels / max(1, crop_pixels)
    visible_ratio = visible_fruit_pixels / max(1, fruit_pixels)

    conf = 1.0

    if fruit_pixels < MIN_VALID_FRUIT_PIXELS:
        conf *= 0.65
        warning = "fruto_visible_pequeno"

    if fruit_fill_ratio < LOW_FRUIT_FILL_THRESHOLD:
        conf *= 0.75
        warning = warning or "segmentacion_pobre_o_fondo_excesivo"

    if occlusion_ratio > OCCLUSION_WARNING_THRESHOLD:
        conf *= 0.80
        warning = warning or "oclusion_parcial"

    if occlusion_ratio > OCCLUSION_HIGH_THRESHOLD:
        conf *= 0.65
        warning = "oclusion_alta"

    # si prácticamente todo es verde o prácticamente todo es rojo, normalmente más seguro
    extremeness = max(pct_red, 1.0 - pct_non_green)
    conf *= 0.85 + 0.15 * _clip01(extremeness)

    conf *= 0.75 + 0.25 * _clip01(visible_ratio)

    return _clip01(conf), warning


# =========================================================
# API PRINCIPAL
# =========================================================
def estimate_maturity_from_crop(image_bgr: np.ndarray,fruit_mask: Optional[np.ndarray] = None) -> MaturityEstimate:
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image_bgr está vacío o es None")

    if len(image_bgr.shape) != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr debe ser una imagen BGR de 3 canales")

    h, w = image_bgr.shape[:2]
    crop_pixels = int(h * w)

    # 1) Máscara del fruto:
    # - si viene fruit_mask, usarla directamente
    # - si no viene, usar la heurística actual
    if fruit_mask is not None:
        if fruit_mask.dtype != np.bool_:
            fruit_mask = fruit_mask > 0

        if fruit_mask.shape[:2] != image_bgr.shape[:2]:
            raise ValueError(
                f"fruit_mask debe tener la misma forma espacial que image_bgr. "
                f"mask={fruit_mask.shape[:2]} image={image_bgr.shape[:2]}"
            )

        fruit_mask = fruit_mask.astype(bool)
    else:
        fruit_mask = _initial_fruit_mask(image_bgr)

    fruit_pixels = int(fruit_mask.sum())

    # Si la máscara sale muy pobre, usar fallback amplio
    if fruit_pixels < MIN_VALID_FRUIT_PIXELS:
        hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
        fallback = (hsv[:, :, 2] > 25)
        fruit_mask = fallback
        fruit_pixels = int(fruit_mask.sum())

   # 2) Si la máscara viene del dataset, usarla como superficie del fruto.
    # La oclusión por hoja deja de ser una heurística fuerte en este modo.
    if fruit_mask is not None:
        visible_fruit_mask = fruit_mask.copy()
        leaf_mask = np.zeros_like(fruit_mask, dtype=bool)
        visible_fruit_pixels = int(visible_fruit_mask.sum())
        leaf_pixels = 0
    else:
        visible_fruit_mask, leaf_mask = _refine_visible_fruit_mask(image_bgr, fruit_mask)
        visible_fruit_pixels = int(visible_fruit_mask.sum())
        leaf_pixels = int(leaf_mask.sum())

        if visible_fruit_pixels < max(200, MIN_VALID_FRUIT_PIXELS // 4):
            hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
            visible_fruit_mask = (hsv[:, :, 2] > 25)
            visible_fruit_pixels = int(visible_fruit_mask.sum())
            leaf_pixels = 0

    # 3) Métricas colorimétricas sobre fruto visible útil
    _, a_star, b_star = _bgr_to_lab_float(image_bgr)
    hue_angle = _compute_hue_angle_deg(a_star, b_star)
    a_over_b = _safe_div(a_star, b_star)

    pixel_classes = _classify_visible_pixels(image_bgr, visible_fruit_mask)
    red_like = pixel_classes["red_like"]
    transition_like = pixel_classes["transition_like"]
    green_visible = pixel_classes["green_visible"]

    pct_red_visible = _percent(red_like, visible_fruit_mask)
    pct_transition_visible = _percent(transition_like, visible_fruit_mask)
    pct_green_visible = _percent(green_visible, visible_fruit_mask)
    pct_non_green_visible = _clip01(pct_red_visible + pct_transition_visible)

    visible_hue = hue_angle[visible_fruit_mask]
    visible_a = a_star[visible_fruit_mask]
    visible_a_over_b = a_over_b[visible_fruit_mask]

    median_hue = float(np.median(visible_hue)) if visible_hue.size else 180.0
    mean_hue = float(np.mean(visible_hue)) if visible_hue.size else 180.0
    median_a = float(np.median(visible_a)) if visible_a.size else -20.0
    mean_a = float(np.mean(visible_a)) if visible_a.size else -20.0
    median_aob = float(np.median(visible_a_over_b)) if visible_a_over_b.size else -1.0
    mean_aob = float(np.mean(visible_a_over_b)) if visible_a_over_b.size else -1.0

    # Como leaf_mask puede incluir parte del fruto verde, limitar su efecto
    raw_occlusion_ratio = float(leaf_pixels) / max(1, fruit_pixels)
    occlusion_ratio = min(raw_occlusion_ratio, 0.30)
    fruit_fill_ratio = float(fruit_pixels) / max(1, crop_pixels)

    # 4) Estado USDA
    stage = _usda_stage_from_percentages(
        pct_red=pct_red_visible,
        pct_non_green=pct_non_green_visible,
    )

    # 5) Porcentaje continuo
    hue_score = _normalize_hue_angle_score(median_hue)
    aob_score = _normalize_a_over_b_score(median_aob)

    maturity_percent = _continuous_percent_from_stage(
        stage=stage,
        pct_non_green=pct_non_green_visible,
        pct_red=pct_red_visible,
        hue_score=hue_score,
        a_over_b_score=aob_score,
    )

    # 6) Confianza
    confidence, warning = _confidence_score(
        fruit_pixels=fruit_pixels,
        crop_pixels=crop_pixels,
        visible_fruit_pixels=visible_fruit_pixels,
        occlusion_ratio=occlusion_ratio,
        pct_red=pct_red_visible,
        pct_non_green=pct_non_green_visible,
    )

    metrics = MaturityMetrics(
        fruit_pixels=fruit_pixels,
        crop_pixels=crop_pixels,
        fruit_fill_ratio=fruit_fill_ratio,
        occlusion_ratio=occlusion_ratio,
        pct_green_visible=pct_green_visible,
        pct_transition_visible=pct_transition_visible,
        pct_red_visible=pct_red_visible,
        pct_non_green_visible=pct_non_green_visible,
        median_hue_angle_deg=median_hue,
        mean_hue_angle_deg=mean_hue,
        median_a_star=median_a,
        mean_a_star=mean_a,
        median_a_over_b=median_aob,
        mean_a_over_b=mean_aob,
    )

    return MaturityEstimate(
        usda_stage=stage,
        maturity_percent=round(float(maturity_percent), 2),
        confidence=round(float(confidence), 3),
        occlusion_ratio=round(float(occlusion_ratio), 3),
        visible_fruit_ratio=round(float(visible_fruit_pixels / max(1, fruit_pixels)), 3),
        warning=warning,
        metrics=metrics,
    )


def visualize_masks(image_bgr: np.ndarray) -> Dict[str, np.ndarray]:
    """
    Utilidad de depuración para ver:
    - máscara amplia del fruto
    - máscara de hoja
    - máscara de fruto visible
    - clasificaciones rojo/transición/verde visible
    """
    fruit_mask = _initial_fruit_mask(image_bgr)
    visible_fruit_mask, leaf_mask = _refine_visible_fruit_mask(image_bgr, fruit_mask)
    pixel_classes = _classify_visible_pixels(image_bgr, visible_fruit_mask)

    out = {
        "fruit_mask": (fruit_mask.astype(np.uint8) * 255),
        "leaf_mask": (leaf_mask.astype(np.uint8) * 255),
        "visible_fruit_mask": (visible_fruit_mask.astype(np.uint8) * 255),
        "red_like": (pixel_classes["red_like"].astype(np.uint8) * 255),
        "transition_like": (pixel_classes["transition_like"].astype(np.uint8) * 255),
        "green_visible": (pixel_classes["green_visible"].astype(np.uint8) * 255),
    }
    return out


def estimate_maturity_from_path(
    image_path: str,
    fruit_mask: Optional[np.ndarray] = None
) -> MaturityEstimate:
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        raise FileNotFoundError(f"No se pudo leer la imagen: {image_path}")
    return estimate_maturity_from_crop(image_bgr, fruit_mask=fruit_mask)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Estimador colorimétrico de maduración para tomates.")
    parser.add_argument("image", help="Ruta al recorte del tomate")
    parser.add_argument("--save-masks-dir", default=None, help="Directorio opcional para guardar máscaras de depuración")
    args = parser.parse_args()

    estimate = estimate_maturity_from_path(args.image)
    print(json.dumps(estimate.to_dict(), indent=2, ensure_ascii=False))

    if args.save_masks_dir:
        out_dir = Path(args.save_masks_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        image_bgr = cv2.imread(args.image)
        masks = visualize_masks(image_bgr)
        for name, mask in masks.items():
            cv2.imwrite(str(out_dir / f"{name}.png"), mask)
