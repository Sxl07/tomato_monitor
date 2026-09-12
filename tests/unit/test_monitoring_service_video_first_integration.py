"""Task 10.3 / 10.4 integration tests (off-Raspberry, real video).

Exercises the video-first flow end to end WITHOUT a camera and WITHOUT heavy
inference, but with REAL recording (cv2.VideoWriter), REAL validation, REAL
atomic promotion, REAL video_path persistence and REAL video reading
(OpenCvVideoReader). Only the heavy detection pipeline is stubbed
(build_pipeline_components / process_frame).

Requires:
    - OpenCV (cv2) available (skipped otherwise).
    - data/videos/video_02.mp4 present (skipped otherwise).

Covers:
    10.3 recording -> temp -> validate -> rename -> video_path -> analyzing -> completed.
    10.4 fatal analysis failure on a valid mp4 -> file same size & still readable.
    10.4 invalid temp -> temp kept, monitoring.mp4 not created, error.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("cv2")
import cv2  # noqa: E402

from src.application.services.monitoring_service import MonitoringService
from src.domain.entities.monitoring import Monitoring
from src.domain.value_objects.monitoring_status import MonitoringState


BASE_DIR = Path(__file__).resolve().parents[2]
SAMPLE_VIDEO = BASE_DIR / "data" / "videos" / "video_02.mp4"


# --------------------------------------------------------------------------- #
# Real DB + repos on a temp SQLite, with outputs redirected to a temp dir.
# --------------------------------------------------------------------------- #

@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """Temp SQLite DB + repos, and BASE_DIR/OUTPUTS_DIR pointed at tmp_path.

    Redirecting BASE_DIR ensures the service's BASE_DIR / relative_path writes
    (temp/final video) land under tmp_path, not the real repo tree.
    """
    if not SAMPLE_VIDEO.exists():
        pytest.skip("data/videos/video_02.mp4 not available")

    from src.infrastructure.persistence.database import DatabaseManager
    from src.infrastructure.persistence.repositories import (
        SqlMonitoringRepository,
        SqlSnapshotRepository,
        SqlInspectionResultRepository,
        SqlMonitoringMetricsRepository,
        SqlModuleRepository,
        SqlGreenhouseRepository,
    )

    db_path = str(tmp_path / "it.db")
    manager = DatabaseManager(db_path=db_path)
    manager.init_db()

    # Redirect the service's path resolution + outputs to tmp_path.
    monkeypatch.setattr(
        "src.application.services.monitoring_service.MonitoringService", MonitoringService
    )
    monkeypatch.setattr("src.infrastructure.config.settings.BASE_DIR", tmp_path)
    monkeypatch.setattr("src.infrastructure.config.settings.OUTPUTS_DIR", tmp_path / "outputs")
    # opencv_video_reader and video_analysis_service import BASE_DIR at module load;
    # redirect those references too so the analysis reader resolves under tmp_path.
    monkeypatch.setattr(
        "src.infrastructure.camera.opencv_video_reader.BASE_DIR", tmp_path, raising=False
    )

    # Background threads build their own DatabaseManager() — force them to use ours.
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

    # Seed a greenhouse + module.
    from src.domain.entities.greenhouse import Greenhouse
    from src.domain.entities.module import Module

    gh = gh_repo.create(Greenhouse(name="GH IT", location="lab"))
    module = mod_repo.create(gh.id, Module(greenhouse_id=gh.id, name="Mod IT"))
    session.commit()

    return {
        "manager": manager,
        "session": session,
        "mon_repo": mon_repo,
        "snap_repo": snap_repo,
        "insp_repo": insp_repo,
        "metrics_repo": metrics_repo,
        "mod_repo": mod_repo,
        "module_id": module.id,
        "tmp_path": tmp_path,
    }


def _service(env, registry=None):
    from src.application.services.monitoring_runtime_registry import (
        MonitoringRuntimeRegistry,
    )

    return MonitoringService(
        monitoring_repo=env["mon_repo"],
        snapshot_repo=env["snap_repo"],
        inspection_result_repo=env["insp_repo"],
        metrics_repo=env["metrics_repo"],
        module_repo=env["mod_repo"],
        runtime_registry=registry or MonitoringRuntimeRegistry(),
    )


class _LoopingFrameSource:
    """Wraps VideoFileFrameSource and repeats it so it never exhausts.

    Mirrors a live camera that keeps producing frames until the operator ends
    capture (finalize). On EOF it reopens the sample video and keeps going, so
    the recording worker only stops when finalize_event is set by
    finalize_capture (a clean finalize, not frame-source exhaustion).
    """

    def __init__(self, video_path):
        from src.infrastructure.camera.video_file_frame_source import (
            VideoFileFrameSource,
        )
        self._video_path = str(video_path)
        self._VideoFileFrameSource = VideoFileFrameSource
        self._inner = VideoFileFrameSource(self._video_path)

    def read(self):
        ok, frame = self._inner.read()
        if not ok:
            # Loop: reopen and read from the start.
            self._inner.release()
            self._inner = self._VideoFileFrameSource(self._video_path)
            ok, frame = self._inner.read()
        return ok, frame

    def release(self):
        self._inner.release()

    def is_available(self):
        return self._inner.is_available()

    def release(self):
        self._inner.release()

    def is_available(self):
        return self._inner.is_available()


def _stub_inference(monkeypatch):
    """Stub the heavy detection pipeline (no detectron2). Video I/O stays real."""
    monkeypatch.setattr(
        "src.infrastructure.vision.pipeline_orchestrator.build_pipeline_components",
        lambda: object(),
    )
    monkeypatch.setattr(
        "src.infrastructure.vision.pipeline_orchestrator.process_frame",
        lambda frame, components, name: {"detections": []},
    )


class _BoundedFrameSource:
    """Delivers N real frames then sets the worker's finalize_event (clean exit)."""

    def __init__(self, video_path, max_frames=8):
        from src.infrastructure.camera.video_file_frame_source import (
            VideoFileFrameSource,
        )
        self._inner = VideoFileFrameSource(str(video_path))
        self._max = max_frames
        self._count = 0
        self._worker = None

    def bind_worker(self, worker):
        self._worker = worker

    def read(self):
        ok, frame = self._inner.read()
        if not ok:
            if self._worker is not None:
                self._worker.finalize_event.set()
            return False, None
        self._count += 1
        if self._count >= self._max and self._worker is not None:
            self._worker.finalize_event.set()
        return True, frame

    def release(self):
        self._inner.release()

    def is_available(self):
        return self._inner.is_available()


def _record_real_video(env, monitoring_id):
    """Build + run a real VideoRecordingWorker over the sample video (helper).

    Used by the 10.4 tests to produce a real valid recording directly (runs the
    worker synchronously; the bounded frame source finalizes after N frames).
    """
    from src.application.services.video_recording_worker import VideoRecordingWorker
    from src.infrastructure.camera.video_recorder import VideoRecorder

    temp_rel = MonitoringService._recording_temp_path(monitoring_id)
    temp_abs = str(env["tmp_path"] / temp_rel)

    recorder = VideoRecorder(
        output_path=temp_rel, fps=5.0, codec_candidates=("mp4v", "avc1"),
        allowed_base=env["tmp_path"],
    )
    frame_source = _BoundedFrameSource(SAMPLE_VIDEO, max_frames=8)
    from src.application.services.recording_sampler import RecordingSampler
    worker = VideoRecordingWorker(
        monitoring_id=monitoring_id,
        frame_source=frame_source,
        video_recorder=recorder,
        monitoring_repo=env["mon_repo"],
        db_session=env["session"],
        configured_recording_fps=5.0,
        configured_camera_stream_fps=20.0,
        recording_sampler=RecordingSampler(recording_fps=5.0),
    )
    frame_source.bind_worker(worker)
    worker.run()
    return worker, temp_abs


# --------------------------------------------------------------------------- #
# 10.3 — full happy path with real recording/validation/promotion/read
# --------------------------------------------------------------------------- #

class _InlineThread:
    """Runs the target synchronously on start() for deterministic tests."""

    def __init__(self, target, args, name=""):
        self._t, self._a, self.name = target, args, name

    def start(self):
        self._t(*self._a)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def test_10_3_recording_validate_promote_persist_analyze_complete(temp_env, monkeypatch):
    """Drive the REAL production flow: start_session -> _start_video_first ->
    VideoRecordingWorker -> finalize_capture -> validate/promote -> video_path ->
    analyzing -> completed. Only heavy inference is stubbed; video I/O is real.
    """
    env = temp_env
    _stub_inference(monkeypatch)

    from src.infrastructure.config.settings import ACTIVE_PROFILE

    # video_first must be enabled for start_session to route to _start_video_first.
    assert ACTIVE_PROFILE.video_first_enabled is True

    service = _service(env)

    # Looping frame source over the real sample video: it never exhausts, so the
    # recording worker keeps running (in a REAL background thread) until
    # finalize_capture signals finalize — the true production sequence.
    frame_source = _LoopingFrameSource(SAMPLE_VIDEO)

    captured = {}
    real_thread = threading.Thread

    def patched_thread(*args, **kwargs):
        name = kwargs.get("name", "")
        target = kwargs.get("target")
        targs = kwargs.get("args", ())
        if name.startswith("video-recording-worker"):
            # Keep the recording worker in a REAL thread so it stays alive until
            # finalize_capture stops it (capture the worker for assertions).
            captured["worker"] = targs[1]
            return real_thread(*args, **kwargs)
        if name.startswith("video-analysis-worker"):
            # Run analysis inline for deterministic assertions.
            captured["analysis_args"] = targs
            return _InlineThread(target, targs, name=name)
        return real_thread(*args, **kwargs)

    monkeypatch.setattr("threading.Thread", patched_thread)

    with monkeypatch.context() as m:
        m.setattr(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            lambda: False,
            raising=False,
        )

        # 1) start_session → _start_video_first → recording worker runs in a REAL
        #    background thread and keeps recording until finalize.
        mon = service.start_session(
            module_id=env["module_id"],
            width_m=5.0,
            length_m=2.0,
            notes=None,
            frame_source=frame_source,
            db_session=env["session"],
        )

        temp_rel = MonitoringService._recording_temp_path(mon.id)
        temp_abs = env["tmp_path"] / temp_rel

        # Wait until at least one frame has actually been written to the temp.
        worker = captured["worker"]
        import time as _time
        deadline = _time.monotonic() + 5.0
        while (
            worker.recording_metrics.frames_written < 1
            and _time.monotonic() < deadline
        ):
            _time.sleep(0.02)
        assert worker.recording_metrics.frames_written >= 1
        assert temp_abs.exists(), "temp recording not created under BASE_DIR/outputs"

        # 2) finalize_capture → _finalize_video_first → validate/promote/persist →
        #    analyzing → analysis (inline) → completed. This sets finalize and
        #    joins the real recording thread before validating.
        # 2) finalize_capture -> _finalize_video_first -> validate/promote/persist
        #    -> ready_for_analysis. Spec 020: NO analysis is launched here.
        service.finalize_capture(mon.id)

        final_rel = MonitoringService._recording_final_path(mon.id)
        final_abs = env["tmp_path"] / final_rel

        # monitoring.recording.mp4 gone after os.replace; monitoring.mp4 present.
        assert not temp_abs.exists(), "temp should have been renamed away"
        assert final_abs.exists(), "monitoring.mp4 was not promoted under BASE_DIR/outputs"

        # Spec 020: after finalize the session is ready_for_analysis, video_path
        # persisted (RELATIVE), and NO analysis thread was launched.
        persisted = env["mon_repo"].get_by_id(mon.id)
        assert persisted.status == MonitoringState.READY_FOR_ANALYSIS.value
        assert persisted.video_path == final_rel
        assert "analysis_args" not in captured, "analysis must NOT auto-start on finalize"

        # 3) Manual deferred start (operator confirmed adequate power source):
        #    start_deferred_analysis -> preflight -> analyzing -> analysis (inline)
        #    -> completed.
        service.start_deferred_analysis(
            mon.id, power_source_confirmed=True, db_session=env["session"]
        )

    # Analysis launched on the final video during the manual start.
    assert captured["analysis_args"] == (mon.id, final_rel)

    # Ends in completed, metrics persisted.
    persisted = env["mon_repo"].get_by_id(mon.id)
    assert persisted.status == MonitoringState.COMPLETED.value
    assert env["metrics_repo"].get_by_monitoring(mon.id) is not None

    # The final video is still readable.
    cap = cv2.VideoCapture(str(final_abs))
    try:
        assert cap.isOpened()
        ok, _ = cap.read()
        assert ok
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# 10.4 — fatal analysis failure never touches a valid monitoring.mp4
# --------------------------------------------------------------------------- #

def test_10_4_fatal_analysis_failure_keeps_video_intact(temp_env, monkeypatch):
    env = temp_env
    service = _service(env)
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_status(mon.id, MonitoringState.RUNNING.value)
    env["session"].commit()

    # Record a real, valid monitoring.mp4 (promote it directly for this test).
    worker, temp_abs = _record_real_video(env, mon.id)
    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel
    final_abs.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_abs, str(final_abs))

    original_size = final_abs.stat().st_size
    original_bytes = final_abs.read_bytes()

    # Force a fatal analysis crash.
    class _BoomAnalysis:
        def __init__(self, *a, **k):
            pass
        def run(self):
            raise RuntimeError("fatal analysis crash")
        error_reason = "boom"
        progress = None

    monkeypatch.setattr(
        "src.application.services.video_analysis_service.VideoAnalysisService",
        _BoomAnalysis,
    )
    # Reader/thermal are constructed but not used before the crash; keep them cheap.
    monkeypatch.setattr(
        "src.infrastructure.camera.opencv_video_reader.OpenCvVideoReader",
        lambda *a, **k: object(),
    )

    service._run_video_analysis(mon.id, final_rel)

    # Session ended in error; the validated video is byte-for-byte intact + readable.
    persisted = env["mon_repo"].get_by_id(mon.id)
    assert persisted.status == MonitoringState.ERROR.value
    assert final_abs.exists()
    assert final_abs.stat().st_size == original_size
    assert final_abs.read_bytes() == original_bytes
    cap = cv2.VideoCapture(str(final_abs))
    try:
        assert cap.isOpened()
        ok, _ = cap.read()
        assert ok
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# 10.4 — invalid temp: kept, monitoring.mp4 not created, error
# --------------------------------------------------------------------------- #

def test_10_4_invalid_temp_kept_no_final_error(temp_env, monkeypatch):
    env = temp_env
    service = _service(env)
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_status(mon.id, MonitoringState.RUNNING.value)
    env["session"].commit()

    # Write a corrupt temp video (exists, size>0, but not a real video).
    temp_rel = MonitoringService._recording_temp_path(mon.id)
    temp_abs = env["tmp_path"] / temp_rel
    temp_abs.parent.mkdir(parents=True, exist_ok=True)
    temp_abs.write_bytes(b"not-a-real-video-payload")

    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel

    # A minimal video worker stub with the video-first duck-type.
    class _Worker:
        _monitoring_id = mon.id
        _log_service = None
        from dataclasses import dataclass  # noqa
        recording_metrics = type("RM", (), {"__dataclass_fields__": {}})()
        def get_last_frame(self):
            return None
    # recording_metrics must be dataclass-asdict-able; use a real dataclass.
    from dataclasses import dataclass

    @dataclass
    class _RM:
        frames_written: int = 0
        codec_used: str = ""
        exit_reason: str = "finalize"

    worker = _Worker()
    worker.recording_metrics = _RM()

    registry = service._registry
    registry.register(mon.id, worker, threading.Thread(target=lambda: None))
    registry.claim_finalization(mon.id)

    with monkeypatch.context() as m:
        m.setattr(
            "src.infrastructure.camera.raspberry_camera_frame_source.is_camera_locked",
            lambda: False,
            raising=False,
        )
        service._finalize_video_first(mon.id, worker)

    # Temp kept, final NOT created, status error, no video_path.
    assert temp_abs.exists(), "invalid temp must be kept for diagnosis"
    assert not final_abs.exists(), "monitoring.mp4 must not be created for invalid temp"
    persisted = env["mon_repo"].get_by_id(mon.id)
    assert persisted.status == MonitoringState.ERROR.value
    assert persisted.video_path is None


# --------------------------------------------------------------------------- #
# 11.2 — recovery after abrupt termination (recover_abrupt_recordings)
# --------------------------------------------------------------------------- #

def test_11_2_recovery_promotes_valid_leftover_temp(temp_env, monkeypatch):
    """A valid leftover monitoring.recording.mp4 is promoted to monitoring.mp4,
    video_path persisted, and the promoted video is reprocessable."""
    env = temp_env
    service = _service(env)
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    # Simulate a crashed session that reconciliation marked error.
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.ERROR.value
    env["session"].commit()

    # Produce a REAL valid temp recording, then leave it (do NOT promote) to
    # mimic an abrupt termination where finalize's rename never ran.
    worker, temp_abs = _record_real_video(env, mon.id)
    assert os.path.exists(temp_abs)

    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel

    service.recover_abrupt_recordings()

    # Promoted: temp gone, final present, video_path persisted (relative).
    assert not os.path.exists(temp_abs), "temp should have been promoted (renamed)"
    assert final_abs.exists(), "valid leftover temp must be promoted to monitoring.mp4"
    persisted = env["mon_repo"].get_by_id(mon.id)
    assert persisted.video_path == final_rel

    # Reprocessable after promotion: the strict guard passes (nothing synced) and
    # reprocess launches analysis on the promoted video. Intercept the thread.
    launched = {}

    class _NoThread:
        def __init__(self, *a, **k):
            launched["args"] = k.get("args")
        def start(self):
            launched["started"] = True
        def is_alive(self):
            return True

    monkeypatch.setattr("threading.Thread", _NoThread)
    service.reprocess_monitoring(mon.id, config="CFG")
    assert launched.get("started") is True
    assert launched["args"] == (mon.id, final_rel, "CFG")
    assert final_abs.exists()  # video preserved through reprocess


def test_11_2_recovery_keeps_invalid_leftover_temp(temp_env):
    """An invalid/corrupt leftover temp is kept, NOT promoted, and video_path
    is not marked as a valid monitoring.mp4."""
    env = temp_env
    service = _service(env)
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.ERROR.value
    env["session"].commit()

    # Corrupt leftover temp (exists, size>0, but unreadable as video).
    temp_rel = MonitoringService._recording_temp_path(mon.id)
    temp_abs = env["tmp_path"] / temp_rel
    temp_abs.parent.mkdir(parents=True, exist_ok=True)
    temp_abs.write_bytes(b"not-a-real-video-payload")

    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel

    service.recover_abrupt_recordings()

    # Kept for diagnosis; NOT promoted; video_path stays None; session stays error.
    assert temp_abs.exists(), "invalid leftover temp must be kept for diagnosis"
    assert not final_abs.exists(), "invalid temp must NOT be promoted"
    persisted = env["mon_repo"].get_by_id(mon.id)
    assert persisted.video_path is None
    assert persisted.status == MonitoringState.ERROR.value


def test_11_2_recovery_does_not_touch_when_final_exists(temp_env):
    """If a monitoring.mp4 already exists, recovery leaves the leftover temp alone
    (never overwrites/deletes the validated final)."""
    env = temp_env
    service = _service(env)
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))

    # A valid final already exists.
    worker, temp_abs = _record_real_video(env, mon.id)
    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel
    final_abs.parent.mkdir(parents=True, exist_ok=True)
    # Copy temp -> final, and ALSO leave a separate leftover temp.
    import shutil
    shutil.copyfile(temp_abs, str(final_abs))
    final_bytes = final_abs.read_bytes()

    service.recover_abrupt_recordings()

    # Final untouched (same bytes); leftover temp preserved (not promoted).
    assert final_abs.read_bytes() == final_bytes
    assert os.path.exists(temp_abs)


# --------------------------------------------------------------------------- #
# 11.6 — reprocess retry: config A fatal -> error -> reprocess config B -> ok
# --------------------------------------------------------------------------- #

class _SyncInlineThread:
    """Runs the target synchronously on start() (deterministic reprocess)."""

    def __init__(self, target=None, args=(), name="", daemon=None, **kwargs):
        self._target = target
        self._args = args
        self.name = name

    def start(self):
        if self._target is not None:
            self._target(*self._args)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def test_11_6_reprocess_config_a_fatal_then_config_b_completes(temp_env, monkeypatch):
    """reprocess(config A) -> fatal analysis -> error -> monitoring.mp4 intact
    -> reprocess(config B) -> allowed -> SAME video -> second analysis completes.

    Asserts the second attempt receives config B (not the prior config), reuses
    the same relative video_path, and never opens a camera or re-records.
    """
    env = temp_env
    service = _service(env)

    # Seed a terminal monitoring with a REAL, valid monitoring.mp4.
    mon = env["mon_repo"].create(env["module_id"], Monitoring(module_id=env["module_id"]))
    env["mon_repo"].update_status(mon.id, MonitoringState.RUNNING.value)
    env["session"].commit()
    worker, temp_abs = _record_real_video(env, mon.id)
    final_rel = MonitoringService._recording_final_path(mon.id)
    final_abs = env["tmp_path"] / final_rel
    final_abs.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_abs, str(final_abs))
    env["mon_repo"].update_video_path(mon.id, final_rel)
    from src.infrastructure.persistence.models.monitoring_model import MonitoringModel
    m = env["session"].get(MonitoringModel, mon.id)
    m.status = MonitoringState.COMPLETED.value
    env["session"].commit()

    original_bytes = final_abs.read_bytes()
    original_size = final_abs.stat().st_size

    # Distinct sentinel configs to prove the SECOND run gets config B, not A.
    config_a = object()
    config_b = object()

    # Capture VideoAnalysisService construction (config + video_path) across runs
    # and control success/failure per attempt. Attempt 1 crashes; attempt 2 ok.
    calls = []
    reader_paths = []

    class _FakeResult:
        status = "completed"
        detector_scheduled_frames = 3
        unique_tomatoes = 2
        # attributes consumed by _build_metrics_from_analysis_result:
        healthy_count = 2
        unhealthy_count = 0
        pct_healthy = 100.0
        pct_unhealthy = 0.0
        snapshots_with_detections = 1
        maturity_distribution = {}

    class _FakeAnalysis:
        def __init__(self, *a, **k):
            calls.append(k.get("config"))
            self._attempt = len(calls)
            self.error_reason = None
            self.progress = None

        def run(self):
            if self._attempt == 1:
                # Fatal crash on the first (config A) attempt.
                raise RuntimeError("fatal analysis crash (config A)")
            return _FakeResult()

    def _fake_reader(path, *a, **k):
        reader_paths.append(path)
        return object()

    monkeypatch.setattr(
        "src.application.services.video_analysis_service.VideoAnalysisService",
        _FakeAnalysis,
    )
    monkeypatch.setattr(
        "src.infrastructure.camera.opencv_video_reader.OpenCvVideoReader",
        _fake_reader,
    )
    # Metrics builder must not depend on real analysis internals.
    monkeypatch.setattr(
        MonitoringService,
        "_build_metrics_from_analysis_result",
        lambda self, mid, result: __import__(
            "src.domain.entities.monitoring_metrics", fromlist=["MonitoringMetrics"]
        ).MonitoringMetrics(
            monitoring_id=mid, total_tomatoes=result.unique_tomatoes,
            healthy_count=result.healthy_count, unhealthy_count=result.unhealthy_count,
            pct_healthy=result.pct_healthy, pct_unhealthy=result.pct_unhealthy,
            snapshots_with_detections=result.snapshots_with_detections,
        ),
    )
    # Guard against any camera/frame-source usage during reprocess.
    import src.infrastructure.camera.raspberry_camera_frame_source as _rcam

    def _forbidden(*a, **k):
        raise AssertionError("reprocess must not open a camera / frame source")

    monkeypatch.setattr(_rcam, "RaspberryCameraFrameSource", _forbidden, raising=False)

    # Run reprocess threads inline for deterministic assertions.
    monkeypatch.setattr("threading.Thread", _SyncInlineThread)

    # --- Attempt 1: config A -> fatal -> error, video intact. ---
    service.reprocess_monitoring(mon.id, config=config_a)

    # The analysis thread commits on its own session; drop the identity-map cache
    # so this session observes the committed ERROR state.
    env["session"].expire_all()
    after_a = env["mon_repo"].get_by_id(mon.id)
    assert after_a.status == MonitoringState.ERROR.value
    assert after_a.video_path == final_rel
    assert final_abs.exists()
    assert final_abs.stat().st_size == original_size
    assert final_abs.read_bytes() == original_bytes

    # --- Attempt 2: config B -> allowed -> same video -> completes. ---
    service.reprocess_monitoring(mon.id, config=config_b)

    env["session"].expire_all()
    after_b = env["mon_repo"].get_by_id(mon.id)
    assert after_b.status == MonitoringState.COMPLETED.value
    assert after_b.video_path == final_rel
    assert env["metrics_repo"].get_by_monitoring(mon.id) is not None

    # Second attempt received config B (NOT config A).
    assert len(calls) == 2
    assert calls[0] is config_a
    assert calls[1] is config_b

    # Same relative video was reused for reading on both attempts (no re-record).
    assert reader_paths == [final_rel, final_rel]

    # Video untouched end-to-end.
    assert final_abs.exists()
    assert final_abs.read_bytes() == original_bytes
