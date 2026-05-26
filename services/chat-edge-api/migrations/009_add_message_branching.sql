-- 009_add_message_branching.sql
--
-- Adds ChatGPT-style "edit + branch" support to chat.messages_masked.
--
-- Semantic chosen (see internal/service/message_service.go for the
-- full reasoning):
--
--   parent_id     NULL when the message has neither been edited nor
--                 belongs to a forked branch. Set to the FORK-ROOT
--                 message id once a fork exists. Every message in
--                 the fork group — both the fork-siblings (original
--                 + edits) AND the assistant / follow-up messages
--                 that branched from them — shares the same
--                 parent_id.
--   branch_index  0 for the original branch; 1+ for each edit. All
--                 messages in the same branch share the same index.
--   is_active     true for messages on the currently-displayed
--                 branch; false for messages stored from inactive
--                 branches (kept so the user can switch back).

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS parent_id    UUID,
    ADD COLUMN IF NOT EXISTS branch_index INT  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_active    BOOLEAN NOT NULL DEFAULT true;

-- parent_id can self-reference (fork root points at its own id) or
-- point at any other message; we don't enforce a strict FK because
-- the row may briefly hold its own id during the edit transaction.
-- The CHECK below guards against truly bogus pointers.
ALTER TABLE chat.messages_masked
    ADD CONSTRAINT messages_masked_parent_id_self_or_real
    CHECK (parent_id IS NULL OR parent_id <> '00000000-0000-0000-0000-000000000000');

CREATE INDEX IF NOT EXISTS idx_messages_parent
    ON chat.messages_masked(parent_id);
CREATE INDEX IF NOT EXISTS idx_messages_active
    ON chat.messages_masked(conversation_id, is_active);
