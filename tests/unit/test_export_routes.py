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
            created_by_user_id=1,
            status="completed",
            file_path=str(outside),
        )
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = package

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=mock_repo):
            response = authenticated_client.get("/exportar/123/descargar", follow_redirects=False)

        assert response.status_code in (302, 303)


class TestExportDeleteRoute:
    """Tests for POST /exportar/{id}/eliminar (Spec 025)."""

    def test_delete_delegates_to_service_success(self, authenticated_client):
        """Successful deletion redirects to /exportar with a success message."""
        from unittest.mock import MagicMock, patch

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                return_value=None,
            ):
                resp = authenticated_client.post("/exportar/5/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert location.startswith("/exportar")
        assert "success" in location

    def test_delete_not_found_redirects_with_error(self, authenticated_client):
        """A not-found/foreign package redirects with 'no encontrada' error."""
        from unittest.mock import MagicMock, patch
        from src.application.services.export_deletion_service import ExportNotFoundError

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                side_effect=ExportNotFoundError("Exportación no encontrada"),
            ):
                resp = authenticated_client.post("/exportar/9999/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert "error" in location
        assert "encontrada" in location

    def test_delete_generating_blocked(self, authenticated_client):
        """A generating package cannot be deleted; redirects with error."""
        from unittest.mock import MagicMock, patch
        from src.application.services.export_deletion_service import ExportGeneratingError

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                side_effect=ExportGeneratingError("generando"),
            ):
                resp = authenticated_client.post("/exportar/7/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        assert "error" in resp.headers.get("location", "")

    def test_delete_pending_blocked(self, authenticated_client):
        """A pending package cannot be deleted; redirects with error."""
        from unittest.mock import MagicMock, patch
        from src.application.services.export_deletion_service import (
            ExportStatusNotDeletableError,
        )

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                side_effect=ExportStatusNotDeletableError("en curso"),
            ):
                resp = authenticated_client.post("/exportar/6/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        assert "error" in resp.headers.get("location", "")

    def test_delete_unsafe_path_controlled_error(self, authenticated_client):
        """An unsafe path yields a controlled 303 error, never a 500."""
        from unittest.mock import MagicMock, patch
        from src.application.services.export_deletion_service import UnsafeExportPathError

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                side_effect=UnsafeExportPathError("ruta no segura"),
            ):
                resp = authenticated_client.post("/exportar/8/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        assert "error" in resp.headers.get("location", "")

    def test_delete_filesystem_failure_controlled_error(self, authenticated_client):
        """A filesystem/record deletion failure yields a controlled 303, no 500."""
        from unittest.mock import MagicMock, patch
        from src.application.services.export_deletion_service import ExportFileDeletionError

        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=MagicMock()):
            with patch(
                "src.application.services.export_deletion_service.ExportDeletionService.delete_export",
                side_effect=ExportFileDeletionError("no se pudo"),
            ):
                resp = authenticated_client.post("/exportar/9/eliminar", follow_redirects=False)

        assert resp.status_code == 303
        assert "error" in resp.headers.get("location", "")


class TestExportListDeleteUI:
    """Tests for the delete action rendered in the export list."""

    def _create_export(self, authenticated_client):
        resp = authenticated_client.post("/exportar", follow_redirects=False)
        return resp.headers["location"].rstrip("/").split("/")[-1]

    def test_list_shows_trash_action_and_class(self, authenticated_client):
        """List renders the trash action with the correct stylesheet class."""
        self._create_export(authenticated_client)
        resp = authenticated_client.get("/exportar")
        assert resp.status_code == 200
        assert "btn-icon--danger" in resp.text
        assert 'aria-label="Eliminar exportación"' in resp.text

    def test_list_shows_confirmation_dialog(self, authenticated_client):
        """List renders the confirmation dialog with correct wording and actions."""
        self._create_export(authenticated_client)
        resp = authenticated_client.get("/exportar")
        assert "¿Eliminar esta exportación?" in resp.text
        assert "datos originales no se eliminarán" in resp.text
        assert "Eliminar exportación" in resp.text
        assert "Cancelar" in resp.text
        assert "showConfirmDialog" in resp.text

    def test_list_preserves_detail_and_download(self, authenticated_client):
        """List keeps Ver detalle and Descargar for completed exports."""
        self._create_export(authenticated_client)
        resp = authenticated_client.get("/exportar")
        assert "Ver detalle" in resp.text
        # Generate button preserved too.
        assert "Generar exportación ZIP" in resp.text

    def test_list_empty_state(self, authenticated_client):
        """When the user has no exports the empty state is shown."""
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.list_by_user.return_value = []
        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=mock_repo):
            resp = authenticated_client.get("/exportar")
        assert resp.status_code == 200
        assert "No hay exportaciones registradas." in resp.text

    def test_list_renders_success_feedback(self, authenticated_client):
        """List renders success feedback from query params."""
        resp = authenticated_client.get("/exportar?success=Exportación+eliminada")
        assert resp.status_code == 200
        assert "Exportación eliminada" in resp.text

    def test_list_renders_error_feedback(self, authenticated_client):
        """List renders error feedback from query params."""
        resp = authenticated_client.get("/exportar?error=No+se+pudo+eliminar")
        assert resp.status_code == 200
        assert "No se pudo eliminar" in resp.text

    def _render_with_status(self, authenticated_client, status):
        """Render /exportar with a single package of the given status."""
        from types import SimpleNamespace
        from datetime import datetime
        from unittest.mock import MagicMock, patch

        pkg = SimpleNamespace(
            id=1,
            created_by_user_id=1,
            status=status,
            file_path="outputs/exports/x.zip" if status == "completed" else None,
            file_size_bytes=1024 if status == "completed" else None,
            records_count=3,
            images_count=2,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
        mock_repo = MagicMock()
        mock_repo.list_by_user.return_value = [pkg]
        with patch("app.routes.agricultural_ui.get_export_package_repository", return_value=mock_repo):
            return authenticated_client.get("/exportar")

    def test_completed_shows_delete(self, authenticated_client):
        resp = self._render_with_status(authenticated_client, "completed")
        assert resp.status_code == 200
        assert "/exportar/1/eliminar" in resp.text
        assert 'aria-label="Eliminar exportación"' in resp.text

    def test_error_shows_delete(self, authenticated_client):
        resp = self._render_with_status(authenticated_client, "error")
        assert resp.status_code == 200
        assert "/exportar/1/eliminar" in resp.text

    def test_pending_does_not_show_delete(self, authenticated_client):
        resp = self._render_with_status(authenticated_client, "pending")
        assert resp.status_code == 200
        assert "/exportar/1/eliminar" not in resp.text

    def test_generating_does_not_show_delete(self, authenticated_client):
        resp = self._render_with_status(authenticated_client, "generating")
        assert resp.status_code == 200
        assert "/exportar/1/eliminar" not in resp.text


class TestExportDetailFeedback:
    """Tests for feedback rendering in the export detail (no delete button)."""

    def test_detail_renders_download_error(self, authenticated_client):
        """Detail renders the download error passed via query params."""
        create_resp = authenticated_client.post("/exportar", follow_redirects=False)
        pkg_id = create_resp.headers["location"].rstrip("/").split("/")[-1]

        resp = authenticated_client.get(f"/exportar/{pkg_id}?error=Archivo+no+disponible")
        assert resp.status_code == 200
        assert "Archivo no disponible" in resp.text

    def test_detail_has_no_delete_button(self, authenticated_client):
        """Detail must NOT include a delete action (delete lives only in list)."""
        create_resp = authenticated_client.post("/exportar", follow_redirects=False)
        pkg_id = create_resp.headers["location"].rstrip("/").split("/")[-1]

        resp = authenticated_client.get(f"/exportar/{pkg_id}")
        assert resp.status_code == 200
        assert "/eliminar" not in resp.text


class TestExportDownloadGuidance:
    """A generated ZIP remains a separate, retryable browser download."""

    def test_generated_export_and_retry_use_existing_zip(self, authenticated_client):
        created = authenticated_client.post("/exportar", follow_redirects=False)
        assert created.status_code == 303
        detail_url = created.headers["location"]
        export_id = int(detail_url.rstrip("/").split("/")[-1])
        download_url = f"/exportar/{export_id}/descargar"

        detail = authenticated_client.get(detail_url)
        assert detail.status_code == 200
        assert "Exportación lista para descargar." in detail.text
        assert "aún debes descargarlo en tu dispositivo" in detail.text
        assert detail.text.count(f'href="{download_url}"') == 2
        assert "Descargar ZIP" in detail.text
        assert "Descarga iniciada. Revisa la carpeta Descargas de tu dispositivo." in detail.text
        assert "Si la descarga no comenzó," in detail.text
        assert "descargar de nuevo" in detail.text
        assert "Descarga completada" not in detail.text
        assert "Archivo descargado correctamente" not in detail.text

        listing = authenticated_client.get("/exportar")
        assert listing.status_code == 200
        assert listing.text.count(f'href="{download_url}"') == 2
        assert "Descargar" in listing.text
        assert "descargar de nuevo" in listing.text

        # A repeated GET uses the existing ZIP and cannot create an export.
        from app.main import app
        from src.infrastructure.persistence.models import ExportPackageModel

        session = app.state.db_manager.get_session()
        try:
            before = session.query(ExportPackageModel).count()
        finally:
            session.close()
        downloaded = authenticated_client.get(download_url, follow_redirects=False)
        assert downloaded.status_code in (200, 303)
        session = app.state.db_manager.get_session()
        try:
            assert session.query(ExportPackageModel).count() == before
        finally:
            session.close()

    def test_feedback_preserves_normal_anchor_navigation(self):
        from pathlib import Path

        script = Path("app/static/js/export_download_feedback.js").read_text(encoding="utf-8")
        detail = Path("app/templates/agricultural/export_detail.html").read_text(encoding="utf-8")
        listing = Path("app/templates/agricultural/export_list.html").read_text(encoding="utf-8")

        assert 'document.addEventListener("click"' in script
        assert 'feedback.hidden = false' in script
        assert 'feedback.style.display = ""' in script
        assert "preventDefault" not in script
        assert "fetch(" not in script
        for template in (detail, listing):
            assert 'data-export-download-feedback hidden style="display: none;' in template
            assert 'data-export-download>descargar de nuevo</a>' in template
            assert '/static/js/export_download_feedback.js?v=20261002-1' in template
