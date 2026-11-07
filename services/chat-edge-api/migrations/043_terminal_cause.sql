-- FIX118 — how an assistant turn ENDED, next to its status.
--
-- `status` alone overloads 'error': a provider error frame, a stream that
-- went quiet, a stream that closed without saying it was done, and a
-- process that died mid-generation all collapse into the same value, and
-- the code the gateway named reaches the browser and is then thrown away.
-- A row read back after a reload therefore says "this answer failed" and
-- cannot say why, and a report over status counts a cut stream as a
-- success when the pump treated EOF as [DONE].
--
-- terminal_cause is a CLOSED set shared with the desktop host and the web
-- page (docs/contracts/terminal_cause.json; each tree's test reads it):
--   completed | stopped_by_user | error | timeout | disconnected |
--   eof_without_terminal | interrupted
-- NULL for user rows, for rows written before this migration, and for a
-- parked (waiting_user) row, which has not ended.
--
-- error_code is the catalogue code the terminal error frame carried
-- (backend/app/core/error_catalog.py plus chat-edge's own), NULL when the
-- turn did not fail. Both columns are additive and nullable: a chat-edge
-- built before this migration never reads them.
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS terminal_cause TEXT NULL,
    ADD COLUMN IF NOT EXISTS error_code TEXT NULL;

ALTER TABLE chat.messages_masked
    DROP CONSTRAINT IF EXISTS messages_masked_terminal_cause_check;
ALTER TABLE chat.messages_masked
    ADD CONSTRAINT messages_masked_terminal_cause_check CHECK (
        terminal_cause IS NULL OR terminal_cause IN (
            'completed', 'stopped_by_user', 'error', 'timeout',
            'disconnected', 'eof_without_terminal', 'interrupted'
        )
    );

COMMENT ON COLUMN chat.messages_masked.terminal_cause IS
    'FIX118 — why the turn ended (closed set, docs/contracts/terminal_cause.json); NULL = unknown/pre-FIX118/not ended';
COMMENT ON COLUMN chat.messages_masked.error_code IS
    'FIX118 — the catalogue error code of the terminal error frame; NULL when the turn did not fail';
