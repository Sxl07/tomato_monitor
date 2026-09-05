"""Property-based test: analysis-claim concurrency (Spec 020).

Testing framework: pytest + hypothesis
Minimum examples: 100 per property

# Feature: 020-deferred-manual-analysis-workflow, Property 2
"""

from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from src.application.services.monitoring_runtime_registry import MonitoringRuntimeRegistry


@settings(max_examples=100, deadline=None)
@given(n=st.integers(min_value=2, max_value=16))
def test_property_2_exactly_one_analysis_claim_winner(n):
    """Property 2: for any number N of concurrent claim_analysis calls on the
    same monitoring, exactly one succeeds; the rest fail without altering the
    granted claim.

    # Feature: 020-deferred-manual-analysis-workflow, Property 2
    """
    registry = MonitoringRuntimeRegistry()
    monitoring_id = 7
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        got = registry.claim_analysis(monitoring_id)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == n - 1
    # The single claim remains held.
    assert registry.is_analysis_claimed(monitoring_id) is True
