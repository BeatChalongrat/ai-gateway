-- FIX74 — the question card on the web path: a turn that calls
-- ask_user_question is PARKED here and resumed when the user answers.
--
-- Why a table and not a goroutine: a held response dies on every chat-edge
-- restart (several a day on the dev box) and on tab close, and it pins the
-- answer to the instance that owns the goroutine. A row survives all of
-- that and costs nothing while it waits. The row holds the exact gateway
-- request that was in flight (request_json), so the continuation is an
-- ordinary new generation on the same assistant message.
--
-- status: pending → answered | skipped | cancelled | superseded | expired.
-- Exactly ONE pending question per conversation (partial unique index):
-- one card at a time is a property of the data, not of the code.
CREATE TABLE IF NOT EXISTS chat.pending_questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    conversation_id UUID NOT NULL,
    message_id      UUID NOT NULL REFERENCES chat.messages_masked(id) ON DELETE CASCADE,
    tool_call_id    TEXT NOT NULL,
    -- The validated questions the card renders (same shape the desktop
    -- bridge posts): [{question, header, multiSelect, options:[{label,description}]}]
    questions_json  JSONB NOT NULL,
    -- The paused turn: {request: <gateway request>, iteration, tools_attached,
    -- served_model, content_prefix, reasoning_prefix, request_id}
    state_json      JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_questions_one_per_conversation
    ON chat.pending_questions(conversation_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_pending_questions_message
    ON chat.pending_questions(message_id);

-- The assistant row's status while its turn is parked. The CHECK from 002
-- (extended in 004) must learn the new value, or the park's persist fails
-- at the database with the card already on screen — the service tests
-- caught exactly that before this block existed.
ALTER TABLE chat.messages_masked
    DROP CONSTRAINT IF EXISTS messages_masked_status_check;
ALTER TABLE chat.messages_masked
    ADD CONSTRAINT messages_masked_status_check
    CHECK (status IN ('pending', 'streaming', 'completed', 'cancelled', 'error', 'regenerated', 'waiting_user'));
