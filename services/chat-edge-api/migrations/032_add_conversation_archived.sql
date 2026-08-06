-- 032_add_conversation_archived.sql
--
-- Phase 10 — archive a conversation instead of deleting it.
--
-- Until now the only way to clear a chat out of the sidebar was DELETE,
-- which soft-deletes (is_deleted = true) and hides the thread from every
-- read path. Archiving is the softer, reversible action the big
-- assistants ship: the chat leaves the main list but stays fully
-- readable and can be brought back.
--
-- Why a separate flag instead of reusing is_deleted:
--   * is_deleted means "gone" — Get, Search, message send and every
--     other read path filter it out, and Restore exists only for the
--     undo path. Overloading it would make an archived chat
--     un-openable, which is the opposite of what archive means.
--   * the two states are independent: a chat can be archived and then
--     deleted, and the delete must still win.
-- So: is_archived only ever affects the SIDEBAR LIST. Opening,
-- searching by title, sending into, and exporting an archived chat all
-- keep working exactly as before.
--
-- The partial index mirrors migration 021's (it is the same list query
-- with one more predicate) and covers only live, unarchived rows —
-- which is the hot path.
--
-- Additive + idempotent — safe to re-run.
--
-- Rollback:
--   DROP INDEX IF EXISTS chat.idx_conversations_user_active;
--   ALTER TABLE chat.conversations DROP COLUMN IF EXISTS is_archived;

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_conversations_user_active
    ON chat.conversations (tenant_id, user_id, is_pinned DESC, updated_at DESC, id DESC)
    WHERE is_deleted = false AND is_archived = false;
