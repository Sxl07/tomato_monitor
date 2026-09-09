-- ============================================================================
-- Spec 022 — REMOTE migration (Supabase / PostgreSQL): user-scoped ownership,
--            RLS, and Storage isolation.
-- File: docs/migrations/022-user-scoped-ownership-rls.sql
-- Project: qpfaeiimoznclhvpcxws
--
-- PURPOSE
--   Introduce multi-user ownership anchored on public.greenhouses and enforce
--   per-user isolation via Row Level Security (RLS) across the whole tenant
--   hierarchy, plus Storage object isolation for the snapshots bucket.
--
--     greenhouses            -> owner_user_id = auth.uid()
--     modules                -> greenhouse (owner)
--     monitorings            -> module -> greenhouse (owner)
--     monitoring_metrics     -> monitoring -> ... -> greenhouse (owner)
--     snapshots              -> monitoring -> ... -> greenhouse (owner)
--     inspection_results     -> snapshot -> ... -> greenhouse (owner)
--     activity_logs          -> module -> greenhouse (owner)
--
--   Storage objects in bucket 'tomato-monitor-snapshots' resolve ownership via
--   the monitoring UUID embedded in the object path:
--     monitorings/{monitoring_uuid}/{snapshot_type}/snapshot_{frame:06d}.jpg
--
-- SCOPE (STRICTLY LIMITED)
--   - Adds public.greenhouses.owner_user_id (UUID NULL) + FK -> auth.users(id).
--   - Replaces the global UNIQUE(name) with UNIQUE(owner_user_id, name).
--   - Enables RLS and replaces the permissive "authenticated" policies on the
--     7 tenant tables with owner-scoped policies.
--   - Replaces exactly the 4 open Storage policies of the snapshots bucket with
--     owner-scoped ones (path unchanged).
--   - Grants minimal CRUD to authenticated on the 7 tenant tables.
--   - Does NOT touch public.activity_types (global catalog).
--   - Does NOT touch monitorings.created_by_user_id or activity_logs.user_id
--     (both FK -> profiles.id): ownership root is greenhouses.owner_user_id ONLY.
--   - Does NOT backfill legacy rows; existing greenhouses stay owner_user_id
--     NULL (and thus invisible to normal users under RLS — intentional, see
--     "LEGACY" below). No data INSERT/UPDATE.
--   - No SECURITY DEFINER functions, no helper functions, no views.
--
-- !!! HUMAN REVIEW REQUIRED — DO NOT APPLY AUTOMATICALLY !!!
--   This is a design/documentation artifact only. It must be reviewed by a
--   human and applied manually against Supabase, inside the transaction below,
--   after confirming the real object names in the target database. The
--   application never connects to Supabase to modify schema.
--
--   Suggested pre-checks before applying:
--     -- current greenhouses constraints/columns
--     SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint WHERE conrelid = 'public.greenhouses'::regclass;
--     -- current policies for a tenant table
--     SELECT policyname, cmd, qual, with_check
--     FROM pg_policies WHERE schemaname = 'public' AND tablename = 'greenhouses';
--     -- current storage policies
--     SELECT policyname, cmd FROM pg_policies
--     WHERE schemaname = 'storage' AND tablename = 'objects';
--
-- LEGACY (INTENTIONAL — NOT SOLVED HERE / C1)
--   After applying this migration, pre-existing remote greenhouses have
--   owner_user_id = NULL and are therefore invisible to normal users under
--   RLS. This is intentional. Legacy owner assignment, if ever needed, is a
--   MANUAL decision for C2 with deterministic evidence. Never infer ownership
--   from "only one user exists" and never auto-assign to the current user.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1-2. Ownership column + FK -> auth.users(id)
-- ----------------------------------------------------------------------------
-- Nullable, NO default, NO trigger, NO backfill. Valid value is the Supabase
-- Auth user id (auth.users.id == auth.uid()). profiles is NOT the ownership
-- root.
ALTER TABLE public.greenhouses
    ADD COLUMN IF NOT EXISTS owner_user_id UUID NULL;

ALTER TABLE public.greenhouses
    DROP CONSTRAINT IF EXISTS greenhouses_owner_user_id_fkey;

ALTER TABLE public.greenhouses
    ADD CONSTRAINT greenhouses_owner_user_id_fkey
    FOREIGN KEY (owner_user_id) REFERENCES auth.users (id);

-- ----------------------------------------------------------------------------
-- 2b. AUTHORIZED legacy ownership backfill (explicit, closed exception).
--
--   The project owner explicitly confirmed that ALL greenhouses created so far
--   belong to test@example.com (Supabase Auth uid
--   3e9b9a22-2742-455a-b4d8-1a5b61d0ebde). This is NOT an automatic inference:
--   it assigns EXACTLY the three enumerated greenhouse UUIDs and nothing else.
--   The general legacy rule still holds — any other (present or future)
--   greenhouse not in this list keeps owner_user_id NULL and is never inferred
--   from "only one user exists", email, profiles, or Storage owner.
-- ----------------------------------------------------------------------------
UPDATE public.greenhouses
SET owner_user_id = '3e9b9a22-2742-455a-b4d8-1a5b61d0ebde'::uuid
WHERE id IN (
    '612de050-1044-4597-8ce7-e247052c4ce3'::uuid,  -- "Camino del sol"
    '7f547080-ca5a-4e4a-9076-b82637c7251c'::uuid,  -- "Sofia"
    'edbfe17c-2b6e-405a-a3b2-93af1fb75cf8'::uuid   -- "usb"
)
AND owner_user_id IS NULL;

-- Fail-closed validation: abort the whole migration (transaction) unless the
-- three authorized greenhouses are now owned by the confirmed user. Anonymous
-- DO block — no persistent function, no SECURITY DEFINER.
DO $$
DECLARE
    assigned_count integer;
BEGIN
    SELECT COUNT(*)
    INTO assigned_count
    FROM public.greenhouses
    WHERE id IN (
        '612de050-1044-4597-8ce7-e247052c4ce3'::uuid,
        '7f547080-ca5a-4e4a-9076-b82637c7251c'::uuid,
        'edbfe17c-2b6e-405a-a3b2-93af1fb75cf8'::uuid
    )
    AND owner_user_id = '3e9b9a22-2742-455a-b4d8-1a5b61d0ebde'::uuid;

    IF assigned_count <> 3 THEN
        RAISE EXCEPTION
            'Spec 022 legacy ownership backfill failed: expected 3 greenhouses assigned to owner 3e9b9a22-2742-455a-b4d8-1a5b61d0ebde, found %',
            assigned_count;
    END IF;
END
$$;

-- ----------------------------------------------------------------------------
-- 3-4. Replace global UNIQUE(name) with per-owner UNIQUE(owner_user_id, name)
-- ----------------------------------------------------------------------------
-- Two users may each own a greenhouse named "USB"; a single owner may not have
-- two greenhouses with the same name.
ALTER TABLE public.greenhouses
    DROP CONSTRAINT IF EXISTS greenhouses_name_key;

ALTER TABLE public.greenhouses
    DROP CONSTRAINT IF EXISTS greenhouses_owner_user_id_name_key;

ALTER TABLE public.greenhouses
    ADD CONSTRAINT greenhouses_owner_user_id_name_key
    UNIQUE (owner_user_id, name);

-- ----------------------------------------------------------------------------
-- 5. Enable RLS on all tenant tables (idempotent; safe if already enabled)
-- ----------------------------------------------------------------------------
ALTER TABLE public.greenhouses         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.modules             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.monitorings         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.monitoring_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.snapshots           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inspection_results  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_logs       ENABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------------------------
-- 6. Drop the exact permissive "authenticated" policies (per confirmed names)
-- ----------------------------------------------------------------------------
-- greenhouses
DROP POLICY IF EXISTS "Authenticated users can create greenhouses" ON public.greenhouses;
DROP POLICY IF EXISTS "Authenticated users can delete greenhouses" ON public.greenhouses;
DROP POLICY IF EXISTS "Authenticated users can read greenhouses"   ON public.greenhouses;
DROP POLICY IF EXISTS "Authenticated users can update greenhouses" ON public.greenhouses;

-- modules
DROP POLICY IF EXISTS "Authenticated users can create modules" ON public.modules;
DROP POLICY IF EXISTS "Authenticated users can delete modules" ON public.modules;
DROP POLICY IF EXISTS "Authenticated users can read modules"   ON public.modules;
DROP POLICY IF EXISTS "Authenticated users can update modules" ON public.modules;

-- monitorings
DROP POLICY IF EXISTS "Authenticated users can create monitorings" ON public.monitorings;
DROP POLICY IF EXISTS "Authenticated users can delete monitorings" ON public.monitorings;
DROP POLICY IF EXISTS "Authenticated users can read monitorings"   ON public.monitorings;
DROP POLICY IF EXISTS "Authenticated users can update monitorings" ON public.monitorings;

-- monitoring_metrics
DROP POLICY IF EXISTS "Authenticated users can create monitoring metrics" ON public.monitoring_metrics;
DROP POLICY IF EXISTS "Authenticated users can delete monitoring metrics" ON public.monitoring_metrics;
DROP POLICY IF EXISTS "Authenticated users can read monitoring metrics"   ON public.monitoring_metrics;
DROP POLICY IF EXISTS "Authenticated users can update monitoring metrics" ON public.monitoring_metrics;

-- snapshots
DROP POLICY IF EXISTS "Authenticated users can create snapshots" ON public.snapshots;
DROP POLICY IF EXISTS "Authenticated users can delete snapshots" ON public.snapshots;
DROP POLICY IF EXISTS "Authenticated users can read snapshots"   ON public.snapshots;
DROP POLICY IF EXISTS "Authenticated users can update snapshots" ON public.snapshots;

-- inspection_results
DROP POLICY IF EXISTS "Authenticated users can create inspection results" ON public.inspection_results;
DROP POLICY IF EXISTS "Authenticated users can delete inspection results" ON public.inspection_results;
DROP POLICY IF EXISTS "Authenticated users can read inspection results"   ON public.inspection_results;
DROP POLICY IF EXISTS "Authenticated users can update inspection results" ON public.inspection_results;

-- activity_logs
DROP POLICY IF EXISTS "Authenticated users can create activity logs" ON public.activity_logs;
DROP POLICY IF EXISTS "Authenticated users can delete activity logs" ON public.activity_logs;
DROP POLICY IF EXISTS "Authenticated users can read activity logs"   ON public.activity_logs;
DROP POLICY IF EXISTS "Authenticated users can update activity logs" ON public.activity_logs;

-- ----------------------------------------------------------------------------
-- 7. GREENHOUSES — owner-scoped policies (root of ownership)
-- ----------------------------------------------------------------------------
CREATE POLICY greenhouses_owner_select ON public.greenhouses
    FOR SELECT TO authenticated
    USING (owner_user_id = (select auth.uid()));

CREATE POLICY greenhouses_owner_insert ON public.greenhouses
    FOR INSERT TO authenticated
    WITH CHECK (owner_user_id = (select auth.uid()));

CREATE POLICY greenhouses_owner_update ON public.greenhouses
    FOR UPDATE TO authenticated
    USING (owner_user_id = (select auth.uid()))
    WITH CHECK (owner_user_id = (select auth.uid()));

CREATE POLICY greenhouses_owner_delete ON public.greenhouses
    FOR DELETE TO authenticated
    USING (owner_user_id = (select auth.uid()));

-- ----------------------------------------------------------------------------
-- 8. DESCENDANTS — ownership inherited via FK chain to greenhouses.
--    NO owner_user_id column is added to child tables.
--    UPDATE has USING (current-row ownership) AND WITH CHECK (resulting-row
--    ownership) so a row cannot be reparented into another user's hierarchy.
-- ----------------------------------------------------------------------------

-- modules: greenhouse_id -> greenhouses.id
CREATE POLICY modules_owner_select ON public.modules
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.greenhouses g
        WHERE g.id = modules.greenhouse_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY modules_owner_insert ON public.modules
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.greenhouses g
        WHERE g.id = modules.greenhouse_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY modules_owner_update ON public.modules
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.greenhouses g
        WHERE g.id = modules.greenhouse_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.greenhouses g
        WHERE g.id = modules.greenhouse_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY modules_owner_delete ON public.modules
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.greenhouses g
        WHERE g.id = modules.greenhouse_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- monitorings: module_id -> modules.id -> greenhouses.id
CREATE POLICY monitorings_owner_select ON public.monitorings
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = monitorings.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitorings_owner_insert ON public.monitorings
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = monitorings.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitorings_owner_update ON public.monitorings
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = monitorings.module_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = monitorings.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitorings_owner_delete ON public.monitorings
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = monitorings.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- monitoring_metrics: monitoring_id -> monitorings -> modules -> greenhouses
CREATE POLICY monitoring_metrics_owner_select ON public.monitoring_metrics
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = monitoring_metrics.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitoring_metrics_owner_insert ON public.monitoring_metrics
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = monitoring_metrics.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitoring_metrics_owner_update ON public.monitoring_metrics
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = monitoring_metrics.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = monitoring_metrics.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY monitoring_metrics_owner_delete ON public.monitoring_metrics
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = monitoring_metrics.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- snapshots: monitoring_id -> monitorings -> modules -> greenhouses
CREATE POLICY snapshots_owner_select ON public.snapshots
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = snapshots.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY snapshots_owner_insert ON public.snapshots
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = snapshots.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY snapshots_owner_update ON public.snapshots
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = snapshots.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = snapshots.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY snapshots_owner_delete ON public.snapshots
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.monitorings mon
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE mon.id = snapshots.monitoring_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- inspection_results: snapshot_id -> snapshots -> monitorings -> modules -> greenhouses
CREATE POLICY inspection_results_owner_select ON public.inspection_results
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.snapshots s
        JOIN public.monitorings mon ON mon.id = s.monitoring_id
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE s.id = inspection_results.snapshot_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY inspection_results_owner_insert ON public.inspection_results
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.snapshots s
        JOIN public.monitorings mon ON mon.id = s.monitoring_id
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE s.id = inspection_results.snapshot_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY inspection_results_owner_update ON public.inspection_results
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.snapshots s
        JOIN public.monitorings mon ON mon.id = s.monitoring_id
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE s.id = inspection_results.snapshot_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.snapshots s
        JOIN public.monitorings mon ON mon.id = s.monitoring_id
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE s.id = inspection_results.snapshot_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY inspection_results_owner_delete ON public.inspection_results
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.snapshots s
        JOIN public.monitorings mon ON mon.id = s.monitoring_id
        JOIN public.modules m ON m.id = mon.module_id
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE s.id = inspection_results.snapshot_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- activity_logs: module_id -> modules -> greenhouses
CREATE POLICY activity_logs_owner_select ON public.activity_logs
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = activity_logs.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY activity_logs_owner_insert ON public.activity_logs
    FOR INSERT TO authenticated
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = activity_logs.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY activity_logs_owner_update ON public.activity_logs
    FOR UPDATE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = activity_logs.module_id
          AND g.owner_user_id = (select auth.uid())
    ))
    WITH CHECK (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = activity_logs.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

CREATE POLICY activity_logs_owner_delete ON public.activity_logs
    FOR DELETE TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.modules m
        JOIN public.greenhouses g ON g.id = m.greenhouse_id
        WHERE m.id = activity_logs.module_id
          AND g.owner_user_id = (select auth.uid())
    ));

-- ----------------------------------------------------------------------------
-- 9. STORAGE — replace ONLY the 4 open policies of the snapshots bucket.
--    Ownership resolved from the object path:
--      segment 1 = 'monitorings', segment 2 = monitoring UUID
--    Uses text comparison (mon.id::text = split_part(name,'/',2)) to avoid an
--    unsafe cast of arbitrary text to UUID. Do NOT change the path.
-- ----------------------------------------------------------------------------
DROP POLICY IF EXISTS "Authenticated users can read tomato monitor snapshots"   ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can upload tomato monitor snapshots" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can update tomato monitor snapshots" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can delete tomato monitor snapshots" ON storage.objects;

CREATE POLICY tomato_monitor_snapshots_owner_select ON storage.objects
    FOR SELECT TO authenticated
    USING (
        bucket_id = 'tomato-monitor-snapshots'
        AND split_part(storage.objects.name, '/', 1) = 'monitorings'
        AND EXISTS (
            SELECT 1
            FROM public.monitorings mon
            JOIN public.modules m ON m.id = mon.module_id
            JOIN public.greenhouses g ON g.id = m.greenhouse_id
            WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
              AND g.owner_user_id = (select auth.uid())
        )
    );

CREATE POLICY tomato_monitor_snapshots_owner_insert ON storage.objects
    FOR INSERT TO authenticated
    WITH CHECK (
        bucket_id = 'tomato-monitor-snapshots'
        AND split_part(storage.objects.name, '/', 1) = 'monitorings'
        AND EXISTS (
            SELECT 1
            FROM public.monitorings mon
            JOIN public.modules m ON m.id = mon.module_id
            JOIN public.greenhouses g ON g.id = m.greenhouse_id
            WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
              AND g.owner_user_id = (select auth.uid())
        )
    );

CREATE POLICY tomato_monitor_snapshots_owner_update ON storage.objects
    FOR UPDATE TO authenticated
    USING (
        bucket_id = 'tomato-monitor-snapshots'
        AND split_part(storage.objects.name, '/', 1) = 'monitorings'
        AND EXISTS (
            SELECT 1
            FROM public.monitorings mon
            JOIN public.modules m ON m.id = mon.module_id
            JOIN public.greenhouses g ON g.id = m.greenhouse_id
            WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
              AND g.owner_user_id = (select auth.uid())
        )
    )
    WITH CHECK (
        bucket_id = 'tomato-monitor-snapshots'
        AND split_part(storage.objects.name, '/', 1) = 'monitorings'
        AND EXISTS (
            SELECT 1
            FROM public.monitorings mon
            JOIN public.modules m ON m.id = mon.module_id
            JOIN public.greenhouses g ON g.id = m.greenhouse_id
            WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
              AND g.owner_user_id = (select auth.uid())
        )
    );

-- DELETE needs a fallback (branch B). FASE 0 deletes the DB root FIRST, letting
-- ON DELETE CASCADE remove monitoring -> module -> greenhouse, and only THEN
-- cleans up the Storage objects. After the cascade the domain hierarchy no
-- longer exists, so the path-based owner resolution (branch A) can no longer
-- authorize the cleanup. To let the same authenticated user remove the now-
-- orphaned objects it created, allow DELETE when Storage's own owner_id matches
-- auth.uid(). This fallback is DELETE-ONLY: SELECT/INSERT/UPDATE never use it.
CREATE POLICY tomato_monitor_snapshots_owner_delete ON storage.objects
    FOR DELETE TO authenticated
    USING (
        bucket_id = 'tomato-monitor-snapshots'
        AND split_part(storage.objects.name, '/', 1) = 'monitorings'
        AND (
            -- Branch A: hierarchy still exists and belongs to the user.
            EXISTS (
                SELECT 1
                FROM public.monitorings mon
                JOIN public.modules m ON m.id = mon.module_id
                JOIN public.greenhouses g ON g.id = m.greenhouse_id
                WHERE mon.id::text = split_part(storage.objects.name, '/', 2)
                  AND g.owner_user_id = (select auth.uid())
            )
            -- Branch B (DELETE-only fallback): hierarchy already removed by
            -- FASE 0 cascade, but the object was created by this same user.
            OR storage.objects.owner_id = (select auth.uid()::text)
        )
    );

-- ----------------------------------------------------------------------------
-- 10. GRANTS — least privilege for the 7 tenant tables.
--     First REVOKE ALL previously-inherited privileges (e.g. TRUNCATE, TRIGGER,
--     REFERENCES) from anon and authenticated, then GRANT exactly the CRUD the
--     normal flow needs to authenticated. RLS still applies on top of these
--     grants. anon keeps NO privilege on these tables. service_role, postgres,
--     supabase_admin, storage.objects and activity_types are left unchanged.
-- ----------------------------------------------------------------------------
REVOKE ALL PRIVILEGES ON TABLE public.greenhouses FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.greenhouses FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.greenhouses TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.modules FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.modules FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.modules TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.monitorings FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.monitorings FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.monitorings TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.monitoring_metrics FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.monitoring_metrics FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.monitoring_metrics TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.snapshots FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.snapshots FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.snapshots TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.inspection_results FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.inspection_results FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.inspection_results TO authenticated;

REVOKE ALL PRIVILEGES ON TABLE public.activity_logs FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.activity_logs FROM authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.activity_logs TO authenticated;

COMMIT;
