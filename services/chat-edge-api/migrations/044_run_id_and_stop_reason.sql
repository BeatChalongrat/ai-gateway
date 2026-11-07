-- FIX119 — one id per turn, and why the model stopped.
--
-- run_id: the id every frame of the generation carried (STREAM_CONTRACT
-- §2 run_id), the id chat-edge sent upstream as X-Request-Id, the id the
-- gateway and the backend log, and the id the person sees on a failed
-- turn's card. Until now it lived only in the stream broker's memory
-- (ten minutes) and in log lines, so "which turn was that" could not be
-- answered from the row. TEXT, not UUID: a client may supply its own id
-- (the desktop's run id, sanitised to [A-Za-z0-9._-]{8,64} by the
-- middleware) and the column must hold exactly what the frames said.
-- NULL for user rows and rows written before this migration.
--
-- stop_reason: WHY THE MODEL STOPPED, distinct from terminal_cause (why
-- the run ended, migration 043). A run that completed can still carry
-- stop_reason = max_tokens — the answer is cut off — which used to be
-- indistinguishable from a normal finish on the wire and in this table.
-- Closed set from docs/contracts/terminal_cause.json → stop_reasons; the
-- gateway maps each provider family's native value into it, and a value
-- nobody named is `unknown`, never end_turn. NULL for user rows, failed
-- runs and pre-044 rows.
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS run_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS stop_reason TEXT NULL;

ALTER TABLE chat.messages_masked
    DROP CONSTRAINT IF EXISTS messages_masked_stop_reason_check;
ALTER TABLE chat.messages_masked
    ADD CONSTRAINT messages_masked_stop_reason_check CHECK (
        stop_reason IS NULL OR stop_reason IN (
            'end_turn', 'max_tokens', 'tool_use', 'stop_sequence',
            'content_filter', 'refusal', 'context_window_exceeded', 'unknown'
        )
    );

-- The support lookup: a person pastes the id from the card.
CREATE INDEX IF NOT EXISTS messages_masked_run_id_idx
    ON chat.messages_masked (run_id) WHERE run_id IS NOT NULL;

COMMENT ON COLUMN chat.messages_masked.run_id IS
    'FIX119 — the generation id every frame carried and every layer logged; NULL = user row / pre-044';
COMMENT ON COLUMN chat.messages_masked.stop_reason IS
    'FIX119 — why the model stopped (closed set, docs/contracts/terminal_cause.json); NULL = user row / failed run / pre-044';
