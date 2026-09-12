"""Static verification tests for the video-first recording preview (Spec 023).

UPDATED for Spec 023: the recording preview is now a continuous MJPEG stream
rendered by an <img> (src -> /api/monitoring/{id}/preview-stream). The old fast
frame-polling loop (PREVIEW_INTERVAL_MS, pollRecordingPreview, AbortController,
generation token, in-flight guard) has been REMOVED. These tests assert the new
stream-based behavior and preserve the still-valid contracts.

Static-analysis style (reads app/static/js/monitoring.js as text). No browser,
no jsdom, no npm.

Covered requirements (Spec 023 Task 10):
  - the fast preview polling loop is GONE (no PREVIEW_INTERVAL_MS / poll fn);
  - the preview is attached/detached via the <img> stream src, driven by state;
  - startPreviewLoop is idempotent (does not reassign src if already streaming);
  - the preview starts while "running" and stops otherwise;
  - the slow status/log cycle stays at 2 s and does not fetch the preview;
  - repeated startMonitoringPolling() cannot duplicate loops/listeners;
  - single-camera-owner contract preserved (no capture_single_frame calls, no
    recording_target_fps mutation from the client).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JS_PATH = _PROJECT_ROOT / "app" / "static" / "js" / "monitoring.js"


@pytest.fixture(scope="module")
def js_content() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _fn_body(js: str, name: str) -> str:
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(.*?)(?=\n    function |\n    // ---)",
        js,
        re.DOTALL,
    )
    assert match is not None, f"function {name} not found"
    return match.group(1)


class TestFastPollingRemoved:
    """The old fast frame-polling machinery must be completely gone."""

    def test_no_preview_interval_constant(self, js_content):
        assert "PREVIEW_INTERVAL_MS" not in js_content

    def test_no_preview_interval_id(self, js_content):
        assert "previewIntervalId" not in js_content

    def test_no_poll_recording_preview_fn(self, js_content):
        assert "pollRecordingPreview" not in js_content

    def test_no_in_flight_guard(self, js_content):
        assert "previewRequestInFlight" not in js_content

    def test_no_abort_controller_state(self, js_content):
        assert "previewAbortController" not in js_content

    def test_no_generation_token(self, js_content):
        assert "previewGeneration" not in js_content


class TestSlowCycleIntact:
    """The slow status/log cycle stays at 2 s and never fetches the preview."""

    def test_slow_poll_interval_still_2000ms(self, js_content):
        match = re.search(r"POLL_INTERVAL_MS\s*=\s*(\d+)", js_content)
        assert match is not None
        assert int(match.group(1)) == 2000

    def test_slow_cycle_still_polls_status_and_log(self, js_content):
        body = _fn_body(js_content, "pollCycle")
        assert "pollStatus" in body
        assert "pollLog" in body

    def test_slow_cycle_does_not_fetch_preview(self, js_content):
        body = _fn_body(js_content, "pollCycle")
        # No per-frame preview fetch in the slow cycle.
        assert "preview-stream" not in body
        assert "pollRecordingPreview" not in body


class TestStreamAttachDetach:
    """startPreviewLoop/stopPreviewLoop attach/detach the MJPEG <img> stream."""

    def test_start_and_stop_defined(self, js_content):
        assert "function startPreviewLoop" in js_content
        assert "function stopPreviewLoop" in js_content

    def test_start_points_img_at_stream(self, js_content):
        body = _fn_body(js_content, "startPreviewLoop")
        assert "recording-preview-img" in body
        assert "/preview-stream" in body
        assert "imgEl.src" in body

    def test_start_is_idempotent(self, js_content):
        # Must NOT blindly reassign src every call (that would restart the MJPEG
        # connection). It checks the streaming flag / current src first.
        body = _fn_body(js_content, "startPreviewLoop")
        assert "recordingPreviewStreaming" in body
        assert "return" in body  # early return when already streaming

    def test_stop_clears_src(self, js_content):
        body = _fn_body(js_content, "stopPreviewLoop")
        assert "removeAttribute" in body or "src" in body
        assert "recordingPreviewStreaming = false" in body


class TestPreviewDrivenByStatus:
    """The stream is attached only while 'running'."""

    def test_sync_preview_loop_defined(self, js_content):
        assert "function syncPreviewLoop" in js_content

    def test_sync_starts_only_when_running(self, js_content):
        body = _fn_body(js_content, "syncPreviewLoop")
        assert 'currentStatus === "running"' in body
        assert "startPreviewLoop()" in body
        assert "stopPreviewLoop()" in body

    def test_sync_stops_when_not_running(self, js_content):
        body = _fn_body(js_content, "syncPreviewLoop")
        assert re.search(r"else\s*\{\s*stopPreviewLoop\(\);", body) is not None

    def test_update_execution_ui_calls_sync(self, js_content):
        fn_match = re.search(
            r"function updateExecutionUI\(data\)\s*\{(.*?)(?=\n    // ---|\n    function )",
            js_content,
            re.DOTALL,
        )
        assert fn_match is not None
        assert "syncPreviewLoop" in fn_match.group(1)


class TestLifecycleTeardown:
    """Leaving running / stopping polling / page hidden detach the stream."""

    def test_stop_monitoring_polling_stops_preview(self, js_content):
        body = _fn_body(js_content, "stopMonitoringPolling")
        assert "stopPreviewLoop()" in body

    def test_hidden_stops_preview(self, js_content):
        body = _fn_body(js_content, "handleVisibilityChange")
        hidden_branch = body.split("else")[0]
        assert "document.hidden" in body
        assert "stopPreviewLoop()" in hidden_branch

    def test_visible_restores_via_sync(self, js_content):
        body = _fn_body(js_content, "handleVisibilityChange")
        else_branch = body.split("else", 1)[1]
        assert "syncPreviewLoop()" in else_branch

    def test_start_calls_stop_unconditionally(self, js_content):
        body = _fn_body(js_content, "startMonitoringPolling")
        assert "stopMonitoringPolling();" in body


class TestSingleCameraOwnerPreserved:
    """The preview never opens a camera and never mutates recording fps."""

    def test_preview_uses_stream_endpoint(self, js_content):
        assert "/api/monitoring/" in js_content
        assert "preview-stream" in js_content

    def test_no_second_camera_calls(self, js_content):
        assert "capture_single_frame(" not in js_content
        assert "capture_preview_frame(" not in js_content

    def test_does_not_mutate_recording_target_fps(self, js_content):
        assert re.search(r"recording_target_fps\s*=", js_content) is None
