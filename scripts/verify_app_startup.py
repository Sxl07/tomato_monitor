"""Verify that the FastAPI app can start and the database is initialized."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app
    print(f"FastAPI app loaded: {app.title} v{app.version}")
    print("App startup OK (lifespan will init DB on actual serve)")
    
    # Verify dependencies module imports
    from app.dependencies import (
        get_db_session,
        get_greenhouse_repository,
        get_module_repository,
        get_monitoring_repository,
        get_snapshot_repository,
        get_inspection_result_repository,
        get_monitoring_metrics_repository,
    )
    print("All new dependency functions importable: OK")
    
    # Quick database init test
    from src.infrastructure.persistence.database import DatabaseManager
    import tempfile
    tmp = tempfile.mktemp(suffix=".db")
    mgr = DatabaseManager(db_path=tmp)
    mgr.init_db()
    session = mgr.get_session()
    session.close()
    os.unlink(tmp)
    print("DatabaseManager init_db() + get_session(): OK")
    
    print("\n=== ALL CHECKS PASSED ===")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
