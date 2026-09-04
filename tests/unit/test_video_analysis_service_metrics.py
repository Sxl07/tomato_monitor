"""Task 8.4 tests: metrics, reporting and thermal for VideoAnalysisService.

Covers cases 1-10 with fakes only (no cv2/torch/detectron2/picamera2):
    1.  pipeline_metrics.json has scheduled/successful/failed + invariant.
    2.  reason counts + sparse config present.
    3.  source FPS correct.
    4.  existing recording/capture section preserved on merge.
    5.  recording metrics are not invented when absent.
    6.  report generated on error with partial data.
    7.  thermal start/pause/stop.
    8.  annotated video not created by default (save_annotated_video False).
    9.  MonitoringMetrics aggregates correct.
    10. fresh import / boundaries without heavy backends.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

import src.application.services.video_analysis_service as svc_mod
from src.application.services.video_analysis_service import (
    VideoAnalysisConfig,
    VideoAnalysisService,
)
from src.application.interfaces.video_reader_port import (
    VideoMetadata,
    VideoReaderError,
    VideoReaderPort,
)
from src.infrastructure.persistence.local.snapshot_analysis_report_writer import (
    SnapshotAnalysisReportWriter,
)


def _frame(seed=0):
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, size=(16, 16, 3), dtype=np.uint8)


class FakeReader(VideoReaderPort):
    def __init__(self, n, fps=30.0, width=64, height=48, open_raises=False):
        self._frames = [_frame(i) for i in range(n)]
        self._i = 0
        self._meta = VideoMetadata(fps=fps, total_frames=n, width=width, height=height)
        self._open_raises = open_raises
        self.released = 0

    def open(self):
        self._i = 0
        if self._open_raises:
            raise VideoReaderError("simulated open failure")

    def is_available(self):
        return True

    def metadata(self):
        return self._meta

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


class SnapshotRepo:
    def __init__(self):
        self._next_id = 1
        # Record has_detections updates so tests can inspect the SAME repo
        # instance handed to the service: {snapshot_id: bool}.
        self.updated_has_detections = {}

    def create(self, monitoring_id, snapshot):
        s = _Snap(self._next_id, monitoring_id, snapshot.image_path, snapshot.frame_index)
        self._next_id += 1
        return s

    def update_has_detections(self, id, has_detections):
        self.updated_has_detections[id] = has_detections


class InspectionRepo:
    def create(self, snapshot_id, result):
        pass


class DbSession:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeThermal:
    """Thermal monitor fake with a pause_event and metrics."""

    def __init__(self, peak=70.0, pauses=0, pause_seconds=0.0, stop_raises=False):
        self.pause_event = threading.Event()
        self.peak_temperature = peak
        self.pause_count = pauses
        self.total_pause_duration_seconds = pause_seconds
        self.started = 0
        self.stopped = 0
        self._stop_raises = stop_raises

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        if self._stop_raises:
            raise RuntimeError("stop failure")


def _det(track_id, bbox, *, health="healthy", stage="red"):
    return {
        "track_id": track_id,
        "bbox": bbox,
        "det_score": 0.9,
        "reused_previous_result": False,
        "health_result": {"label": health, "confidence": 0.8},
        "maturity_result": {"usda_stage": stage, "maturity_percent": 90.0},
    }


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


def _make_service(reader, config, tmp_path, *, process_frame_fn=None, thermal=None,
                  monkeypatch=None, stub_crops=True):
    if monkeypatch is not None:
        # Never touch the real filesystem for image writes.
        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: True,
        )
        if stub_crops:
            monkeypatch.setattr(
                VideoAnalysisService, "_generate_crops",
                lambda self, frame, detections, frame_idx: None,
            )
    writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings")
    return VideoAnalysisService(
        monitoring_id=99,
        video_path="outputs/monitorings/99/video/monitoring.mp4",
        snapshot_repo=SnapshotRepo(),
        inspection_result_repo=InspectionRepo(),
        monitoring_repo=object(),
        db_session=DbSession(),
        config=config,
        video_reader=reader,
        components_factory=lambda: object(),
        process_frame_fn=process_frame_fn or (lambda f, c, n: {"detections": []}),
        thermal_monitor=thermal,
        report_writer=writer,
        profile_name="edge",
    )


def _read_metrics(tmp_path):
    path = tmp_path / "monitorings" / "99" / "pipeline_metrics.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# 1 / 2 / 3. counts + invariant + reason counts + sparse config + fps
# --------------------------------------------------------------------------- #

class TestAnalysisMetrics:
    def test_counts_reason_config_and_fps(self, tmp_path, monkeypatch):
        reader = FakeReader(5, fps=25.0)
        svc = _make_service(reader, _config(), tmp_path, monkeypatch=monkeypatch)
        result = svc.run()
        assert result.status == "completed"

        metrics = _read_metrics(tmp_path)
        analysis = metrics["analysis"]
        ec = analysis["execution_counts"]

        # 1. scheduled/successful/failed present + invariant.
        assert (
            ec["analysis_successful_frames"] + ec["analysis_failed_frames"]
            == ec["detector_scheduled_frames"]
        )
        assert ec["detector_scheduled_frames"] == result.detector_scheduled_frames

        # 2. reason counts + sparse config present.
        assert sum(analysis["reason_counts"].values()) == result.total_frames_read
        sc = analysis["sparse_config"]
        assert sc["min_frames_between_detections"] == 1
        assert sc["max_frames_without_detection"] == 2
        assert sc["use_scene_gate"] is False

        # 3. source fps correct + frame-based sampling note (no auto conversion).
        assert analysis["source_video_fps"] == 25.0
        assert "FRAMES" in analysis["sampling_note"]
        assert analysis["gap_temporal_equivalence"]["min_frames_between_detections"]["frames"] == 1


# --------------------------------------------------------------------------- #
# 4 / 5. recording section preserved; recording metrics not invented
# --------------------------------------------------------------------------- #

class TestRecordingSectionMerge:
    def test_existing_recording_section_preserved(self, tmp_path, monkeypatch):
        # Pre-write a capture/recording section like a VideoRecordingWorker would.
        writer = SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings")
        recording = {
            "configured_recording_fps": 5.0,
            "container_fps": 5.0,
            "effective_recording_fps": 4.7,
            "deviation_between_configured_and_effective_fps": 0.3,
            "frames_written": 141,
            "recording_duration_seconds": 30.0,
        }
        writer.write_capture_metrics(99, recording, profile_name="edge")

        reader = FakeReader(4)
        svc = _make_service(reader, _config(), tmp_path, monkeypatch=monkeypatch)
        svc.run()

        metrics = _read_metrics(tmp_path)
        # analysis section added.
        assert "analysis" in metrics
        # capture/recording section preserved verbatim.
        assert "capture" in metrics
        assert metrics["capture"]["configured_recording_fps"] == 5.0
        assert metrics["capture"]["frames_written"] == 141
        assert metrics["capture"]["effective_recording_fps"] == 4.7

    def test_recording_metrics_not_invented(self, tmp_path, monkeypatch):
        # No capture section pre-written -> analysis must NOT fabricate one.
        reader = FakeReader(3)
        svc = _make_service(reader, _config(), tmp_path, monkeypatch=monkeypatch)
        svc.run()
        metrics = _read_metrics(tmp_path)
        assert "capture" not in metrics
        # analysis does not carry invented recording fps fields.
        assert "configured_recording_fps" not in metrics["analysis"]
        assert "effective_recording_fps" not in metrics["analysis"]


# --------------------------------------------------------------------------- #
# 6. report on error with partial data
# --------------------------------------------------------------------------- #

class TestReportOnError:
    def test_report_written_on_reader_open_error(self, tmp_path, monkeypatch):
        reader = FakeReader(3, open_raises=True)
        svc = _make_service(reader, _config(), tmp_path, monkeypatch=monkeypatch)
        result = svc.run()
        assert result.status == "error"
        metrics = _read_metrics(tmp_path)
        assert metrics["analysis"]["status"] == "error"
        assert metrics["analysis"]["error_reason"] is not None

    def test_report_written_on_recoverable_failures(self, tmp_path, monkeypatch):
        # Some frames fail in process_frame -> completed with partial failures,
        # report reflects the counters and preserves the invariant.
        def pf(frame, components, name):
            if name.endswith("000002"):
                raise RuntimeError("boom")
            return {"detections": []}

        reader = FakeReader(5)
        svc = _make_service(reader, _config(), tmp_path, process_frame_fn=pf,
                            monkeypatch=monkeypatch)
        result = svc.run()
        assert result.status == "completed"
        metrics = _read_metrics(tmp_path)
        ec = metrics["analysis"]["execution_counts"]
        assert ec["analysis_failed_frames"] >= 1
        assert (
            ec["analysis_successful_frames"] + ec["analysis_failed_frames"]
            == ec["detector_scheduled_frames"]
        )
        assert metrics["analysis"]["errors_count"] >= 1


# --------------------------------------------------------------------------- #
# 7. thermal
# --------------------------------------------------------------------------- #

class TestThermal:
    def test_thermal_start_and_stop_called(self, tmp_path, monkeypatch):
        thermal = FakeThermal(peak=73.5, pauses=2, pause_seconds=4.0)
        reader = FakeReader(3)
        svc = _make_service(reader, _config(), tmp_path, thermal=thermal,
                            monkeypatch=monkeypatch)
        result = svc.run()
        assert thermal.started == 1
        assert thermal.stopped == 1
        assert result.thermal_peak_temperature_c == 73.5
        assert result.thermal_pause_count == 2
        assert result.thermal_pause_duration_seconds == 4.0

    def test_thermal_cooperative_pause_waits(self, tmp_path, monkeypatch):
        thermal = FakeThermal()
        thermal.pause_event.set()  # paused

        # On the first cooperative sleep, clear the pause so the loop proceeds.
        original_sleep = svc_mod.time.sleep

        def fake_sleep(_secs):
            thermal.pause_event.clear()

        monkeypatch.setattr(svc_mod.time, "sleep", fake_sleep)

        reader = FakeReader(2)
        svc = _make_service(reader, _config(), tmp_path, thermal=thermal,
                            monkeypatch=monkeypatch)
        result = svc.run()
        assert result.status == "completed"
        assert thermal.stopped == 1

    def test_thermal_stop_failure_does_not_mask_result(self, tmp_path, monkeypatch):
        thermal = FakeThermal(stop_raises=True)
        reader = FakeReader(3)
        svc = _make_service(reader, _config(), tmp_path, thermal=thermal,
                            monkeypatch=monkeypatch)
        result = svc.run()
        # stop() raised but the run still completed and reported.
        assert result.status == "completed"
        assert thermal.stopped == 1

    def test_result_reflects_post_stop_thermal_metrics(self, tmp_path, monkeypatch):
        # Thermal metrics only become final when stop() runs. This monitor
        # publishes its peak/pauses on stop(); the result + pipeline_metrics must
        # reflect the POST-stop values.
        class _StopPublishesThermal(FakeThermal):
            def __init__(self):
                super().__init__(peak=0.0, pauses=0, pause_seconds=0.0)

            def stop(self):
                self.stopped += 1
                # Values become available only after stop().
                self.peak_temperature = 77.7
                self.pause_count = 3
                self.total_pause_duration_seconds = 9.5

        thermal = _StopPublishesThermal()
        reader = FakeReader(3)
        svc = _make_service(reader, _config(), tmp_path, thermal=thermal,
                            monkeypatch=monkeypatch)
        result = svc.run()

        # Result reflects post-stop thermal values (populated after thermal.stop()).
        assert result.thermal_peak_temperature_c == 77.7
        assert result.thermal_pause_count == 3
        assert result.thermal_pause_duration_seconds == 9.5
        # pipeline_metrics.json mirrors them.
        metrics = _read_metrics(tmp_path)
        thermal_section = metrics["analysis"]["thermal"]
        assert thermal_section["peak_temperature_c"] == 77.7
        assert thermal_section["pause_count"] == 3
        assert thermal_section["total_pause_duration_seconds"] == 9.5


class TestUnavailablePopulatesReport:
    def test_is_available_false_report_has_partial_metrics(self, tmp_path, monkeypatch):
        class _UnavailableReader(FakeReader):
            def is_available(self):
                return False

        reader = _UnavailableReader(3)
        svc = _make_service(reader, _config(), tmp_path, monkeypatch=monkeypatch)
        result = svc.run()
        assert result.status == "error"

        metrics = _read_metrics(tmp_path)
        analysis = metrics["analysis"]
        # Report was generated with populated (partial) metrics, not missing keys.
        assert analysis["status"] == "error"
        assert "execution_counts" in analysis
        ec = analysis["execution_counts"]
        assert ec["detector_scheduled_frames"] == 0
        assert ec["analysis_successful_frames"] == 0
        assert ec["analysis_failed_frames"] == 0
        assert analysis["unique_tracks"] == 0
        assert analysis["detections"] == 0


# --------------------------------------------------------------------------- #
# 8. annotated video off by default
# --------------------------------------------------------------------------- #

class TestAnnotatedVideoOff:
    def test_no_annotated_video_file_created(self, tmp_path, monkeypatch):
        # save_annotated_video=False: NO annotated video (.mp4) must be written.
        # This validates ONLY the video artifact; annotated snapshot JPEGs are a
        # separate, allowed feature (see TestAnnotatedSnapshots).
        reader = FakeReader(4)
        cfg = _config(save_annotated_video=False)
        svc = VideoAnalysisService(
            monitoring_id=99,
            video_path="outputs/monitorings/99/video/monitoring.mp4",
            snapshot_repo=SnapshotRepo(),
            inspection_result_repo=InspectionRepo(),
            monitoring_repo=object(),
            db_session=DbSession(),
            config=cfg,
            video_reader=reader,
            components_factory=lambda: object(),
            process_frame_fn=lambda f, c, n: {"detections": []},
            report_writer=SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings"),
            profile_name="edge",
        )
        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: True,
        )
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )
        result = svc.run()
        assert result.status == "completed"
        # No annotated mp4 anywhere under the monitoring dir.
        mp4s = list((tmp_path / "monitorings").rglob("*.mp4"))
        assert mp4s == []


# --------------------------------------------------------------------------- #
# 8b. Annotated SNAPSHOTS (restored video-first regression fix)
# --------------------------------------------------------------------------- #

class TestAnnotatedSnapshots:
    """Annotated snapshot JPEGs are generated for frames WITH detections only,
    using the same frame + frame_result, saved via _save_image, and fully
    recoverable (failures never affect counters/status)."""

    def test_annotated_generated_when_detections(self, tmp_path, monkeypatch):
        # CASE 1: a frame with detections -> renderer called once with the SAME
        # frame object + SAME frame_result object; raw and annotated saved to the
        # exact paths; SnapshotRepo recorded has_detections=True.
        frame_result = {"detections": [_det(1, (0, 0, 10, 10))]}
        # Capture the exact frame object that process_frame_fn received so we can
        # assert identity against what the renderer receives.
        pf_frames = []

        def process_frame_fn(f, c, n):
            pf_frames.append(f)
            return frame_result

        renderer_calls = []

        def renderer(frame, fr):
            renderer_calls.append((frame, fr))
            return frame  # pretend annotated image

        save_calls = []

        def fake_save(self, relative_path, image):
            save_calls.append(relative_path)
            return True

        monkeypatch.setattr(VideoAnalysisService, "_save_image", fake_save)
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )

        repo = SnapshotRepo()
        reader = FakeReader(1)
        cfg = _config()
        svc = VideoAnalysisService(
            monitoring_id=99,
            video_path="outputs/monitorings/99/video/monitoring.mp4",
            snapshot_repo=repo,
            inspection_result_repo=InspectionRepo(),
            monitoring_repo=object(),
            db_session=DbSession(),
            config=cfg,
            video_reader=reader,
            components_factory=lambda: object(),
            process_frame_fn=process_frame_fn,
            annotation_renderer=renderer,
            report_writer=SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings"),
            profile_name="edge",
        )
        result = svc.run()

        assert result.status == "completed"
        # renderer called exactly once.
        assert len(renderer_calls) == 1
        rendered_frame, rendered_fr = renderer_calls[0]
        # SAME frame object that process_frame_fn received.
        assert len(pf_frames) == 1
        assert rendered_frame is pf_frames[0]
        # SAME frame_result object produced by process_frame_fn.
        assert rendered_fr is frame_result
        # raw saved to the EXACT expected relative path.
        assert (
            "outputs/monitorings/99/snapshots/raw/snapshot_000000.jpg"
            in save_calls
        )
        # annotated saved to the EXACT expected relative path.
        assert (
            "outputs/monitorings/99/annotated_snapshots/snapshot_000000.jpg"
            in save_calls
        )
        # SnapshotRepo (SAME instance) recorded has_detections=True for the snapshot.
        assert repo.updated_has_detections == {1: True}

    def test_no_annotation_when_no_detections(self, tmp_path, monkeypatch):
        # CASE 2: detections == [] -> raw persisted, renderer NOT called, no
        # annotated file, SnapshotRepo recorded has_detections=False.
        renderer_calls = {"n": 0}

        def renderer(frame, fr):  # pragma: no cover - must not run
            renderer_calls["n"] += 1
            return frame

        save_calls = []

        def fake_save(self, relative_path, image):
            save_calls.append(relative_path)
            return True

        monkeypatch.setattr(VideoAnalysisService, "_save_image", fake_save)
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )

        repo = SnapshotRepo()
        reader = FakeReader(1)
        svc = VideoAnalysisService(
            monitoring_id=99,
            video_path="outputs/monitorings/99/video/monitoring.mp4",
            snapshot_repo=repo,
            inspection_result_repo=InspectionRepo(),
            monitoring_repo=object(),
            db_session=DbSession(),
            config=_config(),
            video_reader=reader,
            components_factory=lambda: object(),
            process_frame_fn=lambda f, c, n: {"detections": []},
            annotation_renderer=renderer,
            report_writer=SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings"),
            profile_name="edge",
        )
        result = svc.run()

        assert result.status == "completed"
        # raw WAS persisted.
        assert (
            "outputs/monitorings/99/snapshots/raw/snapshot_000000.jpg"
            in save_calls
        )
        # renderer NOT called, no annotated file.
        assert renderer_calls["n"] == 0
        assert not any("annotated_snapshots" in p for p in save_calls)
        # SnapshotRepo recorded has_detections=False.
        assert repo.updated_has_detections == {1: False}

    def test_renderer_failure_is_recoverable(self, tmp_path, monkeypatch):
        # CASE 3: renderer raises -> run completes, frame still SUCCESS, inference
        # results/tracks/metrics untouched by annotation, error recorded on BOTH
        # result.errors and svc.errors, has_detections preserved True.
        frame_result = {"detections": [_det(1, (0, 0, 10, 10))]}

        def renderer(frame, fr):
            raise RuntimeError("renderer boom")

        monkeypatch.setattr(
            VideoAnalysisService, "_save_image",
            lambda self, relative_path, image: True,
        )
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )

        repo = SnapshotRepo()
        reader = FakeReader(1)
        svc = VideoAnalysisService(
            monitoring_id=99,
            video_path="outputs/monitorings/99/video/monitoring.mp4",
            snapshot_repo=repo,
            inspection_result_repo=InspectionRepo(),
            monitoring_repo=object(),
            db_session=DbSession(),
            config=_config(),
            video_reader=reader,
            components_factory=lambda: object(),
            process_frame_fn=lambda f, c, n: frame_result,
            annotation_renderer=renderer,
            report_writer=SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings"),
            profile_name="edge",
        )
        result = svc.run()

        assert result.status == "completed"
        # The scheduled frame is still counted as SUCCESSFUL (annotation is aux).
        assert result.analysis_successful_frames == 1
        assert result.analysis_failed_frames == 0
        assert result.detector_scheduled_frames == 1
        assert (
            result.analysis_successful_frames + result.analysis_failed_frames
            == result.detector_scheduled_frames
        )
        # Inference results / tracks / metrics preserved.
        assert result.total_detections == 1
        assert result.unique_tracks == 1
        assert len(result.best_results_by_track) == 1
        assert result.snapshots_with_detections == 1
        # Annotation error recorded on BOTH the result and the live property.
        assert any("annotation failed" in e for e in result.errors)
        assert any("annotation failed" in e for e in svc.errors)
        # has_detections preserved True.
        assert repo.updated_has_detections == {1: True}

    def test_annotated_save_false_is_recoverable(self, tmp_path, monkeypatch):
        # CASE 4: _save_image returns False for the annotated write -> same
        # recoverable guarantees as CASE 3 (save stub fails ONLY for annotated).
        frame_result = {"detections": [_det(1, (0, 0, 10, 10))]}

        def fake_save(self, relative_path, image):
            # Fail only the annotated write; raw/crops succeed.
            if "annotated_snapshots" in relative_path:
                return False
            return True

        monkeypatch.setattr(VideoAnalysisService, "_save_image", fake_save)
        monkeypatch.setattr(
            VideoAnalysisService, "_generate_crops",
            lambda self, frame, detections, frame_idx: None,
        )

        repo = SnapshotRepo()
        reader = FakeReader(1)
        svc = VideoAnalysisService(
            monitoring_id=99,
            video_path="outputs/monitorings/99/video/monitoring.mp4",
            snapshot_repo=repo,
            inspection_result_repo=InspectionRepo(),
            monitoring_repo=object(),
            db_session=DbSession(),
            config=_config(),
            video_reader=reader,
            components_factory=lambda: object(),
            process_frame_fn=lambda f, c, n: frame_result,
            annotation_renderer=lambda frame, fr: frame,
            report_writer=SnapshotAnalysisReportWriter(base_outputs_dir=tmp_path / "monitorings"),
            profile_name="edge",
        )
        result = svc.run()

        assert result.status == "completed"
        assert result.analysis_successful_frames == 1
        assert result.analysis_failed_frames == 0
        assert result.detector_scheduled_frames == 1
        # Inference results / tracks / metrics preserved.
        assert result.total_detections == 1
        assert result.unique_tracks == 1
        assert len(result.best_results_by_track) == 1
        assert result.snapshots_with_detections == 1
        # Recoverable annotation-save error on BOTH result and live property.
        assert any("failed to save annotated snapshot" in e for e in result.errors)
        assert any("failed to save annotated snapshot" in e for e in svc.errors)
        # has_detections preserved True.
        assert repo.updated_has_detections == {1: True}


# --------------------------------------------------------------------------- #
# 9. MonitoringMetrics aggregates
# --------------------------------------------------------------------------- #

class TestMonitoringMetricsAggregates:
    def test_aggregates_from_best_by_track(self, tmp_path, monkeypatch):
        # 3 tracks: 2 healthy (red, green), 1 unhealthy (turning).
        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [
                    _det(1, (0, 0, 10, 10), health="healthy", stage="red"),
                    _det(2, (0, 0, 8, 8), health="unhealthy", stage="turning"),
                ]}
            if name.endswith("000002"):
                return {"detections": [
                    _det(3, (0, 0, 6, 6), health="healthy", stage="green"),
                ]}
            return {"detections": []}

        reader = FakeReader(5)
        svc = _make_service(reader, _config(), tmp_path, process_frame_fn=pf,
                            monkeypatch=monkeypatch)
        result = svc.run()

        assert result.unique_tracks == 3
        assert result.healthy_count == 2
        assert result.unhealthy_count == 1
        assert result.unknown_health_count == 0
        assert result.maturity_counts["red"] == 1
        assert result.maturity_counts["green"] == 1
        assert result.maturity_counts["turning"] == 1
        # snapshots_with_detections counts frames that had detections (0 and 2).
        assert result.snapshots_with_detections == 2

    def test_unique_tracks_vs_total_detection_rows(self, tmp_path, monkeypatch):
        # Same track appears in 3 successful frames -> 1 unique, 3 detection rows.
        def pf(frame, components, name):
            if name.endswith(("000000", "000002", "000004")):
                return {"detections": [_det(1, (0, 0, 10, 10))]}
            return {"detections": []}

        # min=1,max=2, force first: detector runs at frames 0,2,4.
        reader = FakeReader(5)
        svc = _make_service(reader, _config(), tmp_path, process_frame_fn=pf,
                            monkeypatch=monkeypatch)
        result = svc.run()

        assert result.unique_tracks == 1
        assert result.total_detection_rows == 3

        metrics = _read_metrics(tmp_path)
        assert metrics["analysis"]["unique_tracks"] == 1
        assert metrics["analysis"]["detections"] == 3

    def test_failed_frame_detections_not_counted_in_rows(self, tmp_path, monkeypatch):
        # A failing frame's detections must NOT count toward total_detection_rows.
        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [_det(1, (0, 0, 10, 10))]}
            if name.endswith("000002"):
                # This frame would report 5 detections but fails during crops.
                return {"detections": [_det(i, (0, 0, 5, 5)) for i in range(2, 7)]}
            return {"detections": []}

        def boom_crops(self, frame, detections, frame_idx):
            # Fail only on the second detector frame (5 detections).
            if len(detections) == 5:
                raise RuntimeError("crop failure")

        monkeypatch.setattr(VideoAnalysisService, "_generate_crops", boom_crops)

        reader = FakeReader(5)
        svc = _make_service(reader, _config(), tmp_path, process_frame_fn=pf,
                            monkeypatch=monkeypatch, stub_crops=False)
        result = svc.run()

        assert result.status == "completed"
        assert result.analysis_failed_frames == 1
        # Only the first frame's single detection row is counted.
        assert result.total_detection_rows == 1


class TestMonitoringServiceCompatibility:
    def test_historical_contract_fields_present(self, tmp_path, monkeypatch):
        # MonitoringService._build_metrics_from_analysis_result reads these.
        def pf(frame, components, name):
            if name.endswith("000000"):
                return {"detections": [
                    _det(1, (0, 0, 10, 10), health="healthy", stage="red"),
                ]}
            if name.endswith("000002"):
                return {"detections": [
                    _det(1, (0, 0, 12, 12), health="healthy", stage="red"),
                    _det(2, (0, 0, 6, 6), health="unhealthy", stage="green"),
                ]}
            return {"detections": []}

        reader = FakeReader(5)
        svc = _make_service(reader, _config(), tmp_path, process_frame_fn=pf,
                            monkeypatch=monkeypatch)
        result = svc.run()

        # Canonical historical names.
        assert result.unique_tomatoes == 2
        assert result.total_detection_rows == 3
        assert result.healthy_count == 1
        assert result.unhealthy_count == 1
        assert isinstance(result.maturity_counts, dict)
        assert result.snapshots_with_detections == 2

        # Aliases are exact.
        assert result.unique_tracks == result.unique_tomatoes
        assert result.total_detections == result.total_detection_rows


# --------------------------------------------------------------------------- #
# 10. fresh import / boundaries without heavy backends
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
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_no_module_level_heavy_import(self):
        import ast

        path = (
            Path(__file__).resolve().parents[2]
            / "src" / "application" / "services" / "video_analysis_service.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for heavy in ("cv2", "torch", "torchvision", "detectron2", "picamera2"):
            assert not any(
                m == heavy or m.startswith(heavy + ".") for m in modules
            ), f"module-level import of {heavy} found"
