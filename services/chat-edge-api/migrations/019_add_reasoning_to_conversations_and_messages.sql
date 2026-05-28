-- T4 — Thinking Mode UI.
--
-- chat.conversations gains a per-conversation reasoning preference
-- (mirrors the memory_enabled pattern from migration 014). The
-- frontend toggle persists here so a reload restores the user's
-- thinking-mode setting per conversation.
--
-- chat.messages_masked gains reasoning_content so an assistant turn
-- that the model thought through can replay its reasoning when the
-- user revisits the conversation. Stored masked; raw reasoning text
-- never reaches this table (the Gateway's outbound DLP scan on the
-- reasoning channel runs before the chat-edge sees the fragments).

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS reasoning_enabled BOOLEAN NOT NULL DEFAULT false;

-- Mode values: 'off' | 'auto' | 'low' | 'medium' | 'high'. Stored
-- even when reasoning_enabled=false so flipping the toggle on
-- restores the previously-chosen mode rather than resetting to
-- 'auto'. Default 'auto' is the Gateway's neutral baseline.
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS reasoning_mode VARCHAR(20) NOT NULL DEFAULT 'auto';

-- show_summary controls whether the SSE stream emits reasoning
-- text to the client. Default true matches the spec contract
-- (user enabled reasoning → user expects to see the analysis).
-- A future operator toggle could flip the default to false for
-- "think silently for better answers" mode.
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS show_reasoning_summary BOOLEAN NOT NULL DEFAULT true;

-- Per-message reasoning history. Nullable so non-reasoning
-- responses don't pay the storage. TEXT (not VARCHAR) because
-- thinking summaries can run thousands of tokens on hard problems.
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS reasoning_content TEXT;
