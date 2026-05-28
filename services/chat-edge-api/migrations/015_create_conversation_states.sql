-- 015_create_conversation_states.sql
--
-- Memory System S3 — Structured Conversation State.
--
-- Long conversations (100+ messages) silently lose context once the
-- per-message-history cap in MessageService.maxContext kicks in.
-- This table stores a structured snapshot of the important stuff
-- (goal, decisions, artifacts, open tasks, rejected approaches,
-- unresolved questions) so the message flow can inject it into the
-- system prompt and the AI stops forgetting decisions made 80
-- messages ago.
--
-- One row per conversation; conversation_id is PK so the upsert
-- path is straightforward. tenant_id + user_id are denormalised
-- onto the row so the read path doesn't have to JOIN back to
-- chat.conversations just to verify ownership.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.conversation_states (
    conversation_id UUID PRIMARY KEY REFERENCES chat.conversations(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    user_id   UUID NOT NULL,

    -- One-sentence goal — the closest thing to a "title" for the
    -- intent of the conversation. NULL until the first extraction
    -- pass populates it.
    current_goal TEXT,

    -- Free-form structured context the AI maintains. Typical shape:
    --   {"key_context": "...", "progress": "...", "technical_details": "..."}
    -- Stored as JSONB so we can add fields without a migration.
    structured_summary JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Arrays-of-objects. Each entry has a single load-bearing string
    -- field plus optional metadata (status / version / message_id).
    -- The service layer's appendToJSONArray / removeFromJSONArray
    -- helpers dedup on the load-bearing key.
    confirmed_decisions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    active_artifacts     JSONB NOT NULL DEFAULT '[]'::jsonb,
    open_tasks           JSONB NOT NULL DEFAULT '[]'::jsonb,
    rejected_approaches  JSONB NOT NULL DEFAULT '[]'::jsonb,
    unresolved_questions JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Compaction tracking. summarized_up_to_message_id pins the last
    -- message_id that was folded into the summary so the next
    -- extraction pass only processes messages AFTER that point.
    -- message_count_at_summary lets the auto-trigger decide "have
    -- we accumulated enough new messages to re-extract?".
    summarized_up_to_message_id UUID,
    message_count_at_summary    INTEGER NOT NULL DEFAULT 0,

    -- Approximate token cost of the state block, recorded so future
    -- token-budget logic can decide whether to truncate before
    -- injecting. Not actively used in S3.
    summary_token_count INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Memory-system pre-flight (S0 contract): every read path through
-- this table will filter by user_id + tenant_id, so index that pair.
CREATE INDEX IF NOT EXISTS idx_conversation_states_user
    ON chat.conversation_states(user_id, tenant_id);
