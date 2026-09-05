"""Static verification tests for the video-first preview loop hardening.

HOTFIX post-Spec019 (Bug 2 hardening). Follows the same static-analysis style
as test_monitoring_execution_ui.py: reads app/static/js/monitoring.js as text
and asserts structural requirements with regex/substring checks. No browser,
no jsdom, no npm — no new test framework.

Covered requirements:
  - the live preview runs on a loop SEPARATE from the slow status/log cycle;
  - the fast preview loop cadence is ~5 fps / 200 ms;
  - the preview starts while "running";
  - it stops when the session leaves "running" (analyzing / terminal);
  - it stops when the page is hidden and is restored (only if running) when
    visible again;
  - preview requests never run concurrently (in-flight guard cleared in
    finally);
  - status/log stay on the 2 s slow cycle (preview is not fetched there);
  - repeated startMonitoringPolling() cannot duplicate intervals/listeners;
  - the single-camera-owner contract is preserved (only get_last_frame() via
    the existing /preview endpoint; no second camera).
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
    """Return the source of a named function up to the next top-level function.

    Good enough for structural assertions on a single well-formed function in
    this module (matches the existing test file's approach).
    """
    match = re.search(
        r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{(.*?)(?=\n    function |\n    // ---)",
        js,
        re.DOTALL,
    )
    assert match is not None, f"function {name} not found"
    return match.group(1)


class TestSeparatePreviewCadence:
    """The preview loop is separate from the slow polling cycle at ~5 fps."""

    def test_preview_interval_constant_is_200ms(self, js_content):
        match = re.search(r"PREVIEW_INTERVAL_MS\s*=\s*(\d+)", js_content)
        assert match is not None
        assert int(match.group(1)) == 200

    def test_slow_poll_interval_still_2000ms(self, js_content):
        match = re.search(r"POLL_INTERVAL_MS\s*=\s*(\d+)", js_content)
        assert match is not None
        assert int(match.group(1)) == 2000

    def test_dedicated_preview_interval_id_exists(self, js_content):
        assert "previewIntervalId" in js_content

    def test_start_and_stop_preview_loop_defined(self, js_content):
        assert "function startPreviewLoop" in js_content
        assert "function stopPreviewLoop" in js_content

    def test_preview_loop_uses_preview_interval(self, js_content):
        body = _fn_body(js_content, "startPreviewLoop")
        assert "PREVIEW_INTERVAL_MS" in body
        assert "setInterval" in body
        assert "pollRecordingPreview" in body

    def test_preview_not_fetched_in_slow_cycle(self, js_content):
        """pollCycle must NOT call the recording preview (it has its own loop)."""
        body = _fn_body(js_content, "pollCycle")
        assert "pollRecordingPreview" not in body

    def test_slow_cycle_still_polls_status_and_log(self, js_content):
        body = _fn_body(js_content, "pollCycle")
        assert "pollStatus" in body
        assert "pollLog" in body


class TestPreviewStartsWhileRunning:
    """The fast preview loop is driven by the current status."""

    def test_sync_preview_loop_defined(self, js_content):
        assert "function syncPreviewLoop" in js_content

    def test_sync_starts_only_when_running(self, js_content):
        body = _fn_body(js_content, "syncPreviewLoop")
        assert 'currentStatus === "running"' in body
        assert "startPreviewLoop()" in body
        assert "stopPreviewLoop()" in body

    def test_update_execution_ui_calls_sync_preview_loop(self, js_content):
        fn_match = re.search(
            r"function updateExecutionUI\(data\)\s*\{(.*?)(?=\n    // ---|\n    function )",
            js_content,
            re.DOTALL,
        )
        assert fn_match is not None
        assert "syncPreviewLoop" in fn_match.group(1)


class TestStopsOnAnalyzingOrTerminal:
    """Leaving 'running' (e.g. analyzing/terminal) stops the preview loop."""

    def test_sync_stops_when_not_running(self, js_content):
        # The else branch (any non-running status, including analyzing and the
        # terminal states) stops the loop.
        body = _fn_body(js_content, "syncPreviewLoop")
        # Structure: start only in the running branch; stop otherwise.
        assert re.search(r"else\s*\{\s*stopPreviewLoop\(\);", body) is not None

    def test_stop_monitoring_polling_stops_preview(self, js_content):
        body = _fn_body(js_content, "stopMonitoringPolling")
        assert "stopPreviewLoop()" in body


class TestStopsWhenHidden:
    """document.hidden stops the fast preview loop; visible restores it."""

    def test_hidden_stops_preview_loop(self, js_content):
        body = _fn_body(js_content, "handleVisibilityChange")
        # In the hidden branch the preview loop is stopped.
        hidden_branch = body.split("else")[0]
        assert "document.hidden" in body
        assert "stopPreviewLoop()" in hidden_branch

    def test_visible_restores_preview_via_sync(self, js_content):
        body = _fn_body(js_content, "handleVisibilityChange")
        # The visible branch restores the loop through syncPreviewLoop (which
        # only starts it if currentStatus === "running").
        else_branch = body.split("else", 1)[1]
        assert "syncPreviewLoop()" in else_branch


class TestNoConcurrentPreviewRequests:
    """At most one preview request in flight, guard cleared in finally."""

    def test_in_flight_guard_declared(self, js_content):
        assert "previewRequestInFlight" in js_content

    def test_preview_returns_early_when_in_flight(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        assert "if (previewRequestInFlight) return;" in body

    def test_guard_set_before_fetch_and_cleared_in_finally(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        assert "previewRequestInFlight = true;" in body
        assert "finally" in body
        # Cleared to false inside the finally block.
        finally_idx = body.index("finally")
        assert "previewRequestInFlight = false;" in body[finally_idx:]


class TestNoDuplicateIntervalsOrListeners:
    """Repeated startMonitoringPolling() must not duplicate loops/listeners."""

    def test_start_calls_stop_unconditionally(self, js_content):
        body = _fn_body(js_content, "startMonitoringPolling")
        # stopMonitoringPolling() is called at the top, not gated behind an
        # `if (pollingIntervalId !== null)` check.
        assert "stopMonitoringPolling();" in body
        assert re.search(
            r"if\s*\(pollingIntervalId\s*!==\s*null\)\s*\{\s*stopMonitoringPolling\(\);",
            body,
        ) is None

    def test_start_resets_preview_state_via_teardown(self, js_content):
        # startMonitoringPolling relies on the unconditional stopMonitoringPolling()
        # (→ stopPreviewLoop) to reset the guard, abort any in-flight request,
        # and advance the generation. The guard reset lives in stopPreviewLoop.
        stop_body = _fn_body(js_content, "stopPreviewLoop")
        assert "previewRequestInFlight = false;" in stop_body

    def test_stop_is_idempotent_for_preview(self, js_content):
        body = _fn_body(js_content, "stopPreviewLoop")
        # Guarded clear so repeated calls are safe.
        assert "previewIntervalId !== null" in body
        assert "clearInterval" in body


class TestSingleCameraOwnerPreserved:
    """Preview uses only get_last_frame() via the existing endpoint."""

    def test_preview_uses_existing_endpoint(self, js_content):
        assert "/api/monitoring/" in js_content
        assert "/preview" in js_content

    def test_no_second_camera_calls(self, js_content):
        # The preview path must never trigger a camera open / single-frame
        # capture. (These names may appear only in comments, never as calls.)
        assert "capture_single_frame(" not in js_content
        assert "capture_preview_frame(" not in js_content

    def test_does_not_mutate_recording_target_fps(self, js_content):
        # recording_target_fps may be MENTIONED in a comment, but must never be
        # assigned/mutated from the client.
        assert re.search(r"recording_target_fps\s*=", js_content) is None


class TestAbortControllerCancellation:
    """A dedicated AbortController cancels the active preview request."""

    def test_module_declares_abort_controller_state(self, js_content):
        assert "previewAbortController" in js_content

    def test_preview_creates_own_abort_controller(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        assert "new AbortController()" in body

    def test_fetch_receives_abort_signal(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        # The fetch must be wired to the controller's signal.
        assert "controller.signal" in body
        assert "signal" in body

    def test_stop_preview_loop_aborts_active_request(self, js_content):
        body = _fn_body(js_content, "stopPreviewLoop")
        assert "previewAbortController.abort()" in body
        # And the controller reference is cleared.
        assert "previewAbortController = null;" in body

    def test_controller_registered_before_fetch(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        # The active controller is published to module state so stopPreviewLoop
        # can abort it.
        assert "previewAbortController = controller;" in body


class TestGenerationGuardsStaleResponses:
    """A generation token prevents stale responses from touching the UI/state."""

    def test_module_declares_generation_token(self, js_content):
        assert "previewGeneration" in js_content

    def test_stop_preview_loop_bumps_generation(self, js_content):
        body = _fn_body(js_content, "stopPreviewLoop")
        assert "previewGeneration++" in body

    def test_request_captures_generation_at_launch(self, js_content):
        body = _fn_body(js_content, "pollRecordingPreview")
        assert "myGeneration = previewGeneration" in body

    def test_stale_response_does_not_update_ui(self, js_content):
        """After awaits, a generation mismatch returns before imgEl.src is set."""
        body = _fn_body(js_content, "pollRecordingPreview")
        # There must be a stale-check guard, and it must come BEFORE the line
        # that assigns imgEl.src.
        guard = "myGeneration !== previewGeneration"
        assert guard in body
        first_guard_idx = body.index(guard)
        src_assign_idx = body.index("imgEl.src = recordingPreviewBlobUrl;")
        assert first_guard_idx < src_assign_idx
        # A second re-check must exist after the blob() await (two guards total).
        assert body.count(guard + ") return;") >= 2

    def test_finally_only_clears_when_generation_current(self, js_content):
        """A stale request's finally must not release a newer request's guard."""
        body = _fn_body(js_content, "pollRecordingPreview")
        finally_idx = body.index("finally")
        finally_block = body[finally_idx:]
        # The guard/controller reset is conditioned on owning the current gen.
        assert "myGeneration === previewGeneration" in finally_block
        assert "previewRequestInFlight = false;" in finally_block
        assert "previewAbortController = null;" in finally_block


class TestCadenceUnchangedByRaceFix:
    """The race fix must not alter the ~5 fps / 200 ms preview cadence."""

    def test_preview_interval_still_200ms(self, js_content):
        match = re.search(r"PREVIEW_INTERVAL_MS\s*=\s*(\d+)", js_content)
        assert match is not None
        assert int(match.group(1)) == 200

    def test_slow_cycle_still_2000ms(self, js_content):
        match = re.search(r"POLL_INTERVAL_MS\s*=\s*(\d+)", js_content)
        assert match is not None
        assert int(match.group(1)) == 2000
