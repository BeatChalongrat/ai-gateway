-- 029_create_projects.sql
--
-- Projects (Phase 4): a folder-like container that ALSO carries shared
-- context — per-project instructions plus an optional knowledge-base
-- binding. Every conversation assigned to a project inherits that
-- context at prompt-compile time (see prompt_compiler.go Layer 10 and
-- MessageService.ragContext).
--
-- Design notes:
--  * tenant_id + user_id denormalised on the row, same as chat.folders
--    (migration 022), so reads filter (tenant_id, user_id) without joins.
--  * knowledge_base_id references chat.knowledge_bases (migration 011 —
--    tenant-scoped; there is no per-user KB ownership). ON DELETE SET
--    NULL: deleting a KB detaches it from projects, never deletes them.
--  * conversations.project_id mirrors folder_id exactly. ON DELETE SET
--    NULL: deleting a project un-assigns its chats; chats are never
--    lost with the project.
--  * instructions is free text rendered into the system prompt; the
--    service layer caps its length (SQL keeps TEXT so the cap can move
--    without a migration).
--
-- Idempotent — safe to re-run.
--
-- Rollback:
--   DROP INDEX IF EXISTS chat.idx_conversations_project;
--   ALTER TABLE chat.conversations DROP COLUMN IF EXISTS project_id;
--   DROP INDEX IF EXISTS chat.idx_projects_user;
--   DROP TABLE IF EXISTS chat.projects;

CREATE TABLE IF NOT EXISTS chat.projects (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL,
    user_id           UUID NOT NULL,
    name              VARCHAR(255) NOT NULL,
    instructions      TEXT NOT NULL DEFAULT '',
    knowledge_base_id UUID REFERENCES chat.knowledge_bases(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_projects_user
    ON chat.projects (tenant_id, user_id, created_at);

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS project_id UUID
        REFERENCES chat.projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_project
    ON chat.conversations (project_id)
    WHERE project_id IS NOT NULL;
