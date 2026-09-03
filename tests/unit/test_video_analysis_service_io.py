"""Wave 1 I/O tests for VideoAnalysisService (Spec 019, Task 8).

Covers the offline read lifecycle ONLY (no sparse/tracking/inference):
    A. Reader opened once, released once.
    B. Reads all frames to EOF (3 frames -> 3 consumed), no error, released.
    C. Metadata obtained via the port (no backend access).
    D. Empty video / immediate EOF ends cleanly.
    E. open() VideoReaderError -> error, no frames analyzed, consistent cleanup.
    F. reader unavailable after open -> explicit failure (not empty video).
    G. metadata() VideoReaderError -> error, reader released.
    H. read() backend VideoReaderError -> error, reader released.
    I. unexpected (non-VideoReaderError) exception -> reader still released.
    J. no heavy imports (AST) — cv2/torch/torchvision/detectron2/picamera2.
    K. no inference/sparse symbols referenced yet.
    L. service imports only the port abstraction, not OpenCvVideoReader/VideoFileFrameSource.
    (14). fresh-interpreter import with heavy backends blocked.

Uses a FakeVideoReaderPort (subclass of the real port). No cv2, no real video,
no detector, no torch/detectron2.
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import src.application.services.video_analysis_service as svc_mod
from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisResult,
    VideoAnalysisService,
)
from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderError,
    VideoReaderPort,
)


META = VideoMetadata(fps=30.0, total_frames=3, width=64, height=48)


def _frames(n):
    """n numpy frames (support .copy()/.shape; content irrelevant to I/O tests)."""
    return [np.zeros((4, 4, 3), dtype=np.uint8) for _ in range(n)]


class FakeVideoReaderPort(VideoReaderPort):
    """Configurable in-memory VideoReaderPort for lifecycle tests."""

    def __init__(
        self,
        *,
        frames=None,
        metadata: VideoMetadata = META,
        open_raises=False,
        available=True,
        metadata_raises=False,
        read_raises_at=None,
        read_unexpected_at=None,
    ):
        self._frames = list(frames if frames is not None else [object(), object(), object()])
        self._metadata = metadata
        self._open_raises = open_raises
        self._available = available
        self._metadata_raises = metadata_raises
        self._read_raises_at = read_raises_at
        self._read_unexpected_at = read_unexpected_at

        self.open_count = 0
        self.release_count = 0
        self.metadata_count = 0
        self._i = 0

    def open(self) -> None:
        self.open_count += 1
        if self._open_raises:
            raise VideoReaderError("simulated open failure")

    def is_available(self) -> bool:
        return self._available

    def metadata(self) -> VideoMetadata:
        self.metadata_count += 1
        if self._metadata_raises:
            raise VideoReaderError("simulated metadata failure")
        return self._metadata

    def read(self):
        idx = self._i
        self._i += 1
        if self._read_unexpected_at is not None and idx == self._read_unexpected_at:
            raise RuntimeError("simulated unexpected read failure")
        if self._read_raises_at is not None and idx == self._read_raises_at:
            raise VideoReaderError("simulated read backend failure")
        if idx < len(self._frames):
            return True, self._frames[idx]
        return False, None

    def release(self) -> None:
        self.release_count += 1


class _FakeSnapshot:
    def __init__(self, id, monitoring_id, image_path, frame_index):
        self.id = id
        self.monitoring_id = monitoring_id
        self.image_path = image_path
        self.frame_index = frame_index
        self.has_detections = False


class _FakeSnapshotRepo:
    """Minimal snapshot repo so Task 8.3 raw-snapshot persistence succeeds."""

    def __init__(self):
        self._next_id = 1

    def create(self, monitoring_id, snapshot):
        created = _FakeSnapshot(self._next_id, monitoring_id, snapshot.image_path,
                                snapshot.frame_index)
        self._next_id += 1
        return created

    def update_has_detections(self, id, has_detections):
        return None


class _FakeDbSession:
    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeInspectionRepo:
    def create(self, snapshot_id, result):
        return None


def _config():
    # I/O tests isolate the read lifecycle: no scene gate / no flow so no OpenCV
    # is needed, and inference is stubbed via injected fakes below.
    return VideoAnalysisConfig(
        min_frames_between_detections=3,
        max_frames_without_detection=8,
        use_scene_gate=False,
        enable_flow_propagation=False,
    )


@pytest.fixture(autouse=True)
def _no_filesystem(monkeypatch):
    """Stub JPEG write + crop generation so I/O tests never touch the disk.

    Task 8.3 persists a raw snapshot on detector frames; these lifecycle tests
    are not about persistence, so writing is stubbed out.
    """
    monkeypatch.setattr(
        VideoAnalysisService, "_save_image",
        lambda self, relative_path, image: True,
    )
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_crops",
        lambda self, frame, detections, frame_idx: None,
    )
    # Task 8.4 writes pipeline_metrics.json in run()'s finally; stub it so the
    # I/O lifecycle tests never touch the disk.
    monkeypatch.setattr(
        VideoAnalysisService, "_generate_reports",
        lambda self, result: None,
    )


def _make_service(reader):
    # Inject harmless fakes so the DEFAULT vision pipeline (which needs
    # detectron2) is never loaded during pure-I/O lifecycle tests. Task 8.3
    # persists raw snapshots on detector frames, so provide working fake repos.
    return VideoAnalysisService(
        monitoring_id=1,
        video_path="outputs/monitorings/1/video/monitoring.mp4",
        snapshot_repo=_FakeSnapshotRepo(),
        inspection_result_repo=_FakeInspectionRepo(),
        monitoring_repo=object(),
        db_session=_FakeDbSession(),
        config=_config(),
        video_reader=reader,
        components_factory=lambda: object(),
        process_frame_fn=lambda frame, components, name: {"detections": []},
    )


# --------------------------------------------------------------------------- #
# A / B / C. Happy path
# --------------------------------------------------------------------------- #

class TestHappyPath:
    def test_reader_opened_once_released_once(self):
        reader = FakeVideoReaderPort(frames=_frames(3))
        svc = _make_service(reader)
        result = svc.run()
        assert reader.open_count == 1
        assert reader.release_count == 1
        assert result.status == "completed"

    def test_reads_all_frames_to_eof(self):
        reader = FakeVideoReaderPort(frames=_frames(3))
        svc = _make_service(reader)
        result = svc.run()
        assert result.total_frames_read == 3
        assert result.error_reason is None
        assert svc.progress.status == "completed"
        assert reader.release_count == 1

    def test_metadata_via_port(self):
        reader = FakeVideoReaderPort(
            frames=_frames(1),
            metadata=VideoMetadata(fps=25.0, total_frames=1, width=128, height=96),
        )
        svc = _make_service(reader)
        result = svc.run()
        assert reader.metadata_count == 1
        assert result.source_fps == 25.0
        assert result.source_width == 128
        assert result.source_height == 96
        assert result.source_frame_count == 1


# --------------------------------------------------------------------------- #
# D. Empty video
# --------------------------------------------------------------------------- #

class TestEmptyVideo:
    def test_immediate_eof_completes_cleanly(self):
        reader = FakeVideoReaderPort(frames=[])
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "completed"
        assert result.total_frames_read == 0
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# E. Open failure
# --------------------------------------------------------------------------- #

class TestOpenFailure:
    def test_open_error_releases_and_reads_no_frames(self):
        reader = FakeVideoReaderPort(open_raises=True)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert svc.error_reason is not None
        assert result.total_frames_read == 0
        assert reader.metadata_count == 0
        assert reader.open_count == 1
        # release() is always attempted, even when open() failed (idempotent/safe).
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# F. Unavailable after open
# --------------------------------------------------------------------------- #

class TestUnavailable:
    def test_unavailable_is_explicit_error_not_empty_video(self):
        reader = FakeVideoReaderPort(available=False)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert result.total_frames_read == 0
        # Reader was opened -> must be released.
        assert reader.open_count == 1
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# G. Metadata failure
# --------------------------------------------------------------------------- #

class TestMetadataFailure:
    def test_metadata_error_releases_reader(self):
        reader = FakeVideoReaderPort(metadata_raises=True)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# H. Read backend failure
# --------------------------------------------------------------------------- #

class TestReadBackendFailure:
    def test_read_error_after_some_frames_releases_reader(self):
        # frames at idx 0,1 succeed; idx 2 raises VideoReaderError.
        reader = FakeVideoReaderPort(frames=_frames(2), read_raises_at=2)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert reader.release_count == 1

    def test_partial_progress_preserved_on_video_reader_error(self):
        # 2 successful frames, then the 3rd read raises VideoReaderError.
        reader = FakeVideoReaderPort(frames=_frames(2), read_raises_at=2)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.total_frames_read == 2
        assert svc.progress.total_frames_read == 2
        assert svc.progress.current_frame_index == 1
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# I. Unexpected exception
# --------------------------------------------------------------------------- #

class TestUnexpectedException:
    def test_unexpected_read_exception_still_releases(self):
        reader = FakeVideoReaderPort(frames=_frames(1), read_unexpected_at=1)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.error_reason is not None
        assert reader.release_count == 1

    def test_partial_progress_preserved_on_unexpected_error(self):
        # 1 successful frame, then the 2nd read raises a non-VideoReaderError.
        reader = FakeVideoReaderPort(frames=_frames(1), read_unexpected_at=1)
        svc = _make_service(reader)
        result = svc.run()
        assert result.status == "error"
        assert result.total_frames_read == 1
        assert svc.progress.total_frames_read == 1
        assert reader.release_count == 1


# --------------------------------------------------------------------------- #
# Config validation
# --------------------------------------------------------------------------- #

class TestConfigValidation:
    def test_min_negative_rejected(self):
        with pytest.raises(ValueError):
            VideoAnalysisConfig(
                min_frames_between_detections=-1,
                max_frames_without_detection=8,
                use_scene_gate=True,
                enable_flow_propagation=True,
            )

    def test_max_less_than_min_rejected(self):
        with pytest.raises(ValueError):
            VideoAnalysisConfig(
                min_frames_between_detections=5,
                max_frames_without_detection=3,
                use_scene_gate=True,
                enable_flow_propagation=True,
            )

    def test_max_equal_min_allowed(self):
        cfg = VideoAnalysisConfig(
            min_frames_between_detections=5,
            max_frames_without_detection=5,
            use_scene_gate=True,
            enable_flow_propagation=True,
        )
        assert cfg.max_frames_without_detection == 5


# --------------------------------------------------------------------------- #
# J / K / L. Boundary (AST): no heavy imports, no inference/sparse, port-only
# --------------------------------------------------------------------------- #

class TestBoundary:
    @pytest.fixture(scope="class")
    def module_level_modules(self):
        """Return only MODULE-LEVEL imported module names (not function-local).

        Walking only ``tree.body`` (plus class bodies) captures top-level imports
        while ignoring lazy imports inside functions/methods — which is exactly
        what the boundary should allow.
        """
        import ast

        path = (
            Path(__file__).resolve().parents[2]
            / "src" / "application" / "services" / "video_analysis_service.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()

        def _collect(stmt):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    modules.add(alias.name)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.module:
                    modules.add(stmt.module)

        for node in tree.body:
            _collect(node)
            # Also consider class-body top-level imports (none expected).
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    _collect(sub)
        return modules

    @pytest.mark.parametrize("heavy", ["cv2", "torch", "torchvision", "detectron2", "picamera2"])
    def test_no_heavy_module_imported(self, module_level_modules, heavy):
        for mod in module_level_modules:
            assert not (mod == heavy or mod.startswith(heavy + ".")), (
                f"video_analysis_service must not import {heavy} at module level (found {mod})"
            )

    @pytest.mark.parametrize("forbidden_module", [
        # Heavy vision modules must NOT be imported at MODULE LEVEL (only lazily
        # inside run()/helpers). decide_run_detector is dependency-light and is
        # allowed at module level.
        "src.infrastructure.vision.pipeline_orchestrator",
        "src.infrastructure.vision.visual_tracker",
        "src.infrastructure.vision.capture_gate",
        "src.infrastructure.camera.opencv_video_reader",
        "src.infrastructure.camera.video_file_frame_source",
    ])
    def test_heavy_vision_modules_not_module_level(self, module_level_modules, forbidden_module):
        assert forbidden_module not in module_level_modules, (
            f"service must not import {forbidden_module} at module level (lazy only)"
        )

    def test_decide_run_detector_allowed_at_module_level(self, module_level_modules):
        # Wave 2 integrates the pure decision helper at module level (no heavy deps).
        assert "src.infrastructure.vision.detector_decision" in module_level_modules


# --------------------------------------------------------------------------- #
# 14. Fresh-interpreter import with heavy backends blocked
# --------------------------------------------------------------------------- #

class TestFreshInterpreterImport:
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
assert hasattr(svc, "VideoAnalysisConfig")
assert hasattr(svc, "VideoAnalysisResult")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
