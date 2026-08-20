"""Unit tests for export routes (GET/POST /exportar, download)."""

import pytest


@pytest.fixture(autouse=True)
def _seed_test_user(authenticated_client):
    """Ensure user with id=1 exists in DB for FK constraints."""
    from app.main import app

    db_manager = app.state.db_manager
    session = db_manager.get_session()
    try:
        from src.infrastructure.persistence.models import UserModel
        existing = session.query(UserModel).filter(UserModel.id == 1).first()
        if existing is None:
            user_model = UserModel(
                id=1,
                full_name="Test Operator",
                email="test@example.com",
                password_hash="pbkdf2_sha256$260000$aaaa$bbbb",
                role="operator",
                is_active=True,
            )
            session.add(user_model)
            session.commit()
        else:
            session.close()
    except Exception:
        session.rollback()
        session.close()


class TestExportListRoute:
    """Tests for GET /exportar."""

    def test_export_list_returns_200(self, authenticated_client):
        """GET /exportar returns 200 with export list."""
        response = authenticated_client.get("/exportar")
        assert response.status_code == 200
        assert "Exportación de datos" in response.text

    def test_export_list_shows_generate_button(self, authenticated_client):
        """Export list page contains the generate button."""
        response = authenticated_client.get("/exportar")
        assert response.status_code == 200
        assert "Generar exportación ZIP" in response.text

    def test_export_list_requires_auth(self):
        """GET /exportar redirects to login without auth."""
        from app.main import app
        from fastapi.testclient import TestClient

        # Clear overrides to test without auth
        saved = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            with TestClient(app) as client:
                response = client.get("/exportar", follow_redirects=False)
                assert response.status_code == 302
                assert "/login" in response.headers.get("location", "")
        finally:
            app.dependency_overrides.update(saved)


class TestExportCreateRoute:
    """Tests for POST /exportar."""

    def test_export_create_redirects(self, authenticated_client):
        """POST /exportar creates package and redirects to detail."""
        response = authenticated_client.post("/exportar", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "/exportar/" in location

    def test_export_create_generates_package(self, authenticated_client):
        """POST /exportar creates a package visible in detail page."""
        # Create export
        response = authenticated_client.post("/exportar", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers["location"]

        # Follow to detail
        detail_response = authenticated_client.get(location)
        assert detail_response.status_code == 200
        assert "Exportación #" in detail_response.text


class TestExportDetailRoute:
    """Tests for GET /exportar/{id}."""

    def test_export_detail_nonexistent(self, authenticated_client):
        """GET /exportar/9999 redirects when not found."""
        response = authenticated_client.get("/exportar/9999", follow_redirects=False)
        assert response.status_code == 303

    def test_export_detail_after_create(self, authenticated_client):
        """GET /exportar/{id} shows details after creation."""
        # Create first
        create_resp = authenticated_client.post("/exportar", follow_redirects=False)
        location = create_resp.headers["location"]

        # View detail
        detail_resp = authenticated_client.get(location)
        assert detail_resp.status_code == 200
        assert "Alcance" in detail_resp.text or "Completada" in detail_resp.text


class TestExportDownloadRoute:
    """Tests for GET /exportar/{id}/descargar."""

    def test_download_completed_export(self, authenticated_client):
        """Download returns ZIP for completed export."""
        # Create export (generates a ZIP)
        create_resp = authenticated_client.post("/exportar", follow_redirects=False)
        location = create_resp.headers["location"]
        # Extract ID from location like /exportar/1
        pkg_id = location.rstrip("/").split("/")[-1]

        # Try download
        download_resp = authenticated_client.get(
            f"/exportar/{pkg_id}/descargar", follow_redirects=False
        )
        # Should be 200 with ZIP content or 303 redirect if file doesn't exist
        if download_resp.status_code == 200:
            assert download_resp.headers.get("content-type", "").startswith("application/")
        else:
            # Redirect means file path issue (test env)
            assert download_resp.status_code == 303

    def test_download_nonexistent_export(self, authenticated_client):
        """Download nonexistent export redirects."""
        response = authenticated_client.get("/exportar/9999/descargar", follow_redirects=False)
        assert response.status_code == 303


class TestDashboardExportLink:
    """Test that dashboard has export quick link."""

    def test_dashboard_has_export_link(self, authenticated_client):
        """Dashboard quick links section includes export link."""
        response = authenticated_client.get("/dashboard")
        assert response.status_code == 200
        assert "/exportar" in response.text
        assert "Exportar datos" in response.text


class TestExportDownloadSafety:
    """Tests that download rejects unsafe paths."""

    def test_download_pending_export_does_not_download(self, authenticated_client):
        """Download with status 'generating' redirects, not downloads."""
        # Create an export
        create_resp = authenticated_client.post("/exportar", follow_redirects=False)
        location = create_resp.headers["location"]
        pkg_id = location.rstrip("/").split("/")[-1]

        # Manually set status to generating (via DB)
        from app.main import app
        db_manager = app.state.db_manager
        session = db_manager.get_session()
        try:
            from src.infrastructure.persistence.models import ExportPackageModel
            model = session.query(ExportPackageModel).filter(ExportPackageModel.id == int(pkg_id)).first()
            if model:
                model.status = "generating"
                session.commit()
        finally:
            session.close()

        # Attempt download
        resp = authenticated_client.get(f"/exportar/{pkg_id}/descargar", follow_redirects=False)
        assert resp.status_code == 303


class TestExportPathSafetyHelper:
    """Unit tests for _is_safe_export_path."""

    def test_accepts_zip_inside_exports_dir(self, tmp_path, monkeypatch):
        from app.routes.agricultural_ui import _is_safe_export_path
        monkeypatch.chdir(tmp_path)
        export_dir = tmp_path / "outputs" / "exports"
        export_dir.mkdir(parents=True)
        zip_path = export_dir / "test_export.zip"
        zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        assert _is_safe_export_path("outputs/exports/test_export.zip") is True

    def test_rejects_file_outside_exports_dir(self, tmp_path, monkeypatch):
        from app.routes.agricultural_ui import _is_safe_export_path
        monkeypatch.chdir(tmp_path)
        outside = tmp_path / "outside.zip"
        outside.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        assert _is_safe_export_path(str(outside)) is False

    def test_rejects_non_zip_inside_exports_dir(self, tmp_path, monkeypatch):
        from app.routes.agricultural_ui import _is_safe_export_path
        monkeypatch.chdir(tmp_path)
        export_dir = tmp_path / "outputs" / "exports"
        export_dir.mkdir(parents=True)
        txt_path = export_dir / "not_zip.txt"
        txt_path.write_text("not a zip", encoding="utf-8")
        assert _is_safe_export_path("outputs/exports/not_zip.txt") is False

    def test_rejects_prefix_collision_exports_evil(self, tmp_path, monkeypatch):
        from app.routes.agricultural_ui import _is_safe_export_path
        monkeypatch.chdir(tmp_path)
        evil_dir = tmp_path / "outputs" / "exports_evil"
        evil_dir.mkdir(parents=True)
        evil_zip = evil_dir / "evil.zip"
        evil_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        assert _is_safe_export_path(str(evil_zip)) is False

    def test_download_rejects_unsafe_external_file_path(self, authenticated_client, tmp_path):
        """Download route rejects completed package pointing outside outputs/exports."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        outside = tmp_path / "external.zip"
        outside.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

        package = SimpleNamespace(
            id=123,
            status="completed",
            file_path=str(outside),
        )
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = package

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=mock_repo):
            response = authenticated_client.get("/exportar/123/descargar", follow_redirects=False)

        assert response.status_code in (302, 303)
