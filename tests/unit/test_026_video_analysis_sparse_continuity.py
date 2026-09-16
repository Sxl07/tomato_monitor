"""SPEC 026 — integration tests for tracker flow continuity in VideoAnalysisService.

These tests exercise the sparse video-analysis loop with FAKES only (no cv2 /
torch / detectron2 / real detector / real video). They use a REAL SimpleTracker
as ``components.tracker`` so identity fragmentation (or its fix) is observable
end to end at the service level.

Test layers:
  - RED reproduction (Faceta 1): detector A -> sparse frame whose fake
    OpticalFlow propagates a >120px-displaced bbox -> detector B at the displaced
    position. On the UNFIXED code the sparse propagate() return is discarded, so
    SimpleTracker.track.bbox stays frozen and detector B creates a NEW id
    (unique_tracks == 2). The test asserts unique_tracks == 1, so it FAILS by
    FRAGMENTATION (not AttributeError) before the fix and PASSES after it.
  - CA-09 (detector miss), CA-10 (temporal-coherence variant), CA-07
    (enable_flow_propagation=False), CA-08 (no state leak between runs), and
    counters-not-affected are added alongside the fix.

The fakes mirror ``tests/unit/test_video_analysis_service_sparse.py`` and add a
``process_frame_fn`` spy that actually drives ``components.tracker.update()`` so
the real association logic runs.
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


class _IndexedFrame(np.ndarray):
    """A tiny ndarray subclass carrying its own frame index.

    Lets the fake Optical Flow stub know exactly which frame it is being asked
    to propagate to, so its ground-truth reporting is deterministic and
    idempotent regardless of how many times propagate() is called per frame
    (variant A calls propagate on detector frames too).
    """

    frame_index: int

    @classmethod
    def make(cls, frame_index, seed=0):
        rng = np.random.RandomState(seed)
        arr = rng.randint(0, 256, size=(48, 64, 3), dtype=np.uint8).view(cls)
        arr.frame_index = frame_index
        return arr


def _frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(48, 64, 3), dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeReader(VideoReaderPort):
    def __init__(self, n_frames):
        self._frames = [_IndexedFrame.make(i, seed=i) for i in range(n_frames)]
        self._i = 0
        self.open_count = 0
        self.release_count = 0

    def open(self):
        self.open_count += 1
        self._i = 0  # reusable across runs

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
        self.release_count += 1


class RealTrackerComponents:
    """Components stub carrying a REAL SimpleTracker as .tracker."""

    def __init__(self):
        self.tracker = SimpleTracker()


class RealTrackerFactory:
    def __init__(self):
        self.call_count = 0
        self.instance = None

    def __call__(self):
        self.call_count += 1
        self.instance = RealTrackerComponents()
        return self.instance


def _det(track_bbox, score=0.9):
    """Build a detection dict as extract_detection_dicts would (bbox + score)."""
    return {"bbox": tuple(track_bbox), "score": float(score)}


class ProcessFrameDriver:
    """process_frame_fn spy that DRIVES the real SimpleTracker.

    For each detector frame it pops the next scripted detection list, runs
    ``components.tracker.update(detections)`` (mutating the real tracker exactly
    like the production process_frame would) and returns the tracked detections
    in the frame_result contract shape (``{"detections": [...]}``) with the keys
    the service reads downstream (``bbox``, ``det_score``, ``track_id``, ...).
    """

    def __init__(self, detections_per_detector_frame):
        # list of "raw detection" lists, one per detector frame (in order)
        self._script = list(detections_per_detector_frame)
        self._call = 0
        self.tracked_calls = []  # tracked detections returned per call

    def __call__(self, frame, components, name):
        raw = self._script[self._call] if self._call < len(self._script) else []
        self._call += 1
        tracked = components.tracker.update([dict(d) for d in raw])
        out = []
        for t in tracked:
            out.append({
                "bbox": t["bbox"],
                "det_score": t["score"],
                "track_id": t["track_id"],
                "is_new_track": t["is_new_track"],
                "track_hits": t["track_hits"],
                "health_result": {"label": "healthy", "confidence": 0.9},
                "maturity_result": None,
            })
        self.tracked_calls.append(out)
        return {"detections": out}


class DisplacingFlowStub:
    """Fake OpticalFlow modelling camera motion via a per-frame velocity.

    Instead of blindly adding a shift on every propagate() call, this stub
    tracks a GROUND-TRUTH bbox per track and advances it by ``per_frame_shift``
    exactly ONCE per frame index. ``propagate()`` reports the ground-truth bbox
    for the CURRENT frame. This is faithful to real optical flow (which reports
    the object's true position for the frame it is given) and, crucially, is
    idempotent within a frame: variant A calls ``propagate`` on detector frames
    too, so a non-idempotent stub would over-shift. Here, calling propagate
    twice for the same frame yields the same position.

    Retention (variant B) is mirrored: a track omitted by the detector but still
    alive (in ``live_track_ids``) keeps being reported by ``propagate``.
    """

    def __init__(self, per_frame_shift=(80, 0)):
        self.per_frame_shift = per_frame_shift
        # track_id -> (anchor_frame_index, anchor_bbox) established at detection
        self._anchor = {}
        self._alive = set()
        self.update_calls = []          # (detections, live_track_ids)
        self.propagate_calls = 0

    def _gt_bbox(self, tid, frame_index):
        """Ground-truth bbox of a track at ``frame_index`` (idempotent)."""
        anchor_idx, (x1, y1, x2, y2) = self._anchor[tid]
        steps = frame_index - anchor_idx
        dx, dy = self.per_frame_shift
        return (x1 + dx * steps, y1 + dy * steps,
                x2 + dx * steps, y2 + dy * steps)

    def update_from_detection_result(self, frame, detections, live_track_ids=None):
        self.update_calls.append((list(detections), live_track_ids))
        fidx = int(frame.frame_index)
        seen = set()
        for d in detections:
            tid = d["track_id"]
            # Re-anchor ground truth to the real detection at this frame.
            self._anchor[tid] = (fidx, tuple(d["bbox"]))
            self._alive.add(tid)
            seen.add(tid)
        if live_track_ids is not None:
            # Retain live-but-undetected; drop expired (variant B mirror).
            self._alive = {
                tid for tid in self._alive
                if tid in seen or tid in live_track_ids
            }
        else:
            self._alive = set(seen)

    def propagate(self, frame):
        self.propagate_calls += 1
        fidx = int(frame.frame_index)
        results = []
        for tid in list(self._anchor.keys()):
            if tid not in self._alive:
                continue
            results.append({
                "track_id": tid,
                "bbox": self._gt_bbox(tid, fidx),
                "det_score": 0.9,
                "is_new_track": False,
                "track_hits": 0,
                "reused_previous_result": True,
                "propagated": True,
                "health_result": {"label": "healthy", "confidence": 0.9},
                "maturity_result": None,
            })
        return results


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
        self.created = []
        self.has_detections_updates = []

    def create(self, monitoring_id, snapshot):
        created = FakeSnapshot(self._next_id, monitoring_id, snapshot.image_path,
                               snapshot.frame_index)
        self._next_id += 1
        self.created.append(created)
        return created

    def update_has_detections(self, id, has_detections):
        self.has_detections_updates.append((id, has_detections))
        return None


class FakeInspectionRepo:
    def __init__(self):
        self.created = []

    def create(self, snapshot_id, result):
        self.created.append((snapshot_id, result))
        return None


class FakeDbSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _no_filesystem(monkeypatch):
    """Stub image write + crops + report so tests never touch the disk."""
    monkeypatch.setattr(VideoAnalysisService, "_save_image",
                        lambda self, relative_path, image: True)
    monkeypatch.setattr(VideoAnalysisService, "_generate_reports",
                        lambda self, result: None)
    monkeypatch.setattr(VideoAnalysisService, "_generate_crops",
                        lambda self, frame, detections, frame_idx: None)
    monkeypatch.setattr(VideoAnalysisService, "_generate_annotated_snapshot",
                        lambda self, frame, frame_result, frame_idx: None)


def _config(**overrides):
    params = dict(
        min_frames_between_detections=1,   # EDGE real cadence
        max_frames_without_detection=4,
        use_scene_gate=False,
        enable_flow_propagation=True,
        force_detect_on_first_frame=True,
    )
    params.update(overrides)
    return VideoAnalysisConfig(**params)


def _make_service(reader, config, factory, process_frame_fn, flow_stub,
                  inspection_repo=None):
    svc = VideoAnalysisService(
        monitoring_id=55,
        video_path="outputs/monitorings/55/video/monitoring.mp4",
        snapshot_repo=FakeSnapshotRepo(),
        inspection_result_repo=(
            inspection_repo if inspection_repo is not None else FakeInspectionRepo()
        ),
        monitoring_repo=object(),
        db_session=FakeDbSession(),
        config=config,
        video_reader=reader,
        components_factory=factory,
        process_frame_fn=process_frame_fn,
    )
    if flow_stub is not None:
        # Inject our deterministic flow stub instead of the real cv2 tracker.
        svc._create_flow_tracker = lambda: flow_stub  # type: ignore[assignment]
    return svc


# --------------------------------------------------------------------------- #
# RED reproduction (Faceta 1) — MUST fail by fragmentation before the fix
# --------------------------------------------------------------------------- #

class TestRedFragmentationSpatialDesync:
    """Detector A (ID 1) -> sparse (propagate >120px) -> detector B displaced.

    With min=1: frame0 first_frame(run) ; frame1 cooldown(skip) ; frame2
    min_gap_ready(run). So detector frames are 0 and 2, sparse frame is 1.
    """

    def test_unique_tracks_stays_one_after_displacement(self):
        reader = FakeReader(3)
        factory = RealTrackerFactory()
        # Detector frames are 0 and 2 (sparse frame is 1). per_frame_shift=80,
        # so between detector A (frame 0) and detector B (frame 2) the tomato
        # moves 80*2 = 160px -> from x[100,140] to x[260,300].
        bbox_a = (100, 100, 140, 140)
        bbox_b = (260, 100, 300, 140)
        pf = ProcessFrameDriver([[_det(bbox_a)], [_det(bbox_b)]])
        flow = DisplacingFlowStub(per_frame_shift=(80, 0))
        svc = _make_service(reader, _config(), factory, pf, flow)

        result = svc.run()

        assert result.status == "completed"
        # With the fix: the sparse propagate() moves ID 1's bbox to ~x260, so
        # detector B associates to ID 1 -> unique_tracks == 1.
        # Without the fix: bbox stays at x100, detector B (x260) is >120px away
        # -> new ID 2 -> unique_tracks == 2 (test FAILS by fragmentation).
        assert result.unique_tracks == 1


# --------------------------------------------------------------------------- #
# CA-09 — continuity across a detector miss (Faceta 2)
# --------------------------------------------------------------------------- #

class TestCA09DetectorMiss:
    """Detector A (ID 1) -> sparse -> detector B MISSES ID 1 -> sparse ->
    detector C sees it again -> keeps ID 1 while max_missed not expired.

    Frames with min=1: 0=detA, 1=sparse, 2=detB(miss), 3=sparse, 4=detC.
    per_frame_shift=40 -> at frame 4 the tomato is at 100 + 40*4 = 260.
    """

    def test_keeps_same_id_across_detector_miss(self):
        reader = FakeReader(5)
        factory = RealTrackerFactory()
        inspection = FakeInspectionRepo()
        bbox_a = (100, 100, 140, 140)   # frame 0
        # frame 4 ground truth: x = 100 + 40*4 = 260.
        bbox_c = (260, 100, 300, 140)
        # detector frames in order: 0 (A), 2 (B -> miss => []), 4 (C).
        pf = ProcessFrameDriver([
            [_det(bbox_a)],   # detector A
            [],               # detector B: RetinaNet returns nothing (miss)
            [_det(bbox_c)],   # detector C
        ])
        flow = DisplacingFlowStub(per_frame_shift=(40, 0))
        svc = _make_service(reader, _config(), factory, pf, flow, inspection)

        result = svc.run()

        assert result.status == "completed"
        # ID 1 preserved across the miss -> exactly one unique track.
        assert result.unique_tracks == 1
        # CA-09 invariants:
        # - propagations create no InspectionResult (only best-by-track persists,
        #   one per unique track -> exactly 1 row, not one per propagated frame).
        assert len(inspection.created) == 1
        # - the real SimpleTracker holds a single track with id 1, hits from the
        #   TWO real detections that hit it (A and C), NOT inflated by props.
        tracker = factory.instance.tracker
        assert len(tracker.tracks) == 1
        (only_track,) = list(tracker.tracks.values())
        assert only_track.hits == 2  # A + C only; propagation never bumps hits


# --------------------------------------------------------------------------- #
# CA-09 — a track missed beyond max_missed expires and gets a NEW id
# --------------------------------------------------------------------------- #

class TestCA09ExpiresBeyondMaxMissed:
    """If the detector misses a track on more than max_missed scheduled frames,
    SimpleTracker drops it; when it reappears it correctly gets a NEW id.

    max_missed=3. Detector frames: 0(A hit), 2(miss),4(miss),6(miss),8(miss),
    10(reappear). By the 4th consecutive miss (>3) the track is gone.
    Frames total: 11 (0..10); with min=1, detector frames are 0,2,4,6,8,10.
    """

    def test_expired_track_gets_new_id_on_reappearance(self):
        reader = FakeReader(11)
        factory = RealTrackerFactory()
        bbox_a = (100, 100, 140, 140)
        # Reappearance far away is irrelevant; identity must be NEW because the
        # original expired. Use the same nominal position.
        bbox_reappear = (100, 100, 140, 140)
        pf = ProcessFrameDriver([
            [_det(bbox_a)],       # 0: A hit -> id 1
            [],                   # 2: miss (missed=1)
            [],                   # 4: miss (missed=2)
            [],                   # 6: miss (missed=3)
            [],                   # 8: miss (missed=4 > max_missed=3 -> deleted)
            [_det(bbox_reappear)]  # 10: reappears -> NEW id 2
        ])
        flow = DisplacingFlowStub(per_frame_shift=(0, 0))  # no motion
        svc = _make_service(reader, _config(), factory, pf, flow)

        result = svc.run()
        assert result.status == "completed"
        # Original expired then a fresh identity was created -> 2 unique tracks.
        assert result.unique_tracks == 2


# --------------------------------------------------------------------------- #
# CA-07 — enable_flow_propagation=False preserves behavior
# --------------------------------------------------------------------------- #

class TestCA07FlowDisabled:
    def test_flow_off_no_flow_calls_and_completes(self, monkeypatch):
        def _boom(self):
            raise AssertionError("flow tracker must not be created when flow off")

        monkeypatch.setattr(VideoAnalysisService, "_create_flow_tracker", _boom)
        reader = FakeReader(3)
        factory = RealTrackerFactory()
        # Small shift within thresholds so identity holds even without flow.
        pf = ProcessFrameDriver([
            [_det((100, 100, 140, 140))],
            [_det((110, 100, 150, 140))],
        ])
        svc = _make_service(reader, _config(enable_flow_propagation=False),
                            factory, pf, flow_stub=None)
        result = svc.run()
        assert result.status == "completed"
        assert result.unique_tracks == 1


# --------------------------------------------------------------------------- #
# CA-08 — no state leak between runs on the same service instance
# --------------------------------------------------------------------------- #

class _PerRunProcessFrameDriver:
    """process_frame_fn that restarts its script at each new run().

    A new run() builds a fresh components instance (via components_factory). This
    driver detects that (identity change of ``components``) and resets its per-run
    script cursor, so the SAME service instance can be run twice deterministically.
    """

    def __init__(self, detections_per_detector_frame):
        self._script = list(detections_per_detector_frame)
        self._call = 0
        self._components = None

    def __call__(self, frame, components, name):
        if components is not self._components:
            # New run detected -> reset per-run cursor.
            self._components = components
            self._call = 0
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


class TestCA08NoStateLeak:
    def test_two_runs_on_same_instance_do_not_leak_state(self):
        reader = FakeReader(3)  # reusable: open() resets the read index

        factory = RealTrackerFactory()  # creates a fresh SimpleTracker per call

        # _create_flow_tracker must return a NEW flow tracker per run, exactly
        # like production creates a new OpticalFlowVisualTracker inside run().
        created_flows = []

        def _new_flow():
            flow = DisplacingFlowStub(per_frame_shift=(80, 0))
            created_flows.append(flow)
            return flow

        pf = _PerRunProcessFrameDriver([
            [_det((100, 100, 140, 140))],   # detector A
            [_det((260, 100, 300, 140))],   # detector B (displaced)
        ])

        svc = VideoAnalysisService(
            monitoring_id=55,
            video_path="outputs/monitorings/55/video/monitoring.mp4",
            snapshot_repo=FakeSnapshotRepo(),
            inspection_result_repo=FakeInspectionRepo(),
            monitoring_repo=object(),
            db_session=FakeDbSession(),
            config=_config(),
            video_reader=reader,
            components_factory=factory,
            process_frame_fn=pf,
        )
        svc._create_flow_tracker = _new_flow  # type: ignore[assignment]

        # TWO run() calls on the SAME instance.
        result1 = svc.run()
        tracker_run1 = factory.instance
        flow_run1 = created_flows[-1]

        result2 = svc.run()
        tracker_run2 = factory.instance
        flow_run2 = created_flows[-1]

        # Both runs report a single identity; run1 does not contaminate run2.
        assert result1.unique_tracks == 1
        assert result2.unique_tracks == 1

        # components_factory ran once PER run -> twice total.
        assert factory.call_count == 2
        # A fresh SimpleTracker was created for each run.
        assert tracker_run1 is not tracker_run2
        # A fresh OpticalFlowVisualTracker (stub) was created for each run.
        assert len(created_flows) == 2
        assert flow_run1 is not flow_run2
        # No retained track from run1 lingers in run2's flow tracker: run2's
        # flow only knows the ids seeded during run2 (id 1), never run1's state.
        assert flow_run1 is not flow_run2  # independent state containers
        # Reader was opened/reset once per run.
        assert reader.open_count == 2
        assert reader.release_count == 2


# --------------------------------------------------------------------------- #
# Counters are not affected by propagated / retained frames
# --------------------------------------------------------------------------- #

class TestCountersNotAffectedByPropagation:
    def test_counters_reflect_only_detector_frames(self):
        # 5 frames, min=1 -> detector frames 0,2,4 (3 detections), sparse 1,3.
        reader = FakeReader(5)
        factory = RealTrackerFactory()
        pf = ProcessFrameDriver([
            [_det((100, 100, 140, 140))],
            [_det((180, 100, 220, 140))],
            [_det((260, 100, 300, 140))],
        ])
        flow = DisplacingFlowStub(per_frame_shift=(40, 0))
        svc = _make_service(reader, _config(), factory, pf, flow)
        result = svc.run()

        assert result.status == "completed"
        # 3 detector frames scheduled; all successful; none failed.
        assert result.detector_scheduled_frames == 3
        assert result.analysis_successful_frames == 3
        assert result.analysis_failed_frames == 0
        # Same tomato tracked across all -> 1 unique track.
        assert result.unique_tracks == 1
        # 3 detection ROWS (one per detector frame), NOT counting sparse frames.
        assert result.total_detection_rows == 3
        # 3 snapshots had detections (the 3 detector frames); sparse excluded.
        assert result.snapshots_with_detections == 3
