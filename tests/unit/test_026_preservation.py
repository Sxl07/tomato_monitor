"""SPEC 026 — preservation tests (Property 2: Preservation).

These assert that behavior OUTSIDE the bug condition (¬C(X)) is unchanged by the
fix: F(X) == F'(X). They pass on the UNFIXED code (baseline) and must keep
passing after the fix.

Covered here (no dependency on the new apply_propagated_positions method):
  - Small movement / consecutive detection with a REAL SimpleTracker associates
    to the same track_id under the existing thresholds (IoU 0.30 / 120px).
  - enable_flow_propagation=False at the VideoAnalysisService level: the flow
    tracker is never created and the run completes with the same tracking result.

Structural invariants of apply_propagated_positions itself are POST-method and
live in test_026_tracker_apply_propagated.py.
"""

import numpy as np
import pytest

from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
)
from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderPort,
)
from src.infrastructure.vision.tracker_adapter import SimpleTracker


META = VideoMetadata(fps=5.0, total_frames=0, width=640, height=480)


def _frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(48, 64, 3), dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Small movement / consecutive detection — real SimpleTracker (¬C)
# --------------------------------------------------------------------------- #

class TestSmallMovementAssociation:
    def test_small_shift_keeps_same_track_id_without_propagation(self):
        tracker = SimpleTracker()
        out_a = tracker.update([{"bbox": (100, 100, 140, 140), "score": 0.9}])
        assert out_a[0]["is_new_track"] is True
        id_a = out_a[0]["track_id"]

        # ~30px shift, within center_distance_threshold (120px): same id, no
        # propagation involved.
        out_b = tracker.update([{"bbox": (130, 100, 170, 140), "score": 0.9}])
        assert out_b[0]["is_new_track"] is False
        assert out_b[0]["track_id"] == id_a
        assert len(tracker.tracks) == 1


# --------------------------------------------------------------------------- #
# enable_flow_propagation=False at the service level (¬C)
# --------------------------------------------------------------------------- #

class FakeReader(VideoReaderPort):
    def __init__(self, n_frames):
        self._frames = [_frame(i) for i in range(n_frames)]
        self._i = 0

    def open(self):
        self._i = 0

    def is_available(self):
        return True

    def metadata(self):
        return META

    def read(self):
        idx = self._i
        self._i += 1
        if idx < len(self._frames):
            return True, self._frames[idx]
        return False, None

    def release(self):
        pass


class RealTrackerComponents:
    def __init__(self):
        self.tracker = SimpleTracker()


class RealTrackerFactory:
    def __call__(self):
        return RealTrackerComponents()


class ProcessFrameDriver:
    def __init__(self, detections_per_detector_frame):
        self._script = list(detections_per_detector_frame)
        self._call = 0

    def __call__(self, frame, components, name):
        raw = self._script[self._call] if self._call < len(self._script) else []
        self._call += 1
        tracked = components.tracker.update([dict(d) for d in raw])
        out = [{
            "bbox": t["bbox"], "det_score": t["score"], "track_id": t["track_id"],
            "is_new_track": t["is_new_track"], "track_hits": t["track_hits"],
            "health_result": {"label": "healthy", "confidence": 0.9},
            "maturity_result": None,
        } for t in tracked]
        return {"detections": out}


class FakeSnapshot:
    def __init__(self, id, monitoring_id, image_path, frame_index):
        self.id = id
        self.monitoring_id = monitoring_id
        self.image_path = image_path
        self.frame_index = frame_index
        self.has_detections = False


class FakeSnapshotRepo:
    def __init__(self):
        self._next_id = 1

    def create(self, monitoring_id, snapshot):
        created = FakeSnapshot(self._next_id, monitoring_id, snapshot.image_path,
                               snapshot.frame_index)
        self._next_id += 1
        return created

    def update_has_detections(self, id, has_detections):
        return None


class FakeInspectionRepo:
    def create(self, snapshot_id, result):
        return None


class FakeDbSession:
    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture(autouse=True)
def _no_filesystem(monkeypatch):
    monkeypatch.setattr(VideoAnalysisService, "_save_image",
                        lambda self, relative_path, image: True)
    monkeypatch.setattr(VideoAnalysisService, "_generate_reports",
                        lambda self, result: None)
    monkeypatch.setattr(VideoAnalysisService, "_generate_crops",
                        lambda self, frame, detections, frame_idx: None)
    monkeypatch.setattr(VideoAnalysisService, "_generate_annotated_snapshot",
                        lambda self, frame, frame_result, frame_idx: None)


def _make_service(reader, config, factory, process_frame_fn):
    return VideoAnalysisService(
        monitoring_id=55,
        video_path="outputs/monitorings/55/video/monitoring.mp4",
        snapshot_repo=FakeSnapshotRepo(),
        inspection_result_repo=FakeInspectionRepo(),
        monitoring_repo=object(),
        db_session=FakeDbSession(),
        config=config,
        video_reader=reader,
        components_factory=factory,
        process_frame_fn=process_frame_fn,
    )


class TestFlowDisabledPreserved:
    def test_flow_off_never_creates_flow_tracker_and_completes(self, monkeypatch):
        def _boom(self):
            raise AssertionError("flow tracker must not be created when flow off")

        monkeypatch.setattr(VideoAnalysisService, "_create_flow_tracker", _boom)
        reader = FakeReader(3)
        cfg = VideoAnalysisConfig(
            min_frames_between_detections=1,
            max_frames_without_detection=4,
            use_scene_gate=False,
            enable_flow_propagation=False,
            force_detect_on_first_frame=True,
        )
        # Same tomato detected on both detector frames (0 and 2), small shift.
        pf = ProcessFrameDriver([
            [{"bbox": (100, 100, 140, 140), "score": 0.9}],
            [{"bbox": (110, 100, 150, 140), "score": 0.9}],
        ])
        svc = _make_service(reader, cfg, RealTrackerFactory(), pf)
        result = svc.run()
        assert result.status == "completed"
        # Small shift within thresholds -> single identity even without flow.
        assert result.unique_tracks == 1
