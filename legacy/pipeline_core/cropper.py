from __future__ import annotations

from typing import Tuple

import numpy as np

from config.thresholds import (
    CROP_EXPAND_RATIO,
    MIN_CROP_WIDTH,
    MIN_CROP_HEIGHT,
)


def clamp_box_xyxy(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    height: int,
) -> Tuple[int, int, int, int]:
    x1 = max(0, min(int(x1), width - 1))
    y1 = max(0, min(int(y1), height - 1))
    x2 = max(1, min(int(x2), width))
    y2 = max(1, min(int(y2), height))

    if x2 <= x1:
        x2 = min(width, x1 + 1)
    if y2 <= y1:
        y2 = min(height, y1 + 1)

    return x1, y1, x2, y2


def expand_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    height: int,
    pad_ratio: float = CROP_EXPAND_RATIO,
) -> Tuple[int, int, int, int]:
    bw = x2 - x1
    bh = y2 - y1

    px = int(bw * pad_ratio)
    py = int(bh * pad_ratio)

    return clamp_box_xyxy(
        x1 - px,
        y1 - py,
        x2 + px,
        y2 + py,
        width,
        height,
    )


def crop_from_box(image_bgr: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    return image_bgr[y1:y2, x1:x2].copy()


def is_crop_large_enough(crop_bgr: np.ndarray) -> bool:
    if crop_bgr is None or crop_bgr.size == 0:
        return False

    h, w = crop_bgr.shape[:2]
    return w >= MIN_CROP_WIDTH and h >= MIN_CROP_HEIGHT