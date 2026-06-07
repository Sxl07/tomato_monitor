"""Runs the full inference pipeline on a single snapshot image.

This module provides a class-based facade over the existing functional vision
components (detector, health classifier, maturity estimator, cropper). It is
designed for the monitoring execution flow where models are loaded once and
reused across all snapshots in a session.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import cv2
import numpy as np

from src.infrastructure.config.thresholds import (
    MATURITY_MIN_DET_SCORE,
)
from src.infrastructure.vision.cropper import (
    clamp_box_xyxy,
    crop_from_box,
    expand_box,
    is_crop_large_enough,
)
from src.infrastructure.vision.detectron_detector import (
    extract_detection_dicts,
    run_detection,
)
from src.infrastructure.vision.maturity_estimator import estimate_maturity_for_crop
from src.infrastructure.vision.resnet_health_classifier import predict_health

logger = logging.getLogger(__name__)


class SnapshotInferenceRunner:
    """Executes detector + health classifier + maturity estimator on a single image.

    Models are injected via constructor and reused across snapshots.
    Handles per-detection failures gracefully (skip and continue).
    """

    def __init__(
        self,
        detector: Any,
        health_model: Any,
        health_transform: Any,
        *,
        skip_maturity: bool = False,
        detection_score_threshold: float = 0.80,
        inference_input_size: tuple[int, int] | None = None,
        run_maturity_only_for_healthy: bool = True,
    ) -> None:
        """Initialize with pre-loaded model components.

        Args:
            detector: Detectron2 DefaultPredictor instance (from build_tomato_detector).
            health_model: PyTorch ResNet-18 model (from build_health_model_resnet).
            health_transform: torchvision transforms for health model input.
            skip_maturity: If True, skip maturity estimation entirely.
            detection_score_threshold: Minimum detection confidence to process
                a detection (default 0.80, matching original behavior).
            inference_input_size: Optional (width, height) to resize input before
                detection. None means no resize (use original frame size).
            run_maturity_only_for_healthy: If True, only run maturity estimation
                on detections classified as healthy.
        """
        self._detector = detector
        self._health_model = health_model
        self._health_transform = health_transform
        self._skip_maturity = skip_maturity
        self._detection_score_threshold = detection_score_threshold
        self._inference_input_size = inference_input_size
        self._run_maturity_only_for_healthy = run_maturity_only_for_healthy

    def run_inference(self, image: np.ndarray) -> list[dict[str, Any]]:
        """Run full inference pipeline on a single image.

        Pipeline: detect → crop → health classify → maturity estimate.

        Args:
            image: BGR image as numpy array (from camera or file).

        Returns:
            List of detection results, each containing:
            - bbox_x1, bbox_y1, bbox_x2, bbox_y2: bounding box coordinates
            - detection_score: float
            - health_label: "healthy" or "unhealthy"
            - health_confidence: float
            - maturity_stage: str or None (USDA stage)
            - maturity_percent: float or None
        """
        results: list[dict[str, Any]] = []

        # Resize for inference if configured (preserves original for cropping)
        inference_frame = image
        if self._inference_input_size is not None:
            ih, iw = image.shape[:2]
            tw, th = self._inference_input_size
            if (iw, ih) != (tw, th):
                inference_frame = cv2.resize(
                    image, (tw, th), interpolation=cv2.INTER_LINEAR
                )

        # Run detection on the (possibly resized) inference frame
        try:
            outputs = run_detection(self._detector, inference_frame)
            detections = extract_detection_dicts(outputs)
        except Exception as e:
            logger.error(f"Detector failed on snapshot: {e}")
            return results

        if not detections:
            return results

        h, w = image.shape[:2]

        # Scale bounding boxes back to original resolution if resized
        if self._inference_input_size is not None and inference_frame is not image:
            scale_x = w / inference_frame.shape[1]
            scale_y = h / inference_frame.shape[0]
            for det in detections:
                bbox = det["bbox"]
                det["bbox"] = [
                    bbox[0] * scale_x,
                    bbox[1] * scale_y,
                    bbox[2] * scale_x,
                    bbox[3] * scale_y,
                ]

        for detection in detections:
            score = float(detection["score"])

            # Apply detection confidence threshold
            if score < self._detection_score_threshold:
                continue

            try:
                result = self._process_single_detection(image, detection, w, h)
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning(
                    f"Failed to process detection {detection.get('detection_id')}: {e}"
                )
                continue

        return results

    def _process_single_detection(
        self,
        image: np.ndarray,
        detection: dict[str, Any],
        image_width: int,
        image_height: int,
    ) -> Optional[dict[str, Any]]:
        """Process a single detection: crop → health → maturity.

        Args:
            image: Full BGR image.
            detection: Detection dict with bbox, score, detection_id.
            image_width: Image width in pixels.
            image_height: Image height in pixels.

        Returns:
            Result dict or None if crop is invalid.
        """
        x1, y1, x2, y2 = detection["bbox"]
        score = float(detection["score"])

        # Clamp and expand bounding box (same logic as pipeline_orchestrator)
        x1, y1, x2, y2 = clamp_box_xyxy(x1, y1, x2, y2, image_width, image_height)
        x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, image_width, image_height)

        # Crop the detection region
        crop = crop_from_box(image, (x1, y1, x2, y2))

        if not is_crop_large_enough(crop):
            return None

        # Health classification
        health_result = predict_health(
            self._health_model,
            self._health_transform,
            crop,
        )
        health_label = health_result["label"]
        health_confidence = float(health_result["confidence"])

        # Maturity estimation (only for healthy detections with sufficient score)
        maturity_stage: Optional[str] = None
        maturity_percent: Optional[float] = None

        should_run_maturity = (
            not self._skip_maturity
            and (not self._run_maturity_only_for_healthy or health_label == "healthy")
            and score >= MATURITY_MIN_DET_SCORE
        )

        if should_run_maturity:
            try:
                maturity_payload = estimate_maturity_for_crop(crop)
                estimate = maturity_payload["estimate"]
                maturity_stage = estimate["usda_stage"]
                maturity_percent = float(estimate["maturity_percent"])
            except Exception as e:
                logger.warning(f"Maturity estimation failed: {e}")

        return {
            "bbox_x1": x1,
            "bbox_y1": y1,
            "bbox_x2": x2,
            "bbox_y2": y2,
            "detection_score": score,
            "health_label": health_label,
            "health_confidence": health_confidence,
            "maturity_stage": maturity_stage,
            "maturity_percent": maturity_percent,
        }
