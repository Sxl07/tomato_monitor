"""Lightweight annotation renderer for snapshot images.

Draws bounding boxes, track IDs, health labels, and maturity stages on a frame.
Extracted from draw_frame_annotations() in video_inspection_runner.py to avoid
importing that module and its heavier dependencies.
"""

from __future__ import annotations

import cv2
import numpy as np


def render_snapshot_annotations(frame_bgr: np.ndarray, frame_result: dict) -> np.ndarray:
    """Draw detection annotations on a snapshot image.

    Args:
        frame_bgr: The raw BGR image.
        frame_result: Dict from process_frame() with "detections" list.

    Returns:
        Annotated image copy with bboxes, IDs, labels drawn.
    """
    drawn = frame_bgr.copy()

    detections = frame_result.get("detections", [])
    healthy_count = 0
    unhealthy_count = 0
    unknown_count = 0

    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        track_id = det["track_id"]
        score = det["det_score"]

        health = det.get("health_result") or {}
        maturity = det.get("maturity_result") or {}

        health_label = health.get("label", "unknown")
        usda_stage = maturity.get("usda_stage", "-")
        reused = det.get("reused_previous_result", False)

        # Count health categories
        if health_label == "healthy":
            healthy_count += 1
        elif health_label == "unhealthy":
            unhealthy_count += 1
        else:
            unknown_count += 1

        # Color coding
        if reused:
            color = (180, 180, 180)  # Gray for reused
        elif det.get("is_new_track", False):
            color = (0, 255, 255)  # Yellow for new tracks
        else:
            color = (255, 0, 255)  # Magenta for existing tracks

        cv2.rectangle(drawn, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)

        # Labels
        reused_tag = " [R]" if reused else ""
        label_1 = f"ID:{track_id} score:{score:.2f}{reused_tag}"
        label_2 = f"H:{health_label} M:{usda_stage}"

        cv2.putText(
            drawn,
            label_1,
            (int(x1), max(20, int(y1) - 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )
        cv2.putText(
            drawn,
            label_2,
            (int(x1), max(40, int(y1) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )

    # Overlay summary text
    total = len(detections)
    reused_count = sum(1 for d in detections if d.get("reused_previous_result", False))
    overlay_1 = (
        f"det={frame_result.get('detections_count', 0)} "
        f"tracked={frame_result.get('tracked_count', 0)} "
        f"new={frame_result.get('new_tracks_count', 0)} "
        f"reused={reused_count}"
    )
    overlay_2 = (
        f"healthy={healthy_count} unhealthy={unhealthy_count} "
        f"unknown={unknown_count} total={total}"
    )
    overlay_3 = (
        f"time={frame_result.get('times', {}).get('total_frame_sec', 0.0):.3f}s"
    )

    cv2.putText(drawn, overlay_1, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.putText(drawn, overlay_2, (15, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.putText(drawn, overlay_3, (15, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

    return drawn
