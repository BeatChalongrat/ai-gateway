-- 013_add_file_user_id.sql
--
-- Memory-system pre-flight (Session S0 — identity binding audit).
--
-- chat.conversation_files predates the multi-user-per-tenant model.
-- Ownership was inferred through the conversation_id → conversations.
-- user_id chain. Sufficient for tenancy isolation; insufficient once
-- (a) tenants legitimately carry multiple human users (Portal "member"
-- role) and (b) the upcoming user_memories table wants to filter user-
-- private artifacts in a single indexed WHERE clause.
--
-- This migration:
--   1. Adds user_id (nullable first so existing rows survive).
--   2. Backfills from the parent conversation row.
--   3. Promotes to NOT NULL so future inserts must populate it.
--   4. Indexes (tenant_id, user_id) — the access pattern the memory
--      system will use to walk "this user's files in this tenant"
--      without re-joining conversations.
--
-- Idempotent — safe to re-run.

ALTER TABLE chat.conversation_files
    ADD COLUMN IF NOT EXISTS user_id UUID;

-- Backfill any rows still missing user_id from the parent conversation.
UPDATE chat.conversation_files f
   SET user_id = c.user_id
  FROM chat.conversations c
 WHERE f.conversation_id = c.id
   AND f.user_id IS NULL;

-- Promote to NOT NULL only if every row has a value. The guarded
-- DO block keeps the migration replayable on partial states (e.g.
-- a conversations row was hard-deleted and an orphan file remains).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM chat.conversation_files WHERE user_id IS NULL
    ) THEN
        ALTER TABLE chat.conversation_files
            ALTER COLUMN user_id SET NOT NULL;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_conv_files_owner
    ON chat.conversation_files (tenant_id, user_id);
