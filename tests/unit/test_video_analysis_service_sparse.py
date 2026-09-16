"""Wave 2 tests for VideoAnalysisService sparse loop (Spec 019, Task 8.2).

Covers cases A-M with fakes only (no cv2/torch/detectron2, no real detector/video):
    A. components_factory called exactly once; same components to every process call.
    B. no second SimpleTracker created; components.tracker untouched.
    C. decide_run_detector called once per frame, with correct args.
    D. gap / last_detection_frame legacy semantics + frame.copy().
    E. process_frame only on detector frames.
    F. flow: update on detector frames, propagate on skip frames, single tracker.
    G. flow disabled: tracker not created, no update/propagate.
    H. scene gate OFF: gate not loaded/called; min_gap_ready reason.
    I. full_detection (and enable_sparse_detection=False): all frames, full_detection.
    J. first frame decision comes from decide_run_detector (reason first_frame).
    K. reason counts == total frames read.
    L. process_frame receives same components + deterministic unique frame names.
    M. repos never used.
"""

import numpy as np
import pytest

import src.application.services.video_analysis_service as svc_mod
from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
)
from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderPort,
)


META = VideoMetadata(fps=30.0, total_frames=0, width=64, height=48)


def _frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(8, 8, 3), dtype=np.uint8)


class FakeReader(VideoReaderPort):
    def __init__(self, n_frames):
        self._frames = [_frame(i) for i in range(n_frames)]
        self._i = 0
        self.released = 0

    def open(self):
        pass

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
        self.released += 1


class ResettableFakeReader(VideoReaderPort):
    """FakeReader whose open() resets the read index (reusable across runs)."""

    def __init__(self, n_frames):
        self._frames = [_frame(i) for i in range(n_frames)]
        self._i = 0
        self.open_count = 0
        self.release_count = 0

    def open(self):
        self.open_count += 1
        self._i = 0  # a reusable reader restarts from the first frame

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


class _SentinelTracker:
    """Stand-in for components.tracker; must remain untouched by the service.

    SPEC 026: the service now feeds Optical Flow predictions back to the tracker
    via ``apply_propagated_positions`` (sparse frames, and detector frames under
    variant A) and reads ``tracks`` to compute live_track_ids. This inert stub
    accepts those interactions without maintaining real state.
    """

    tracks: dict = {}

    def apply_propagated_positions(self, propagated):
        # No-op: this sentinel holds no real tracks.
        return None


class FakeComponents:
    def __init__(self):
        self.tracker = _SentinelTracker()


class ComponentsFactory:
    def __init__(self):
        self.call_count = 0
        self.instance = None

    def __call__(self):
        self.call_count += 1
        self.instance = FakeComponents()
        return self.instance


class ProcessFrameSpy:
    """Records (frame, components, name) and returns configurable detections."""

    def __init__(self, detections_by_call=None):
        self.calls = []
        self._detections_by_call = detections_by_call or {}

    def __call__(self, frame, components, name):
        idx = len(self.calls)
        self.calls.append({"frame": frame, "components": components, "name": name})
        dets = self._detections_by_call.get(idx, [])
        return {"detections": dets}


class FlowSpy:
    def __init__(self):
        self.updates = []
        self.propagations = []

    def update_from_detection_result(self, frame, detections, live_track_ids=None):
        # SPEC 026: accept the optional live_track_ids arg (variant B).
        self.updates.append({
            "frame": frame,
            "detections": detections,
            "live_track_ids": live_track_ids,
        })

    def propagate(self, frame):
        self.propagations.append(frame)
        return []


class DecideSpy:
    """Wraps the real decide_run_detector, recording call kwargs + decisions."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        from src.infrastructure.vision.detector_decision import decide_run_detector

        decision = decide_run_detector(**kwargs)
        self.calls.append({"kwargs": kwargs, "decision": decision})
        return decision


class _FakeSnapshot:
    def __init__(self, id, monitoring_id, image_path, frame_index):
        self.id = id
        self.monitoring_id = monitoring_id
        self.image_path = image_path
        self.frame_index = frame_index
        self.has_detections = False


class FakeSnapshotRepo:
    """Records created snapshots + has_detections updates (Task 8.3)."""

    def __init__(self):
        self._next_id = 1
        self.created = []
        self.has_detections_updates = []

    def create(self, monitoring_id, snapshot):
        created = _FakeSnapshot(self._next_id, monitoring_id, snapshot.image_path,
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
    """Stub image write + crops + report so sparse tests never touch the disk."""
    monkeypatch.setattr(
        VideoAnalysisService, "_save_image",
        lambda self, relative_path, image: True,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_reports",
        lambda self, result: None,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_crops",
        lambda self, frame, detections, frame_idx: None,
    )


def _config(**overrides):
    params = dict(
        min_frames_between_detections=3,
        max_frames_without_detection=8,
        use_scene_gate=False,
        enable_flow_propagation=False,
        force_detect_on_first_frame=True,
    )
    params.update(overrides)
    return VideoAnalysisConfig(**params)


def _make_service(reader, config, *, components_factory, process_frame_fn,
                  snapshot_repo=None, inspection_repo=None, db_session=None):
    return VideoAnalysisService(
        monitoring_id=7,
        video_path="outputs/monitorings/7/video/monitoring.mp4",
        snapshot_repo=snapshot_repo if snapshot_repo is not None else FakeSnapshotRepo(),
        inspection_result_repo=(
            inspection_repo if inspection_repo is not None else FakeInspectionRepo()
        ),
        monitoring_repo=object(),
        db_session=db_session if db_session is not None else FakeDbSession(),
        config=config,
        video_reader=reader,
        components_factory=components_factory,
        process_frame_fn=process_frame_fn,
    )


# --------------------------------------------------------------------------- #
# A. components_factory exactly once, same components
# --------------------------------------------------------------------------- #

class TestComponentsOnce:
    def test_factory_called_once_and_same_components(self):
        reader = FakeReader(10)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        svc.run()
        assert factory.call_count == 1
        # Every process_frame call received the SAME components instance.
        assert pf.calls  # detector ran at least once
        for call in pf.calls:
            assert call["components"] is factory.instance


# --------------------------------------------------------------------------- #
# B. no second SimpleTracker
# --------------------------------------------------------------------------- #

class TestNoSecondTracker:
    def test_components_tracker_untouched(self):
        reader = FakeReader(6)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        svc.run()
        original_tracker = factory.instance.tracker
        assert isinstance(original_tracker, _SentinelTracker)
        # The service never replaced components.tracker.
        assert factory.instance.tracker is original_tracker

    def test_service_source_does_not_import_simpletracker(self):
        import ast
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src" / "application" / "services" / "video_analysis_service.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        assert "SimpleTracker" not in imported
        assert "SimpleTracker" not in called


# --------------------------------------------------------------------------- #
# C. decide_run_detector once per frame with correct args
# --------------------------------------------------------------------------- #

class TestDecisionPerFrame:
    def test_called_once_per_frame_with_args(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)

        reader = FakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config()
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        svc.run()

        assert len(spy.calls) == 5
        for idx, call in enumerate(spy.calls):
            kw = call["kwargs"]
            assert kw["frame_idx"] == idx
            assert kw["enable_sparse_detection"] is True  # sparse on, full off
            assert kw["use_scene_gate"] is False
            assert kw["min_frames_between_detections"] == 3
            assert kw["max_frames_without_detection"] == 8
            assert kw["force_detect_on_first_frame"] is True
            assert callable(kw["scene_gate_fn"])
            assert kw["current_frame"] is not None
            assert isinstance(kw["frames_since_last_detection"], int)


# --------------------------------------------------------------------------- #
# D. gap / last_detection_frame semantics
# --------------------------------------------------------------------------- #

class TestGapSemantics:
    def test_gap_sequence_and_reference_copy(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)

        # min=3,max=8: frame0 first_frame(run,reset0); f1 gap0 cooldown; f2 gap1;
        # f3 gap2; f4 gap3 -> min_gap_ready(run). Sequence of gaps seen: 0,0,1,2,3.
        reader = FakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        svc.run()

        gaps = [c["kwargs"]["frames_since_last_detection"] for c in spy.calls]
        assert gaps == [0, 0, 1, 2, 3]

        # frame 0 has no reference yet; after the frame-0 run a reference exists.
        assert spy.calls[0]["kwargs"]["last_detection_frame"] is None
        assert spy.calls[1]["kwargs"]["last_detection_frame"] is not None

        # The stored reference is a COPY of the frame-0 frame, not the same object.
        frame0 = reader._frames[0]
        ref = spy.calls[1]["kwargs"]["last_detection_frame"]
        assert ref is not frame0
        assert np.array_equal(ref, frame0)


# --------------------------------------------------------------------------- #
# E. process_frame only on detector frames
# --------------------------------------------------------------------------- #

class TestProcessOnlyOnRun:
    def test_process_called_only_for_run_frames(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)

        # min=3,max=8, gate off, force first: f0 run(reset0); f1 gap0; f2 gap1;
        # f3 gap2; f4 gap3>=min -> min_gap_ready run. Runs at indices 0 and 4.
        reader = FakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        svc.run()

        run_indices = [i for i, c in enumerate(spy.calls) if c["decision"].run_detector]
        assert run_indices == [0, 4]
        assert pf.calls and len(pf.calls) == 2
        # Names correspond to detector frame indices 0 and 4.
        assert pf.calls[0]["name"].endswith("frame_000000")
        assert pf.calls[1]["name"].endswith("frame_000004")


# --------------------------------------------------------------------------- #
# F. flow update vs propagate
# --------------------------------------------------------------------------- #

class TestFlow:
    def test_update_on_run_propagate_on_skip_single_tracker(self, monkeypatch):
        flow = FlowSpy()
        monkeypatch.setattr(
            VideoAnalysisService, "_create_flow_tracker", lambda self: flow
        )
        # min=2: f0 run(reset0); f1 gap0 skip; f2 gap1 skip; f3 gap2>=2 run.
        # -> detector frames 0,3 ; skip frames 1,2.
        reader = FakeReader(4)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config(min_frames_between_detections=2, enable_flow_propagation=True)
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        svc.run()

        # update_from_detection_result on detector frames (0, 3) -> 2.
        assert len(flow.updates) == 2
        # SPEC 026 (variant A): propagate now fires on EVERY detector frame
        # (before detection, to keep the visual state coherent) AND on every
        # skip frame. Detector frames 0,3 + skip frames 1,2 -> 4 propagate calls.
        assert len(flow.propagations) == 4
        # single tracker reused: FlowSpy instance is the same for all calls (by construction).


# --------------------------------------------------------------------------- #
# G. flow disabled
# --------------------------------------------------------------------------- #

class TestFlowDisabled:
    def test_tracker_not_created_when_flow_off(self, monkeypatch):
        created = {"count": 0}

        def _boom(self):
            created["count"] += 1
            raise AssertionError("flow tracker must not be created when flow is off")

        monkeypatch.setattr(VideoAnalysisService, "_create_flow_tracker", _boom)
        reader = FakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config(enable_flow_propagation=False)
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        result = svc.run()

        assert created["count"] == 0
        assert result.status == "completed"
        # sparse decisions still ran (process_frame on detector frames).
        assert len(pf.calls) >= 1


# --------------------------------------------------------------------------- #
# H. scene gate OFF
# --------------------------------------------------------------------------- #

class TestSceneGateOff:
    def test_gate_not_loaded_or_called(self, monkeypatch):
        def _boom(self):
            raise AssertionError("scene gate must not be resolved when use_scene_gate is False")

        monkeypatch.setattr(VideoAnalysisService, "_resolve_scene_gate_fn", _boom)
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)

        reader = FakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config(use_scene_gate=False)
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        svc.run()

        # frame 4 reaches min (gap 3 >= 3) with gate off -> min_gap_ready.
        reasons = [c["decision"].reason for c in spy.calls]
        assert "min_gap_ready" in reasons


# --------------------------------------------------------------------------- #
# I. full_detection
# --------------------------------------------------------------------------- #

class TestFullDetection:
    def test_full_detection_all_frames(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)
        flow = FlowSpy()
        monkeypatch.setattr(VideoAnalysisService, "_create_flow_tracker", lambda self: flow)

        reader = FakeReader(4)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config(full_detection=True, enable_flow_propagation=True)
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        svc.run()

        assert all(c["decision"].reason == "full_detection" for c in spy.calls)
        assert len(pf.calls) == 4  # every frame processed
        # SPEC 026 (variant A): in full detection every frame is a detector
        # frame, and propagate now runs on each detector frame BEFORE detection
        # to keep the visual state temporally coherent -> 4 propagate calls.
        # (update_from_detection_result also runs on every frame -> 4 updates.)
        assert len(flow.propagations) == 4
        assert len(flow.updates) == 4

    def test_enable_sparse_false_also_full_detection(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)
        reader = FakeReader(3)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        cfg = _config(enable_sparse_detection=False)
        svc = _make_service(reader, cfg, components_factory=factory, process_frame_fn=pf)
        svc.run()
        assert all(c["kwargs"]["enable_sparse_detection"] is False for c in spy.calls)
        assert all(c["decision"].reason == "full_detection" for c in spy.calls)
        assert len(pf.calls) == 3


# --------------------------------------------------------------------------- #
# J. first frame
# --------------------------------------------------------------------------- #

class TestFirstFrame:
    def test_first_frame_reason_from_decision(self, monkeypatch):
        spy = DecideSpy()
        monkeypatch.setattr(svc_mod, "decide_run_detector", spy)
        reader = FakeReader(2)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(force_detect_on_first_frame=True),
                            components_factory=factory, process_frame_fn=pf)
        svc.run()
        assert spy.calls[0]["decision"].reason == "first_frame"
        assert spy.calls[0]["decision"].run_detector is True
        assert pf.calls[0]["name"].endswith("frame_000000")


# --------------------------------------------------------------------------- #
# K. reason counts
# --------------------------------------------------------------------------- #

class TestReasonCounts:
    def test_counts_sum_to_total_frames(self):
        reader = FakeReader(7)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        result = svc.run()
        counts = svc.detector_reason_counts
        assert sum(counts.values()) == result.total_frames_read == 7

    def test_reason_counts_reset_between_runs_on_same_instance(self):
        # Reusable reader: open() restarts the read index so run() can repeat.
        reader = ResettableFakeReader(5)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)

        # First run.
        result1 = svc.run()
        assert result1.total_frames_read == 5
        counts1 = dict(svc.detector_reason_counts)
        assert sum(counts1.values()) == 5

        # Second run on the SAME instance: counts must NOT accumulate (not 10).
        result2 = svc.run()
        assert result2.total_frames_read == 5
        counts2 = dict(svc.detector_reason_counts)
        assert sum(counts2.values()) == 5  # per-run counter, not 10

        # The second run produced its own counts (same distribution here).
        assert counts2 == counts1

        # components_factory runs once PER run() -> twice across two runs.
        assert factory.call_count == 2
        # Reader reused: opened and released once per run.
        assert reader.open_count == 2
        assert reader.release_count == 2


# --------------------------------------------------------------------------- #
# L. process_frame same components + deterministic unique names
# --------------------------------------------------------------------------- #

class TestProcessFrameArgs:
    def test_same_components_and_unique_names(self):
        reader = FakeReader(12)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        svc.run()

        names = [c["name"] for c in pf.calls]
        assert len(names) == len(set(names))  # unique
        for c in pf.calls:
            assert c["components"] is factory.instance
        # Reproducible pattern includes monitoring id and zero-padded index.
        assert all(n.startswith("monitoring_7_frame_") for n in names)


# --------------------------------------------------------------------------- #
# M. sparse loop completes with working repos (Task 8.3 persistence enabled)
# --------------------------------------------------------------------------- #

class TestSparseLoopCompletes:
    def test_completes_with_working_repos(self):
        # Task 8.3 persists raw snapshots on detector frames; the sparse loop
        # must still complete end to end. Detailed persistence assertions live in
        # test_video_analysis_service_persistence.py.
        reader = FakeReader(8)
        factory = ComponentsFactory()
        pf = ProcessFrameSpy()
        svc = _make_service(reader, _config(), components_factory=factory, process_frame_fn=pf)
        result = svc.run()
        assert result.status == "completed"
