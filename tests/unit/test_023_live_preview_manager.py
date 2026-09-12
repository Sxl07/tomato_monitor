"""Tests for LivePreviewManager (Spec 023, Task 5). Fakes only; no hardware.

The JPEG encoder (CameraService.encode_frame_jpeg) is monkeypatched to a cheap
stub so no cv2 is needed.
"""

import threading
import time

import pytest

import src.application.services.live_preview_manager as lpm_mod
from src.application.services.live_preview_manager import (
    STREAM_STOPPED,
    CaptureState,
    LivePreviewManager,
)


@pytest.fixture(autouse=True)
def _stub_encoder(monkeypatch):
    # Encode returns deterministic bytes; never touches cv2.
    monkeypatch.setattr(
        "src.application.services.camera_service.CameraService.encode_frame_jpeg",
        staticmethod(lambda frame: b"jpeg:" + bytes([int(frame) & 0xFF])),
    )


class _FakeRegistry:
    def __init__(self, active_capture=False, global_analysis=False):
        self._active_capture = active_capture
        self._global_analysis = global_analysis

    def has_active_capture(self):
        return self._active_capture

    def is_global_analysis_active(self):
        return self._global_analysis


class _FakeSource:
    """Yields incrementing ints as 'frames'; counts acquisitions and releases.

    ``block_read`` makes read() block on an event to simulate a stuck camera.
    """

    instances = 0

    def __init__(self, *, block_read=False, read_raises=False, encode_none=False,
                 read_delay=0.0):
        type(self).instances += 1
        self._value = 0
        self.released = 0
        self._block = threading.Event() if block_read else None
        self._read_raises = read_raises
        self._read_delay = read_delay

    def read(self):
        if self._read_raises:
            raise RuntimeError("simulated read failure")
        if self._block is not None:
            self._block.wait(timeout=5.0)
            return False, None
        if self._read_delay:
            time.sleep(self._read_delay)
        self._value += 1
        return True, self._value

    def unblock(self):
        if self._block is not None:
            self._block.set()

    def release(self):
        self.released += 1


def _factory(records, source_kwargs=None):
    source_kwargs = source_kwargs or {}

    def factory(**kwargs):
        records.append(kwargs)
        return _FakeSource(**source_kwargs)

    return factory


def _manager(records=None, *, registry=None, camera_locked=False,
             source_kwargs=None, idle_timeout_s=0.1):
    records = records if records is not None else []
    # Default: a tiny read delay so the capture loop does not busy-spin at full
    # CPU during tests. Individual tests may override via source_kwargs.
    if source_kwargs is None:
        source_kwargs = {"read_delay": 0.005}
    elif "read_delay" not in source_kwargs and "block_read" not in source_kwargs:
        source_kwargs = {**source_kwargs, "read_delay": 0.005}
    reg = registry or _FakeRegistry()
    # camera_is_locked is a callable that can flip based on manager ownership; a
    # simple boolean holder is enough for most tests.
    locked = {"v": camera_locked}
    mgr = LivePreviewManager(
        _factory(records, source_kwargs),
        reg,
        lambda: locked["v"],
        camera_stream_fps=20.0,
        camera_wh=(960, 720),
        idle_timeout_s=idle_timeout_s,
    )
    return mgr, records, locked


def _wait_running(mgr, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if mgr.is_running():
            return True
        time.sleep(0.005)
    return mgr.is_running()


class TestStartup:
    def test_factory_called_with_video_mode_and_lock_timeout(self):
        _FakeSource.instances = 0
        mgr, records, _ = _manager()
        sub = mgr.subscribe()
        assert sub is not None
        assert len(records) == 1
        kw = records[0]
        assert kw["camera_mode"] == "video"
        assert kw["fps"] == 20.0
        assert kw["camera_lock_timeout_seconds"] == 1.0
        mgr.stop()

    def test_device_busy_rejects_subscribe(self):
        mgr, records, _ = _manager(registry=_FakeRegistry(active_capture=True))
        assert mgr.subscribe() is None
        assert records == []  # no camera created


class TestSingleOwner:
    def test_two_concurrent_subscribes_one_source_same_generation(self):
        _FakeSource.instances = 0
        mgr, records, _ = _manager()
        results = []

        def _sub():
            results.append(mgr.subscribe())

        threads = [threading.Thread(target=_sub) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        assert all(r is not None for r in results)
        # Exactly one physical source created.
        assert len(records) == 1
        assert _FakeSource.instances == 1
        # Same generation for both subscribers.
        gens = {r[1] for r in results}
        assert len(gens) == 1
        mgr.stop()

    def test_running_multi_client_when_camera_locked_by_self(self):
        # Manager owns the camera; camera_is_locked() reports True. A SECOND
        # subscriber must still associate (no availability re-check while RUNNING)
        # and NOT create a second source.
        _FakeSource.instances = 0
        mgr, records, locked = _manager()
        first = mgr.subscribe()
        assert first is not None
        assert _wait_running(mgr)
        # Simulate the physical lock now being held by this preview.
        locked["v"] = True
        second = mgr.subscribe()
        assert second is not None
        assert len(records) == 1  # no second source
        assert _FakeSource.instances == 1
        assert second[1] == first[1]  # same generation
        mgr.stop()


class TestStartupFailure:
    def test_factory_returns_none_waiters_get_none(self):
        def factory(**kwargs):
            return None

        mgr = LivePreviewManager(
            factory, _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.1,
        )
        assert mgr.subscribe() is None
        assert mgr._capture_state in (CaptureState.IDLE,)


class TestSubscriberLifecycle:
    def test_unsubscribe_one_keeps_camera_with_other_active(self):
        mgr, records, _ = _manager(idle_timeout_s=0.1)
        a = mgr.subscribe()
        b = mgr.subscribe()
        assert a is not None and b is not None
        assert _wait_running(mgr)
        mgr.unsubscribe(a[0])
        time.sleep(0.25)  # longer than idle timeout
        # Still a subscriber (b) -> capture must remain running.
        assert mgr.is_running()
        mgr.stop()

    def test_last_unsubscribe_idle_stops_without_self_join(self):
        mgr, records, _ = _manager(idle_timeout_s=0.05)
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        src = mgr._frame_source
        mgr.unsubscribe(a[0])
        # Wait for the capture loop to hit idle timeout and stop by itself.
        end = time.monotonic() + 2.0
        while time.monotonic() < end and mgr.is_running():
            time.sleep(0.01)
        assert not mgr.is_running()
        assert mgr._capture_state == CaptureState.IDLE
        assert src.released == 1  # released exactly once, no self-join errors

    def test_unsubscribe_idempotent(self):
        mgr, _, _ = _manager()
        a = mgr.subscribe()
        mgr.unsubscribe(a[0])
        mgr.unsubscribe(a[0])  # repeat
        mgr.unsubscribe(9999)  # unknown
        mgr.stop()

    def test_new_generation_after_idle_stop(self):
        mgr, records, _ = _manager(idle_timeout_s=0.05)
        a = mgr.subscribe()
        gen1 = a[1]
        mgr.unsubscribe(a[0])
        end = time.monotonic() + 2.0
        while time.monotonic() < end and mgr.is_running():
            time.sleep(0.01)
        assert not mgr.is_running()
        b = mgr.subscribe()
        assert b is not None
        assert b[1] == gen1 + 1  # new physical session -> new generation
        assert len(records) == 2  # two physical sources over time
        mgr.stop()


class TestSuspendAndResume:
    def test_suspend_for_handoff_blocks_new_subscribe(self):
        mgr, _, _ = _manager()
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        ok = mgr.suspend_for_handoff(timeout=2.0)
        assert ok is True
        assert mgr._capture_state == CaptureState.IDLE
        assert mgr._subscriptions_suspended is True
        assert mgr.subscribe() is None  # cannot reacquire

    def test_resume_does_not_reset_or_start(self):
        mgr, records, _ = _manager()
        a = mgr.subscribe()
        gen_before = mgr._buffer.current_generation()
        mgr.suspend_for_handoff(timeout=2.0)
        n_sources = len(records)
        mgr.resume_after_failed_handoff()
        # No reset (generation unchanged), no new source, subscriptions re-enabled.
        assert mgr._buffer.current_generation() == gen_before
        assert len(records) == n_sources
        assert mgr._subscriptions_suspended is False

    def test_enable_preview_does_not_reset(self):
        mgr, records, _ = _manager()
        a = mgr.subscribe()
        gen_before = mgr._buffer.current_generation()
        mgr.suspend_for_handoff(timeout=2.0)
        mgr.enable_preview()
        assert mgr._buffer.current_generation() == gen_before
        assert mgr._subscriptions_suspended is False


class TestStopTimeoutAndError:
    def test_stop_timeout_on_blocked_read_then_recover(self):
        _FakeSource.instances = 0
        mgr, records, _ = _manager(source_kwargs={"block_read": True})
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        src = mgr._frame_source
        # read() is blocked; a short stop timeout cannot join -> ERROR, no force
        # release while the thread may be inside read().
        ok = mgr.stop(timeout=0.2)
        assert ok is False
        assert mgr._capture_state == CaptureState.ERROR
        assert src.released == 0  # NOT force-released while thread alive
        # Unblock read; the thread exits and its finally releases exactly once.
        src.unblock()
        end = time.monotonic() + 2.0
        while time.monotonic() < end and src.released == 0:
            time.sleep(0.01)
        assert src.released == 1


class TestStartupRaces:
    """A.6: cancellation during STARTING and thread.start() failure."""

    def _blocking_factory(self, records, gate, source_holder):
        """Factory that blocks inside creation until ``gate`` is set, then
        returns a fresh _FakeSource captured in ``source_holder``."""
        def factory(**kwargs):
            records.append(kwargs)
            gate.wait(timeout=5.0)
            src = _FakeSource(read_delay=0.005)
            source_holder.append(src)
            return src
        return factory

    def test_suspend_during_starting_cancels_startup(self):
        records = []
        gate = threading.Event()
        sources = []
        mgr = LivePreviewManager(
            self._blocking_factory(records, gate, sources),
            _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.1,
        )
        result = {}

        def _sub():
            result["r"] = mgr.subscribe()

        t = threading.Thread(target=_sub)
        t.start()
        # Wait until the factory is engaged (state == STARTING, blocked in create).
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not records:
            time.sleep(0.005)
        assert records, "factory not engaged"

        # Request suspension while the startup owner is blocked creating source.
        # suspend_for_handoff calls _stop_capture; thread not started yet -> quick.
        susp = threading.Thread(target=lambda: mgr.suspend_for_handoff(timeout=2.0))
        susp.start()
        time.sleep(0.05)
        gate.set()  # unblock the factory; the source is created but must not run
        t.join(timeout=2.0)
        susp.join(timeout=2.0)

        assert result["r"] is None                 # subscriber rejected
        assert not mgr.is_running()                 # never RUNNING
        assert mgr._subscriptions_suspended is True  # suspension preserved
        assert len(sources) == 1
        assert sources[0].released == 1            # source released exactly once

    def test_stop_during_starting_cancels_startup(self):
        records = []
        gate = threading.Event()
        sources = []
        mgr = LivePreviewManager(
            self._blocking_factory(records, gate, sources),
            _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.1,
        )
        result = {}
        t = threading.Thread(target=lambda: result.__setitem__("r", mgr.subscribe()))
        t.start()
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not records:
            time.sleep(0.005)
        assert records

        stop_t = threading.Thread(target=lambda: mgr.stop(timeout=2.0))
        stop_t.start()
        time.sleep(0.05)
        gate.set()
        t.join(timeout=2.0)
        stop_t.join(timeout=2.0)

        assert result["r"] is None
        assert not mgr.is_running()
        assert len(sources) == 1
        assert sources[0].released == 1

    def test_thread_start_failure(self, monkeypatch):
        _FakeSource.instances = 0
        records = []
        sources = []

        def factory(**kwargs):
            records.append(kwargs)
            src = _FakeSource(read_delay=0.005)
            sources.append(src)
            return src

        mgr = LivePreviewManager(
            factory, _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.1,
        )

        real_thread = lpm_mod.threading.Thread

        class _BadThread(real_thread):
            def start(self):
                raise RuntimeError("cannot start thread")

        monkeypatch.setattr(lpm_mod.threading, "Thread", _BadThread)

        sub = mgr.subscribe()
        assert sub is None
        assert mgr._capture_state != CaptureState.RUNNING
        assert len(sources) == 1
        assert sources[0].released == 1  # released exactly once
        assert mgr._frame_source is None

    def test_second_subscriber_times_out_without_second_camera(self, monkeypatch):
        # A startup that never resolves: a second subscriber must return None
        # after a bounded wait and NOT create a second source.
        records = []
        gate = threading.Event()  # never set -> startup owner stays blocked
        sources = []
        mgr = LivePreviewManager(
            self._blocking_factory(records, gate, sources),
            _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.1,
        )
        # Shorten the waiter deadline for a fast test.
        import src.application.services.live_preview_manager as m
        orig_wait = m.LivePreviewManager._wait_for_startup_locked

        def _short_wait(self):
            import time as _t
            deadline = _t.monotonic() + 0.2
            while self._capture_state == CaptureState.STARTING:
                remaining = deadline - _t.monotonic()
                if remaining <= 0:
                    return None
                self._state_cond.wait(timeout=remaining)
            if self._capture_state == CaptureState.RUNNING:
                tok = self._add_subscriber_locked()
                return (tok, self._generation)
            return None

        monkeypatch.setattr(
            m.LivePreviewManager, "_wait_for_startup_locked", _short_wait
        )

        owner_result = {}
        t_owner = threading.Thread(
            target=lambda: owner_result.__setitem__("r", mgr.subscribe())
        )
        t_owner.start()
        end = time.monotonic() + 1.0
        while time.monotonic() < end and not records:
            time.sleep(0.005)
        assert records  # one factory engaged (owner)

        # Second subscriber sees STARTING and must time out -> None, no 2nd source.
        second = mgr.subscribe()
        assert second is None
        assert len(records) == 1  # NO second factory call

        gate.set()  # let the owner finish; clean up
        t_owner.join(timeout=2.0)
        mgr.stop(timeout=2.0)


class TestGenerationAntiReset:
    def test_waiter_gen1_never_gets_gen2_frame(self):
        # Throttle reads so few frames publish; the gen1 consumer stays blocked
        # (asks for a very high sequence) until stop() wakes it.
        mgr, _, _ = _manager(idle_timeout_s=5.0, source_kwargs={"read_delay": 0.05})
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        gen1 = a[1]
        results = []

        def _consumer():
            results.append(
                mgr.wait_for_preview(gen1, after_sequence=10_000_000, timeout=2.0)
            )

        t = threading.Thread(target=_consumer)
        t.start()
        time.sleep(0.05)
        mgr.stop()  # stops buffer -> gen1 waiter wakes with STREAM_STOPPED
        # Start a brand new session (gen2).
        mgr.subscribe()
        t.join(timeout=2.0)
        assert results == [STREAM_STOPPED]
        mgr.stop()


class TestMetrics:
    def test_encode_failure_counts_camera_not_preview(self, monkeypatch):
        # Encoder returns None -> camera_frames_produced grows, encoded/sequence do not.
        monkeypatch.setattr(
            "src.application.services.camera_service.CameraService.encode_frame_jpeg",
            staticmethod(lambda frame: None),
        )
        mgr, _, _ = _manager(idle_timeout_s=5.0, source_kwargs={"read_delay": 0.02})
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        time.sleep(0.1)  # let some frames be read
        mgr.stop()
        d = mgr.diagnostics()
        assert d["camera_frames_produced"] >= 1
        assert d["preview_frames_encoded"] == 0
        assert mgr._buffer.latest() is None  # nothing published

    def test_diagnostics_fields_present(self):
        mgr, _, _ = _manager(idle_timeout_s=5.0, source_kwargs={"read_delay": 0.02})
        a = mgr.subscribe()
        assert _wait_running(mgr)
        time.sleep(0.05)
        d = mgr.diagnostics()
        for key in (
            "camera_frames_produced", "preview_frames_encoded", "active_subscribers",
            "camera_capture_elapsed_seconds", "effective_camera_stream_fps",
            "capture_state", "subscriptions_suspended", "generation",
        ):
            assert key in d
        mgr.stop()


class TestLockingNoDeadlock:
    def test_release_can_take_a_lock_without_deadlock(self):
        # A source whose release() itself grabs a lock and calls a manager method
        # must not deadlock (release runs OUTSIDE the manager lock).
        _FakeSource.instances = 0
        records = []

        class _LockyRelease(_FakeSource):
            def __init__(self, **kw):
                super().__init__(**kw)
                self._mgr = None

            def release(self):
                # Touch a manager method that acquires the manager lock.
                if self._mgr is not None:
                    self._mgr.is_running()
                super().release()

        def factory(**kwargs):
            records.append(kwargs)
            src = _LockyRelease()
            src._mgr = holder.get("mgr")
            return src

        holder = {}
        mgr = LivePreviewManager(
            factory, _FakeRegistry(), lambda: False,
            camera_stream_fps=20.0, camera_wh=(960, 720), idle_timeout_s=0.05,
        )
        holder["mgr"] = mgr
        a = mgr.subscribe()
        assert a is not None
        assert _wait_running(mgr)
        ok = mgr.stop(timeout=2.0)
        assert ok is True  # joined without deadlock
