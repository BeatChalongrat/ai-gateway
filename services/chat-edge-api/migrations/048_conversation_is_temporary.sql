-- 048 — FIX127 B1: an explicit "temporary chat" flag.
--
-- Until now a temporary chat was recognised only by memory_enabled = false,
-- so the title service (title_service.go planTitle: "temporary_chat") also
-- skipped every ordinary chat whose owner had switched memory off, and that
-- chat stayed "New conversation" for good. Temporary now means "the user
-- started it as temporary" (sidebar button, quick window, memory page),
-- recorded at creation. Memory off remains a separate, per-chat switch.
--
-- Additive and idempotent. No backfill: existing memory-off chats are
-- ordinary chats with memory off (they get a title on their next first
-- successful turn, if they still have the placeholder).
--
-- Reversal: ALTER TABLE chat.conversations DROP COLUMN is_temporary;

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS is_temporary BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN chat.conversations.is_temporary IS
    'FIX127 B1: the user started this chat as temporary. The only marker the title service honours; memory_enabled is a separate switch.';
