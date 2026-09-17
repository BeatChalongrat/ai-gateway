-- FIX121 — a failed turn keeps the list of what was tried, and a send that
-- arrives twice is one turn.
--
-- attempts: the per-model attempt log the gateway puts on the terminal
-- error frame of a turn whose fallback walk was exhausted
-- ([{provider, model, error_code, error_message, latency_ms, attempted_at}]).
-- Until now it lived only on the live frame: the page showed "Connection
-- attempts (n)" while the turn was on screen, a reload lost it, and the
-- desktop never had it. With the streaming path now walking the Auto
-- router's alternatives one failed turn can name several models, and the
-- day after is when someone asks which. NULL = the turn did not fail this
-- way / a user row / a row written before this migration.
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS attempts JSONB NULL;

COMMENT ON COLUMN chat.messages_masked.attempts IS
    'FIX121 — per-model attempt log of a failed turn, as the terminal error frame carried it; NULL = none / user row / pre-047';

-- One user row per (conversation, run id) on the native send path.
--
-- run_id (044) is the id a turn carries through every layer; a client may
-- supply it as X-Request-Id. The send path now treats a supplied id as the
-- idempotency key: a second POST with the same id replays the first turn
-- instead of creating a second one. The service checks before it inserts;
-- this index is what makes that true when two copies of the request arrive
-- in the same instant — the second insert fails on it and is answered as
-- the replay it is.
--
-- Scope: role = 'user' only (an assistant row is never the arbiter —
-- regenerate and edit mint their own run ids and write no user row with
-- one), and client_id IS NULL only (imported desktop rows are already
-- idempotent on (conversation_id, client_id), migration 026, and INSERT ...
-- ON CONFLICT can name one arbiter). No row written before this migration
-- has role = 'user' with a run_id, so the index builds on any existing
-- database without touching a row.
CREATE UNIQUE INDEX IF NOT EXISTS messages_masked_user_run_uidx
    ON chat.messages_masked (conversation_id, run_id)
    WHERE role = 'user' AND run_id IS NOT NULL AND client_id IS NULL;
