"""Tests for build_snapshot_gallery — annotated snapshot preference with raw fallback.

Uses tmp_path to avoid writing to real outputs/.
Patches app.context_builders.OUTPUTS_DIR.
No cv2, numpy, FastAPI TestClient, or browser required.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _make_snapshot(
    id: int = 1,
    monitoring_id: int = 1,
    frame_index: int = 0,
    image_path: str = "outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg",
    has_detections: bool = True,
):
    return SimpleNamespace(
        id=id,
        monitoring_id=monitoring_id,
        frame_index=frame_index,
        image_path=image_path,
        has_detections=has_detections,
    )


@pytest.fixture
def outputs_dir(tmp_path, monkeypatch):
    """Set up a temporary OUTPUTS_DIR and patch it into context_builders."""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr("app.context_builders.OUTPUTS_DIR", outputs)
    return outputs


def _create_annotated(outputs_dir: Path, monitoring_id: int, frame_index: int):
    """Create a fake annotated snapshot file on disk."""
    d = outputs_dir / "monitorings" / str(monitoring_id) / "annotated_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    filename = f"snapshot_{frame_index:06d}.jpg"
    (d / filename).write_bytes(b"fake")


def _create_raw(outputs_dir: Path, monitoring_id: int, frame_index: int):
    """Create a fake raw snapshot file on disk."""
    d = outputs_dir / "monitorings" / str(monitoring_id) / "snapshots" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    filename = f"snapshot_{frame_index:06d}.jpg"
    (d / filename).write_bytes(b"fake")


# ---------------------------------------------------------------------------
# 1. Annotated snapshot preferred when it exists
# ---------------------------------------------------------------------------


class TestAnnotatedPreferred:
    def test_annotated_url_when_file_exists(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        _create_annotated(outputs_dir, 1, 3)
        snap = _make_snapshot(frame_index=3, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000003.jpg")

        result = build_snapshot_gallery([snap], monitoring_id=1)

        assert len(result) == 1
        assert result[0].image_url == "/snapshots/1/annotated_snapshots/snapshot_000003.jpg"


# ---------------------------------------------------------------------------
# 2. Fallback to raw when annotated doesn't exist
# ---------------------------------------------------------------------------


class TestRawFallback:
    def test_raw_url_when_no_annotated(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        _create_raw(outputs_dir, 1, 3)
        snap = _make_snapshot(frame_index=3, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000003.jpg")

        result = build_snapshot_gallery([snap], monitoring_id=1)

        assert len(result) == 1
        assert result[0].image_url == "/snapshots/1/snapshots/raw/snapshot_000003.jpg"


# ---------------------------------------------------------------------------
# 3. Filename uses 6-digit padding
# ---------------------------------------------------------------------------


class TestPadding:
    def test_six_digit_padding(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snap = _make_snapshot(frame_index=7, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000007.jpg")
        result = build_snapshot_gallery([snap], monitoring_id=1)

        assert "snapshot_000007.jpg" in result[0].image_url


# ---------------------------------------------------------------------------
# 4. Snapshot without detections excluded
# ---------------------------------------------------------------------------


class TestNoDetectionsExcluded:
    def test_has_detections_false_excluded(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snap = _make_snapshot(has_detections=False)
        result = build_snapshot_gallery([snap], monitoring_id=1)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 5. Path traversal skipped
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_traversal_path_omitted(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snap = _make_snapshot(image_path="outputs/monitorings/1/../../etc/passwd")
        result = build_snapshot_gallery([snap], monitoring_id=1)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 6. Invalid image_path with existing annotated still omitted
# ---------------------------------------------------------------------------


class TestInvalidPathWithAnnotated:
    def test_invalid_path_skips_even_if_annotated_exists(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        _create_annotated(outputs_dir, 1, 0)
        snap = _make_snapshot(image_path="outputs/monitorings/1/../../secret.jpg")

        result = build_snapshot_gallery([snap], monitoring_id=1)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 7. Gallery sorted by frame_index ascending
# ---------------------------------------------------------------------------


class TestSortOrder:
    def test_sorted_by_frame_index(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snaps = [
            _make_snapshot(id=1, frame_index=5, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000005.jpg"),
            _make_snapshot(id=2, frame_index=2, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000002.jpg"),
            _make_snapshot(id=3, frame_index=8, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000008.jpg"),
        ]

        result = build_snapshot_gallery(snaps, monitoring_id=1)

        assert [t.frame_index for t in result] == [2, 5, 8]


# ---------------------------------------------------------------------------
# 8. Mixed annotated and raw
# ---------------------------------------------------------------------------


class TestMixed:
    def test_mixed_annotated_and_raw(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        _create_annotated(outputs_dir, 1, 0)
        # frame_index=1 has no annotated
        _create_raw(outputs_dir, 1, 1)

        snaps = [
            _make_snapshot(id=1, frame_index=0, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg"),
            _make_snapshot(id=2, frame_index=1, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000001.jpg"),
            _make_snapshot(id=3, frame_index=2, has_detections=False, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000002.jpg"),
        ]

        result = build_snapshot_gallery(snaps, monitoring_id=1)

        assert len(result) == 2
        assert result[0].image_url == "/snapshots/1/annotated_snapshots/snapshot_000000.jpg"
        assert result[1].image_url == "/snapshots/1/snapshots/raw/snapshot_000001.jpg"


# ---------------------------------------------------------------------------
# 9. Old incorrect URL format not generated
# ---------------------------------------------------------------------------


class TestNoOldUrl:
    def test_no_old_url_format(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snap = _make_snapshot(frame_index=3, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000003.jpg")
        result = build_snapshot_gallery([snap], monitoring_id=1)

        # The old format was /snapshots/1/snapshots/snapshot_3.jpg (no raw, no padding)
        assert "/snapshots/1/snapshots/snapshot_3.jpg" not in result[0].image_url


# ---------------------------------------------------------------------------
# 10. Input snapshots not mutated
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_not_mutated(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        snap = _make_snapshot(frame_index=0, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg")
        original_path = snap.image_path

        build_snapshot_gallery([snap], monitoring_id=1)

        assert snap.image_path == original_path
        assert snap.has_detections is True


# ---------------------------------------------------------------------------
# 11. Raw doesn't exist physically but valid path → still returns raw URL
# ---------------------------------------------------------------------------


class TestRawNotOnDisk:
    def test_raw_fallback_even_if_file_missing(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        # Neither annotated nor raw exists on disk, but path is valid
        snap = _make_snapshot(frame_index=5, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000005.jpg")

        result = build_snapshot_gallery([snap], monitoring_id=1)

        assert len(result) == 1
        assert result[0].image_url == "/snapshots/1/snapshots/raw/snapshot_000005.jpg"


# ---------------------------------------------------------------------------
# 12. URLs don't contain absolute system paths
# ---------------------------------------------------------------------------


class TestNoAbsolutePaths:
    def test_urls_are_relative(self, outputs_dir):
        from app.context_builders import build_snapshot_gallery

        _create_annotated(outputs_dir, 1, 0)
        snap = _make_snapshot(frame_index=0, image_path="outputs/monitorings/1/snapshots/raw/snapshot_000000.jpg")

        result = build_snapshot_gallery([snap], monitoring_id=1)

        for t in result:
            assert t.image_url.startswith("/snapshots/")
            assert "C:" not in t.image_url
            assert str(outputs_dir) not in t.image_url


# ---------------------------------------------------------------------------
# 13. Function doesn't access repositories or DB
# ---------------------------------------------------------------------------


class TestNoDbAccess:
    def test_no_repository_calls(self, outputs_dir):
        """build_snapshot_gallery only uses its arguments, not repos."""
        from app.context_builders import build_snapshot_gallery
        import inspect

        sig = inspect.signature(build_snapshot_gallery)
        params = list(sig.parameters.keys())
        # Only takes snapshots and monitoring_id
        assert params == ["snapshots", "monitoring_id"]


# ---------------------------------------------------------------------------
# 14. Static verification: main.py mounts /snapshots → outputs/monitorings
# ---------------------------------------------------------------------------


class TestMainMountExists:
    def test_snapshots_mount_in_main(self):
        main_path = Path(__file__).resolve().parents[2] / "app" / "main.py"
        content = main_path.read_text(encoding="utf-8")

        assert '/snapshots"' in content or "'/snapshots'" in content
        assert "outputs/monitorings" in content
