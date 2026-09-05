"""Task 12.2 — MANDATORY path-traversal security tests (Spec 019).

Property 10 (Seguridad de rutas): for every supplied video path, including
traversal sequences and absolute paths, the system sanitizes or rejects the
path BEFORE opening, reading, writing or deleting the file, and always persists
RELATIVE paths. Reuses the shared ``path_sanitizer`` (no parallel solution).

Covers the three access points required by 12.2 plus recovery:
    1. VideoRecorder (write)      — valid / ``..`` / absolute-outside.
    2. OpenCvVideoReader (read)   — valid / traversal / absolute-external, and
                                    rejection happens BEFORE cv2.VideoCapture.
    3. MonitoringService.reprocess_monitoring — malicious video_path rejected
       BEFORE any file access or result clearing (nothing deleted).
    4. Recovery — never follows manipulated paths outside outputs/monitorings.

Also asserts an absolute path is never persisted as ``video_path``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("cv2")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import src.infrastructure.camera.opencv_video_reader as reader_mod
from src.infrastructure.camera.opencv_video_reader import OpenCvVideoReader
from src.infrastructure.camera.video_recorder import VideoRecorder, VideoRecorderError
from src.infrastructure.security.path_sanitizer import PathTraversalError
from src.application.services.monitoring_service import (
    MonitoringService,
    ReprocessPreconditionError,
)
from src.domain.entities.monitoring import Monitoring
from src.domain.value_objects.monitoring_status import MonitoringState


# Traversal / absolute payloads exercised across the access points.
TRAVERSAL_REL = "outputs/monitorings/1/video/../../../../etc/passwd.mp4"
if os.name == "nt":
    ABSOLUTE_EXTERNAL = "C:\\Windows\\System32\\evil.mp4"
else:
    ABSOLUTE_EXTERNAL = "/etc/passwd.mp4"


# --------------------------------------------------------------------------- #
# 1. VideoRecorder (write)
# --------------------------------------------------------------------------- #

class TestVideoRecorderWriteSecurity:
    def test_valid_relative_path_writes_under_allowed_base(self, tmp_path):
        """A valid relative path is sanitized and the file is written inside base."""
        rel = "outputs/monitorings/1/video/monitoring.recording.mp4"
        recorder = VideoRecorder(
            output_path=rel, fps=5.0, codec_candidates=("mp4v", "avc1"),
            allowed_base=tmp_path,
        )
        try:
            recorder.open(frame_size=(32, 24))
        except VideoRecorderError:
            pytest.skip("no usable codec in this environment")
        recorder.write(np.zeros((24, 32, 3), dtype=np.uint8))
        recorder.close()

        written = tmp_path / rel
        assert written.exists()
        # The resolved write path stays confined to allowed_base.
        assert str(written.resolve()).startswith(str(tmp_path.resolve()))

    def test_traversal_path_rejected_before_write(self, tmp_path):
        """A ``..`` path is rejected in __init__, before any writer is opened."""
        with pytest.raises(PathTraversalError):
            VideoRecorder(output_path=TRAVERSAL_REL, fps=5.0, allowed_base=tmp_path)

    def test_absolute_external_path_rejected_before_write(self, tmp_path):
        """An absolute path outside the allowed area is rejected before writing."""
        with pytest.raises(PathTraversalError):
            VideoRecorder(output_path=ABSOLUTE_EXTERNAL, fps=5.0, allowed_base=tmp_path)

    # --- Default protection: NO allowed_base passed (uses BASE_DIR) --------- #

    def test_default_traversal_rejected_without_allowed_base(self, monkeypatch):
        """Without allowed_base, a ``..`` path is still rejected (BASE_DIR default)
        and cv2.VideoWriter is never constructed."""
        import src.infrastructure.camera.video_recorder as rec_mod

        constructed = {"count": 0}

        def _spy_writer(*a, **k):
            constructed["count"] += 1
            raise AssertionError("cv2.VideoWriter must not be constructed")

        monkeypatch.setattr(rec_mod.cv2, "VideoWriter", _spy_writer)

        with pytest.raises(PathTraversalError):
            VideoRecorder("../outside.mp4", fps=5.0)
        assert constructed["count"] == 0

    def test_default_absolute_rejected_without_allowed_base(self, monkeypatch):
        """Without allowed_base, an absolute external path is still rejected and
        cv2.VideoWriter is never constructed."""
        import src.infrastructure.camera.video_recorder as rec_mod

        constructed = {"count": 0}

        def _spy_writer(*a, **k):
            constructed["count"] += 1
            raise AssertionError("cv2.VideoWriter must not be constructed")

        monkeypatch.setattr(rec_mod.cv2, "VideoWriter", _spy_writer)

        with pytest.raises(PathTraversalError):
            VideoRecorder(ABSOLUTE_EXTERNAL, fps=5.0)
        assert constructed["count"] == 0


# --------------------------------------------------------------------------- #
# 2. OpenCvVideoReader (read)
# --------------------------------------------------------------------------- #

class _SpyCapture:
    def __init__(self, *a, **k):
        self._opened = True

    def isOpened(self):
        return self._opened

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return 30.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 5.0
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return 32.0
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return 24.0
        return 0.0

    def read(self):
        return True, np.zeros((24, 32, 3), dtype=np.uint8)

    def release(self):
        self._opened = False


class _CountingCaptureFactory:
    def __init__(self):
        self.count = 0

    def __call__(self, path):
        self.count += 1
        return _SpyCapture()


class TestOpenCvVideoReaderReadSecurity:
    def test_valid_relative_path_available(self, tmp_path):
        """A valid relative path resolves and the reader opens a capture."""
        factory = _CountingCaptureFactory()
        # Patch cv2 in the reader module; the path is confined to tmp_path.
        import unittest.mock as _mock
        with _mock.patch.object(reader_mod.cv2, "VideoCapture", factory):
            reader = OpenCvVideoReader(
                "outputs/monitorings/1/video/monitoring.mp4", allowed_base=tmp_path
            )
            reader.open()
            assert reader.is_available() is True
            assert factory.count == 1
            reader.release()

    def test_traversal_rejected_before_videocapture(self, tmp_path):
        """A traversal path never reaches cv2.VideoCapture (rejected first)."""
        factory = _CountingCaptureFactory()
        import unittest.mock as _mock
        with _mock.patch.object(reader_mod.cv2, "VideoCapture", factory):
            reader = OpenCvVideoReader(TRAVERSAL_REL, allowed_base=tmp_path)
            reader.open()  # must not raise; stays unavailable
            assert reader.is_available() is False
            assert factory.count == 0, "cv2.VideoCapture must NOT be constructed"

    def test_absolute_external_rejected_before_videocapture(self, tmp_path):
        """An absolute external path never reaches cv2.VideoCapture."""
        factory = _CountingCaptureFactory()
        import unittest.mock as _mock
        with _mock.patch.object(reader_mod.cv2, "VideoCapture", factory):
            reader = OpenCvVideoReader(ABSOLUTE_EXTERNAL, allowed_base=tmp_path)
            reader.open()
            assert reader.is_available() is False
            assert factory.count == 0, "cv2.VideoCapture must NOT be constructed"


# --------------------------------------------------------------------------- #
# 3 & 4. MonitoringService.reprocess_monitoring + recovery
# --------------------------------------------------------------------------- #

@pytest.fixture
def env(tmp_path, monkeypatch):
    from src.infrastructure.persistence.database import DatabaseManager
    from src.infrastructure.persistence.repositories import (
        SqlMonitoringRepository,
        SqlSnapshotRepository,
        SqlInspectionResultRepository,
        SqlMonitoringMetricsRepository,
        SqlModuleRepository,
        SqlGreenhouseRepository,
    )
    from src.application.services.monitoring_runtime_registry import (
        MonitoringRuntimeRegistry,
    )
    from src.domain.entities.greenhouse import Greenhouse
    from src.domain.entities.module import Module

    manager = DatabaseManager(db_path=str(tmp_path / "sec.db"))
    manager.init_db()

    monkeypatch.setattr("src.infrastructure.config.settings.BASE_DIR", tmp_path)
    monkeypatch.setattr(
        "src.infrastructure.config.settings.OUTPUTS_DIR", tmp_path / "outputs"
    )
    monkeypatch.setattr(
        "src.infrastructure.persistence.database.DatabaseManager",
        lambda *a, **k: manager,
    )

    session = manager.get_session()
    gh_repo = SqlGreenhouseRepository(session=session)
    mod_repo = SqlModuleRepository(session=session)
    mon_repo = SqlMonitoringRepository(session=session)
    snap_repo = SqlSnapshotRepository(session=session)
    insp_repo = SqlInspectionResultRepository(session=session)
    metrics_repo = SqlMonitoringMetricsRepository(session=session)

    gh = gh_repo.create(Greenhouse(name="GH SEC", location="lab"))
    module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod SEC"))
    session.commit()

    service = MonitoringService(
        monitoring_repo=mon_repo,
        snapshot_repo=snap_repo,
        inspection_result_repo=insp_repo,
        metrics_repo=metrics_repo,
        module_repo=mod_repo,
        runtime_registry=MonitoringRuntimeRegistry(),
    )
    return {
        "manager": manager, "session": session, "service": service,
        "mon_repo": mon_repo, "snap_repo": snap_repo, "metrics_repo": metrics_repo,
        "module_id": module.id, "tmp_path": tmp_path,
    }


def _set_terminal_with_video_path(env, video_path):
    """Create a completed monitoring, force video_path directly (bypass helpers)."""
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.COMPLETED.value
    m.video_path = video_path
    env["session"].commit()
    return mon.id


def _intercept_thread(monkeypatch):
    launched = {}

    class _NoThread:
        def __init__(self, *a, **k):
            launched["started_ctor"] = True
        def start(self):
            launched["started"] = True
        def is_alive(self):
            return True

    monkeypatch.setattr("threading.Thread", _NoThread)
    return launched


class TestReprocessPathSecurity:
    @pytest.mark.parametrize("bad_path", [TRAVERSAL_REL, ABSOLUTE_EXTERNAL])
    def test_malicious_video_path_rejected_before_access(self, env, monkeypatch, bad_path):
        """A malicious video_path is rejected before touching the file or clearing
        results (no analysis launched, no derived-artifact deletion)."""
        mon_id = _set_terminal_with_video_path(env, bad_path)

        # Spy that clearing/reset is never called when the path is rejected.
        cleared = {"analysis": False, "reset": False}
        orig_clear = env["mon_repo"].clear_analysis_results
        orig_reset = env["mon_repo"].reset_for_reprocess

        def _clear(mid):
            cleared["analysis"] = True
            return orig_clear(mid)

        def _reset(mid):
            cleared["reset"] = True
            return orig_reset(mid)

        monkeypatch.setattr(env["mon_repo"], "clear_analysis_results", _clear)
        monkeypatch.setattr(env["mon_repo"], "reset_for_reprocess", _reset)

        launched = _intercept_thread(monkeypatch)

        with pytest.raises(ReprocessPreconditionError):
            env["service"].reprocess_monitoring(mon_id, config="CFG")

        assert cleared["analysis"] is False, "results must NOT be cleared"
        assert cleared["reset"] is False, "state must NOT be reset"
        assert launched.get("started") is None, "analysis must NOT be launched"
        # State unchanged (still completed).
        env["session"].expire_all()
        assert env["mon_repo"].get_by_id(mon_id).status == MonitoringState.COMPLETED.value


class TestRecoveryPathSecurity:
    def test_recovery_stays_within_outputs_monitorings(self, env):
        """Recovery only scans int-named dirs under outputs/monitorings and never
        promotes anything from a non-numeric / manipulated sibling directory."""
        tmp = env["tmp_path"]
        monitorings = tmp / "outputs" / "monitorings"
        monitorings.mkdir(parents=True, exist_ok=True)

        # A malicious, non-numeric sibling with a fake recording. Recovery must
        # ignore it (name is not an integer id) and never escape the tree.
        evil = monitorings / ".." / "evil"
        evil = evil.resolve()
        evil.mkdir(parents=True, exist_ok=True)
        (evil / "monitoring.recording.mp4").write_bytes(b"not-a-video")

        # A non-numeric dir directly under monitorings is also ignored.
        weird = monitorings / "not_an_id"
        (weird / "video").mkdir(parents=True, exist_ok=True)
        (weird / "video" / "monitoring.recording.mp4").write_bytes(b"not-a-video")

        # Must not raise and must not create any monitoring.mp4 anywhere.
        env["service"].recover_abrupt_recordings()

        assert not (evil / "monitoring.mp4").exists()
        assert not (weird / "video" / "monitoring.mp4").exists()

    def test_recovery_never_persists_absolute_video_path(self, env):
        """After a valid recovery promotion, the persisted video_path is RELATIVE."""
        # Build a real, valid leftover temp for a numeric monitoring dir.
        mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
        from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
        m = env["session"].get(MonitoringModel, mon.id)
        m.status = MonitoringState.ERROR.value
        env["session"].commit()

        temp_rel = MonitoringService._recording_temp_path(mon.id)
        temp_abs = env["tmp_path"] / temp_rel
        temp_abs.parent.mkdir(parents=True, exist_ok=True)

        # Write a tiny real mp4 as the leftover temp.
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(temp_abs), fourcc, 5.0, (32, 24))
        if not writer.isOpened():
            writer.release()
            pytest.skip("no usable codec in this environment")
        for _ in range(3):
            writer.write(np.zeros((24, 32, 3), dtype=np.uint8))
        writer.release()
        if not temp_abs.exists() or temp_abs.stat().st_size == 0:
            pytest.skip("leftover temp could not be written")

        env["service"].recover_abrupt_recordings()

        env["session"].expire_all()
        persisted = env["mon_repo"].get_by_id(mon.id)
        if persisted.video_path is not None:
            vp = persisted.video_path
            # Never absolute; always relative.
            assert not os.path.isabs(vp)
            assert not (len(vp) >= 2 and vp[1] == ":")  # no Windows drive prefix
            assert vp == MonitoringService._recording_final_path(mon.id)
