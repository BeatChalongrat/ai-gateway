-- 002_create_chat_schema.sql
--
-- The chat domain stores ONLY masked / pseudonymized content. Raw
-- user prompts and restored AI replies are transient and never
-- written to these tables. This is the privacy contract the
-- Gateway integration relies on.

CREATE SCHEMA IF NOT EXISTS chat;

CREATE TABLE chat.conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL,
    user_id       UUID NOT NULL,
    -- ``title`` stores DLP-sanitized text. The TitleService routes
    -- the candidate title through Gateway and persists the
    -- pseudonymized form so the sidebar never reveals raw PII.
    title         TEXT NOT NULL DEFAULT 'New conversation',
    model_id      TEXT NOT NULL,
    message_count INT  NOT NULL DEFAULT 0,
    is_deleted    BOOLEAN NOT NULL DEFAULT false,
    deleted_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_conversations_tenant_user ON chat.conversations(tenant_id, user_id);
CREATE INDEX idx_conversations_updated     ON chat.conversations(updated_at DESC);

CREATE TABLE chat.messages_masked (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id    UUID NOT NULL REFERENCES chat.conversations(id),
    tenant_id          UUID NOT NULL,
    user_id            UUID NOT NULL,
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    -- ``content_masked`` carries pseudonym tokens only (e.g.
    -- ``<<PERSON_1>>``, ``<<INCOME_1>>``). Anything raw is a bug.
    content_masked     TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'completed'
                       CHECK (status IN ('pending', 'streaming', 'completed', 'cancelled', 'error')),
    model_id           TEXT,
    token_usage_input  INT,
    token_usage_output INT,
    -- Pointer at Gateway's secure-trace row. Lets an authorized
    -- admin pull the matching raw / restored snapshots from the
    -- Gateway-owned ``security.secure_trace`` table without those
    -- ever crossing into chat-edge.
    secure_trace_id    UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_conversation ON chat.messages_masked(conversation_id, created_at);
CREATE INDEX idx_messages_tenant       ON chat.messages_masked(tenant_id);
CREATE INDEX idx_messages_created      ON chat.messages_masked(created_at);

-- Thumbs-only feedback in Phase 1. A free-text comment field would
-- be a new PII ingress channel and is deferred to Phase 2 once it
-- picks up the same DLP-sanitize-on-write treatment as titles.
CREATE TABLE chat.feedback (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES chat.messages_masked(id),
    tenant_id  UUID NOT NULL,
    user_id    UUID NOT NULL,
    rating     TEXT NOT NULL CHECK (rating IN ('thumbs_up', 'thumbs_down')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feedback_message ON chat.feedback(message_id);
