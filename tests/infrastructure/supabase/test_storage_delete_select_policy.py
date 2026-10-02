"""Static guards for the corrective Storage SELECT policy migration.

These checks protect the SQL artifact; they do not execute Supabase RLS.
"""

from pathlib import Path
import re


MIGRATIONS = Path(__file__).resolve().parents[3] / "docs" / "migrations"


def _sql_without_comments(filename: str) -> str:
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    return re.sub(r"--[^\n]*", "", sql)


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def test_select_policy_keeps_hierarchy_and_scopes_owner_fallback_to_delete():
    """Bucket/path, hierarchy, operation, and owner guards stay grouped."""
    sql = _sql_without_comments("023-storage-delete-select-policy.sql")
    expected = """
        CREATE POLICY tomato_monitor_snapshots_owner_select ON storage.objects
        FOR SELECT TO authenticated
        USING (
            bucket_id = 'tomato-monitor-snapshots'
            AND split_part(storage.objects.name, '/', 1) = 'monitorings'
            AND (
                EXISTS (
                    SELECT 1
                    FROM public.monitorings mon
                    JOIN public.modules m ON m.id = mon.module_id
                    JOIN public.greenhouses g ON g.id = m.greenhouse_id
                    WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
                      AND g.owner_user_id = (select auth.uid())
                )
                OR (
                    storage.allow_only_operation('object.delete')
                    AND storage.objects.owner_id = (select auth.uid()::text)
                )
            )
        );
    """
    create_statement = re.search(
        r"CREATE POLICY tomato_monitor_snapshots_owner_select\b.*?;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )

    assert create_statement is not None
    assert _compact(create_statement.group()) == _compact(expected)


def test_migration_replaces_only_select_policy_and_preserves_delete_policy():
    """No INSERT/UPDATE/DELETE policy is dropped or recreated."""
    sql = _sql_without_comments("023-storage-delete-select-policy.sql")
    policy_changes = re.findall(
        r"(?:DROP|CREATE) POLICY(?: IF EXISTS)?\s+(\w+)\s+ON\s+storage\.objects",
        sql,
        re.IGNORECASE,
    )
    assert policy_changes == ["tomato_monitor_snapshots_owner_select"] * 2
    assert "storage.allow_only_operation('delete')" not in sql

    historical = _sql_without_comments("022-user-scoped-ownership-rls.sql")
    assert "CREATE POLICY tomato_monitor_snapshots_owner_delete" in historical
    assert "OR storage.objects.owner_id = (select auth.uid()::text)" in historical
