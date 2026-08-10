-- 030_create_canvas_artifacts.sql
--
-- Canvas artifacts (Phase 6/C3): versioned inline artifacts the AI
-- renders into a conversation's canvas panel (html / react / mermaid /
-- svg / markdown). Rows sharing (conversation_id, artifact_key) form
-- one artifact's lineage; EVERY row is an immutable version and the
-- latest version is MAX(version). A revert never mutates or deletes
-- history — it appends a NEW version whose content is copied from the
-- reverted-to row, same linear-lineage decision as the Phase-4
-- workspace rollback (migration 012).
--
-- Design notes:
--  * tenant_id + user_id denormalised on the row, same as chat.projects
--    (migration 029), so reads filter tenant scope without joins.
--  * conversation_id references chat.conversations (migration 002).
--    ON DELETE CASCADE: hard-deleting a conversation removes its
--    artifacts with it (they have no life outside the conversation).
--    Note the normal API delete is a soft delete (is_deleted flag),
--    which leaves artifact rows in place by design.
--  * message_id optionally links the assistant message that produced
--    the version (chat.messages_masked, migration 002). ON DELETE SET
--    NULL: pruning messages detaches versions, never deletes them.
--  * UNIQUE (conversation_id, artifact_key, version) is the concurrency
--    backstop for the INSERT ... SELECT MAX(version)+1 in the repo —
--    two racing saves can never silently share a version number.
--  * content is TEXT; the service layer caps its size (SQL keeps TEXT
--    so the cap can move without a migration).
--
-- Idempotent — safe to re-run.
--
-- Rollback:
--   DROP INDEX IF EXISTS chat.idx_canvas_artifacts_lookup;
--   DROP TABLE IF EXISTS chat.canvas_artifacts;

CREATE TABLE IF NOT EXISTS chat.canvas_artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    conversation_id UUID NOT NULL REFERENCES chat.conversations(id) ON DELETE CASCADE,
    message_id      UUID REFERENCES chat.messages_masked(id) ON DELETE SET NULL,
    artifact_key    TEXT NOT NULL,
    type            TEXT NOT NULL,
    title           TEXT,
    content         TEXT NOT NULL,
    version         INT  NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, artifact_key, version)
);

CREATE INDEX IF NOT EXISTS idx_canvas_artifacts_lookup
    ON chat.canvas_artifacts(conversation_id, artifact_key, version DESC);
