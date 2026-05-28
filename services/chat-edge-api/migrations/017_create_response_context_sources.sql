-- 017_create_response_context_sources.sql
--
-- Memory System S7 — Source Attribution.
--
-- Records which context sources were used to produce each
-- assistant message so the UI can render a "Sources used" card
-- under the response. Without this the AI's reliance on saved
-- memories / past chats / conversation state is invisible to the
-- user — they have no way to know "how did it know my name?".
--
-- One row per (assistant message, source). A single response can
-- carry 3-10 sources easily (a few memories + state + a search
-- hit). The query pattern is "give me all sources for message X"
-- and the batch variant "for these N messages" used by the
-- conversation message-list endpoint to avoid N+1.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.response_context_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The assistant message that USED this source. Logical FK only
    -- (no enforced reference) because the message table is
    -- chat.messages_masked and we'd rather a dangling row survive
    -- than a delete cascade we didn't plan for.
    response_message_id UUID NOT NULL,
    tenant_id           UUID NOT NULL,
    user_id             UUID NOT NULL,
    conversation_id     UUID NOT NULL,

    -- What kind of source.
    --   'saved_memory'        — from <saved_user_memory> block (S1)
    --   'conversation_state'  — from <conversation_state> block (S3)
    --   'past_conversation'   — hit from search_past_conversations (S6)
    --   'file_context'        — uploaded / generated file (future)
    --   'compacted_history'   — older messages folded into state (S4)
    source_type VARCHAR(30) NOT NULL,

    -- Pointer at the actual source row:
    --   memory_id           for saved_memory
    --   conversation_id     for past_conversation
    --   conversation_id     for conversation_state (own conv)
    --   file_id             for file_context
    --   NULL                for compacted_history
    source_id UUID,

    -- Human-readable label rendered in the UI card. Truncated by
    -- the writer (80-120 chars) so the wire payload stays small.
    display_label TEXT NOT NULL,

    -- Optional per-type metadata, e.g.
    --   {"memory_type":"explicit_fact","category":"identity"}
    --   {"conversation_title":"DLP Review","message_snippet":"..."}
    --   {"filename":"report.xlsx","file_type":"xlsx"}
    metadata JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot path: "all sources for this message" — message-list rendering
-- (batched) and the per-message API call both filter on this.
CREATE INDEX IF NOT EXISTS idx_response_sources_message
    ON chat.response_context_sources(response_message_id);

-- User-scope index for the memory-system pre-flight pattern: every
-- read filters by user_id + tenant_id first so a leaked message id
-- from another tenant can't surface here.
CREATE INDEX IF NOT EXISTS idx_response_sources_user
    ON chat.response_context_sources(user_id, tenant_id);
