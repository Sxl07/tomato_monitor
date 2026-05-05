from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_image(image, path: Path) -> None:
    ensure_dir(path.parent)
    cv2.imwrite(str(path), image)


def save_mask(mask_bool: np.ndarray, path: Path) -> None:
    ensure_dir(path.parent)
    mask_u8 = (mask_bool.astype("uint8") * 255)
    cv2.imwrite(str(path), mask_u8)