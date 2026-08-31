-- 037_conversation_state_manual_compact.sql
--
-- FIX33 A-W7 / A-W7b — manual compaction + per-conversation compaction
-- instructions (Claude's `/compact` + `instructions`).
--
--   manual_compact_boundary_message_id — when set, the ContextCompiler drops
--     every loaded message at or before this id on the next turn (the first
--     exchange still survives, A-W4), regardless of the token budget. Set by
--     POST /conversations/{id}/compact after the state extraction that covers
--     those messages has succeeded — never before, so a compact request that
--     fails to summarise drops nothing.
--   compaction_instructions — the user's guidance for the summariser ("keep
--     the code snippets and variable names", "drop the debugging"). Folded
--     into EVERY later extraction prompt for this conversation, auto or
--     manual, until replaced.
--
-- Idempotent — safe to re-run.

ALTER TABLE chat.conversation_states
    ADD COLUMN IF NOT EXISTS manual_compact_boundary_message_id UUID,
    ADD COLUMN IF NOT EXISTS compaction_instructions TEXT NOT NULL DEFAULT '';
