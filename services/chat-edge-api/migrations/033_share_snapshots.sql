-- 033_share_snapshots.sql
--
-- Batch 2 T3 — share links become SNAPSHOTS. A share row now pins the
-- exact message ids that existed when the link was created; messages
-- sent afterwards never appear on the public view (the old behaviour
-- was a live view — a privacy defect: continuing a conversation kept
-- publishing to recipients until revoke).
--
-- Semantics (owner decisions, 2026-08-19):
--   * storage shape: message-id list. Edits fork NEW rows (branching),
--     so pinned ids keep showing the pre-edit content while it exists;
--   * hard-DELETED/purged messages simply drop out of the shared view —
--     deletion always wins over the snapshot (retention/purge safe:
--     nothing is copied, so nothing outlives the source row);
--   * existing links: FROZEN AT MIGRATION TIME via the backfill below
--     (recipients keep exactly today's view; no link breaks). Any row
--     the backfill misses is lazily snapshotted on first resolve.
--
-- Locking: ADD COLUMN nullable/no-default is metadata-only; the
-- backfill updates only chat.shared_conversations rows (small table)
-- and reads messages per shared conversation.
-- Reversible: ALTER TABLE chat.shared_conversations DROP COLUMN snapshot_message_ids;

ALTER TABLE chat.shared_conversations
  ADD COLUMN IF NOT EXISTS snapshot_message_ids UUID[];

-- Freeze every pre-existing (live-view) link at its current state.
-- Mirrors the Resolve read: active branch only, chronological.
UPDATE chat.shared_conversations s
SET snapshot_message_ids = COALESCE(
  (SELECT array_agg(m.id ORDER BY m.created_at, m.id)
     FROM chat.messages_masked m
    WHERE m.conversation_id = s.conversation_id
      AND m.tenant_id = s.tenant_id
      AND m.is_active = true),
  '{}'::uuid[])
WHERE s.snapshot_message_ids IS NULL;
