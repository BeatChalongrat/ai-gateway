-- 022_create_folders.sql
--
-- Chat folders: user-defined buckets for organising conversations in the
-- sidebar, shown ABOVE the PINNED and TODAY / YESTERDAY / … time buckets.
-- Flat only (no nested folders).
--
-- tenant_id + user_id are BOTH stored on the row (denormalised, matching
-- chat.conversation_states — migration 015) so the read path filters by
-- (tenant_id, user_id) without a JOIN and stays tenant-isolated. The spec
-- only called for user_id; tenant_id is added for consistency with the rest
-- of the chat schema where every user-owned row is also tenant-scoped.
--
-- position drives user-controlled folder ordering (Reorder). folder_id on
-- chat.conversations is the membership link; ON DELETE SET NULL moves a
-- folder's chats back to "unfiled" when the folder is deleted (chats are
-- never lost with the folder).
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.folders (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL,
    user_id    UUID NOT NULL,
    name       VARCHAR(255) NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Every read path filters (tenant_id, user_id) and orders by (position,
-- created_at), so index that tuple to keep ListByUser index-only-ish.
CREATE INDEX IF NOT EXISTS idx_folders_user
    ON chat.folders (tenant_id, user_id, position, created_at);

-- Membership link. ON DELETE SET NULL → deleting a folder unfiles its chats.
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS folder_id UUID
        REFERENCES chat.folders(id) ON DELETE SET NULL;

-- Partial index: only filed conversations carry a folder_id, so the index
-- stays small and the folder-membership count join is fast.
CREATE INDEX IF NOT EXISTS idx_conversations_folder
    ON chat.conversations (folder_id)
    WHERE folder_id IS NOT NULL;
