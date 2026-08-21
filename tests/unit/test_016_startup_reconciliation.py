"""Integration test: app lifespan calls reconcile_orphan_exports at startup.

This test does NOT reimplement the reconciliation logic. It verifies that
the real app/main.py lifespan invokes it by observing the side effect
(generating → error) after the app starts.
"""

import os
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text


class TestLifespanReconciliation:
    """Verify that starting the app reconciles orphan exports."""

    def test_generating_becomes_error_after_app_startup(self, tmp_path):
        """Insert a 'generating' export, start the app, confirm it's 'error'."""
        db_path = tmp_path / "startup_test.db"

        # 1. Create the database with schema via DatabaseManager
        from src.infrastructure.persistence.database import DatabaseManager

        dm = DatabaseManager(db_path=str(db_path))
        dm.init_db()

        # 2. Insert a user + orphan export directly via session
        session = dm.get_session()
        from src.infrastructure.persistence.models.user_model import UserModel
        from src.infrastructure.persistence.models.export_package_model import (
            ExportPackageModel,
        )

        user = UserModel(
            full_name="Startup Test",
            email="startup@test.com",
            password_hash="pbkdf2_sha256$100000$salt$hash",
            role="operator",
        )
        session.add(user)
        session.flush()

        orphan = ExportPackageModel(
            created_by_user_id=user.id,
            scope="full",
            status="generating",
            records_count=0,
            images_count=0,
        )
        session.add(orphan)
        session.commit()
        orphan_id = orphan.id
        session.close()
        dm.engine.dispose()

        # 3. Start the REAL app via TestClient — this triggers lifespan
        #    Override the DB path via environment or monkey-patch DatabaseManager
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        def patched_init(self_dm, db_path_arg="data/tomato_monitor.db"):
            # Force the test DB path
            original_init = DatabaseManager.__init__.__wrapped__ if hasattr(
                DatabaseManager.__init__, "__wrapped__"
            ) else None
            # Just call with our path
            type(self_dm).__init__(self_dm, db_path=str(db_path))

        with patch.object(
            DatabaseManager, "__init__", lambda self, db_path_arg=None: None
        ):
            # We need a different approach — patch at the lifespan level
            pass

        # Simpler approach: patch the DatabaseManager constructor to use our path
        original_dm_init = DatabaseManager.__init__

        def override_init(self_inst, db_path_arg="data/tomato_monitor.db"):
            original_dm_init(self_inst, db_path=str(db_path))

        with patch.object(DatabaseManager, "__init__", override_init):
            from app.main import app

            with TestClient(app) as client:
                # App has started — lifespan ran
                pass

        # 4. Verify the orphan was reconciled
        dm2 = DatabaseManager(db_path=str(db_path))
        verify_session = dm2.get_session()
        record = (
            verify_session.query(ExportPackageModel)
            .filter(ExportPackageModel.id == orphan_id)
            .first()
        )
        assert record is not None, "Orphan record disappeared"
        assert record.status == "error", (
            f"Expected status='error' after lifespan, got '{record.status}'"
        )
        assert record.error_message is not None
        assert "interrumpida" in record.error_message.lower()
        verify_session.close()
        dm2.engine.dispose()
