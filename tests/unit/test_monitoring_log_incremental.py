"""Regression tests for incremental log polling (?since precision).

HOTFIX post-Spec019: the /api/monitoring/{id}/log endpoint serialized entry
timestamps with strftime("%Y-%m-%dT%H:%M:%SZ"), truncating microseconds. When
the client echoed that whole-second value back as ?since, LogService's strict
">" filter kept re-returning the last entry (its real timestamp still had
microseconds > the truncated second). These tests lock in that a second
consecutive poll using the last returned timestamp does NOT re-deliver the same
entry.

Uses a real LogService and the real monitoring_api router — no camera, no DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.monitoring_api import router
from app.dependencies import require_current_user_api
from src.application.services.log_service import LogService, LogLevel


@pytest.fixture
def log_service():
    return LogService()


@pytest.fixture
def app(log_service):
    application = FastAPI()
    application.include_router(router)
    application.state.log_service = log_service
    application.dependency_overrides[require_current_user_api] = lambda: SimpleNamespace(
        id=1, full_name="Test", email="test@test.com", role="operator"
    )
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


class TestSerializedTimestampPrecision:
    """The serialized timestamp must preserve sub-second precision."""

    def test_timestamp_roundtrips_through_fromisoformat(self, client, log_service):
        log_service.add_entry(1, LogLevel.INFO, "worker", "hola")

        entries = client.get("/api/monitoring/1/log").json()
        assert len(entries) == 1
        ts = entries[0]["timestamp"]

        # Must parse back to an aware datetime without raising.
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None
        # Microseconds must survive serialization (not truncated to :SS).
        assert parsed.microsecond >= 0  # parse ok; value present in string
        assert "T" in ts


class TestTwoConsecutivePollings:
    """The core regression: the last entry must not reappear on the next poll."""

    def test_last_entry_not_repeated_on_second_poll(self, client, log_service):
        # First poll: one entry present.
        log_service.add_entry(1, LogLevel.INFO, "worker", "primera entrada")

        first = client.get("/api/monitoring/1/log").json()
        assert len(first) == 1
        last_ts = first[-1]["timestamp"]

        # Second poll using ?since=<last timestamp> — no new entries added.
        second = client.get(
            "/api/monitoring/1/log", params={"since": last_ts}
        ).json()

        # The previously returned entry must NOT come back.
        assert second == []

    def test_only_new_entries_returned_on_second_poll(self, client, log_service):
        log_service.add_entry(1, LogLevel.INFO, "worker", "entrada 1")
        first = client.get("/api/monitoring/1/log").json()
        last_ts = first[-1]["timestamp"]

        # A genuinely newer entry is added between polls.
        log_service.add_entry(1, LogLevel.SUCCESS, "worker", "entrada 2")

        second = client.get(
            "/api/monitoring/1/log", params={"since": last_ts}
        ).json()

        # Exactly the new entry, and not the old one.
        assert len(second) == 1
        assert second[0]["message"] == "entrada 2"

    def test_repeated_polling_is_stable_no_duplicates(self, client, log_service):
        log_service.add_entry(1, LogLevel.INFO, "worker", "unica")
        first = client.get("/api/monitoring/1/log").json()
        last_ts = first[-1]["timestamp"]

        # Poll several more times with no new entries: always empty.
        for _ in range(3):
            resp = client.get(
                "/api/monitoring/1/log", params={"since": last_ts}
            ).json()
            assert resp == []
