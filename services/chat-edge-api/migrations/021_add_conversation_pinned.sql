-- 021: per-conversation pin for the sidebar "PINNED" section.
--
-- Pinned conversations float to the top of the sidebar in their own
-- section, independent of the TODAY / YESTERDAY / … time buckets. The
-- column is a plain boolean (default false) so every existing row is
-- unpinned. The partial index supports the pinned-first keyset ordering
-- ListByUser uses: (is_pinned DESC, updated_at DESC, id DESC).

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_conversations_pinned_sort
    ON chat.conversations (tenant_id, user_id, is_pinned DESC, updated_at DESC, id DESC)
    WHERE is_deleted = false;
