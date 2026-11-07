-- 026: Desktop agent-conversation import (Phase 3 data safety).
--
-- The SRAN Desktop client persists agent-mode (gateway-path) turns in local
-- SQLite only; these columns let it push a one-way backup mirror here
-- idempotently. ``client_id`` carries the desktop's local uuid — the natural
-- dedup key, unique per-owner for conversations and per-conversation for
-- messages. Partial indexes so the ~100% of rows without a client_id pay
-- nothing.
--
-- All changes are additive (nullable / defaulted, no backfill): existing
-- reads and writes are unaffected.

ALTER TABLE chat.conversations ADD COLUMN IF NOT EXISTS client_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_client_id
    ON chat.conversations (tenant_id, user_id, client_id)
    WHERE client_id IS NOT NULL;

ALTER TABLE chat.messages_masked ADD COLUMN IF NOT EXISTS client_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_id
    ON chat.messages_masked (conversation_id, client_id)
    WHERE client_id IS NOT NULL;

-- origin distinguishes how the conversation came to exist:
--   'server'        — created through the normal chat flow (default)
--   'desktop_agent' — imported backup mirror of a desktop agent-mode chat
-- Deliberately no CHECK constraint: future origins must not break old
-- deployments that haven't applied the migration adding them.
ALTER TABLE chat.conversations ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'server';
