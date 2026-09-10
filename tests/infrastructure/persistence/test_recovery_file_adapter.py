"""Unit tests for LocalRecoveryFileAdapter (Spec 022, block D3.2).

Validates atomic local writes under an outputs base directory:
- Atomic successful write creates parent directories with exact bytes.
- exists() reports presence without modifying the filesystem.
- Absolute paths and traversal (`..`, nested) are rejected (fail closed).
- A write failure leaves no partial final file and cleans up the temp file.
- An existing final file is never removed/truncated.

All tests use tmp_path; nothing touches the real outputs directory.
"""

from pathlib import Path

from src.application.interfaces.recovery_file_port import RecoveryFileResult
from src.infrastructure.persistence.recovery_file_adapter import (
    LocalRecoveryFileAdapter,
)

_REL = "monitorings/12/snapshots/raw/snapshot_000003.jpg"
_CONTENT = b"\xff\xd8\xff-fake-jpeg-bytes"


def _adapter(tmp_path):
    return LocalRecoveryFileAdapter(outputs_dir=tmp_path)


class TestAtomicWrite:
    def test_successful_write_creates_file_with_exact_bytes(self, tmp_path):
        adapter = _adapter(tmp_path)
        result = adapter.write_atomic(_REL, _CONTENT)

        assert isinstance(result, RecoveryFileResult)
        assert result.success is True
        assert result.already_exists is False
        target = tmp_path / _REL
        assert target.is_file()
        assert target.read_bytes() == _CONTENT

    def test_creates_parent_directories(self, tmp_path):
        adapter = _adapter(tmp_path)
        assert not (tmp_path / "monitorings" / "12").exists()
        adapter.write_atomic(_REL, _CONTENT)
        assert (tmp_path / "monitorings" / "12" / "snapshots" / "raw").is_dir()

    def test_exists_true_after_write(self, tmp_path):
        adapter = _adapter(tmp_path)
        assert adapter.exists(_REL) is False
        adapter.write_atomic(_REL, _CONTENT)
        assert adapter.exists(_REL) is True

    def test_no_temp_files_left_after_success(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.write_atomic(_REL, _CONTENT)
        parent = tmp_path / "monitorings" / "12" / "snapshots" / "raw"
        leftovers = [p.name for p in parent.iterdir() if p.name.startswith(".recovery-")]
        assert leftovers == []


class TestPathSafety:
    def test_absolute_path_rejected(self, tmp_path):
        adapter = _adapter(tmp_path)
        result = adapter.write_atomic("/etc/passwd", _CONTENT)
        assert result.success is False
        # Nothing written outside the base dir.
        assert not (tmp_path / "etc").exists()

    def test_traversal_rejected(self, tmp_path):
        adapter = _adapter(tmp_path)
        result = adapter.write_atomic("../escape.jpg", _CONTENT)
        assert result.success is False
        assert not (tmp_path.parent / "escape.jpg").exists()

    def test_nested_traversal_rejected(self, tmp_path):
        adapter = _adapter(tmp_path)
        result = adapter.write_atomic("monitorings/../../escape.jpg", _CONTENT)
        assert result.success is False

    def test_exists_false_for_absolute_path(self, tmp_path):
        adapter = _adapter(tmp_path)
        assert adapter.exists("/etc/passwd") is False

    def test_exists_false_for_traversal(self, tmp_path):
        adapter = _adapter(tmp_path)
        assert adapter.exists("../whatever.jpg") is False


class TestExistingFilePreserved:
    def test_existing_file_not_removed_by_exists(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.write_atomic(_REL, _CONTENT)
        # exists() must not truncate/remove.
        assert adapter.exists(_REL) is True
        assert (tmp_path / _REL).read_bytes() == _CONTENT

    def test_existing_file_not_overwritten(self, tmp_path):
        adapter = _adapter(tmp_path)
        adapter.write_atomic(_REL, _CONTENT)
        result = adapter.write_atomic(_REL, b"different-bytes")
        assert result.success is True
        assert result.already_exists is True
        # Original content preserved (not truncated/overwritten).
        assert (tmp_path / _REL).read_bytes() == _CONTENT


class TestSecondaryContainment:
    """The adapter does not blindly trust validate_safe_path.

    Its final authority is Path.relative_to over resolved paths, so even a
    defective/spoofed helper returning an out-of-base path is rejected.
    """

    def test_helper_returning_external_path_is_rejected(self, tmp_path, monkeypatch):
        import src.infrastructure.persistence.recovery_file_adapter as mod

        base = tmp_path / "outputs"
        base.mkdir()
        # Sibling directory sharing a prefix with the base (str.startswith trap).
        evil = tmp_path / "outputs_evil"
        evil.mkdir()
        escape_target = evil / "escape.jpg"

        def fake_validate_safe_path(relative_path, allowed_base):
            # Deliberately return a path OUTSIDE the base (prefix-similar).
            return escape_target

        monkeypatch.setattr(mod, "validate_safe_path", fake_validate_safe_path)

        adapter = LocalRecoveryFileAdapter(outputs_dir=base)
        result = adapter.write_atomic("safe-looking.jpg", b"payload")

        assert result.success is False
        # Nothing written to the external location, no stray temp file there.
        assert not escape_target.exists()
        leftovers = [p.name for p in evil.iterdir()]
        assert leftovers == []

    def test_helper_returning_external_path_makes_exists_false(
        self, tmp_path, monkeypatch
    ):
        import src.infrastructure.persistence.recovery_file_adapter as mod

        base = tmp_path / "outputs"
        base.mkdir()
        evil = tmp_path / "outputs_evil"
        evil.mkdir()
        (evil / "escape.jpg").write_bytes(b"present-but-external")

        monkeypatch.setattr(
            mod, "validate_safe_path", lambda rel, b: evil / "escape.jpg"
        )

        adapter = LocalRecoveryFileAdapter(outputs_dir=base)
        # Even though the external file exists, containment rejects it.
        assert adapter.exists("escape.jpg") is False


class TestWriteFailureCleanup:
    def test_failure_leaves_no_partial_final_file_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        adapter = _adapter(tmp_path)

        # Force os.replace to fail AFTER the temp file has been written, so we
        # can assert the final file is absent and the temp file is cleaned up.
        import src.infrastructure.persistence.recovery_file_adapter as mod

        def boom(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(mod.os, "replace", boom)

        result = adapter.write_atomic(_REL, _CONTENT)
        assert result.success is False

        target = tmp_path / _REL
        assert not target.exists()  # no partial final file
        parent = tmp_path / "monitorings" / "12" / "snapshots" / "raw"
        # Parent dir may exist (created), but no temp file must remain.
        if parent.exists():
            leftovers = [p.name for p in parent.iterdir() if p.name.startswith(".recovery-")]
            assert leftovers == []
