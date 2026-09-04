-- FIX58 — memory scoped to a project.
--
-- No column and no constraint change is needed: user_memories.scope_type
-- is a plain VARCHAR(30) with no CHECK, and scope_id is already a
-- nullable UUID. A project memory is simply
--   scope_type = 'project', scope_id = <chat.projects.id>.
--
-- What IS needed is an index. GetActiveForInjection runs on every turn
-- that builds a prompt, and it gained a third predicate:
--
--   WHERE user_id = $1 AND tenant_id = $2 AND status = 'active'
--     AND scope_type = 'project' AND scope_id = $3
--
-- The existing idx_user_memories_scope covers (user_id, scope_type)
-- only, so without this the project branch would filter scope_id by
-- reading every project memory the user owns — the cost growing with
-- the number of projects, on the hot path, which is exactly where the
-- +50 ms budget is spent.
--
-- Partial on status = 'active' to match the existing scope index and
-- the query's own predicate: deleted rows are never injected.
CREATE INDEX IF NOT EXISTS idx_user_memories_project
    ON chat.user_memories(user_id, scope_id)
    WHERE status = 'active' AND scope_type = 'project';

-- Deliberately NOT added:
--
--   * a FOREIGN KEY from scope_id to chat.projects(id). scope_id is
--     polymorphic — it already holds conversation ids and file ids for
--     the other scopes — so a single FK cannot be expressed here. The
--     orphan case it would have covered is handled in code instead:
--     ProjectService.Delete erases the project's memories before
--     dropping the project row, with an audit event per memory.
--
--   * a CHECK that scope_id IS NOT NULL when scope_type = 'project'.
--     Worth having, but it would have to be validated against existing
--     rows, and there are none yet; the service is the only writer and
--     always sets both. Left as a note rather than a silent omission.
