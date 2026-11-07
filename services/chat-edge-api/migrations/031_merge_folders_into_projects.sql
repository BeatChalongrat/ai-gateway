-- 031_merge_folders_into_projects.sql
--
-- Phase 9 — collapse Folders into Projects.
--
-- The product carried TWO organisers stacked on each other: folders
-- (migration 022, grouping only) and projects (migration 029, grouping +
-- instructions + knowledge base). The big assistants ship ONE. A folder
-- is exactly "a project with no instructions and no KB", so every folder
-- becomes a project and its conversations move with it.
--
-- ── IDENTITY MAPPING (read this first) ────────────────────────────────
-- The project created for a folder REUSES THE FOLDER'S id. That is
-- deliberate: it makes the folder→project mapping self-evident with no
-- side table, makes the conversation repoint a plain
-- ``project_id = folder_id``, and makes the rollback exact (the rows to
-- undo are precisely those whose id is a folder id). The two id spaces
-- are gen_random_uuid() so a pre-existing project cannot collide in
-- practice — and if one somehow did, ON CONFLICT DO NOTHING leaves the
-- existing project untouched rather than corrupting it.
--
-- ── PRECEDENCE ────────────────────────────────────────────────────────
-- A conversation that ALREADY has a project_id keeps it. Projects are
-- the richer container (they carry instructions + KB that the prompt
-- compiler injects), so an explicit project assignment always wins over
-- a legacy folder assignment. Only conversations with
-- ``project_id IS NULL AND folder_id IS NOT NULL`` are repointed.
--
-- ── WHAT THIS MIGRATION DOES NOT DO ───────────────────────────────────
-- It does NOT drop chat.folders, does NOT drop conversations.folder_id,
-- and does NOT clear any folder_id value. Everything stays readable for
-- one release so the rollback below can restore the previous state
-- exactly; the application simply stops reading folders. Removing the
-- table is a separate, later migration.
--
-- Ordering note: chat.folders has a user-controlled ``position``;
-- chat.projects has none and lists newest-first by created_at. The
-- folder's created_at is carried over, so relative age is preserved but
-- a hand-ordered folder list will appear newest-first after the merge.
--
-- Idempotent — safe to re-run (ON CONFLICT DO NOTHING + the IS NULL
-- guard on the repoint).
--
-- ── ROLLBACK ──────────────────────────────────────────────────────────
--   -- 1. un-assign the conversations this migration repointed. folder_id
--   --    was never cleared, so this restores the exact previous state.
--   UPDATE chat.conversations c
--      SET project_id = NULL, updated_at = NOW()
--    WHERE c.project_id IS NOT NULL
--      AND EXISTS (SELECT 1 FROM chat.folders f WHERE f.id = c.project_id);
--   -- 2. delete the projects this migration created (identity mapping:
--   --    their id is a folder id). Projects created by users are never
--   --    touched because their ids are not folder ids.
--   DELETE FROM chat.projects p
--    WHERE EXISTS (SELECT 1 FROM chat.folders f WHERE f.id = p.id);

-- 1. Every folder becomes a project: same id, same owner, same name,
--    empty instructions, no KB. created_at/updated_at are carried over
--    so the sidebar keeps a sensible age order.
INSERT INTO chat.projects (
    id, tenant_id, user_id, name, instructions, knowledge_base_id,
    created_at, updated_at
)
SELECT f.id, f.tenant_id, f.user_id, f.name, '', NULL, f.created_at, f.updated_at
FROM chat.folders f
ON CONFLICT (id) DO NOTHING;

-- 2. Repoint conversations. An existing project assignment wins.
UPDATE chat.conversations c
   SET project_id = c.folder_id,
       updated_at = NOW()
 WHERE c.folder_id IS NOT NULL
   AND c.project_id IS NULL
   -- Defensive: only repoint into a project that actually exists, so a
   -- conversation can never point at a dangling id if step 1 skipped a
   -- row for any reason.
   AND EXISTS (SELECT 1 FROM chat.projects p WHERE p.id = c.folder_id);
