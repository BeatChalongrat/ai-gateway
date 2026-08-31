-- 038_compaction_usage.sql
--
-- FIX33 A-W8 — the token cost of compaction, kept apart from the cost
-- of answering (Claude's `usage.iterations[]`). One row per extraction
-- call the ConversationStateService makes, auto or manual. Read by
-- GET /usage/me (per-user totals for the period) so users and admins
-- can see that compacting is not free.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.compaction_usage (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL,
    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    model_id        TEXT NOT NULL DEFAULT '',
    trigger         TEXT NOT NULL DEFAULT 'auto',   -- auto | manual | background
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0,
    messages_folded INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_compaction_usage_user_period
    ON chat.compaction_usage(tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compaction_usage_conv
    ON chat.compaction_usage(conversation_id, created_at DESC);
