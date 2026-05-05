from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import detectron2
import numpy as np
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor

from config.settings import DETECTION_MODEL_PATH, DEVICE
from config.thresholds import DETECTION_SCORE_THRESHOLD


def build_tomato_detector(model_weights: Path | None = None) -> DefaultPredictor:
    weights_path = Path(model_weights) if model_weights else DETECTION_MODEL_PATH

    cfg = get_cfg()
    cfg_path = os.path.join(
        os.path.dirname(detectron2.__file__),
        "model_zoo",
        "configs",
        "COCO-Detection",
        "retinanet_R_50_FPN_1x.yaml",
    )
    cfg.merge_from_file(cfg_path)

    cfg.MODEL.RETINANET.NUM_CLASSES = 1
    cfg.MODEL.WEIGHTS = str(weights_path)
    cfg.MODEL.RETINANET.SCORE_THRESH_TEST = DETECTION_SCORE_THRESHOLD
    cfg.MODEL.DEVICE = DEVICE

    return DefaultPredictor(cfg)


def run_detection(predictor: DefaultPredictor, image_bgr: np.ndarray) -> Dict[str, Any]:
    return predictor(image_bgr)


def extract_detection_dicts(outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    instances = outputs["instances"].to("cpu")

    if len(instances) == 0 or not instances.has("pred_boxes"):
        return []

    boxes = instances.pred_boxes.tensor.numpy()
    scores = instances.scores.numpy() if instances.has("scores") else np.ones(len(boxes))
    classes = instances.pred_classes.numpy() if instances.has("pred_classes") else np.zeros(len(boxes))

    detections: List[Dict[str, Any]] = []

    for idx, (box, score, cls_id) in enumerate(zip(boxes, scores, classes), start=1):
        x1, y1, x2, y2 = [int(v) for v in box.tolist()]

        detections.append(
            {
                "detection_id": idx,
                "bbox": (x1, y1, x2, y2),
                "score": float(score),
                "class_id": int(cls_id),
            }
        )

    return detections