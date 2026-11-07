-- 027: File-attachment dedup key for cross-client sync (FIX 10).
--
-- The SRAN Desktop client uploads user attachments with its deterministic
-- local file id as ``client_id`` so a re-sync (offline retry, reopened
-- conversation) upserts instead of duplicating the blob row. Mirrors
-- migration 026's contract for conversations/messages: unique
-- per-conversation, partial index so rows without a client_id (all web
-- uploads and generated files) pay nothing.
--
-- Additive only (nullable, no backfill): existing reads/writes unaffected.

ALTER TABLE chat.conversation_files ADD COLUMN IF NOT EXISTS client_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_files_client_id
    ON chat.conversation_files (conversation_id, client_id)
    WHERE client_id IS NOT NULL;
