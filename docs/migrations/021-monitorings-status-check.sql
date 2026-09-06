-- ============================================================================
-- Spec 021 — Prerequisite REMOTE migration (Supabase / PostgreSQL)
-- File: docs/migrations/021-monitorings-status-check.sql
--
-- PURPOSE
--   Add the value 'ready_for_analysis' (introduced by Spec 020) to the CHECK
--   constraint of public.monitorings.status so that upserting a monitoring in
--   that state is not rejected by the remote backend.
--
-- SCOPE (STRICTLY LIMITED)
--   - Modifies ONLY the CHECK constraint named `ck_monitorings_status` on the
--     column public.monitorings.status.
--   - Preserves every previously allowed value.
--   - Does NOT touch primary keys, foreign keys, UUID columns, RLS policies,
--     indexes, triggers, or any table relationship.
--   - Does NOT add, drop, or rename any column or table.
--
-- !!! HUMAN REVIEW REQUIRED — DO NOT APPLY AUTOMATICALLY !!!
--   This is a design/documentation artifact only. It must be reviewed by a
--   human and applied manually against Supabase, inside a transaction, after
--   confirming the real constraint name and current allowed values in the
--   target database. It is intentionally NOT executed by the application and
--   the application never connects to Supabase to modify schema.
--
--   Verify the current constraint before applying, e.g.:
--     SELECT conname, pg_get_constraintdef(oid)
--     FROM pg_constraint
--     WHERE conrelid = 'public.monitorings'::regclass
--       AND contype = 'c';
-- ============================================================================

BEGIN;

-- Drop the existing status CHECK constraint (name per approved design:
-- ck_monitorings_status). If the deployed name differs, a reviewer must adjust
-- this statement to match the actual constraint name found above.
ALTER TABLE public.monitorings
    DROP CONSTRAINT IF EXISTS ck_monitorings_status;

-- Recreate it adding 'ready_for_analysis' while preserving all prior values.
ALTER TABLE public.monitorings
    ADD CONSTRAINT ck_monitorings_status
    CHECK (
        status IN (
            'initializing',
            'running',
            'paused',
            'finishing',
            'analyzing',
            'ready_for_analysis',
            'completed',
            'aborted',
            'error'
        )
    );

COMMIT;
