-- 040_retention_and_deletion_audit.sql
--
-- Two tables backing the retention & deletion program
-- (docs/DATA_RETENTION_PLAN.md):
--
-- 1. chat.deletion_audit — the durable record of every destructive
--    action AND every retention-setting change. This table is the
--    evidence an auditor reads, so three properties are structural:
--      * it is excluded from every purge/cascade (nothing in this
--        service deletes from it except the dedicated aging step,
--        which itself writes an aging entry);
--      * it never stores deleted CONTENT — ids, kinds, and counts
--        only (a "counts" jsonb of {kind: n});
--      * it records the rule that caused the action
--        (retention_expired | user_request | admin_request |
--        settings_change | dry_run) so "why was this deleted?" is
--        answerable per row.
--
-- 2. chat.tenant_retention_settings — per-tenant transcript
--    retention. transcript_retention_days has a DB-level floor of
--    30 (NULL = keep forever); the API layer enforces the same
--    floor so the constraint should never fire in practice.
--    enabled defaults FALSE and dry_run defaults TRUE: a tenant
--    that turns retention on gets a counting pass first, and a
--    human flips dry_run off after reading the numbers.
--
-- Reversal: both tables are new and hold no pre-existing data —
--   DROP TABLE chat.tenant_retention_settings;
--   DROP TABLE chat.deletion_audit;
-- restores the previous schema exactly.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.deletion_audit (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL,
    user_id     uuid,
    actor       text NOT NULL,
    action      text NOT NULL,
    scope       text NOT NULL,
    resource_id uuid,
    rule        text NOT NULL,
    counts      jsonb NOT NULL DEFAULT '{}'::jsonb,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deletion_audit_tenant_time
    ON chat.deletion_audit (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_deletion_audit_action
    ON chat.deletion_audit (action, created_at DESC);

CREATE TABLE IF NOT EXISTS chat.tenant_retention_settings (
    tenant_id                       uuid PRIMARY KEY,
    transcript_retention_enabled    boolean NOT NULL DEFAULT false,
    transcript_retention_days       integer
        CHECK (transcript_retention_days IS NULL
               OR transcript_retention_days >= 30),
    deleted_conversation_purge_days integer NOT NULL DEFAULT 30
        CHECK (deleted_conversation_purge_days >= 1),
    dry_run                         boolean NOT NULL DEFAULT true,
    created_at                      timestamptz NOT NULL DEFAULT now(),
    updated_at                      timestamptz NOT NULL DEFAULT now()
);
