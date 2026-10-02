-- ============================================================================
-- REMOTE corrective migration (Supabase / PostgreSQL): Storage DELETE SELECT.
-- File: docs/migrations/023-storage-delete-select-policy.sql
--
-- FASE 0 deletes the database root before removing its Storage objects. The
-- cascade removes the hierarchy used by the existing SELECT policy, although
-- the DELETE policy can still authorize the object's owner via owner_id.
-- Storage also checks SELECT during deletion, so allow that owner fallback
-- only for the single-object delete operation used by this application.
--
-- This changes only tomato_monitor_snapshots_owner_select. Normal reads still
-- require the active hierarchy. INSERT, UPDATE, and DELETE policies are intact.
--
-- !!! HUMAN REVIEW REQUIRED — DO NOT APPLY AUTOMATICALLY !!!
-- Confirm the deployed policy and storage.allow_only_operation(text) before
-- applying manually. This artifact does not alter existing outbox records.
-- ============================================================================

BEGIN;

DROP POLICY IF EXISTS tomato_monitor_snapshots_owner_select ON storage.objects;

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
                -- Supabase names this endpoint's operation storage.object.delete;
                -- the helper normalizes the storage. prefix before comparison.
                storage.allow_only_operation('object.delete')
                AND storage.objects.owner_id = (select auth.uid()::text)
            )
        )
    );

COMMIT;
