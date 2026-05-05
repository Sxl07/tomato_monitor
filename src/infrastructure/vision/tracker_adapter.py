from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import math


@dataclass
class Track:
    track_id: int
    bbox: Tuple[int, int, int, int]
    score: float
    hits: int = 1
    missed: int = 0
    best_area: int = 0
    last_health: Optional[dict] = None
    last_maturity: Optional[dict] = None
    has_been_processed: bool = False

    def update(self, bbox: Tuple[int, int, int, int], score: float) -> None:
        self.bbox = bbox
        self.score = score
        self.hits += 1
        self.missed = 0

        area = bbox_area(bbox)
        if area > self.best_area:
            self.best_area = area


def bbox_area(bbox: Tuple[int, int, int, int]) -> int:
    x1, y1, x2, y2 = bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def center_distance(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    return math.hypot(ax - bx, ay - by)


def iou(box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = bbox_area(box_a)
    area_b = bbox_area(box_b)
    union = area_a + area_b - inter_area

    if union <= 0:
        return 0.0
    return inter_area / union


class SimpleTracker:
    def __init__(
        self,
        iou_threshold: float = 0.30,
        center_distance_threshold: float = 120.0,
        max_missed: int = 3,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.center_distance_threshold = center_distance_threshold
        self.max_missed = max_missed

        self.tracks: Dict[int, Track] = {}
        self.next_track_id = 1

    def update(self, detections: List[dict]) -> List[dict]:
        assigned_track_ids = set()
        output = []

        for det in detections:
            det_bbox = det["bbox"]
            det_score = float(det["score"])

            best_track_id = None
            best_iou = -1.0
            best_dist = float("inf")

            for track_id, track in self.tracks.items():
                if track_id in assigned_track_ids:
                    continue

                overlap = iou(det_bbox, track.bbox)
                dist = center_distance(det_bbox, track.bbox)

                valid_match = (
                    overlap >= self.iou_threshold
                    or dist <= self.center_distance_threshold
                )

                if not valid_match:
                    continue

                if overlap > best_iou or (math.isclose(overlap, best_iou) and dist < best_dist):
                    best_iou = overlap
                    best_dist = dist
                    best_track_id = track_id

            if best_track_id is None:
                track_id = self.next_track_id
                self.next_track_id += 1

                new_track = Track(
                    track_id=track_id,
                    bbox=det_bbox,
                    score=det_score,
                    best_area=bbox_area(det_bbox),
                )
                self.tracks[track_id] = new_track

                det["track_id"] = track_id
                det["is_new_track"] = True
                det["track_hits"] = 1
                assigned_track_ids.add(track_id)
            else:
                track = self.tracks[best_track_id]
                track.update(det_bbox, det_score)

                det["track_id"] = best_track_id
                det["is_new_track"] = False
                det["track_hits"] = track.hits
                assigned_track_ids.add(best_track_id)

            output.append(det)

        to_delete = []
        for track_id, track in self.tracks.items():
            if track_id not in assigned_track_ids:
                track.missed += 1
                if track.missed > self.max_missed:
                    to_delete.append(track_id)

        for track_id in to_delete:
            del self.tracks[track_id]

        return output

    def get_track(self, track_id: int) -> Optional[Track]:
        return self.tracks.get(track_id)