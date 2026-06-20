-- DLP canonical artifact persistence (Phase 4A.2.2).
--
-- chat.messages_masked gains a CANONICAL representation alongside the
-- existing display content. ``content_masked`` keeps the restored,
-- user-visible DISPLAY text (UI unchanged). The new columns hold the
-- masked/tokenized, pre-restore CANONICAL content that is safe to replay
-- to the provider as history, plus integrity + lifecycle metadata.
--
-- Provider history (when DLP_CANONICAL_HISTORY_ENABLED=true) uses
-- ``content_canonical`` ONLY for rows with ``canonical_status='complete'``;
-- every other status is EXCLUDED from provider history (never a display
-- fallback). Existing rows default to 'legacy' and are excluded once the
-- feature is enabled.
--
-- Backward-compatible: all columns nullable / defaulted; ``content_masked``
-- is NOT renamed or altered. No backfill is performed.
--
-- Invariants enforced by the application layer (not the DB):
--   * canonical_status='complete' => content_canonical NOT NULL and
--     schema/generation/sha/length present.
--   * empty complete canonical uses content_canonical='' (never NULL).
--   * canonical_error_code holds a STABLE safe code only (no raw text).

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS content_canonical TEXT;

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_status VARCHAR(32) NOT NULL DEFAULT 'legacy';

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_schema_version SMALLINT;

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_generation_id VARCHAR(128);

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_sha256 CHAR(64);

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_length INTEGER;

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_error_code VARCHAR(64);

ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS canonical_received_at TIMESTAMPTZ;

-- Idempotency / audit lookup of a generation within an owner scope.
CREATE INDEX IF NOT EXISTS idx_messages_canonical_generation
    ON chat.messages_masked (tenant_id, canonical_generation_id);
