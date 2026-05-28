-- 016_add_search_vectors.sql
--
-- Memory System S6 — Past Conversation Search.
--
-- Adds full-text search infrastructure so the search_past_conversations
-- AI tool (and the GET /api/v1/search endpoint) can pull historical
-- messages + conversation titles by keyword. user_memories already
-- had a tsvector + GIN index from migration 014 (S1), so this
-- migration only touches messages + conversations.
--
-- Storage uses the 'simple' tsvector dictionary because:
--   - content is mixed Thai + English + identifiers; 'english'
--     stemming butchers Thai
--   - we don't ship a Thai stemmer in postgres
--   - keyword search across mixed-language corpora benefits more
--     from token literals than from aggressive stemming
--
-- Idempotent — safe to re-run.
--
-- NOTE on table name: the messages table is chat.messages_masked
-- (a schema-name carry-over from the gateway-only build before
-- chat-edge stored real content). The S6 spec's "chat.messages" is
-- the logical concept; the physical table is .messages_masked.

-- ---------------------------------------------------------------------------
-- chat.messages_masked — search_vector + trigger + backfill
-- ---------------------------------------------------------------------------

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS search_vector tsvector;

CREATE INDEX IF NOT EXISTS idx_messages_search
    ON chat.messages_masked USING gin(search_vector);

-- Composite index for the user-scoped query pattern the search
-- service uses: JOIN through conversations to filter by user_id,
-- then walk back into messages by conversation_id. Already-existing
-- idx_messages_conversation (from migration 002) covers (conv_id,
-- created_at) ASC; this one adds DESC ordering so the "most
-- recent first" tiebreaker is index-served.
CREATE INDEX IF NOT EXISTS idx_messages_conv_created_desc
    ON chat.messages_masked(conversation_id, created_at DESC);

-- Trigger keeps search_vector in sync with content_masked on every
-- write. We index ONLY user + assistant messages — system /
-- placeholder rows have no semantic search value and would dilute
-- the ranking.
CREATE OR REPLACE FUNCTION chat.update_message_search_vector() RETURNS trigger AS $$
BEGIN
    IF NEW.role IN ('user', 'assistant') THEN
        NEW.search_vector := to_tsvector('simple', COALESCE(NEW.content_masked, ''));
    ELSE
        NEW.search_vector := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS messages_search_vector_trigger ON chat.messages_masked;
CREATE TRIGGER messages_search_vector_trigger
    BEFORE INSERT OR UPDATE OF content_masked ON chat.messages_masked
    FOR EACH ROW EXECUTE FUNCTION chat.update_message_search_vector();

-- Backfill — only user/assistant rows, only where the vector
-- is still NULL. Run once; the trigger keeps things fresh from
-- here on.
UPDATE chat.messages_masked
   SET search_vector = to_tsvector('simple', COALESCE(content_masked, ''))
 WHERE role IN ('user', 'assistant')
   AND search_vector IS NULL;

-- ---------------------------------------------------------------------------
-- chat.conversations — search_vector on title + trigger + backfill
-- ---------------------------------------------------------------------------

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS search_vector tsvector;

CREATE INDEX IF NOT EXISTS idx_conversations_search
    ON chat.conversations USING gin(search_vector);

CREATE OR REPLACE FUNCTION chat.update_conversation_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple', COALESCE(NEW.title, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversations_search_vector_trigger ON chat.conversations;
CREATE TRIGGER conversations_search_vector_trigger
    BEFORE INSERT OR UPDATE OF title ON chat.conversations
    FOR EACH ROW EXECUTE FUNCTION chat.update_conversation_search_vector();

-- Backfill — all rows, since title is NOT NULL.
UPDATE chat.conversations
   SET search_vector = to_tsvector('simple', COALESCE(title, ''))
 WHERE search_vector IS NULL;
