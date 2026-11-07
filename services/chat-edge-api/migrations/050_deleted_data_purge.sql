-- 050_deleted_data_purge.sql — FIX132 C (the user owns their data).
--
-- A chat the user deletes disappears at once (soft delete) and is
-- permanently removed deleted_conversation_purge_days later (30 by
-- default). Until FIX132 that second step ran only for tenants with
-- transcript_retention_enabled = true AND dry_run = false — both off by
-- default — so on the defaults nothing was ever erased.
--
-- The owner decided (2026-09-24) that the deleted-data purge is ON for
-- every tenant. It is now governed by its own two flags, separate from
-- the inactive-transcript rule (retention_expired), which stays opt-in:
--
--   deleted_purge_enabled  (default TRUE)  — erase deleted chats (and
--                                           deleted accounts) after
--                                           deleted_conversation_purge_days
--   deleted_purge_dry_run  (default FALSE) — count only, delete nothing
--
-- A tenant with no settings row gets the same defaults (model/retention.go).
-- The env DELETED_DATA_PURGE_MODE (on | dry_run | off) can hold every
-- tenant at dry_run or off regardless of these rows.
--
-- The partial index serves the nightly "which tenants have deleted chats
-- past the window" scan; the export index serves the sweep that removes
-- expired export files.
--
-- Idempotent — safe to re-run. Reversal:
--   ALTER TABLE chat.tenant_retention_settings
--       DROP COLUMN deleted_purge_enabled, DROP COLUMN deleted_purge_dry_run;
--   DROP INDEX chat.idx_conversations_deleted_at;
--   DROP INDEX chat.idx_data_export_expiry;

ALTER TABLE chat.tenant_retention_settings
    ADD COLUMN IF NOT EXISTS deleted_purge_enabled boolean NOT NULL DEFAULT true;

ALTER TABLE chat.tenant_retention_settings
    ADD COLUMN IF NOT EXISTS deleted_purge_dry_run boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_conversations_deleted_at
    ON chat.conversations (deleted_at)
    WHERE is_deleted;

CREATE INDEX IF NOT EXISTS idx_data_export_expiry
    ON chat.data_export_requests (expires_at)
    WHERE file_path IS NOT NULL;
