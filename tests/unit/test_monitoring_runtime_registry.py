"""Unit tests for MonitoringRuntimeRegistry — Spec 009, C2 Part 1.

Validates:
1. register/get_worker/get_thread
2. set_worker updates worker without changing thread
3. claim_finalization: first call returns True, second returns False
4. release_finalization allows new claim
5. remove_runtime preserves finalization claim
6. remove() discards finalization claim
7. concurrent access (two threads claiming simultaneously — one wins)
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from src.application.services.monitoring_runtime_registry import MonitoringRuntimeRegistry


# ---------------------------------------------------------------------------
# Test: register / get_worker / get_thread
# ---------------------------------------------------------------------------


class TestRegisterAndGet:
    """Verify register, get_worker, get_thread basic operations."""

    def test_register_and_get_worker(self):
        """After register, get_worker returns the registered worker."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)

        registry.register(1, worker, thread)

        assert registry.get_worker(1) is worker

    def test_register_and_get_thread(self):
        """After register, get_thread returns the registered thread."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)

        registry.register(1, worker, thread)

        assert registry.get_thread(1) is thread

    def test_get_worker_returns_none_for_unknown(self):
        """get_worker returns None for unregistered monitoring_id."""
        registry = MonitoringRuntimeRegistry()
        assert registry.get_worker(999) is None

    def test_get_thread_returns_none_for_unknown(self):
        """get_thread returns None for unregistered monitoring_id."""
        registry = MonitoringRuntimeRegistry()
        assert registry.get_thread(999) is None


# ---------------------------------------------------------------------------
# Test: set_worker updates worker without changing thread
# ---------------------------------------------------------------------------


class TestSetWorker:
    """Verify set_worker replaces worker but preserves the thread."""

    def test_set_worker_updates_worker(self):
        """set_worker replaces the worker for the given monitoring_id."""
        registry = MonitoringRuntimeRegistry()
        worker1 = MagicMock(name="worker1")
        worker2 = MagicMock(name="worker2")
        thread = threading.Thread(target=lambda: None)

        registry.register(1, worker1, thread)
        registry.set_worker(1, worker2)

        assert registry.get_worker(1) is worker2

    def test_set_worker_preserves_thread(self):
        """set_worker does not change the registered thread."""
        registry = MonitoringRuntimeRegistry()
        worker1 = MagicMock(name="worker1")
        worker2 = MagicMock(name="worker2")
        thread = threading.Thread(target=lambda: None)

        registry.register(1, worker1, thread)
        registry.set_worker(1, worker2)

        assert registry.get_thread(1) is thread

    def test_set_worker_without_prior_register(self):
        """set_worker works even without prior register (no thread)."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()

        registry.set_worker(5, worker)

        assert registry.get_worker(5) is worker
        assert registry.get_thread(5) is None


# ---------------------------------------------------------------------------
# Test: claim_finalization — first True, second False
# ---------------------------------------------------------------------------


class TestClaimFinalization:
    """Verify exclusive finalization claims."""

    def test_first_claim_returns_true(self):
        """First claim_finalization for an ID returns True."""
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_finalization(1) is True

    def test_second_claim_returns_false(self):
        """Second claim_finalization for same ID returns False."""
        registry = MonitoringRuntimeRegistry()
        registry.claim_finalization(1)
        assert registry.claim_finalization(1) is False

    def test_different_ids_both_claimable(self):
        """Different monitoring IDs can both be claimed."""
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_finalization(1) is True
        assert registry.claim_finalization(2) is True

    def test_is_finalization_claimed_false_initially(self):
        """is_finalization_claimed returns False for unclaimed IDs."""
        registry = MonitoringRuntimeRegistry()
        assert registry.is_finalization_claimed(1) is False

    def test_is_finalization_claimed_true_after_claim(self):
        """is_finalization_claimed returns True after successful claim."""
        registry = MonitoringRuntimeRegistry()
        registry.claim_finalization(1)
        assert registry.is_finalization_claimed(1) is True


# ---------------------------------------------------------------------------
# Test: release_finalization allows new claim
# ---------------------------------------------------------------------------


class TestReleaseFinalization:
    """Verify releasing a claim allows re-claiming."""

    def test_release_allows_new_claim(self):
        """After release_finalization, a new claim for the same ID succeeds."""
        registry = MonitoringRuntimeRegistry()
        registry.claim_finalization(1)
        registry.release_finalization(1)

        assert registry.claim_finalization(1) is True

    def test_release_unclaimed_does_not_raise(self):
        """Releasing an unclaimed ID does not raise."""
        registry = MonitoringRuntimeRegistry()
        # Should not raise
        registry.release_finalization(999)

    def test_is_finalization_claimed_false_after_release(self):
        """is_finalization_claimed returns False after release."""
        registry = MonitoringRuntimeRegistry()
        registry.claim_finalization(1)
        registry.release_finalization(1)
        assert registry.is_finalization_claimed(1) is False


# ---------------------------------------------------------------------------
# Test: remove_runtime preserves finalization claim
# ---------------------------------------------------------------------------


class TestRemoveRuntime:
    """Verify remove_runtime removes worker/thread but keeps finalization."""

    def test_remove_runtime_clears_worker(self):
        """remove_runtime removes the worker."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)

        registry.remove_runtime(1)

        assert registry.get_worker(1) is None

    def test_remove_runtime_clears_thread(self):
        """remove_runtime removes the thread."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)

        registry.remove_runtime(1)

        assert registry.get_thread(1) is None

    def test_remove_runtime_preserves_finalization_claim(self):
        """remove_runtime does NOT discard finalization claim."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)
        registry.claim_finalization(1)

        registry.remove_runtime(1)

        assert registry.is_finalization_claimed(1) is True

    def test_remove_runtime_unknown_id_does_not_raise(self):
        """remove_runtime for unknown ID does not raise."""
        registry = MonitoringRuntimeRegistry()
        registry.remove_runtime(999)  # Should not raise


# ---------------------------------------------------------------------------
# Test: remove() discards finalization claim
# ---------------------------------------------------------------------------


class TestRemove:
    """Verify remove() clears everything including finalization claim."""

    def test_remove_clears_worker(self):
        """remove() removes the worker."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)

        registry.remove(1)

        assert registry.get_worker(1) is None

    def test_remove_clears_thread(self):
        """remove() removes the thread."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)

        registry.remove(1)

        assert registry.get_thread(1) is None

    def test_remove_discards_finalization_claim(self):
        """remove() discards the finalization claim."""
        registry = MonitoringRuntimeRegistry()
        worker = MagicMock()
        thread = threading.Thread(target=lambda: None)
        registry.register(1, worker, thread)
        registry.claim_finalization(1)

        registry.remove(1)

        assert registry.is_finalization_claimed(1) is False
        # Can claim again
        assert registry.claim_finalization(1) is True


# ---------------------------------------------------------------------------
# Test: concurrent access — two threads claiming simultaneously
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    """Verify thread safety of claim_finalization under contention."""

    def test_only_one_thread_wins_claim(self):
        """Two threads trying to claim the same ID — exactly one wins."""
        registry = MonitoringRuntimeRegistry()
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def try_claim():
            barrier.wait()
            result = registry.claim_finalization(42)
            results.append(result)

        t1 = threading.Thread(target=try_claim)
        t2 = threading.Thread(target=try_claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one True and one False
        assert sorted(results) == [False, True]

    def test_concurrent_register_and_get(self):
        """Concurrent register and get operations don't crash."""
        registry = MonitoringRuntimeRegistry()
        errors: list[str] = []

        def register_workers(start_id: int):
            for i in range(50):
                mid = start_id + i
                worker = MagicMock(name=f"worker_{mid}")
                thread = threading.Thread(target=lambda: None)
                registry.register(mid, worker, thread)

        def read_workers(start_id: int):
            for i in range(50):
                mid = start_id + i
                # May return None or worker — both valid during concurrent writes
                registry.get_worker(mid)
                registry.get_thread(mid)

        threads = []
        for base in range(0, 200, 50):
            t = threading.Thread(target=register_workers, args=(base,))
            threads.append(t)
            t = threading.Thread(target=read_workers, args=(base,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # If we got here without deadlock or exception, concurrency is safe
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Test: analysis claims + device-global live-thread guard — Spec 020
# ---------------------------------------------------------------------------


class TestAnalysisClaimsSpec020:
    """Deferred-analysis claims are separate from finalization claims, and the
    device-global guard (has_any_live_thread) reflects live capture/analysis
    threads only (ready_for_analysis sessions without a thread do not count).
    """

    def test_claim_analysis_first_true_second_false(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_analysis(1) is True
        assert registry.claim_analysis(1) is False

    def test_release_analysis_allows_new_claim(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_analysis(1) is True
        registry.release_analysis(1)
        assert registry.claim_analysis(1) is True

    def test_release_analysis_is_idempotent(self):
        registry = MonitoringRuntimeRegistry()
        # Releasing a non-existent claim must not raise.
        registry.release_analysis(99)
        registry.release_analysis(99)
        assert registry.is_analysis_claimed(99) is False

    def test_analysis_and_finalization_claims_are_independent(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_finalization(1) is True
        # An active finalization claim must NOT block an analysis claim.
        assert registry.claim_analysis(1) is True
        assert registry.is_finalization_claimed(1) is True
        assert registry.is_analysis_claimed(1) is True
        # Releasing one must not release the other.
        registry.release_finalization(1)
        assert registry.is_finalization_claimed(1) is False
        assert registry.is_analysis_claimed(1) is True

    def test_remove_discards_analysis_claim(self):
        registry = MonitoringRuntimeRegistry()
        registry.claim_analysis(1)
        registry.remove(1)
        assert registry.is_analysis_claimed(1) is False

    def test_concurrent_claim_analysis_single_winner(self):
        registry = MonitoringRuntimeRegistry()
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(registry.claim_analysis(42))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 7

    def test_has_any_live_thread_false_when_no_threads(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.has_any_live_thread() is False

    def test_has_any_live_thread_true_with_live_thread(self):
        registry = MonitoringRuntimeRegistry()
        stop = threading.Event()
        t = threading.Thread(target=lambda: stop.wait(5))
        t.start()
        try:
            registry.register(1, MagicMock(), t)
            assert registry.has_any_live_thread() is True
        finally:
            stop.set()
            t.join()
        # After the thread dies, the guard reports no live thread.
        assert registry.has_any_live_thread() is False

    def test_ready_for_analysis_claim_without_thread_is_not_live(self):
        """A ready_for_analysis session holds no worker/thread, so it must NOT
        count as an active hardware phase in the device-global guard."""
        registry = MonitoringRuntimeRegistry()
        # Simulate a ready_for_analysis monitoring: no register() call at all.
        assert registry.has_any_live_thread() is False


# ---------------------------------------------------------------------------
# Test: device-global capture/analysis coordination — Spec 020 (Task 3B)
# ---------------------------------------------------------------------------


class TestDeviceGlobalCoordinationSpec020:
    """has_active_capture() is GLOBAL (module-independent) and claim_global_analysis
    enforces at most one heavy analysis on the whole device.
    """

    def test_has_active_capture_false_initially(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.has_active_capture() is False

    def test_mark_capture_active_requires_live_thread(self):
        """A capture mark without a live thread is pruned (stale)."""
        registry = MonitoringRuntimeRegistry()
        registry.mark_capture_active(1)
        # No live thread registered for id 1 -> considered stale -> not active.
        assert registry.has_active_capture() is False

    def test_has_active_capture_true_with_live_capture_thread(self):
        registry = MonitoringRuntimeRegistry()
        stop = threading.Event()
        t = threading.Thread(target=lambda: stop.wait(5))
        t.start()
        try:
            registry.register(1, MagicMock(), t)
            registry.mark_capture_active(1)
            assert registry.has_active_capture() is True
        finally:
            stop.set()
            t.join()
        # After the capture thread dies the stale mark is pruned.
        assert registry.has_active_capture() is False

    def test_has_active_capture_is_module_independent(self):
        """A capture on ANY module makes has_active_capture() True (global)."""
        registry = MonitoringRuntimeRegistry()
        stop = threading.Event()
        t = threading.Thread(target=lambda: stop.wait(5))
        t.start()
        try:
            # monitoring id 77 belongs to some other module; still counts globally.
            registry.register(77, MagicMock(), t)
            registry.mark_capture_active(77)
            assert registry.has_active_capture() is True
        finally:
            stop.set()
            t.join()

    def test_clear_capture_active_clears_mark(self):
        registry = MonitoringRuntimeRegistry()
        stop = threading.Event()
        t = threading.Thread(target=lambda: stop.wait(5))
        t.start()
        try:
            registry.register(1, MagicMock(), t)
            registry.mark_capture_active(1)
            assert registry.has_active_capture() is True
            registry.clear_capture_active(1)
            assert registry.has_active_capture() is False
        finally:
            stop.set()
            t.join()

    def test_claim_global_analysis_first_true_second_false(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_global_analysis(1) is True
        # A DIFFERENT monitoring cannot take the single device slot.
        assert registry.claim_global_analysis(2) is False
        assert registry.is_global_analysis_active() is True

    def test_release_global_analysis_frees_slot(self):
        registry = MonitoringRuntimeRegistry()
        registry.claim_global_analysis(1)
        registry.release_global_analysis(1)
        assert registry.is_global_analysis_active() is False
        assert registry.claim_global_analysis(2) is True

    def test_release_global_analysis_owner_guarded(self):
        """Only the owner can release the device-global slot."""
        registry = MonitoringRuntimeRegistry()
        registry.claim_global_analysis(1)
        # A non-owner release is a no-op.
        registry.release_global_analysis(2)
        assert registry.is_global_analysis_active() is True
        registry.release_global_analysis(1)
        assert registry.is_global_analysis_active() is False

    def test_remove_releases_global_slot_and_capture_mark(self):
        registry = MonitoringRuntimeRegistry()
        registry.claim_global_analysis(1)
        registry.mark_capture_active(1)
        registry.remove(1)
        assert registry.is_global_analysis_active() is False
        assert registry.has_active_capture() is False

    def test_concurrent_claim_global_analysis_single_winner(self):
        registry = MonitoringRuntimeRegistry()
        results = []
        barrier = threading.Barrier(8)

        def worker(mid):
            barrier.wait()
            results.append(registry.claim_global_analysis(mid))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 7


# ---------------------------------------------------------------------------
# Test: ATOMIC capture <-> analysis mutual exclusion — Spec 020 (final)
# ---------------------------------------------------------------------------


class TestAtomicCaptureAnalysisExclusion:
    """reserve_capture and claim_global_analysis contend in ONE atomic decision:
    a simultaneous capture-start and analysis-start can never both win.
    """

    def test_reserve_capture_blocks_global_analysis(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.reserve_capture(1) is True
        # A reservation (even before the worker thread starts) blocks analysis.
        assert registry.claim_global_analysis(2) is False
        assert registry.has_active_capture() is True

    def test_global_analysis_blocks_reserve_capture(self):
        registry = MonitoringRuntimeRegistry()
        assert registry.claim_global_analysis(1) is True
        assert registry.reserve_capture(2) is False

    def test_reservation_not_pruned_before_thread_starts(self):
        """A reservation held before any thread registration must persist."""
        registry = MonitoringRuntimeRegistry()
        registry.reserve_capture(5)  # no register() / no thread yet
        assert registry.has_active_capture() is True
        assert registry.claim_global_analysis(9) is False

    def test_release_reservation_frees_device(self):
        registry = MonitoringRuntimeRegistry()
        registry.reserve_capture(1)
        registry.release_capture_reservation(1)
        assert registry.has_active_capture() is False
        assert registry.claim_global_analysis(2) is True

    def test_clear_capture_active_also_clears_reservation(self):
        registry = MonitoringRuntimeRegistry()
        registry.reserve_capture(1)
        registry.clear_capture_active(1)
        assert registry.has_active_capture() is False

    def test_concurrent_capture_and_analysis_never_both_win(self):
        """N threads race: half try reserve_capture, half try claim_global_analysis
        on a fresh device. At most ONE side may succeed; both winning is forbidden.
        Repeated many times to shake out the race."""
        for _ in range(200):
            registry = MonitoringRuntimeRegistry()
            barrier = threading.Barrier(2)
            results = {}

            def do_capture():
                barrier.wait()
                results["capture"] = registry.reserve_capture(1)

            def do_analysis():
                barrier.wait()
                results["analysis"] = registry.claim_global_analysis(2)

            t1 = threading.Thread(target=do_capture)
            t2 = threading.Thread(target=do_analysis)
            t1.start(); t2.start()
            t1.join(); t2.join()

            # Never both True (mutual exclusion). Coercion because both are bools.
            assert not (results["capture"] and results["analysis"]), (
                f"both won: {results}"
            )
            # At least one must win on an idle device (no deadlock/starvation).
            assert results["capture"] or results["analysis"]
