"""Task 8.3 tests: derived persistence + per-frame fault tolerance.

Covers cases 1-14 with fakes only (no cv2/torch/detectron2, no real video):
    1.  order raw JPEG -> Snapshot DB -> commit -> process_frame.
    2.  snapshots created == detector_scheduled_frames.
    3.  snapshot path + frame_index correct.
    4.  process_frame fails on some frames -> snapshot kept, video continues.
    5.  invariant successful + failed == scheduled.
    6.  raw save OR snapshot commit failure -> fatal (status="error").
    7.  has_detections true/false correct.
    8.  crops ignore reused and track_id=None.
    9.  crop failure is recoverable.
    10. best-by-track selects largest bbox.
    11. failed frame does not modify best-by-track.
    12. <=1 DetectionInspectionResult per track.
    13. 8.3 state resets between two run() on the same instance.
    14. fresh-import without heavy backends still passes.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import src.application.services.video_analysis_service as svc_mod
from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
    _FatalPersistenceError,
)
from src.application.interfaces.video_reader_port import VideoMetadata, VideoReaderPort


META = VideoMetadata(fps=30.0, total_frames=0, width=64, height=48)


def _frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(16, 16, 3), dtype=np.uint8)


class FakeReader(VideoReaderPort):
    def __init__(self, n):
        self._frames = [_frame(i) for i in range(n)]
        self._i = 0
        self.released = 0

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
        self.released += 1


class _Snap:
    def __init__(self, id, monitoring_id, image_path, frame_index):
        self.id = id
        self.monitoring_id = monitoring_id
        self.image_path = image_path
        self.frame_index = frame_index
        self.has_detections = False


class SnapshotRepoSpy:
    def __init__(self, create_raises_at=None):
        self._next_id = 1
        self.created = []
        self.has_detections_updates = []
        self._create_raises_at = create_raises_at  # frame_index that raises

    def create(self, monitoring_id, snapshot):
        if self._create_raises_at is not None and snapshot.frame_index == self._create_raises_at:
            raise RuntimeError("simulated snapshot create failure")
        snap = _Snap(self._next_id, monitoring_id, snapshot.image_path, snapshot.frame_index)
        self._next_id += 1
        self.created.append(snap)
        return snap

    def update_has_detections(self, id, has_detections):
        self.has_detections_updates.append((id, has_detections))


class InspectionRepoSpy:
    def __init__(self):
        self.created = []

    def create(self, snapshot_id, result):
        self.created.append((snapshot_id, result))


class DbSpy:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.events = []

    def commit(self):
        self.commits += 1
        self.events.append("commit")

    def rollback(self):
        self.rollbacks += 1
        self.events.append("rollback")


def _config(**overrides):
    params = dict(
        min_frames_between_detections=1,
        max_frames_without_detection=2,
        use_scene_gate=False,
        enable_flow_propagation=False,
        force_detect_on_first_frame=True,
    )
    params.update(overrides)
    return VideoAnalysisConfig(**params)


def _det(track_id, bbox, *, reused=False, health="healthy", stage="red"):
    return {
        "track_id": track_id,
        "bbox": bbox,
        "det_score": 0.9,
        "reused_previous_result": reused,
        "health_result": {"label": health, "confidence": 0.8},
        "maturity_result": {"usda_stage": stage, "maturity_percent": 90.0},
    }


def _make_service(reader, config, *, process_frame_fn, snapshot_repo, inspection_repo,
                  db_session, stub_crops=True, monkeypatch=None):
    if stub_crops and monkeypatch is not None:
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )
    return VideoAnalysisService(
        monitoring_id=42,
        video_path="outputs/monitorings/42/video/monitoring.mp4",
        snapshot_repo=snapshot_repo,
        inspection_result_repo=inspection_repo,
        monitoring_repo=object(),
        db_session=db_session,
        config=config,
        video_reader=reader,
        components_factory=lambda: object(),
        process_frame_fn=process_frame_fn,
    )


@pytest.fixture(autouse=True)
def _stub_save_image(monkeypatch):
    """Stub image write + report generation so tests never touch the disk."""
    monkeypatch.setattr(
        VideoAnalysisService, "_save_image",
        lambda self, relative_path, image: True,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_reports",
        lambda self, result: None,
    )


# --------------------------------------------------------------------------- #
# 1. order: raw JPEG -> Snapshot DB -> commit -> process_frame
# --------------------------------------------------------------------------- #

class TestPersistOrder:
    def test_snapshot_committed_before_process_frame(self, monkeypatch):
        order = []

        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: order.append("imwrite") or True,
        )

        class _SnapRepo(SnapshotRepoSpy):
            def create(self, monitoring_id, snapshot):
                order.append("snapshot_create")
                return super().create(monitoring_id, snapshot)

        class _Db(DbSpy):
            def commit(self):
                order.append("commit")
                super().commit()

        def pf(frame, components, name):
            order.append("process_frame")
            return {"detections": []}

        reader = FakeReader(1)  # frame 0 forced -> one detector frame
        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=_SnapRepo(), inspection_repo=InspectionRepoSpy(),
            db_session=_Db(), monkeypatch=monkeypatch,
        )
        svc.run()

        # For the single detector frame: imwrite -> snapshot_create -> commit ->
        # process_frame (before any later commit).
        i_imwrite = order.index("imwrite")
        i_create = order.index("snapshot_create")
        i_commit = order.index("commit")
        i_process = order.index("process_frame")
        assert i_imwrite < i_create < i_commit < i_process


# --------------------------------------------------------------------------- #
# 2 / 3. snapshots == scheduled; path + frame_index correct
# --------------------------------------------------------------------------- #

class TestSnapshotsMatchScheduled:
    def test_snapshots_equal_scheduled_and_paths(self, monkeypatch):
        # min=1,max=2, force first: f0 run; f1 gap0<1? gap after reset=0 -> f1 sees0 <1 cooldown;
        # f2 gap1>=1 -> run; f3 gap0 cooldown; f4 gap1 run. Detector frames: 0,2,4.
        reader = FakeReader(5)
        snap_repo = SnapshotRepoSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=lambda f, c, n: {"detections": []},
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()

        assert svc.detector_scheduled_frames == len(snap_repo.created)
        # frame indexes match scheduled frames.
        created_indexes = [s.frame_index for s in snap_repo.created]
        assert created_indexes == [0, 2, 4]
        # Paths are relative + correctly zero-padded.
        for s in snap_repo.created:
            assert s.image_path == (
                f"outputs/monitorings/42/snapshots/raw/snapshot_{s.frame_index:06d}.jpg"
            )
            assert not s.image_path.startswith("/")


# --------------------------------------------------------------------------- #
# 4 / 5. per-frame fault tolerance + invariant
# --------------------------------------------------------------------------- #

class TestFaultTolerance:
    def test_process_failures_keep_snapshot_and_continue(self, monkeypatch):
        reader = FakeReader(5)
        snap_repo = SnapshotRepoSpy()

        call = {"n": 0}

        def pf(frame, components, name):
            call["n"] += 1
            # Fail on the 2nd detector call.
            if call["n"] == 2:
                raise RuntimeError("simulated RetinaNet failure")
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        result = svc.run()

        assert result.status == "completed"  # not aborted by a per-frame failure
        # All scheduled frames still produced a raw snapshot.
        assert len(snap_repo.created) == svc.detector_scheduled_frames
        assert svc.analysis_failed_frames == 1
        assert svc.analysis_successful_frames == svc.detector_scheduled_frames - 1

    def test_invariant_successful_plus_failed_equals_scheduled(self, monkeypatch):
        reader = FakeReader(9)

        def pf(frame, components, name):
            # Fail on frames whose name ends with an even hundreds digit — just
            # fail deterministically on some.
            if name.endswith("000002") or name.endswith("000006"):
                raise RuntimeError("boom")
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        result = svc.run()
        assert result.status == "completed"
        assert (
            svc.analysis_successful_frames + svc.analysis_failed_frames
            == svc.detector_scheduled_frames
        )


# --------------------------------------------------------------------------- #
# 6. fatal persistence
# --------------------------------------------------------------------------- #

class TestFatalPersistence:
    def test_raw_save_failure_is_fatal(self, monkeypatch):
        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: False,
        )
        reader = FakeReader(3)
        db = DbSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=lambda f, c, n: {"detections": []},
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=db, monkeypatch=monkeypatch,
        )
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert db.rollbacks >= 1

    def test_snapshot_commit_failure_is_fatal(self, monkeypatch):
        reader = FakeReader(3)
        # snapshot create raises on the first detector frame (index 0).
        snap_repo = SnapshotRepoSpy(create_raises_at=0)
        db = DbSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=lambda f, c, n: {"detections": []},
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=db, monkeypatch=monkeypatch,
        )
        result = svc.run()
        assert result.status == "error"
        assert db.rollbacks >= 1


# --------------------------------------------------------------------------- #
# 7. has_detections
# --------------------------------------------------------------------------- #

class TestHasDetections:
    def test_true_when_detections_false_otherwise(self, monkeypatch):
        reader = FakeReader(3)
        snap_repo = SnapshotRepoSpy()

        def pf(frame, components, name):
            # First detector frame has a detection; others don't.
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()

        updates = dict(snap_repo.has_detections_updates)
        # snapshot id 1 = frame 0 (has detection True); others False.
        assert updates[1] is True
        assert all(v is False for k, v in updates.items() if k != 1)

    def test_has_detections_stays_false_on_process_failure(self, monkeypatch):
        reader = FakeReader(1)
        snap_repo = SnapshotRepoSpy()

        def pf(frame, components, name):
            raise RuntimeError("fail before has_detections update")

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        # process_frame failed -> update_has_detections never called; snapshot
        # was created with has_detections=False.
        assert snap_repo.has_detections_updates == []
        assert snap_repo.created[0].has_detections is False


# --------------------------------------------------------------------------- #
# 8 / 9. crops
# --------------------------------------------------------------------------- #

class TestCrops:
    def test_crops_ignore_reused_and_none_track(self, monkeypatch):
        written = []
        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: written.append(relative_path) or True,
        )
        reader = FakeReader(1)

        def pf(frame, components, name):
            return {"detections": [
                _det(1, (0, 0, 10, 10)),                 # crop expected
                _det(2, (0, 0, 12, 12), reused=True),    # reused -> skipped
                _det(None, (0, 0, 8, 8)),                # track None -> skipped
            ]}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), stub_crops=False, monkeypatch=monkeypatch,
        )
        svc.run()

        crop_paths = [p for p in written if "/crops/" in p]
        assert len(crop_paths) == 1
        assert crop_paths[0].endswith("/crops/snapshot_000000/track_001.jpg")

    def test_crop_failure_is_recoverable(self, monkeypatch):
        reader = FakeReader(1)

        def boom_crops(self, frame, detections, frame_idx):
            raise RuntimeError("crop failure")

        monkeypatch.setattr(VideoAnalysisService, "_generate_crops", boom_crops)

        def pf(frame, components, name):
            return {"detections": [_det(1, (0, 0, 10, 10))]}

        snap_repo = SnapshotRepoSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), stub_crops=False, monkeypatch=monkeypatch,
        )
        result = svc.run()
        # Crop failure is recoverable: frame counts as failed, snapshot kept,
        # run completes.
        assert result.status == "completed"
        assert svc.analysis_failed_frames == 1
        assert len(snap_repo.created) == svc.detector_scheduled_frames

    def test_crop_save_image_false_is_recoverable(self, monkeypatch):
        # save_image returns True for the raw snapshot but False for the crop.
        def save_image_stub(self, relative_path, image):
            return "/crops/" not in relative_path  # False for crop paths

        monkeypatch.setattr(VideoAnalysisService, "_save_image", save_image_stub)

        reader = FakeReader(1)

        def pf(frame, components, name):
            return {"detections": [_det(1, (0, 0, 10, 10))]}

        snap_repo = SnapshotRepoSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=snap_repo, inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), stub_crops=False, monkeypatch=monkeypatch,
        )
        result = svc.run()
        # Crop save_image=False -> recoverable frame failure; raw snapshot kept.
        assert result.status == "completed"
        assert svc.analysis_failed_frames == 1
        assert len(snap_repo.created) == svc.detector_scheduled_frames


# --------------------------------------------------------------------------- #
# 3. writer receives a physical path under BASE_DIR
# --------------------------------------------------------------------------- #

class TestPhysicalPathUnderBaseDir:
    def test_save_image_writes_under_base_dir(self, monkeypatch):
        # Do NOT stub _save_image here: exercise the real helper so we verify it
        # resolves BASE_DIR / relative_path. Stub the infrastructure save_image to
        # capture the physical path (and avoid touching the disk).
        import src.infrastructure.persistence.local.file_utils as file_utils
        from src.infrastructure.config.settings import BASE_DIR

        # Remove the autouse stubs for this test, then re-stub report generation
        # (we only want to exercise the real _save_image path here).
        monkeypatch.undo()
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_reports",
            lambda self, result: None,
        )

        captured = []

        def fake_save_image(image, output_path):
            captured.append(output_path)
            return True

        monkeypatch.setattr(file_utils, "save_image", fake_save_image)

        reader = FakeReader(1)

        def pf(frame, components, name):
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), stub_crops=True, monkeypatch=monkeypatch,
        )
        result = svc.run()
        assert result.status == "completed"

        # The raw snapshot was written under BASE_DIR with the relative path.
        assert captured, "save_image was not called"
        physical = Path(captured[0])
        assert physical.is_absolute()
        expected = BASE_DIR / "outputs/monitorings/42/snapshots/raw/snapshot_000000.jpg"
        assert physical == expected


# --------------------------------------------------------------------------- #
# 10 / 11 / 12. best-by-track
# --------------------------------------------------------------------------- #

class TestBestByTrack:
    def test_largest_bbox_wins(self, monkeypatch):
        reader = FakeReader(5)

        def pf(frame, components, name):
            # track 1 seen with growing then shrinking bbox across detector frames.
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}   # area 100
            if name.endswith("000002"):
                return {"detections": [_det(1, (0, 0, 30, 30))]}   # area 900 (largest)
            if name.endswith("000004"):
                return {"detections": [_det(1, (0, 0, 5, 5))]}     # area 25
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        best = svc.best_by_track[1]
        assert best.best_area == 900
        assert best.bbox == (0, 0, 30, 30)

    def test_failed_frame_does_not_pollute_best(self, monkeypatch):
        reader = FakeReader(3)

        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}   # area 100 committed
            if name.endswith("000002"):
                # a bigger bbox but this frame fails AFTER staging.
                raise RuntimeError("fail after would-be larger detection")
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        # The failed frame never merged into best_by_track.
        assert svc.best_by_track[1].best_area == 100

    def test_reused_does_not_replace_best(self, monkeypatch):
        reader = FakeReader(3)

        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}
            if name.endswith("000002"):
                # larger but reused -> must be ignored for best.
                return {"detections": [_det(1, (0, 0, 40, 40), reused=True)]}
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        assert svc.best_by_track[1].best_area == 100

    def test_one_inspection_result_per_track(self, monkeypatch):
        reader = FakeReader(5)

        def pf(frame, components, name):
            # Two tracks appear across multiple detector frames.
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10)), _det(2, (0, 0, 5, 5))]}
            if name.endswith("000002"):
                return {"detections": [_det(1, (0, 0, 20, 20))]}
            if name.endswith("000004"):
                return {"detections": [_det(2, (0, 0, 8, 8))]}
            return {"detections": []}

        insp = InspectionRepoSpy()
        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=insp,
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        # Exactly one result per unique track (1 and 2).
        track_indexes = sorted(r.detection_index for (_sid, r) in insp.created)
        assert track_indexes == [1, 2]
        assert len(insp.created) == 2


# --------------------------------------------------------------------------- #
# 13. state resets between runs
# --------------------------------------------------------------------------- #

class TestStateResetBetweenRuns:
    def test_counts_and_best_reset(self, monkeypatch):
        reader = FakeReader(3)

        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}
            return {"detections": []}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=InspectionRepoSpy(),
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()
        scheduled1 = svc.detector_scheduled_frames
        assert scheduled1 > 0
        assert len(svc.best_by_track) == 1

        svc.run()  # same instance, reader.open() resets index
        assert svc.detector_scheduled_frames == scheduled1  # not doubled
        assert len(svc.best_by_track) == 1  # not accumulated to 2
        assert (
            svc.analysis_successful_frames + svc.analysis_failed_frames
            == svc.detector_scheduled_frames
        )


# --------------------------------------------------------------------------- #
# 14. fresh import without heavy backends
# --------------------------------------------------------------------------- #

class TestFreshImport:
    def test_importable_when_heavy_backends_blocked(self):
        repo_root = Path(__file__).resolve().parents[2]
        script = r'''
import builtins

blocked = {"torch", "torchvision", "detectron2", "cv2", "picamera2"}
real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in blocked:
        raise ImportError("blocked heavy backend: " + name)
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import

import src.application.services.video_analysis_service as svc
assert hasattr(svc, "VideoAnalysisService")
assert hasattr(svc, "TrackBestResult")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# Regression: non-estimable maturity ("unknown") normalized to NULL
# --------------------------------------------------------------------------- #

class TestUnknownMaturityNormalizedToNull:
    def test_unknown_stage_persisted_as_null(self, monkeypatch):
        reader = FakeReader(1)
        insp = InspectionRepoSpy()

        def pf(frame, components, name):
            return {"detections": [_det(1, (0, 0, 10, 10), stage="unknown")]}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=insp,
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()

        assert len(insp.created) == 1
        _sid, result = insp.created[0]
        assert result.maturity_stage is None
        assert result.maturity_percent is None
        # And it is excluded from maturity coverage counts.
        best = svc.best_by_track[1]
        assert best.maturity_stage is None
        assert best.maturity_percent is None

    def test_valid_stage_still_persisted(self, monkeypatch):
        reader = FakeReader(1)
        insp = InspectionRepoSpy()

        def pf(frame, components, name):
            return {"detections": [_det(1, (0, 0, 10, 10), stage="red")]}

        svc = _make_service(
            reader, _config(), process_frame_fn=pf,
            snapshot_repo=SnapshotRepoSpy(), inspection_repo=insp,
            db_session=DbSpy(), monkeypatch=monkeypatch,
        )
        svc.run()

        _sid, result = insp.created[0]
        assert result.maturity_stage == "red"
        assert result.maturity_percent == 90.0
