from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class VisualTrackState:
    track_id: int
    bbox: Tuple[int, int, int, int]
    points: np.ndarray
    det_score: float
    health_result: Optional[dict]
    maturity_result: Optional[dict]


def clamp_bbox_to_frame(
    bbox: Tuple[int, int, int, int],
    frame_width: int,
    frame_height: int,
) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox

    x1 = max(0, min(int(x1), frame_width - 1))
    y1 = max(0, min(int(y1), frame_height - 1))
    x2 = max(1, min(int(x2), frame_width))
    y2 = max(1, min(int(y2), frame_height))

    if x2 <= x1:
        x2 = min(frame_width, x1 + 1)
    if y2 <= y1:
        y2 = min(frame_height, y1 + 1)

    return x1, y1, x2, y2


class OpticalFlowVisualTracker:
    def __init__(
        self,
        max_corners: int = 20,
        quality_level: float = 0.01,
        min_distance: int = 5,
        min_points_to_keep: int = 4,
    ) -> None:
        self.max_corners = max_corners
        self.quality_level = quality_level
        self.min_distance = min_distance
        self.min_points_to_keep = min_points_to_keep

        self.prev_gray: Optional[np.ndarray] = None
        self.tracks: Dict[int, VisualTrackState] = {}

    def _build_mask_for_bbox(
        self,
        gray: np.ndarray,
        bbox: Tuple[int, int, int, int],
    ) -> np.ndarray:
        mask = np.zeros_like(gray, dtype=np.uint8)
        x1, y1, x2, y2 = bbox
        mask[y1:y2, x1:x2] = 255
        return mask

    def _extract_points_in_bbox(
        self,
        gray: np.ndarray,
        bbox: Tuple[int, int, int, int],
    ) -> np.ndarray:
        mask = self._build_mask_for_bbox(gray, bbox)

        pts = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=self.max_corners,
            qualityLevel=self.quality_level,
            minDistance=self.min_distance,
            mask=mask,
        )

        if pts is not None and len(pts) > 0:
            return pts.astype(np.float32)

        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        return np.array([[[cx, cy]]], dtype=np.float32)

    def update_from_detection_result(
        self,
        frame_bgr: np.ndarray,
        detections: List[dict],
    ) -> None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        new_tracks: Dict[int, VisualTrackState] = {}

        for det in detections:
            bbox = det["bbox"]
            track_id = det["track_id"]

            pts = self._extract_points_in_bbox(gray, bbox)

            new_tracks[track_id] = VisualTrackState(
                track_id=track_id,
                bbox=bbox,
                points=pts,
                det_score=float(det["det_score"]),
                health_result=det.get("health_result"),
                maturity_result=det.get("maturity_result"),
            )

        self.tracks = new_tracks
        self.prev_gray = gray

    def propagate(
        self,
        frame_bgr: np.ndarray,
    ) -> List[dict]:
        if self.prev_gray is None or not self.tracks:
            self.prev_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            return []

        curr_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        frame_h, frame_w = curr_gray.shape[:2]

        propagated_results: List[dict] = []
        updated_tracks: Dict[int, VisualTrackState] = {}

        for track_id, track in self.tracks.items():
            if track.points is None or len(track.points) == 0:
                continue

            next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                self.prev_gray,
                curr_gray,
                track.points,
                None,
            )

            if next_pts is None or status is None:
                continue

            status_flat = status.reshape(-1).astype(bool)

            good_new = next_pts.reshape(-1, 2)[status_flat]
            good_old = track.points.reshape(-1, 2)[status_flat]

            if len(good_new) < self.min_points_to_keep:
                reseeded = self._extract_points_in_bbox(curr_gray, track.bbox)
                if reseeded is None or len(reseeded) == 0:
                    continue

                updated_tracks[track_id] = VisualTrackState(
                    track_id=track.track_id,
                    bbox=track.bbox,
                    points=reseeded,
                    det_score=track.det_score,
                    health_result=track.health_result,
                    maturity_result=track.maturity_result,
                )

                propagated_results.append({
                    "track_id": track.track_id,
                    "bbox": track.bbox,
                    "det_score": track.det_score,
                    "is_new_track": False,
                    "track_hits": 0,
                    "reused_previous_result": True,
                    "propagated": True,
                    "health_result": track.health_result,
                    "maturity_result": track.maturity_result,
                })
                continue

            flow = good_new - good_old

            dx = float(np.median(flow[:, 0]))
            dy = float(np.median(flow[:, 1]))

            x1, y1, x2, y2 = track.bbox
            new_bbox = (
                int(round(x1 + dx)),
                int(round(y1 + dy)),
                int(round(x2 + dx)),
                int(round(y2 + dy)),
            )
            new_bbox = clamp_bbox_to_frame(new_bbox, frame_w, frame_h)

            new_points = good_new.reshape(-1, 1, 2).astype(np.float32)

            updated_tracks[track_id] = VisualTrackState(
                track_id=track.track_id,
                bbox=new_bbox,
                points=new_points,
                det_score=track.det_score,
                health_result=track.health_result,
                maturity_result=track.maturity_result,
            )

            propagated_results.append({
                "track_id": track.track_id,
                "bbox": new_bbox,
                "det_score": track.det_score,
                "is_new_track": False,
                "track_hits": 0,
                "reused_previous_result": True,
                "propagated": True,
                "health_result": track.health_result,
                "maturity_result": track.maturity_result,
            })

        self.tracks = updated_tracks
        self.prev_gray = curr_gray

        return propagated_results