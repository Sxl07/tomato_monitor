from __future__ import annotations

from typing import Any, Dict, Tuple

import cv2
import numpy as np

from src.infrastructure.config.thresholds import MATURITY_MIN_CROP_WIDTH, MATURITY_MIN_CROP_HEIGHT
from src.infrastructure.vision.cropper import is_crop_large_enough
from src.infrastructure.vision.maturity_colorimetry import estimate_maturity_from_crop


def create_center_ellipse_mask(
    shape: Tuple[int, int, int] | Tuple[int, int],
    scale_x: float = 0.72,
    scale_y: float = 0.72,
) -> np.ndarray:
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    center = (w // 2, h // 2)
    axes = (
        max(10, int(w * scale_x / 2)),
        max(10, int(h * scale_y / 2)),
    )

    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
    return mask


def largest_centered_component(binary_mask: np.ndarray) -> np.ndarray:
    binary_mask = binary_mask.astype(np.uint8)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary_mask, connectivity=8
    )

    if num_labels <= 1:
        return binary_mask.astype(bool)

    h, w = binary_mask.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    best_idx = None
    best_score = None

    for idx in range(1, num_labels):
        area = stats[idx, cv2.CC_STAT_AREA]
        if area < 100:
            continue

        comp_cx, comp_cy = centroids[idx]
        dist = np.sqrt((comp_cx - cx) ** 2 + (comp_cy - cy) ** 2)

        score = dist - 0.002 * area

        if best_score is None or score < best_score:
            best_score = score
            best_idx = idx

    if best_idx is None:
        return binary_mask.astype(bool)

    return labels == best_idx


def build_local_fruit_mask_with_grabcut(crop_bgr: np.ndarray) -> np.ndarray:
    h, w = crop_bgr.shape[:2]

    if h < 20 or w < 20:
        return np.ones((h, w), dtype=bool)

    gc_mask = np.full((h, w), cv2.GC_PR_BGD, dtype=np.uint8)

    border = max(3, int(min(h, w) * 0.08))
    gc_mask[:border, :] = cv2.GC_BGD
    gc_mask[-border:, :] = cv2.GC_BGD
    gc_mask[:, :border] = cv2.GC_BGD
    gc_mask[:, -border:] = cv2.GC_BGD

    center_mask = create_center_ellipse_mask(crop_bgr.shape, scale_x=0.72, scale_y=0.72)
    gc_mask[center_mask > 0] = cv2.GC_PR_FGD

    strong_center = create_center_ellipse_mask(crop_bgr.shape, scale_x=0.40, scale_y=0.40)
    gc_mask[strong_center > 0] = cv2.GC_FGD

    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(
            crop_bgr,
            gc_mask,
            None,
            bg_model,
            fg_model,
            5,
            cv2.GC_INIT_WITH_MASK,
        )
    except cv2.error:
        return center_mask > 0

    binary = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD),
        1,
        0,
    ).astype(np.uint8)

    kernel = np.ones((5, 5), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    final_mask = largest_centered_component(binary)
    return final_mask


def apply_mask_to_crop(crop_bgr: np.ndarray, mask_bool: np.ndarray) -> np.ndarray:
    out = np.zeros_like(crop_bgr)
    out[mask_bool] = crop_bgr[mask_bool]
    return out


def estimate_maturity_for_crop(crop_bgr: np.ndarray) -> Dict[str, Any]:
    if crop_bgr is None or crop_bgr.size == 0:
        raise ValueError("crop_bgr está vacío o es None")

    if not is_crop_large_enough(crop_bgr):
        h, w = crop_bgr.shape[:2]
        raise ValueError(
            f"Crop demasiado pequeño para madurez: width={w}, height={h}, "
            f"mínimos=({MATURITY_MIN_CROP_WIDTH}, {MATURITY_MIN_CROP_HEIGHT})"
        )

    fruit_mask = build_local_fruit_mask_with_grabcut(crop_bgr)

    if int(fruit_mask.sum()) < 100:
        fruit_mask = create_center_ellipse_mask(
            crop_bgr.shape,
            scale_x=0.65,
            scale_y=0.65,
        ) > 0

    masked_crop = apply_mask_to_crop(crop_bgr, fruit_mask)

    estimate = estimate_maturity_from_crop(crop_bgr, fruit_mask=fruit_mask)

    return {
        "fruit_mask": fruit_mask,
        "masked_crop": masked_crop,
        "estimate": estimate.to_dict(),
    }