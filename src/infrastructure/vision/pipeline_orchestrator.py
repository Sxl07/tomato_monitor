from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from src.domain.services.deduplication_policy import DeduplicationPolicy
from src.domain.services.inspection_policy import InspectionPolicy
from src.infrastructure.config.settings import RUN_MATURITY_ONLY_FOR_HEALTHY
from src.infrastructure.config.thresholds import MATURITY_MIN_DET_SCORE

from src.infrastructure.vision.cropper import (
    clamp_box_xyxy,
    expand_box,
    crop_from_box,
    is_crop_large_enough,
)
from src.infrastructure.vision.detectron_detector import (
    build_tomato_detector,
    run_detection,
    extract_detection_dicts,
)
from src.infrastructure.vision.resnet_health_classifier import (
    build_health_model_resnet,
    predict_health,
)
from src.infrastructure.vision.maturity_estimator import estimate_maturity_for_crop
from src.infrastructure.vision.tracker_adapter import SimpleTracker


@dataclass
class PipelineComponents:
    detector: Any
    health_model: Any
    health_transform: Any
    tracker: SimpleTracker
    deduplication_policy: DeduplicationPolicy
    inspection_policy: InspectionPolicy


def build_pipeline_components() -> PipelineComponents:
    detector = build_tomato_detector()
    health_model, health_transform = build_health_model_resnet()
    tracker = SimpleTracker()
    deduplication_policy = DeduplicationPolicy()
    inspection_policy = InspectionPolicy()

    return PipelineComponents(
        detector=detector,
        health_model=health_model,
        health_transform=health_transform,
        tracker=tracker,
        deduplication_policy=deduplication_policy,
        inspection_policy=inspection_policy,
    )


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
            "decision_reason": None,
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

        dedup_decision = components.deduplication_policy.should_reuse_result(
            is_new_track=det["is_new_track"],
            has_been_processed=track.has_been_processed,
            current_area=current_area,
            best_area=track.best_area,
        )

        if dedup_decision.should_reuse_previous_result:
            det_result["health_result"] = track.last_health
            det_result["maturity_result"] = track.last_maturity
            det_result["reused_previous_result"] = True
            det_result["decision_reason"] = dedup_decision.reason
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

        crop_is_valid = is_crop_large_enough(crop_bgr)

        initial_decision = components.inspection_policy.decide(
            detection_score=float(det["score"]),
            crop_is_large_enough=crop_is_valid,
            maturity_min_score=MATURITY_MIN_DET_SCORE,
            run_maturity_only_for_healthy=RUN_MATURITY_ONLY_FOR_HEALTHY,
            health_result=None,
        )

        det_result["decision_reason"] = initial_decision.reason

        health_result = None
        maturity_result = None

        if initial_decision.should_run_health:
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

        final_decision = components.inspection_policy.decide(
            detection_score=float(det["score"]),
            crop_is_large_enough=crop_is_valid,
            maturity_min_score=MATURITY_MIN_DET_SCORE,
            run_maturity_only_for_healthy=RUN_MATURITY_ONLY_FOR_HEALTHY,
            health_result=health_result,
        )

        det_result["decision_reason"] = final_decision.reason

        if final_decision.should_run_maturity:
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