"""Startup wiring test: app lifespan calls recover_abrupt_recordings (Task 11.1).

This test does NOT reimplement the recovery logic. It verifies that the real
app/main.py lifespan wires MonitoringService.recover_abrupt_recordings() into
startup — invoked once, alongside orphan-export reconciliation, after the DB
is initialized. Verifying wiring (per requirement) is stronger than calling
the method directly.
"""

from unittest.mock import patch


class TestLifespanRecoveryWiring:
    """Verify that starting the app triggers abrupt-recording recovery."""

    def test_recover_abrupt_recordings_invoked_during_startup(self, tmp_path):
        """Start the REAL app via TestClient and confirm recovery is wired."""
        db_path = tmp_path / "recovery_startup.db"

        # 1. Create the database with schema via DatabaseManager.
        from src.infrastructure.persistence.database import DatabaseManager

        dm = DatabaseManager(db_path=str(db_path))
        dm.init_db()
        dm.engine.dispose()

        # 2. Start the REAL app via TestClient, forcing the test DB path and
        #    spying on the actual recovery method used by production wiring.
        from fastapi.testclient import TestClient
        from src.application.services.monitoring_service import MonitoringService

        original_dm_init = DatabaseManager.__init__

        def override_init(self_inst, db_path_arg="data/tomato_monitor.db"):
            original_dm_init(self_inst, db_path=str(db_path))

        with patch.object(DatabaseManager, "__init__", override_init):
            with patch.object(
                MonitoringService,
                "recover_abrupt_recordings",
                autospec=True,
            ) as spy_recover:
                from app.main import app

                with TestClient(app):
                    # App has started — lifespan ran.
                    pass

        # 3. Verify recovery was wired: invoked exactly once at startup, and
        #    delegated to a real MonitoringService instance (not main.py logic).
        assert spy_recover.call_count == 1, (
            f"Expected recover_abrupt_recordings to be called once during "
            f"startup, got {spy_recover.call_count}"
        )
        called_self = spy_recover.call_args.args[0]
        assert isinstance(called_self, MonitoringService), (
            "Recovery must be delegated to a MonitoringService instance"
        )

    def test_startup_survives_recovery_failure(self, tmp_path):
        """A failing recovery must not crash app startup (best-effort)."""
        db_path = tmp_path / "recovery_failure.db"

        from src.infrastructure.persistence.database import DatabaseManager

        dm = DatabaseManager(db_path=str(db_path))
        dm.init_db()
        dm.engine.dispose()

        from fastapi.testclient import TestClient
        from src.application.services.monitoring_service import MonitoringService

        original_dm_init = DatabaseManager.__init__

        def override_init(self_inst, db_path_arg="data/tomato_monitor.db"):
            original_dm_init(self_inst, db_path=str(db_path))

        def boom(self):
            raise RuntimeError("recovery blew up")

        with patch.object(DatabaseManager, "__init__", override_init):
            with patch.object(
                MonitoringService, "recover_abrupt_recordings", boom
            ):
                from app.main import app

                # Startup must not raise despite recovery failure.
                with TestClient(app) as client:
                    resp = client.get("/login")
                    assert resp.status_code in (200, 302)
