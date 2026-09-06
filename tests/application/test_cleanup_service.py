"""Directed tests for CleanupService (Spec 021, Task 11).

Deferred physical cleanup of OUTPUTS_DIR artifacts driven by the durable
Deletion_Outbox. Pure unit tests with in-memory fakes and a tmp_path OUTPUTS_DIR
— NO SQLAlchemy, network, hardware, camera, or vision.

Covers:
- Retention gating: only entries whose Retention_Window elapsed are processed;
  a not-yet-elapsed entry is skipped.
- Only local_delete_status='completed' + cleanup_status='pending' entries are
  eligible (delegated to get_pending_cleanup_entries).
- relative_path is resolved via validate_safe_path against OUTPUTS_DIR with NO
  'outputs/' prefix; the resolved path stays inside OUTPUTS_DIR.
- Existing artifact removed -> marked 'done'; entry -> cleanup_status='done'.
- Non-existent path -> idempotent success ('done'), no removal error.
- Individual removal failure -> durable 'error' (retryable), remaining artifacts
  still processed, entry NOT marked done.
- cleanup_status='done' only when ALL artifact rows are 'done'.
- No dependency on Supabase / remote status ('synced' irrelevant).

Requirements: 13 (Retention_Window, deferred physical cleanup).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pytest

from src.application.interfaces.deletion_outbox_port import (
    DeletionOutboxEntry,
    OutboxLocalArtifactRow,
)
from src.application.services.cleanup_service import CleanupService
from src.infrastructure.security.path_sanitizer import validate_safe_path


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _entry(
    entry_id: int,
    *,
    deleted_at: Optional[datetime],
    local_delete_status: str = "completed",
    cleanup_status: str = "pending",
    status: str = "pending",
) -> DeletionOutboxEntry:
    return DeletionOutboxEntry(
        id=entry_id,
        entity_type="monitoring",
        entity_local_id=entry_id,
        remote_table="monitorings",
        remote_id=None,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        status=status,
        local_delete_status=local_delete_status,
        cleanup_status=cleanup_status,
        deleted_at=deleted_at,
        last_error=None,
        retry_count=0,
    )


class FakeOutbox:
    """In-memory outbox exposing only the cleanup-related read/write methods."""

    def __init__(self) -> None:
        self._entries: List[DeletionOutboxEntry] = []
        self._artifacts: dict[int, List[OutboxLocalArtifactRow]] = {}
        self.cleanup_done: list[int] = []
        self.artifact_marks: list[tuple[int, str, Optional[str]]] = []

    def add_entry(self, entry: DeletionOutboxEntry) -> None:
        self._entries.append(entry)
        self._artifacts.setdefault(entry.id, [])

    def add_artifact(
        self, outbox_id: int, artifact_id: int, relative_path: str, status: str = "pending"
    ) -> None:
        self._artifacts.setdefault(outbox_id, []).append(
            OutboxLocalArtifactRow(
                id=artifact_id, relative_path=relative_path, status=status
            )
        )

    # --- port methods used by CleanupService ---

    def get_pending_cleanup_entries(self) -> List[DeletionOutboxEntry]:
        return [
            e
            for e in self._entries
            if e.local_delete_status == "completed" and e.cleanup_status == "pending"
        ]

    def get_local_artifacts_for_entry(self, outbox_id: int) -> List[OutboxLocalArtifactRow]:
        return list(self._artifacts.get(outbox_id, []))

    def mark_local_artifact_status(
        self, artifact_id: int, status: str, last_error: Optional[str] = None
    ) -> None:
        self.artifact_marks.append((artifact_id, status, last_error))
        for rows in self._artifacts.values():
            for row in rows:
                if row.id == artifact_id:
                    row.status = status
                    row.last_error = last_error

    def mark_cleanup_done(self, outbox_id: int) -> None:
        self.cleanup_done.append(outbox_id)
        for e in self._entries:
            if e.id == outbox_id:
                # dataclass is not frozen -> mutate in place for assertions
                object.__setattr__(e, "cleanup_status", "done")


def _now_fn(fixed: datetime):
    return lambda: fixed


def _build(outbox, outputs_dir, hours=24, now=None, remove_path=None):
    return CleanupService(
        deletion_outbox=outbox,
        outputs_dir=Path(outputs_dir),
        retention_window_hours=hours,
        validate_safe_path=validate_safe_path,
        remove_path=remove_path,
        now_fn=_now_fn(now or datetime(2025, 6, 1, tzinfo=timezone.utc)),
    )


# ---------------------------------------------------------------------------
# Retention gating
# ---------------------------------------------------------------------------


def test_entry_within_retention_window_is_skipped(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Deleted only 1h ago; window is 24h -> not elapsed.
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=1)))
    art_dir = tmp_path / "monitorings" / "1"
    art_dir.mkdir(parents=True)
    outbox.add_artifact(1, 10, "monitorings/1")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.entries_examined == 1
    assert result.artifacts_removed == 0
    assert outbox.cleanup_done == []
    assert art_dir.exists()  # untouched


def test_entry_after_retention_window_is_processed(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=25)))
    art_dir = tmp_path / "monitorings" / "1"
    art_dir.mkdir(parents=True)
    (art_dir / "file.txt").write_text("x")
    outbox.add_artifact(1, 10, "monitorings/1")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.artifacts_removed == 1
    assert not art_dir.exists()
    assert outbox.cleanup_done == [1]


# ---------------------------------------------------------------------------
# Eligibility (delegated) + no Supabase dependency
# ---------------------------------------------------------------------------


def test_only_completed_pending_entries_processed(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    # Not completed -> excluded by the fake's query.
    outbox.add_entry(
        _entry(1, deleted_at=now - timedelta(hours=48), local_delete_status="prepared")
    )
    # Already cleaned -> excluded.
    outbox.add_entry(
        _entry(2, deleted_at=now - timedelta(hours=48), cleanup_status="done")
    )
    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.entries_examined == 0
    assert outbox.cleanup_done == []


def test_cleanup_independent_of_remote_status_synced(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    # Remote status is 'pending' (NOT synced) but cleanup still proceeds.
    outbox.add_entry(
        _entry(1, deleted_at=now - timedelta(hours=48), status="pending")
    )
    art_dir = tmp_path / "monitorings" / "1"
    art_dir.mkdir(parents=True)
    outbox.add_artifact(1, 10, "monitorings/1")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.artifacts_removed == 1
    assert outbox.cleanup_done == [1]


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------


def test_relative_path_resolved_within_outputs_dir_no_prefix(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))
    art_dir = tmp_path / "monitorings" / "7"
    art_dir.mkdir(parents=True)
    outbox.add_artifact(1, 10, "monitorings/7")

    removed: list[Path] = []
    svc = _build(outbox, tmp_path, hours=24, now=now, remove_path=removed.append)
    svc.run()

    # Resolved path is inside OUTPUTS_DIR and does NOT contain an 'outputs/' prefix.
    assert len(removed) == 1
    resolved = removed[0]
    assert resolved == (tmp_path / "monitorings" / "7").resolve()
    resolved.relative_to(tmp_path.resolve())  # raises if escaped


def test_nonexistent_path_is_idempotent_success(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))
    # No directory created on disk.
    outbox.add_artifact(1, 10, "monitorings/999")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.artifacts_removed == 0  # nothing to remove
    assert result.artifacts_failed == 0
    assert (10, "done", None) in outbox.artifact_marks
    assert outbox.cleanup_done == [1]


# ---------------------------------------------------------------------------
# Failures + partial completion
# ---------------------------------------------------------------------------


def test_individual_failure_is_durable_and_others_still_processed(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))

    ok_dir = tmp_path / "monitorings" / "1"
    ok_dir.mkdir(parents=True)
    bad_dir = tmp_path / "monitorings" / "2"
    bad_dir.mkdir(parents=True)
    outbox.add_artifact(1, 10, "monitorings/1")
    outbox.add_artifact(1, 11, "monitorings/2")

    def _remove(path: Path) -> None:
        if path == (tmp_path / "monitorings" / "2").resolve():
            raise OSError("permission denied")
        # Remove the ok one for real.
        import shutil

        shutil.rmtree(path)

    svc = _build(outbox, tmp_path, hours=24, now=now, remove_path=_remove)
    result = svc.run()

    # One removed, one failed; both were attempted.
    assert result.artifacts_removed == 1
    assert result.artifacts_failed == 1
    marks = {(a, s) for a, s, _ in outbox.artifact_marks}
    assert (10, "done") in marks
    assert (11, "error") in marks
    # Entry NOT completed because not all artifacts are done.
    assert outbox.cleanup_done == []


def test_cleanup_done_only_when_all_artifacts_done(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))
    # One already-done artifact (skipped) + one pending that will be removed.
    d = tmp_path / "monitorings" / "1"
    d.mkdir(parents=True)
    outbox.add_artifact(1, 10, "monitorings/already", status="done")
    outbox.add_artifact(1, 11, "monitorings/1", status="pending")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    svc.run()

    assert outbox.cleanup_done == [1]


def test_error_artifact_from_previous_run_is_retried(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))
    d = tmp_path / "monitorings" / "1"
    d.mkdir(parents=True)
    # Previously failed -> must be reprocessed now.
    outbox.add_artifact(1, 10, "monitorings/1", status="error")

    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()

    assert result.artifacts_removed == 1
    assert (10, "done", None) in outbox.artifact_marks
    assert outbox.cleanup_done == [1]


def test_deleted_at_none_is_skipped(tmp_path):
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=None))
    svc = _build(outbox, tmp_path, hours=24, now=now)
    result = svc.run()
    assert result.entries_examined == 1
    assert outbox.cleanup_done == []


def test_done_persistence_failure_keeps_parent_pending_then_succeeds(tmp_path):
    """A physical remove that succeeds but whose 'done' persistence FAILS must
    NOT complete the parent; the next run (path already gone) persists 'done'
    idempotently and only then the parent becomes cleanup_status='done'."""
    outbox = FakeOutbox()
    now = datetime(2025, 6, 2, tzinfo=timezone.utc)
    outbox.add_entry(_entry(1, deleted_at=now - timedelta(hours=48)))
    art_dir = tmp_path / "monitorings" / "1"
    art_dir.mkdir(parents=True)
    (art_dir / "file.txt").write_text("x")
    outbox.add_artifact(1, 10, "monitorings/1")

    # Make the FIRST mark_local_artifact_status(..., "done") raise, so the
    # physical remove happens but the 'done' cannot be persisted.
    real_mark = outbox.mark_local_artifact_status
    calls = {"n": 0}

    def flaky_mark(artifact_id, status, last_error=None):
        if status == "done":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated persistence failure on done")
        return real_mark(artifact_id, status, last_error=last_error)

    outbox.mark_local_artifact_status = flaky_mark  # type: ignore[assignment]

    # --- First run: physical removal happens, but 'done' fails to persist ---
    svc = _build(outbox, tmp_path, hours=24, now=now)
    result1 = svc.run()

    assert result1.artifacts_removed == 1  # file was physically removed
    assert not art_dir.exists()
    assert result1.artifacts_failed == 1  # done could not be persisted
    # Parent NOT completed; artifact remains retryable (still 'pending').
    assert outbox.cleanup_done == []
    assert outbox._artifacts[1][0].status == "pending"

    # --- Second run: path already gone (idempotent success); done persists ---
    result2 = svc.run()

    assert result2.artifacts_removed == 0  # nothing left to remove
    assert result2.artifacts_failed == 0
    assert outbox._artifacts[1][0].status == "done"
    assert outbox.cleanup_done == [1]  # parent finally completed
