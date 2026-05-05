from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from config.settings import RUN_MATURITY_ONLY_FOR_HEALTHY
from config.thresholds import MATURITY_MIN_DET_SCORE

from pipeline_core.cropper import (
    clamp_box_xyxy,
    expand_box,
    crop_from_box,
    is_crop_large_enough,
)
from pipeline_core.detector import (
    build_tomato_detector,
    run_detection,
    extract_detection_dicts,
)
from pipeline_core.health import (
    build_health_model_resnet,
    predict_health,
)
from pipeline_core.maturity import estimate_maturity_for_crop
from pipeline_core.tracker import SimpleTracker


@dataclass
class PipelineComponents:
    detector: Any
    health_model: Any
    health_transform: Any
    tracker: SimpleTracker


def build_pipeline_components() -> PipelineComponents:
    detector = build_tomato_detector()
    health_model, health_transform = build_health_model_resnet()
    tracker = SimpleTracker()

    return PipelineComponents(
        detector=detector,
        health_model=health_model,
        health_transform=health_transform,
        tracker=tracker,
    )


def should_run_maturity(det: dict, crop_bgr: np.ndarray, health_result: Optional[dict]) -> bool:
    if float(det["score"]) < MATURITY_MIN_DET_SCORE:
        return False

    if not is_crop_large_enough(crop_bgr):
        return False

    if RUN_MATURITY_ONLY_FOR_HEALTHY:
        if not health_result:
            return False
        if health_result["label"] != "healthy":
            return False

    return True


def process_frame(
    image_bgr: np.ndarray,
    components: PipelineComponents,
    image_name: str,
) -> Dict[str, Any]:
    frame_start = time.time()

    h, w = image_bgr.shape[:2]

    t0 = time.time()
    outputs = run_detection(components.detector, image_bgr)
    detections = extract_detection_dicts(outputs)
    detection_time = time.time() - t0

    t1 = time.time()
    tracked_detections = components.tracker.update(detections)
    tracking_time = time.time() - t1

    per_detection_results: List[Dict[str, Any]] = []

    health_executed_count = 0
    maturity_executed_count = 0
    reused_count = 0
    new_tracks_count = 0

    for det in tracked_detections:
        det_result: Dict[str, Any] = {
            "image_name": image_name,
            "track_id": det["track_id"],
            "is_new_track": det["is_new_track"],
            "track_hits": det["track_hits"],
            "detection_id": det["detection_id"],
            "bbox": det["bbox"],
            "det_score": det["score"],
            "health_executed": False,
            "maturity_executed": False,
            "reused_previous_result": False,
            "health_result": None,
            "maturity_result": None,
            "times": {
                "crop_sec": 0.0,
                "health_sec": 0.0,
                "maturity_sec": 0.0,
                "detection_pipeline_sec": 0.0,
            },
        }

        if det["is_new_track"]:
            new_tracks_count += 1

        track = components.tracker.get_track(det["track_id"])
        if track is None:
            per_detection_results.append(det_result)
            continue

        current_area = max(0, det["bbox"][2] - det["bbox"][0]) * max(0, det["bbox"][3] - det["bbox"][1])
        should_process = det["is_new_track"] or (current_area > track.best_area)

        if not should_process and track.has_been_processed:
            det_result["health_result"] = track.last_health
            det_result["maturity_result"] = track.last_maturity
            det_result["reused_previous_result"] = True
            reused_count += 1
            per_detection_results.append(det_result)
            continue

        crop_start = time.time()
        x1, y1, x2, y2 = det["bbox"]
        x1, y1, x2, y2 = clamp_box_xyxy(x1, y1, x2, y2, w, h)
        x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, w, h)
        crop_bgr = crop_from_box(image_bgr, (x1, y1, x2, y2))
        crop_time = time.time() - crop_start
        det_result["times"]["crop_sec"] = crop_time

        health_result = None
        maturity_result = None

        if is_crop_large_enough(crop_bgr):
            health_start = time.time()
            health_result = predict_health(
                components.health_model,
                components.health_transform,
                crop_bgr,
            )
            det_result["times"]["health_sec"] = time.time() - health_start
            det_result["health_executed"] = True
            det_result["health_result"] = health_result
            health_executed_count += 1

        if should_run_maturity(det, crop_bgr, health_result):
            maturity_start = time.time()
            maturity_payload = estimate_maturity_for_crop(crop_bgr)
            det_result["times"]["maturity_sec"] = time.time() - maturity_start
            det_result["maturity_executed"] = True
            maturity_result = maturity_payload["estimate"]
            det_result["maturity_result"] = maturity_result
            det_result["fruit_mask"] = maturity_payload["fruit_mask"]
            det_result["masked_crop"] = maturity_payload["masked_crop"]
            maturity_executed_count += 1
        else:
            det_result["fruit_mask"] = None
            det_result["masked_crop"] = None

        det_result["times"]["detection_pipeline_sec"] = (
            det_result["times"]["crop_sec"]
            + det_result["times"]["health_sec"]
            + det_result["times"]["maturity_sec"]
        )

        track.last_health = health_result
        track.last_maturity = maturity_result
        track.has_been_processed = True
        if current_area > track.best_area:
            track.best_area = current_area

        per_detection_results.append(det_result)

    total_time = time.time() - frame_start

    return {
        "image_name": image_name,
        "detections_count": len(detections),
        "tracked_count": len(tracked_detections),
        "health_executed_count": health_executed_count,
        "maturity_executed_count": maturity_executed_count,
        "reused_count": reused_count,
        "new_tracks_count": new_tracks_count,
        "times": {
            "detection_sec": detection_time,
            "tracking_sec": tracking_time,
            "total_frame_sec": total_time,
        },
        "detections": per_detection_results,
    }