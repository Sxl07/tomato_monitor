"""Unit tests for ExportDeletionService (Spec 025).

These tests exercise the service directly, without TestClient, using a fake
in-memory repository and a temporary ``exports_root``. They cover ownership,
status guards, filesystem safety (fail-closed), and partial-failure ordering.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest

from src.application.services.export_deletion_service import (
    ExportDeletionService,
    ExportNotFoundError,
    ExportGeneratingError,
    ExportStatusNotDeletableError,
    UnsafeExportPathError,
    ExportFileDeletionError,
    ExportRecordDeletionError,
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


@dataclass
class _Pkg:
    id: int
    created_by_user_id: int
    status: str = "completed"
    file_path: Optional[str] = None


class FakeExportRepository:
    """Minimal in-memory ExportPackageRepository double."""

    def __init__(self, packages=None, fail_delete=False):
        self._packages = {p.id: p for p in (packages or [])}
        self.deleted_ids = []
        self.fail_delete = fail_delete

    def get_by_id(self, id):
        return self._packages.get(id)

    def delete(self, id):
        if self.fail_delete:
            raise RuntimeError("simulated repository failure")
        self.deleted_ids.append(id)
        self._packages.pop(id, None)

    # Unused methods for the interface surface (not needed by the service).
    def create(self, export_package):  # pragma: no cover - guard
        raise NotImplementedError

    def list_by_user(self, user_id):  # pragma: no cover - guard
        raise NotImplementedError

    def update(self, id, fields):  # pragma: no cover - guard
        raise NotImplementedError

    def list_pending(self):  # pragma: no cover - guard
        raise NotImplementedError


def _make_zip(directory: Path, name: str = "export.zip") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    zip_path = directory / name
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    return zip_path


@pytest.fixture
def exports_root(tmp_path):
    root = tmp_path / "outputs" / "exports"
    root.mkdir(parents=True)
    return root


# --------------------------------------------------------------------------- #
# Ownership / status
# --------------------------------------------------------------------------- #


class TestOwnershipAndStatus:
    def test_own_completed_package_deletes(self, exports_root):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == [1]
        assert not zip_path.exists()

    def test_foreign_package_not_found(self, exports_root):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(ExportNotFoundError):
            service.delete_export(package_id=1, user_id=999)

        assert repo.deleted_ids == []
        assert zip_path.exists()  # no filesystem I/O for foreign package

    def test_missing_package_not_found(self, exports_root):
        repo = FakeExportRepository([])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(ExportNotFoundError):
            service.delete_export(package_id=42, user_id=7)

        assert repo.deleted_ids == []

    def test_error_status_deletes(self, exports_root):
        pkg = _Pkg(id=2, created_by_user_id=7, status="error", file_path=None)
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        service.delete_export(package_id=2, user_id=7)
        assert repo.deleted_ids == [2]

    def test_pending_status_blocked(self, exports_root):
        """pending is NOT deletable: it can represent a package in use during
        synchronous generation (e.g. POST /sincronizacion/local)."""
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=3, created_by_user_id=7, status="pending", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(ExportStatusNotDeletableError):
            service.delete_export(package_id=3, user_id=7)

        # No filesystem I/O and no repository delete for a blocked status.
        assert repo.deleted_ids == []
        assert zip_path.exists()

    def test_generating_status_blocked(self, exports_root):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=4, created_by_user_id=7, status="generating", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(ExportGeneratingError):
            service.delete_export(package_id=4, user_id=7)

        assert repo.deleted_ids == []
        assert zip_path.exists()


# --------------------------------------------------------------------------- #
# Filesystem safety
# --------------------------------------------------------------------------- #


class TestFilesystemSafety:
    def test_file_path_none_deletes_record(self, exports_root):
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=None)
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        service.delete_export(package_id=1, user_id=7)
        assert repo.deleted_ids == [1]

    def test_valid_existing_zip_deleted(self, exports_root):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        service.delete_export(package_id=1, user_id=7)
        assert not zip_path.exists()
        assert repo.deleted_ids == [1]

    def test_valid_missing_zip_deletes_record(self, exports_root):
        missing = exports_root / "gone.zip"
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(missing))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        service.delete_export(package_id=1, user_id=7)
        assert repo.deleted_ids == [1]

    def test_traversal_outside_is_unsafe(self, exports_root, tmp_path):
        outside = tmp_path / "secret.zip"
        outside.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        # Path expressed with traversal from inside the root.
        traversal = str(exports_root / ".." / ".." / "secret.zip")
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=traversal)
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(UnsafeExportPathError):
            service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == []
        assert outside.exists()

    def test_absolute_external_path_is_unsafe(self, exports_root, tmp_path):
        outside = tmp_path / "external.zip"
        outside.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(outside))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(UnsafeExportPathError):
            service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == []
        assert outside.exists()

    def test_prefix_collision_is_unsafe(self, tmp_path):
        root = tmp_path / "outputs" / "exports"
        root.mkdir(parents=True)
        evil_dir = tmp_path / "outputs" / "exports_evil"
        evil_dir.mkdir(parents=True)
        evil_zip = evil_dir / "evil.zip"
        evil_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(evil_zip))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=root)

        with pytest.raises(UnsafeExportPathError):
            service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == []
        assert evil_zip.exists()

    @pytest.mark.skipif(
        sys.platform.startswith("win"),
        reason="Symlink creation typically requires privileges on Windows.",
    )
    def test_symlink_resolving_outside_is_unsafe(self, exports_root, tmp_path):
        outside = tmp_path / "target.zip"
        outside.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        link = exports_root / "link.zip"
        link.symlink_to(outside)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(link))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(UnsafeExportPathError):
            service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == []
        assert outside.exists()

    def test_directory_path_is_unsafe(self, exports_root):
        directory = exports_root / "not_a_file.zip"
        directory.mkdir()
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(directory))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(UnsafeExportPathError):
            service.delete_export(package_id=1, user_id=7)

        assert repo.deleted_ids == []
        assert directory.exists()


# --------------------------------------------------------------------------- #
# Partial failures (order guarantees)
# --------------------------------------------------------------------------- #


class TestPartialFailures:
    def test_unlink_oserror_preserves_record(self, exports_root, monkeypatch):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(zip_path))
        repo = FakeExportRepository([pkg])
        service = ExportDeletionService(repo, exports_root=exports_root)

        def boom(self):
            raise OSError("cannot unlink")

        monkeypatch.setattr(Path, "unlink", boom, raising=True)

        with pytest.raises(ExportFileDeletionError):
            service.delete_export(package_id=1, user_id=7)

        # repo.delete must NOT be called when unlink fails.
        assert repo.deleted_ids == []

    def test_repo_delete_failure_after_unlink(self, exports_root):
        zip_path = _make_zip(exports_root)
        pkg = _Pkg(id=1, created_by_user_id=7, status="completed", file_path=str(zip_path))
        repo = FakeExportRepository([pkg], fail_delete=True)
        service = ExportDeletionService(repo, exports_root=exports_root)

        with pytest.raises(ExportRecordDeletionError):
            service.delete_export(package_id=1, user_id=7)

        # File already removed; record preserved (recoverable state).
        assert not zip_path.exists()
        assert repo.get_by_id(1) is not None
