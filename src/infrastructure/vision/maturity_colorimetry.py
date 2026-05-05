from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class MaturityEstimate:
    usda_stage: str
    maturity_percent: float
    confidence: float
    occlusion_ratio: float
    visible_fruit_ratio: float
    warning: Optional[str]
    metrics: dict

    def to_dict(self) -> dict:
        return {
            "usda_stage": self.usda_stage,
            "maturity_percent": self.maturity_percent,
            "confidence": self.confidence,
            "occlusion_ratio": self.occlusion_ratio,
            "visible_fruit_ratio": self.visible_fruit_ratio,
            "warning": self.warning,
            "metrics": self.metrics,
        }


def _rgb_to_lab_a_b_stats(image_bgr: np.ndarray, mask_bool: np.ndarray) -> dict:
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    lab = lab.astype(np.float32)

    l_chan, a_chan, b_chan = cv2.split(lab)

    a_star = a_chan - 128.0
    b_star = b_chan - 128.0

    valid_a = a_star[mask_bool]
    valid_b = b_star[mask_bool]

    eps = 1e-6
    a_over_b = valid_a / (valid_b + eps)

    hue_angles = np.degrees(np.arctan2(valid_b, valid_a))
    hue_angles = np.where(hue_angles < 0, hue_angles + 360, hue_angles)

    return {
        "median_hue_angle_deg": float(np.median(hue_angles)) if hue_angles.size else 0.0,
        "mean_hue_angle_deg": float(np.mean(hue_angles)) if hue_angles.size else 0.0,
        "median_a_star": float(np.median(valid_a)) if valid_a.size else 0.0,
        "mean_a_star": float(np.mean(valid_a)) if valid_a.size else 0.0,
        "median_a_over_b": float(np.median(a_over_b)) if a_over_b.size else 0.0,
        "mean_a_over_b": float(np.mean(a_over_b)) if a_over_b.size else 0.0,
    }


def estimate_maturity_from_crop(
    crop_bgr: np.ndarray,
    fruit_mask: Optional[np.ndarray] = None,
) -> MaturityEstimate:
    if crop_bgr is None or crop_bgr.size == 0:
        return MaturityEstimate(
            usda_stage="unknown",
            maturity_percent=0.0,
            confidence=0.0,
            occlusion_ratio=1.0,
            visible_fruit_ratio=0.0,
            warning="empty_crop",
            metrics={},
        )

    h, w = crop_bgr.shape[:2]
    crop_pixels = h * w

    if fruit_mask is None:
        fruit_mask = np.ones((h, w), dtype=bool)
    else:
        fruit_mask = fruit_mask.astype(bool)

    fruit_pixels = int(fruit_mask.sum())

    if fruit_pixels <= 0:
        return MaturityEstimate(
            usda_stage="unknown",
            maturity_percent=0.0,
            confidence=0.0,
            occlusion_ratio=1.0,
            visible_fruit_ratio=0.0,
            warning="empty_mask",
            metrics={
                "fruit_pixels": 0,
                "crop_pixels": crop_pixels,
                "fruit_fill_ratio": 0.0,
            },
        )

    fruit_fill_ratio = fruit_pixels / max(1, crop_pixels)

    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h_chan, s_chan, v_chan = cv2.split(hsv)

    sat_ok = s_chan > 40
    val_ok = v_chan > 35
    valid_mask = fruit_mask & sat_ok & val_ok

    valid_pixels = int(valid_mask.sum())
    visible_fruit_ratio = valid_pixels / max(1, fruit_pixels)
    occlusion_ratio = 1.0 - visible_fruit_ratio

    if valid_pixels <= 0:
        return MaturityEstimate(
            usda_stage="unknown",
            maturity_percent=0.0,
            confidence=0.0,
            occlusion_ratio=1.0,
            visible_fruit_ratio=0.0,
            warning="no_valid_pixels",
            metrics={
                "fruit_pixels": fruit_pixels,
                "crop_pixels": crop_pixels,
                "fruit_fill_ratio": fruit_fill_ratio,
                "occlusion_ratio": 1.0,
            },
        )

    hue = h_chan[valid_mask]

    green_mask = ((hue >= 35) & (hue <= 95))
    transition_mask = ((hue >= 10) & (hue < 35))
    red_mask = ((hue < 10) | (hue >= 165))

    pct_green_visible = float(green_mask.mean()) if hue.size else 0.0
    pct_transition_visible = float(transition_mask.mean()) if hue.size else 0.0
    pct_red_visible = float(red_mask.mean()) if hue.size else 0.0
    pct_non_green_visible = pct_transition_visible + pct_red_visible

    if pct_green_visible >= 0.90:
        usda_stage = "green"
        maturity_percent = 0.0
    elif pct_green_visible >= 0.60:
        usda_stage = "breaker"
        maturity_percent = 20.0
    elif pct_green_visible >= 0.30:
        usda_stage = "turning"
        maturity_percent = 45.0
    elif pct_red_visible < 0.60:
        usda_stage = "pink"
        maturity_percent = 70.0
    elif pct_red_visible < 0.90:
        usda_stage = "light_red"
        maturity_percent = 85.0
    else:
        usda_stage = "red"
        maturity_percent = 100.0

    confidence = max(0.0, min(1.0, visible_fruit_ratio))

    warning = None
    if visible_fruit_ratio < 0.50:
        warning = "low_visible_fruit_ratio"

    color_stats = _rgb_to_lab_a_b_stats(crop_bgr, valid_mask)

    metrics = {
        "fruit_pixels": fruit_pixels,
        "crop_pixels": crop_pixels,
        "fruit_fill_ratio": fruit_fill_ratio,
        "occlusion_ratio": occlusion_ratio,
        "pct_green_visible": pct_green_visible,
        "pct_transition_visible": pct_transition_visible,
        "pct_red_visible": pct_red_visible,
        "pct_non_green_visible": pct_non_green_visible,
        **color_stats,
    }

    return MaturityEstimate(
        usda_stage=usda_stage,
        maturity_percent=float(maturity_percent),
        confidence=float(confidence),
        occlusion_ratio=float(occlusion_ratio),
        visible_fruit_ratio=float(visible_fruit_ratio),
        warning=warning,
        metrics=metrics,
    )