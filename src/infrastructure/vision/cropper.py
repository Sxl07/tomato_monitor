from __future__ import annotations

from typing import Tuple

from src.infrastructure.config.thresholds import (
    CROP_EXPAND_RATIO,
    MIN_CROP_WIDTH,
    MIN_CROP_HEIGHT,
)


def clamp_box_xyxy(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    image_width: int,
    image_height: int,
) -> Tuple[int, int, int, int]:
    x1 = max(0, min(x1, image_width - 1))
    y1 = max(0, min(y1, image_height - 1))
    x2 = max(0, min(x2, image_width - 1))
    y2 = max(0, min(y2, image_height - 1))
    return x1, y1, x2, y2


def expand_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    image_width: int,
    image_height: int,
    expand_ratio: float = CROP_EXPAND_RATIO,
) -> Tuple[int, int, int, int]:
    w = x2 - x1
    h = y2 - y1

    dw = int(w * expand_ratio)
    dh = int(h * expand_ratio)

    nx1 = max(0, x1 - dw)
    ny1 = max(0, y1 - dh)
    nx2 = min(image_width - 1, x2 + dw)
    ny2 = min(image_height - 1, y2 + dh)

    return nx1, ny1, nx2, ny2


def crop_from_box(image_bgr, bbox_xyxy: Tuple[int, int, int, int]):
    x1, y1, x2, y2 = bbox_xyxy
    return image_bgr[y1:y2, x1:x2].copy()


def is_crop_large_enough(
    crop_bgr,
    min_width: int = MIN_CROP_WIDTH,
    min_height: int = MIN_CROP_HEIGHT,
) -> bool:
    if crop_bgr is None or crop_bgr.size == 0:
        return False

    h, w = crop_bgr.shape[:2]
    return w >= min_width and h >= min_height