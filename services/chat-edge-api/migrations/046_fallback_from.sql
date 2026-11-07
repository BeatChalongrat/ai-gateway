-- FIX120 — a rescued turn says which model was asked for.
--
-- fallback_from: the model the person (or the conversation) requested when
-- a DIFFERENT model produced the answer — the gateway's fallback chain
-- rescued the turn and said so in a ``fallback_info`` frame. model_id on
-- the same row already names the model that answered (FIX35). Until now
-- the pair lived only in the page's memory: a reload lost the notice and
-- the swap looked like a normal turn. NULL when the requested model
-- answered, on user rows, and on rows written before this migration.
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS fallback_from TEXT NULL;

COMMENT ON COLUMN chat.messages_masked.fallback_from IS
    'FIX120 — the requested model when a fallback model answered (model_id names the one that did); NULL = no fallback / user row / pre-046';
