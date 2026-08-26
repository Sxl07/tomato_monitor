"""Real Supabase smoke tests — Task 18.

Requires network and a real Supabase project. These tests are excluded
from the default test suite via the 'supabase' marker.

Run explicitly with:
    py -m pytest -m supabase tests/infrastructure/supabase/test_supabase_real_smoke.py -v

Required environment variables:
    SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_STORAGE_BUCKET,
    SUPABASE_TEST_EMAIL, SUPABASE_TEST_PASSWORD

SUPABASE_SERVICE_ROLE_KEY must NOT be set.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import pytest

pytestmark = pytest.mark.supabase


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    """Get env var or skip test."""
    val = os.environ.get(name, "").strip()
    if not val:
        pytest.skip(f"Required env var {name} not set")
    return val


@pytest.fixture(scope="module")
def supabase_config():
    """Load and validate SupabaseConfig from env."""
    from src.infrastructure.supabase.supabase_config import SupabaseConfig

    url = _require_env("SUPABASE_URL")
    key = _require_env("SUPABASE_PUBLISHABLE_KEY")
    bucket = _require_env("SUPABASE_STORAGE_BUCKET")

    # Safety: no service_role
    assert not os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(), (
        "SUPABASE_SERVICE_ROLE_KEY must NOT be set for smoke tests"
    )

    # Validate project host
    assert "qpfaeiimoznclhvpcxws.supabase.co" in url, (
        "SUPABASE_URL does not match the expected project"
    )

    return SupabaseConfig(url=url, publishable_key=key, storage_bucket=bucket)


@pytest.fixture(scope="module")
def test_credentials():
    """Load test user credentials from env."""
    email = _require_env("SUPABASE_TEST_EMAIL")
    password = _require_env("SUPABASE_TEST_PASSWORD")
    return email, password


# ---------------------------------------------------------------------------
# Auth fixture (module-scoped — one login per test module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def auth_context(supabase_config, test_credentials):
    """Authenticate test user, return (access_token, user_id).

    Attempts sign_in first; if user doesn't exist, does sign_up then sign_in.
    """
    from src.infrastructure.supabase.supabase_auth_adapter import SupabaseAuthAdapter

    email, password = test_credentials
    adapter = SupabaseAuthAdapter(supabase_config)

    # Try sign_in
    result = adapter.sign_in(email, password)
    if result.success:
        assert result.access_token is not None
        assert result.user_id is not None
        return result.access_token, result.user_id

    # If credentials wrong (not just "user not found"), fail
    if result.error_type == "INVALID_CREDENTIALS":
        # Could be user doesn't exist — try sign_up
        signup_result = adapter.sign_up(email, password, "Smoke Test User")
        if signup_result.success:
            # Now sign in to get a proper JWT
            login_result = adapter.sign_in(email, password)
            assert login_result.success, (
                f"sign_up succeeded but sign_in failed: {login_result.error_type}"
            )
            return login_result.access_token, login_result.user_id
        elif "already" in (signup_result.error_message or "").lower():
            # User exists but password is wrong
            pytest.fail(
                "Test user exists but password doesn't match. "
                "Check SUPABASE_TEST_PASSWORD."
            )
        else:
            pytest.fail(
                f"sign_up failed: {signup_result.error_type} "
                f"({signup_result.error_message})"
            )

    pytest.fail(f"sign_in failed: {result.error_type}")


# ---------------------------------------------------------------------------
# Run ID and pre-generated UUIDs for this smoke run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_id():
    """Unique run identifier for this smoke test execution."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def remote_uuids():
    """Pre-generated UUIDs for the test entity tree."""
    return {
        "greenhouse": str(uuid.uuid4()),
        "module": str(uuid.uuid4()),
        "monitoring": str(uuid.uuid4()),
        "monitoring_metrics": str(uuid.uuid4()),
        "snapshot": str(uuid.uuid4()),
        "inspection_result": str(uuid.uuid4()),
        "activity_log": str(uuid.uuid4()),
    }


# ---------------------------------------------------------------------------
# REST helper
# ---------------------------------------------------------------------------


def _rest_get(config, access_token: str, table: str, params: dict) -> httpx.Response:
    """GET from PostgREST with auth."""
    url = f"{config.rest_url}/{table}"
    headers = {
        "apikey": config.publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    return httpx.get(url, headers=headers, params=params, timeout=15.0)


def _rest_delete(config, access_token: str, table: str, params: dict) -> httpx.Response:
    """DELETE from PostgREST with auth."""
    url = f"{config.rest_url}/{table}"
    headers = {
        "apikey": config.publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    return httpx.delete(url, headers=headers, params=params, timeout=15.0)


def _storage_get(config, access_token: str, path: str) -> httpx.Response:
    """GET object from Supabase Storage with auth."""
    url = f"{config.storage_url}/object/{config.storage_bucket}/{path}"
    headers = {
        "apikey": config.publishable_key,
        "Authorization": f"Bearer {access_token}",
    }
    return httpx.get(url, headers=headers, timeout=15.0)


def _storage_delete(config, access_token: str, paths: list[str]) -> httpx.Response:
    """DELETE objects from Supabase Storage with auth.

    Uses DELETE /object/{bucket} with body {"prefixes": [paths...]}.
    httpx.delete() does not support json= bodies, so we use httpx.request().
    """
    url = f"{config.storage_url}/object/{config.storage_bucket}"
    headers = {
        "apikey": config.publishable_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return httpx.request("DELETE", url, headers=headers, json={"prefixes": paths}, timeout=15.0)


def _rest_get_anon(config, table: str, params: dict) -> httpx.Response:
    """GET from PostgREST with apikey only (no JWT — anonymous)."""
    url = f"{config.rest_url}/{table}"
    headers = {"apikey": config.publishable_key}
    return httpx.get(url, headers=headers, params=params, timeout=15.0)


def _storage_get_anon(config, path: str) -> httpx.Response:
    """GET object from Storage with apikey only (no JWT)."""
    url = f"{config.storage_url}/object/{config.storage_bucket}/{path}"
    headers = {"apikey": config.publishable_key}
    return httpx.get(url, headers=headers, timeout=15.0)


# ---------------------------------------------------------------------------
# Fake SyncStatePort for exercising RemoteSyncService
# ---------------------------------------------------------------------------


class FakeSyncState:
    """Minimal in-memory SyncState for smoke testing RemoteSyncService.

    Removes entities from pending once mark_synced is called, matching
    the real SyncStateRepository behavior where synced entities are no
    longer returned by get_pending_entities.
    """

    def __init__(self, entities_by_type: dict, remote_ids: dict, storage_paths: dict):
        self._entities = entities_by_type  # {entity_type: [dict, ...]}
        self._remote_ids = remote_ids  # {(entity_type, local_id): uuid_str}
        self._storage_paths = storage_paths  # {snapshot_local_id: (raw, annotated)}
        self._synced_ids: set = set()  # (entity_type, local_id) pairs
        self._errors = {}

    def get_pending_entities(self, entity_type: str) -> list[dict]:
        """Return entities not yet synced."""
        return [
            e for e in self._entities.get(entity_type, [])
            if (entity_type, e["id"]) not in self._synced_ids
        ]

    def get_remote_id(self, entity_type: str, local_id: int) -> Optional[str]:
        return self._remote_ids.get((entity_type, local_id))

    def reserve_remote_id(self, entity_type: str, local_id: int, remote_id: str):
        self._remote_ids[(entity_type, local_id)] = remote_id

    def mark_syncing(self, entity_type: str, local_id: int):
        pass

    def mark_synced(self, entity_type: str, local_id: int, remote_id: str):
        self._synced_ids.add((entity_type, local_id))
        self._remote_ids[(entity_type, local_id)] = remote_id

    def mark_error(self, entity_type: str, local_id: int, error_msg: str):
        self._errors[(entity_type, local_id)] = error_msg

    def get_storage_paths(self, snapshot_id: int):
        from src.application.interfaces.sync_state_port import StoragePaths
        paths = self._storage_paths.get(snapshot_id, (None, None))
        return StoragePaths(raw_storage_path=paths[0], annotated_storage_path=paths[1])

    def set_storage_paths(self, snapshot_id: int, raw_path, annotated_path):
        self._storage_paths[snapshot_id] = (raw_path, annotated_path)


# ---------------------------------------------------------------------------
# Task 18.1 — AUTH REAL
# ---------------------------------------------------------------------------


class TestAuthReal:
    """Real authentication against Supabase."""

    def test_sign_in_returns_valid_jwt_and_uuid(self, auth_context):
        """sign_in produces valid UUID and non-empty access_token."""
        access_token, user_id = auth_context
        # UUID format validation
        uuid.UUID(user_id)  # Raises if not valid UUID
        assert len(access_token) > 20

    def test_profile_exists_for_auth_user(self, supabase_config, auth_context):
        """Profile row exists with correct data for authenticated user."""
        access_token, user_id = auth_context

        resp = _rest_get(supabase_config, access_token, "profiles", {
            "id": f"eq.{user_id}",
            "select": "id,email,role,is_active",
        })
        assert resp.status_code == 200, f"Profile GET failed: {resp.status_code}"
        rows = resp.json()
        assert len(rows) == 1, f"Expected 1 profile, got {len(rows)}"

        profile = rows[0]
        assert profile["id"] == user_id
        test_email = os.environ["SUPABASE_TEST_EMAIL"]
        assert profile["email"] == test_email
        assert profile["role"] == "operator"
        assert profile["is_active"] is True

    def test_activity_types_accessible_with_jwt(self, supabase_config, auth_context):
        """activity_types catalog readable with authenticated JWT."""
        access_token, _ = auth_context

        resp = _rest_get(supabase_config, access_token, "activity_types", {
            "select": "code,name,category",
        })
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) > 0, "activity_types catalog is empty"
        codes = {r["code"] for r in rows}
        assert "riego" in codes, f"'riego' not found in catalog. Codes: {codes}"


# ---------------------------------------------------------------------------
# Task 18.2 — REMOTE TREE (Data)
# Task 18.3 — STORAGE
# Task 18.4 — IDEMPOTENCY
# Task 18.5 — RLS
# ---------------------------------------------------------------------------


class TestRemoteTreeAndStorage:
    """Create, verify, re-sync, and cleanup a full entity tree remotely."""

    def test_full_sync_tree_and_storage(
        self, supabase_config, auth_context, remote_uuids, run_id, tmp_path
    ):
        """Full integration: sync tree, verify PostgreSQL, verify Storage,
        idempotency re-sync, RLS check, and cleanup."""
        from src.application.services.remote_sync_service import RemoteSyncService
        from src.application.services.sync_runtime_state import SyncRuntimeState
        from src.infrastructure.supabase.supabase_data_adapter import SupabaseDataAdapter
        from src.infrastructure.supabase.supabase_storage_adapter import SupabaseStorageAdapter

        access_token, user_id = auth_context
        now_iso = datetime.now(timezone.utc).isoformat()

        # --- Prepare temp snapshot files ---
        raw_dir = tmp_path / "snapshots" / "raw"
        raw_dir.mkdir(parents=True)
        annotated_dir = tmp_path / "annotated_snapshots"
        annotated_dir.mkdir(parents=True)

        raw_content = b"RAW_SMOKE_TEST_BYTES_" + run_id.encode()
        annotated_content = b"ANNOTATED_SMOKE_TEST_BYTES_" + run_id.encode()

        raw_file = raw_dir / "snapshot_000007.jpg"
        raw_file.write_bytes(raw_content)
        annotated_file = annotated_dir / "snapshot_000007.jpg"
        annotated_file.write_bytes(annotated_content)

        # --- Build entity data for FakeSyncState ---
        gh_id, mod_id, mon_id = 1, 2, 3
        metrics_id, snap_id, ir_id, al_id = 4, 5, 6, 7

        entities = {
            "greenhouse": [{
                "id": gh_id,
                "name": f"Smoke GH {run_id}",
                "location": "Test",
                "created_at": now_iso,
                "updated_at": now_iso,
                "remote_sync_status": "pending",
            }],
            "module": [{
                "id": mod_id,
                "greenhouse_id": gh_id,
                "name": f"Smoke Mod {run_id}",
                "crop_type": "Tomate Cherry",
                "width_m": 5.0,
                "length_m": 2.0,
                "monitoring_frequency_days": 7,
                "created_at": now_iso,
                "updated_at": now_iso,
                "remote_sync_status": "pending",
            }],
            "monitoring": [{
                "id": mon_id,
                "module_id": mod_id,
                "created_by_user_id": gh_id,  # will be remapped to user_id
                "status": "completed",
                "started_at": now_iso,
                "completed_at": now_iso,
                "width_m": 5.0,
                "length_m": 2.0,
                "total_snapshots": 1,
                "total_detections": 3,
                "remote_sync_status": "pending",
            }],
            "monitoring_metrics": [{
                "id": metrics_id,
                "monitoring_id": mon_id,
                "total_tomatoes": 3,
                "healthy_count": 2,
                "unhealthy_count": 1,
                "pct_healthy": 66.7,
                "pct_unhealthy": 33.3,
                "pct_green": 0.0,
                "pct_breaker": 33.3,
                "pct_turning": 33.3,
                "pct_pink": 0.0,
                "pct_light_red": 0.0,
                "pct_red": 33.3,
                "snapshots_with_detections": 1,
                "computed_at": now_iso,
                "remote_sync_status": "pending",
            }],
            "snapshot": [{
                "id": snap_id,
                "monitoring_id": mon_id,
                "captured_at": now_iso,
                "image_path": str(raw_file),
                "frame_index": 7,
                "change_score": 0.85,
                "has_detections": True,
                "remote_sync_status": "pending",
            }],
            "inspection_result": [{
                "id": ir_id,
                "snapshot_id": snap_id,
                "detection_index": 0,
                "bbox_x1": 10,
                "bbox_y1": 20,
                "bbox_x2": 100,
                "bbox_y2": 120,
                "detection_score": 0.92,
                "health_label": "healthy",
                "health_confidence": 0.88,
                "maturity_stage": "red",
                "maturity_percent": 95.0,
                "created_at": now_iso,
                "remote_sync_status": "pending",
            }],
            "activity_log": [{
                "id": al_id,
                "module_id": mod_id,
                "activity_type_id": 1,
                "activity_type_code": "riego",
                "user_id": gh_id,  # will be remapped to user_id
                "product_name": "Agua",
                "quantity": 5.0,
                "unit": "L",
                "notes": f"Smoke test {run_id}",
                "occurred_at": now_iso,
                "created_at": now_iso,
                "remote_sync_status": "pending",
            }],
        }

        # Pre-reserve all remote IDs so the service uses them
        remote_ids = {
            ("greenhouse", gh_id): remote_uuids["greenhouse"],
            ("module", mod_id): remote_uuids["module"],
            ("monitoring", mon_id): remote_uuids["monitoring"],
            ("monitoring_metrics", metrics_id): remote_uuids["monitoring_metrics"],
            ("snapshot", snap_id): remote_uuids["snapshot"],
            ("inspection_result", ir_id): remote_uuids["inspection_result"],
            ("activity_log", al_id): remote_uuids["activity_log"],
        }

        # Storage paths for the snapshot
        raw_remote_path = f"monitorings/{remote_uuids['monitoring']}/raw/snapshot_000007.jpg"
        annotated_remote_path = f"monitorings/{remote_uuids['monitoring']}/annotated/snapshot_000007.jpg"
        storage_paths = {
            snap_id: (raw_remote_path, annotated_remote_path),
        }

        fake_state = FakeSyncState(entities, remote_ids, storage_paths)
        runtime = SyncRuntimeState()
        runtime.try_acquire()

        data_adapter = SupabaseDataAdapter(supabase_config)
        storage_adapter = SupabaseStorageAdapter(supabase_config)

        sync_service = RemoteSyncService(
            remote_data=data_adapter,
            remote_storage=storage_adapter,
            sync_state=fake_state,
            runtime_state=runtime,
        )

        # ============ FIRST SYNC ============
        result = sync_service.execute_sync(access_token, user_id)
        runtime.release(None)

        assert result.success, f"Sync failed: {result.errors}"
        assert result.entities_synced >= 7
        assert result.entities_failed == 0
        assert result.images_uploaded == 2  # raw + annotated
        assert result.images_failed == 0

        # ============ VERIFY POSTGRESQL ============
        try:
            # Greenhouse
            resp = _rest_get(supabase_config, access_token, "greenhouses", {
                "id": f"eq.{remote_uuids['greenhouse']}",
                "select": "id,name",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["name"] == f"Smoke GH {run_id}"

            # Module FK
            resp = _rest_get(supabase_config, access_token, "modules", {
                "id": f"eq.{remote_uuids['module']}",
                "select": "id,greenhouse_id,name",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["greenhouse_id"] == remote_uuids["greenhouse"]

            # Monitoring FK
            resp = _rest_get(supabase_config, access_token, "monitorings", {
                "id": f"eq.{remote_uuids['monitoring']}",
                "select": "id,module_id,created_by_user_id",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["module_id"] == remote_uuids["module"]
            assert rows[0]["created_by_user_id"] == user_id

            # Metrics FK
            resp = _rest_get(supabase_config, access_token, "monitoring_metrics", {
                "id": f"eq.{remote_uuids['monitoring_metrics']}",
                "select": "id,monitoring_id",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["monitoring_id"] == remote_uuids["monitoring"]

            # Snapshot FK
            resp = _rest_get(supabase_config, access_token, "snapshots", {
                "id": f"eq.{remote_uuids['snapshot']}",
                "select": "id,monitoring_id",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["monitoring_id"] == remote_uuids["monitoring"]

            # InspectionResult FK
            resp = _rest_get(supabase_config, access_token, "inspection_results", {
                "id": f"eq.{remote_uuids['inspection_result']}",
                "select": "id,snapshot_id",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["snapshot_id"] == remote_uuids["snapshot"]

            # ActivityLog FK
            resp = _rest_get(supabase_config, access_token, "activity_logs", {
                "id": f"eq.{remote_uuids['activity_log']}",
                "select": "id,module_id,user_id,activity_type_code",
            })
            assert resp.status_code == 200
            rows = resp.json()
            assert len(rows) == 1
            assert rows[0]["module_id"] == remote_uuids["module"]
            assert rows[0]["user_id"] == user_id
            assert rows[0]["activity_type_code"] == "riego"

            # ============ VERIFY STORAGE (Task 18.3) ============
            # Download raw
            resp = _storage_get(supabase_config, access_token, raw_remote_path)
            assert resp.status_code == 200, f"Raw download failed: {resp.status_code}"
            assert resp.content == raw_content

            # Download annotated
            resp = _storage_get(supabase_config, access_token, annotated_remote_path)
            assert resp.status_code == 200, f"Annotated download failed: {resp.status_code}"
            assert resp.content == annotated_content

            # ============ IDEMPOTENCY (Task 18.4) ============
            # Reset fake state to make entities "pending" again
            fake_state2 = FakeSyncState(entities, dict(remote_ids), dict(storage_paths))
            runtime2 = SyncRuntimeState()
            runtime2.try_acquire()

            sync_service2 = RemoteSyncService(
                remote_data=data_adapter,
                remote_storage=storage_adapter,
                sync_state=fake_state2,
                runtime_state=runtime2,
            )

            result2 = sync_service2.execute_sync(access_token, user_id)
            runtime2.release(None)

            assert result2.success, f"Second sync failed: {result2.errors}"
            assert result2.entities_failed == 0

            # Verify no duplicates — still exactly 1 row per UUID
            resp = _rest_get(supabase_config, access_token, "greenhouses", {
                "id": f"eq.{remote_uuids['greenhouse']}",
                "select": "id",
            })
            assert len(resp.json()) == 1

            resp = _rest_get(supabase_config, access_token, "monitorings", {
                "id": f"eq.{remote_uuids['monitoring']}",
                "select": "id",
            })
            assert len(resp.json()) == 1

            # Storage still correct after re-upload
            resp = _storage_get(supabase_config, access_token, raw_remote_path)
            assert resp.status_code == 200
            assert resp.content == raw_content

            # ============ RLS WITHOUT JWT (Task 18.5) ============
            # Anonymous access to greenhouses should be blocked
            resp_anon = _rest_get_anon(supabase_config, "greenhouses", {
                "id": f"eq.{remote_uuids['greenhouse']}",
                "select": "id,name",
            })
            # RLS: either 401/403 or empty result (Supabase returns empty for RLS-blocked)
            if resp_anon.status_code == 200:
                anon_rows = resp_anon.json()
                assert len(anon_rows) == 0, (
                    "Anonymous access returned data — RLS not enforced"
                )
            else:
                assert resp_anon.status_code in (401, 403)

            # Anonymous access to activity_types
            resp_anon_at = _rest_get_anon(supabase_config, "activity_types", {
                "select": "code",
            })
            if resp_anon_at.status_code == 200:
                anon_at_rows = resp_anon_at.json()
                assert len(anon_at_rows) == 0, (
                    "Anonymous access to activity_types returned data — RLS not enforced"
                )
            else:
                assert resp_anon_at.status_code in (401, 403)

            # Anonymous access to Storage
            resp_anon_storage = _storage_get_anon(supabase_config, raw_remote_path)
            assert resp_anon_storage.status_code in (400, 401, 403, 404), (
                f"Anonymous storage access returned {resp_anon_storage.status_code}"
            )

            # Confirm authenticated access still works (control)
            resp_auth = _rest_get(supabase_config, access_token, "greenhouses", {
                "id": f"eq.{remote_uuids['greenhouse']}",
                "select": "id,name",
            })
            assert resp_auth.status_code == 200
            assert len(resp_auth.json()) == 1

        finally:
            # ============ CLEANUP ============
            # Delete storage objects (must succeed)
            del_resp = _storage_delete(supabase_config, access_token, [
                raw_remote_path,
                annotated_remote_path,
            ])
            # Supabase returns 200 with array of deleted objects, or 200 with
            # empty array if paths didn't match. Check response body.
            assert del_resp.status_code == 200, (
                f"Storage cleanup failed: {del_resp.status_code} {del_resp.text[:100]}"
            )
            deleted_objects = del_resp.json()
            assert len(deleted_objects) >= 2, (
                f"Expected 2 storage objects deleted, got {len(deleted_objects)}. "
                f"Paths attempted: {raw_remote_path}, {annotated_remote_path}"
            )

            # Delete greenhouse (cascade removes descendants)
            gh_del = _rest_delete(supabase_config, access_token, "greenhouses", {
                "id": f"eq.{remote_uuids['greenhouse']}",
            })
            assert gh_del.status_code in (200, 204), (
                f"Greenhouse cleanup failed: {gh_del.status_code}"
            )

        # ============ VERIFY CLEANUP ============
        import time
        time.sleep(2)  # Allow Storage eventual consistency

        resp = _rest_get(supabase_config, access_token, "greenhouses", {
            "id": f"eq.{remote_uuids['greenhouse']}",
            "select": "id",
        })
        assert resp.status_code == 200
        assert len(resp.json()) == 0, "Greenhouse not cleaned up"

        resp = _rest_get(supabase_config, access_token, "modules", {
            "id": f"eq.{remote_uuids['module']}",
            "select": "id",
        })
        assert resp.status_code == 200
        assert len(resp.json()) == 0, "Module not cleaned up (cascade)"

        resp = _rest_get(supabase_config, access_token, "monitorings", {
            "id": f"eq.{remote_uuids['monitoring']}",
            "select": "id",
        })
        assert resp.status_code == 200
        assert len(resp.json()) == 0, "Monitoring not cleaned up (cascade)"

        # Verify storage objects deleted via list API (avoids CDN caching)
        list_url = f"{supabase_config.storage_url}/object/list/{supabase_config.storage_bucket}"
        list_headers = {
            "apikey": supabase_config.publishable_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        # Check raw folder
        raw_folder = f"monitorings/{remote_uuids['monitoring']}/raw/"
        list_resp = httpx.post(
            list_url, headers=list_headers,
            json={"prefix": raw_folder, "limit": 10}, timeout=15,
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 0, (
            f"Raw storage objects still listed after delete: {list_resp.json()}"
        )

        # Check annotated folder
        ann_folder = f"monitorings/{remote_uuids['monitoring']}/annotated/"
        list_resp2 = httpx.post(
            list_url, headers=list_headers,
            json={"prefix": ann_folder, "limit": 10}, timeout=15,
        )
        assert list_resp2.status_code == 200
        assert len(list_resp2.json()) == 0, (
            f"Annotated storage objects still listed after delete: {list_resp2.json()}"
        )
