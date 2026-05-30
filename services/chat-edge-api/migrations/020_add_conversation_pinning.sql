-- B2.2 — Pin / favorite conversations.
--
-- A pinned conversation surfaces in a dedicated "Pinned" section at
-- the top of the sidebar, above the time-based groups. Pinning does
-- NOT touch updated_at (the time-group sort for non-pinned chats stays
-- stable); the pinned section sorts by pinned_at DESC so the most
-- recently pinned floats to the top.
--
-- Per-user note: chat.conversations already carries user_id, and every
-- pin/unpin path is ownership-checked at the service layer, so a pin
-- is inherently per-(user, conversation).

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false;

-- Nullable: set to NOW() on pin, cleared to NULL on unpin. Drives the
-- pinned-section ordering.
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;

-- Partial index covering the pinned-section lookup. The pinned set per
-- user is tiny, so a partial index keeps it cheap and out of the way of
-- the main (tenant_id, user_id, updated_at) browse path.
CREATE INDEX IF NOT EXISTS idx_conversations_user_pinned
    ON chat.conversations (tenant_id, user_id, pinned_at DESC)
    WHERE is_pinned = true AND is_deleted = false;
