from __future__ import annotations

from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from src.infrastructure.config.thresholds import (
    MIN_FRAMES_BETWEEN_CAPTURES,
    MAX_FRAMES_WITHOUT_CAPTURE,
    CENTER_CROP_RATIO,
    ORB_MIN_MATCH_COUNT,
    HSV_HIST_DIFF_THRESHOLD,
    USE_HISTOGRAM_VALIDATION,
)


def crop_center_region(image_bgr, crop_ratio: float = CENTER_CROP_RATIO):
    h, w = image_bgr.shape[:2]

    new_w = int(w * crop_ratio)
    new_h = int(h * crop_ratio)

    x1 = (w - new_w) // 2
    y1 = (h - new_h) // 2
    x2 = x1 + new_w
    y2 = y1 + new_h

    return image_bgr[y1:y2, x1:x2]


def preprocess_for_scene_compare(image_bgr, gate_resolution: Optional[tuple[int, int]] = (320, 320)):
    cropped = crop_center_region(image_bgr, CENTER_CROP_RATIO)
    effective_resolution = gate_resolution if gate_resolution is not None else (320, 320)
    resized = cv2.resize(cropped, effective_resolution)
    blurred = cv2.GaussianBlur(resized, (5, 5), 0)
    return blurred


def compute_orb_features(image_bgr):
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=500)
    keypoints, descriptors = orb.detectAndCompute(gray, None)
    return keypoints, descriptors


def compute_orb_match_count(reference_bgr, current_bgr, gate_resolution: Optional[tuple[int, int]] = (320, 320)) -> int:
    reference_proc = preprocess_for_scene_compare(reference_bgr, gate_resolution)
    current_proc = preprocess_for_scene_compare(current_bgr, gate_resolution)

    _, des1 = compute_orb_features(reference_proc)
    _, des2 = compute_orb_features(current_proc)

    if des1 is None or des2 is None:
        return 0

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)

    if not matches:
        return 0

    matches = sorted(matches, key=lambda m: m.distance)
    good_matches = [m for m in matches if m.distance < 45]
    return len(good_matches)


def compute_hsv_histogram(image_bgr):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist = cv2.normalize(hist, hist).flatten()
    return hist


def compute_histogram_difference(reference_bgr, current_bgr, gate_resolution: Optional[tuple[int, int]] = (320, 320)) -> float:
    reference_proc = preprocess_for_scene_compare(reference_bgr, gate_resolution)
    current_proc = preprocess_for_scene_compare(current_bgr, gate_resolution)

    hist1 = compute_hsv_histogram(reference_proc)
    hist2 = compute_hsv_histogram(current_proc)

    similarity = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
    diff = 1.0 - similarity
    return float(diff)


def should_capture_new_image(
    reference_bgr,
    current_bgr,
    frames_since_last_capture: int,
    *,
    cooldown_frames: int = MIN_FRAMES_BETWEEN_CAPTURES,
    timeout_frames: int = MAX_FRAMES_WITHOUT_CAPTURE,
    orb_threshold: int = ORB_MIN_MATCH_COUNT,
    hsv_threshold: float = HSV_HIST_DIFF_THRESHOLD,
    gate_resolution: Optional[tuple[int, int]] = (320, 320),
) -> Tuple[bool, Dict[str, float]]:
    orb_matches = compute_orb_match_count(reference_bgr, current_bgr, gate_resolution)
    hist_diff = compute_histogram_difference(reference_bgr, current_bgr, gate_resolution)

    cooldown_ok = frames_since_last_capture >= cooldown_frames
    timeout_force = frames_since_last_capture >= timeout_frames

    orb_changed = orb_matches < orb_threshold
    hist_changed = hist_diff > hsv_threshold

    if USE_HISTOGRAM_VALIDATION:
        trigger = cooldown_ok and orb_changed and hist_changed
    else:
        trigger = cooldown_ok and orb_changed

    if timeout_force:
        trigger = True

    orb_change_amount = max(0.0, float(orb_threshold - orb_matches))

    metrics = {
        "orb_matches": float(orb_matches),
        "hist_diff": float(hist_diff),
        "orb_change_amount": float(orb_change_amount),
        "cooldown_ok": float(cooldown_ok),
        "timeout_force": float(timeout_force),
        "orb_changed": float(orb_changed),
        "hist_changed": float(hist_changed),
        "base_trigger": float(trigger),
    }

    return trigger, metrics


def should_run_detector_by_scene_change(
    reference_bgr,
    current_bgr,
    frames_since_last_detection: int,
    *,
    cooldown_frames: int = MIN_FRAMES_BETWEEN_CAPTURES,
    timeout_frames: int = MAX_FRAMES_WITHOUT_CAPTURE,
) -> Tuple[bool, Dict[str, float]]:
    """Scene Gate wrapper used as the injected ``scene_gate_fn``.

    ``cooldown_frames`` / ``timeout_frames`` default to the legacy capture
    thresholds (18 / 45) so the legacy runner and capture-first keep their
    exact observable behavior. Callers that operate with different sampling
    gaps (e.g. video-first ``VideoAnalysisService`` using ``VideoAnalysisConfig``)
    pass explicit overrides so the internal cooldown/timeout match the gaps the
    outer ``decide_run_detector`` already enforces — otherwise the internal
    legacy cooldown would double-gate and the gate could never fire in the
    min-gap window. Visual thresholds (ORB/HSV) are unchanged.
    """
    return should_capture_new_image(
        reference_bgr=reference_bgr,
        current_bgr=current_bgr,
        frames_since_last_capture=frames_since_last_detection,
        cooldown_frames=cooldown_frames,
        timeout_frames=timeout_frames,
    )