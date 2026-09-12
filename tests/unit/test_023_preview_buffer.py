"""Tests for PreviewBuffer (Spec 023, Task 5). Pure, thread-safe, no hardware."""

import threading
import time

from src.application.services.live_preview_manager import (
    STREAM_STOPPED,
    PreviewBuffer,
)


class TestPreviewBufferBasics:
    def test_latest_none_before_publish(self):
        buf = PreviewBuffer()
        assert buf.latest() is None

    def test_publish_increments_sequence(self):
        buf = PreviewBuffer()
        buf.publish(b"a")
        seq1, jpeg1 = buf.latest()
        buf.publish(b"b")
        seq2, jpeg2 = buf.latest()
        assert seq2 == seq1 + 1
        assert jpeg2 == b"b"

    def test_reset_bumps_generation_and_clears(self):
        buf = PreviewBuffer()
        g0 = buf.current_generation()
        buf.publish(b"a")
        g1 = buf.reset()
        assert g1 == g0 + 1
        assert buf.latest() is None  # frame cleared

    def test_reset_clears_sequence(self):
        buf = PreviewBuffer()
        buf.publish(b"a")
        buf.publish(b"b")
        buf.reset()
        buf.publish(b"c")
        seq, jpeg = buf.latest()
        assert seq == 1  # sequence restarted from 0
        assert jpeg == b"c"


class TestWaitForNew:
    def test_returns_new_frame_immediately(self):
        buf = PreviewBuffer()
        gen = buf.current_generation()
        buf.publish(b"x")
        result = buf.wait_for_new(gen, after_sequence=0, timeout=1.0)
        assert result == (1, b"x")

    def test_timeout_returns_none(self):
        buf = PreviewBuffer()
        gen = buf.current_generation()
        start = time.monotonic()
        result = buf.wait_for_new(gen, after_sequence=0, timeout=0.15)
        assert result is None
        assert time.monotonic() - start >= 0.15 - 0.02

    def test_stop_returns_stream_stopped(self):
        buf = PreviewBuffer()
        gen = buf.current_generation()
        buf.stop()
        assert buf.wait_for_new(gen, after_sequence=0, timeout=1.0) is STREAM_STOPPED

    def test_generation_mismatch_returns_stream_stopped(self):
        buf = PreviewBuffer()
        gen = buf.current_generation()
        buf.reset()  # advances generation past `gen`
        assert buf.wait_for_new(gen, after_sequence=0, timeout=1.0) is STREAM_STOPPED

    def test_spurious_wakeup_does_not_return_old_frame(self):
        # A notify without a new frame (e.g. via stop on a DIFFERENT wait) must
        # not yield a stale frame. Here: publish seq1, consumer already has seq1,
        # a bare notify should keep it waiting until timeout (no new frame).
        buf = PreviewBuffer()
        gen = buf.current_generation()
        buf.publish(b"x")  # sequence 1

        def _notify_soon():
            time.sleep(0.05)
            with buf._condition:
                buf._condition.notify_all()  # spurious wake, no new frame

        t = threading.Thread(target=_notify_soon)
        t.start()
        # Consumer already saw sequence 1; asks for something newer.
        result = buf.wait_for_new(gen, after_sequence=1, timeout=0.2)
        t.join()
        assert result is None  # spurious wake did not fabricate a frame


class TestGenerationIsolation:
    def test_gen1_consumer_never_receives_gen2_frame(self):
        """A consumer waiting in generation 1, after stop()+reset() to gen 2,
        must receive STREAM_STOPPED and NEVER a gen-2 JPEG."""
        buf = PreviewBuffer()
        gen1 = buf.current_generation()
        results = []

        def _consumer():
            results.append(buf.wait_for_new(gen1, after_sequence=0, timeout=2.0))

        t = threading.Thread(target=_consumer)
        t.start()
        time.sleep(0.05)  # ensure the consumer is blocked in wait

        buf.stop()          # wake the gen1 consumer
        buf.reset()         # -> generation 2 (immediately, racing the consumer)
        buf.publish(b"gen2-frame")  # a gen2 frame appears

        t.join(timeout=2.0)
        assert results == [STREAM_STOPPED]
        assert results[0] is not None
        assert results[0] != (1, b"gen2-frame")
