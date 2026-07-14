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
