"""Unit tests for SyncRuntimeState.

Validates:
- Initial state is idle
- try_acquire mutual exclusion
- Second acquire does not modify active process
- release allows new acquire
- release(None) works
- update_progress sets phase/processed/total
- release does not clear progress
- New acquire resets progress/errors but preserves last_result
- errors returned as copy (isolation)
- Instances have independent locks/lists
- Concurrent acquire: exactly one True among many threads
- Property 15: exception + finally release always leaves state unlocked

Spec 017 — Supabase Remote Sync.
Requirements: 8.5, 8.9, 21.3
"""

import threading

import pytest
from hypothesis import given, strategies as st

from src.application.services.sync_runtime_state import SyncRuntimeState


# ===========================================================================
# Initial state
# ===========================================================================


class TestInitialState:
    """New instance starts idle."""

    def test_initial_status(self):
        state = SyncRuntimeState()
        status = state.get_status()
        assert status["is_syncing"] is False
        assert status["phase"] == ""
        assert status["processed"] == 0
        assert status["total"] == 0
        assert status["errors"] == []
        assert status["last_result"] is None


# ===========================================================================
# try_acquire
# ===========================================================================


class TestTryAcquire:
    """try_acquire provides mutual exclusion."""

    def test_first_acquire_succeeds(self):
        state = SyncRuntimeState()
        assert state.try_acquire() is True
        assert state.get_status()["is_syncing"] is True

    def test_second_acquire_fails(self):
        state = SyncRuntimeState()
        state.try_acquire()
        assert state.try_acquire() is False

    def test_second_acquire_does_not_modify_active(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.update_progress("snapshots", 3, 10)

        # Second acquire attempt
        state.try_acquire()

        status = state.get_status()
        assert status["phase"] == "snapshots"
        assert status["processed"] == 3
        assert status["total"] == 10
        assert status["is_syncing"] is True


# ===========================================================================
# release
# ===========================================================================


class TestRelease:
    """release clears syncing flag and stores result."""

    def test_release_with_result(self):
        state = SyncRuntimeState()
        state.try_acquire()
        result = {"success": True, "entities_synced": 5}
        state.release(result)

        status = state.get_status()
        assert status["is_syncing"] is False
        assert status["last_result"] == result

    def test_release_none(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.release(None)

        status = state.get_status()
        assert status["is_syncing"] is False
        assert status["last_result"] is None

    def test_release_allows_new_acquire(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.release({"success": True})
        assert state.try_acquire() is True


# ===========================================================================
# update_progress
# ===========================================================================


class TestUpdateProgress:
    """update_progress sets phase/processed/total."""

    def test_sets_values(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.update_progress("monitorings", 4, 12)

        status = state.get_status()
        assert status["phase"] == "monitorings"
        assert status["processed"] == 4
        assert status["total"] == 12
        assert status["is_syncing"] is True


# ===========================================================================
# Progress persistence across release
# ===========================================================================


class TestProgressPersistence:
    """release does not clear progress; new acquire does."""

    def test_release_preserves_progress(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.update_progress("activity_logs", 8, 8)
        state.release({"success": True})

        status = state.get_status()
        assert status["phase"] == "activity_logs"
        assert status["processed"] == 8
        assert status["total"] == 8
        assert status["is_syncing"] is False

    def test_new_acquire_resets_progress(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.update_progress("snapshots", 9, 10)
        state.release({"success": True})

        # New acquire
        state.try_acquire()
        status = state.get_status()
        assert status["phase"] == ""
        assert status["processed"] == 0
        assert status["total"] == 0
        assert status["errors"] == []

    def test_new_acquire_preserves_last_result(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.release({"success": True, "count": 7})

        state.try_acquire()
        assert state.get_status()["last_result"] == {"success": True, "count": 7}


# ===========================================================================
# Errors copy isolation
# ===========================================================================


class TestErrorsIsolation:
    """get_status returns a copy of errors list."""

    def test_external_mutation_does_not_affect_internal(self):
        state = SyncRuntimeState(errors=["original"])
        status = state.get_status()
        status["errors"].append("outside")

        assert state.get_status()["errors"] == ["original"]


# ===========================================================================
# Instance independence
# ===========================================================================


class TestInstanceIndependence:
    """Multiple instances have independent locks and lists."""

    def test_independent_acquire(self):
        a = SyncRuntimeState()
        b = SyncRuntimeState()
        assert a.try_acquire() is True
        assert b.try_acquire() is True

    def test_independent_errors(self):
        a = SyncRuntimeState()
        b = SyncRuntimeState()
        a.try_acquire()
        # Directly mutate for independence test
        with a._lock:
            a.errors.append("a-error")
        assert b.get_status()["errors"] == []


# ===========================================================================
# Concurrency: atomic acquire
# ===========================================================================


class TestConcurrentAcquire:
    """Only one thread can acquire among many simultaneous attempts."""

    def test_exactly_one_acquire(self):
        state = SyncRuntimeState()
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        results = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            got = state.try_acquire()
            with results_lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "Thread did not finish"

        assert results.count(True) == 1
        assert results.count(False) == num_threads - 1
        assert state.get_status()["is_syncing"] is True

        # Cleanup
        state.release(None)
        assert state.try_acquire() is True


# ===========================================================================
# Concurrent status reads
# ===========================================================================


class TestConcurrentReads:
    """Concurrent get_status and update_progress do not crash."""

    def test_no_exception(self):
        state = SyncRuntimeState()
        state.try_acquire()
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                state.update_progress("phase", i, 100)
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    s = state.get_status()
                    assert "is_syncing" in s
                    assert "phase" in s
                    assert "processed" in s
                except Exception as e:
                    errors.append(str(e))

        writer_t = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(5)]

        writer_t.start()
        for r in readers:
            r.start()

        # Let them run briefly
        stop.set()

        writer_t.join(timeout=2.0)
        for r in readers:
            r.join(timeout=2.0)

        assert errors == []
        state.release(None)


# ===========================================================================
# Property 15: exception + finally release
# ===========================================================================


class TestProperty15ExceptionRelease:
    """Exception in sync body + finally release always leaves state unlocked."""

    def test_exception_before_progress(self):
        state = SyncRuntimeState()
        state.try_acquire()
        try:
            raise ValueError("before sync")
        except ValueError:
            pass
        finally:
            state.release(None)

        assert state.get_status()["is_syncing"] is False
        assert state.try_acquire() is True
        state.release(None)

    def test_exception_after_progress(self):
        state = SyncRuntimeState()
        state.try_acquire()
        try:
            state.update_progress("snapshots", 2, 10)
            raise RuntimeError("mid sync")
        except RuntimeError:
            pass
        finally:
            state.release(None)

        status = state.get_status()
        assert status["is_syncing"] is False
        assert status["phase"] == "snapshots"
        assert status["processed"] == 2
        assert state.try_acquire() is True
        state.release(None)

    @given(
        phase=st.text(max_size=30),
        processed=st.integers(min_value=0, max_value=1000),
        total=st.integers(min_value=0, max_value=1000),
    )
    def test_property_always_releases(self, phase, processed, total):
        state = SyncRuntimeState()
        assert state.try_acquire() is True
        try:
            state.update_progress(phase, processed, total)
            raise RuntimeError("synthetic")
        except RuntimeError:
            pass
        finally:
            state.release(None)

        assert state.get_status()["is_syncing"] is False
        assert state.try_acquire() is True
        state.release(None)


# ===========================================================================
# Recovery mutual exclusion (Spec 022, block E)
# ===========================================================================


class TestRecoveryMutualExclusion:
    """try_acquire_recovery / release_recovery + sync mutual exclusion."""

    def test_idle_acquire_recovery_succeeds(self):
        state = SyncRuntimeState()
        assert state.try_acquire_recovery() is True
        assert state.get_active_operation() == "recovery"

    def test_second_recovery_fails(self):
        state = SyncRuntimeState()
        assert state.try_acquire_recovery() is True
        assert state.try_acquire_recovery() is False

    def test_sync_active_blocks_recovery(self):
        state = SyncRuntimeState()
        assert state.try_acquire() is True
        assert state.try_acquire_recovery() is False
        assert state.get_active_operation() == "sync"

    def test_recovery_active_blocks_sync(self):
        state = SyncRuntimeState()
        assert state.try_acquire_recovery() is True
        assert state.try_acquire() is False
        assert state.get_active_operation() == "recovery"

    def test_release_recovery_allows_sync(self):
        state = SyncRuntimeState()
        state.try_acquire_recovery()
        state.release_recovery()
        assert state.try_acquire() is True
        assert state.get_active_operation() == "sync"

    def test_release_sync_allows_recovery(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.release(None)
        assert state.try_acquire_recovery() is True
        assert state.get_active_operation() == "recovery"

    def test_release_recovery_is_idempotent(self):
        state = SyncRuntimeState()
        state.try_acquire_recovery()
        state.release_recovery()
        state.release_recovery()  # no error, still idle
        assert state.get_active_operation() is None
        assert state.try_acquire_recovery() is True

    def test_get_active_operation_none_when_idle(self):
        state = SyncRuntimeState()
        assert state.get_active_operation() is None

    def test_recovery_does_not_touch_sync_progress(self):
        state = SyncRuntimeState()
        state.try_acquire()
        state.update_progress("snapshots", 5, 10)
        state.release({"success": True})
        # Now recovery runs; sync progress snapshot must be untouched.
        state.try_acquire_recovery()
        status = state.get_status()
        assert status["is_syncing"] is False
        assert status["phase"] == "snapshots"
        assert status["processed"] == 5
        assert status["total"] == 10
        assert status["last_result"] == {"success": True}
        state.release_recovery()

    def test_get_status_never_exposes_recovery(self):
        state = SyncRuntimeState()
        state.try_acquire_recovery()
        status = state.get_status()
        assert "is_recovering" not in status
        assert "recovery_progress" not in status
        assert "recovery_result" not in status

    def test_concurrent_sync_vs_recovery_exactly_one(self):
        state = SyncRuntimeState()
        num_threads = 20
        barrier = threading.Barrier(num_threads)
        results = []
        results_lock = threading.Lock()

        def worker(index):
            barrier.wait()
            # Half attempt sync, half attempt recovery.
            if index % 2 == 0:
                got = state.try_acquire()
            else:
                got = state.try_acquire_recovery()
            with results_lock:
                results.append(got)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive()

        # Exactly one operation (sync OR recovery) won the lock.
        assert results.count(True) == 1
        assert state.get_active_operation() in ("sync", "recovery")
