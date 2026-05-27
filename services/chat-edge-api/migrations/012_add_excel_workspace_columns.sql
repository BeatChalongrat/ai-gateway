-- 012_add_excel_workspace_columns.sql
--
-- Phase 4 Session 1 — Excel Smart Workspace. Adds the metadata
-- needed to:
--   * cache the deep-inspection JSON produced by excel_inspector.py
--     so re-prompting doesn't re-shell into Python (inspection_json)
--   * flag files born from an AI edit plan vs. user uploads so the
--     UI can render them as "generated" cards (is_generated)
--   * track lineage so the version-history endpoint can walk
--     parent → child → grandchild without a separate join table
--     (parent_file_id + version)
--   * surface the change-log produced by excel_executor.py so the
--     result card can list "Created sheet X, wrote N cells, ..."
--     without parsing free-form text (change_log)
--
-- All columns are nullable / default so existing rows stay valid.

ALTER TABLE chat.conversation_files
    ADD COLUMN IF NOT EXISTS inspection_json TEXT,
    ADD COLUMN IF NOT EXISTS is_generated    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS parent_file_id  UUID REFERENCES chat.conversation_files(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS version         INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS change_log      TEXT;

CREATE INDEX IF NOT EXISTS idx_conv_files_parent ON chat.conversation_files(parent_file_id);
