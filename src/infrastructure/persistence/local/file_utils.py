from __future__ import annotations

import csv
from pathlib import Path

import cv2


def write_csv(rows: list[dict], output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write("")
        return

    fieldnames = list(rows[0].keys())

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_image(image_bgr, output_path: Path) -> bool:
    """Write an image to disk, creating parent dirs.

    Returns True if the write succeeded, False otherwise. Existing callers may
    ignore the return value (backward-compatible).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(output_path), image_bgr))