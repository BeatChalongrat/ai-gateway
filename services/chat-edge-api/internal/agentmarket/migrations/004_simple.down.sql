-- AM4 down: undoes all of 004 (an image rollback to AM3 reads none of it).
-- The intent chips are AM4's own rows: they go first, so the restored CHECK
-- constraint holds. A second thread's items go with its thread; its runs
-- stay (they fall back to the install's first thread).
DELETE FROM agent_market.agent_thread_items WHERE kind = 'intent';
DROP TABLE IF EXISTS agent_market.run_access_log;

ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice'));

ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS evidence_removed_reason;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS evidence_removed_by;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS evidence_removed_at;
ALTER TABLE agent_market.org_settings DROP CONSTRAINT IF EXISTS org_settings_retention_years_check;
ALTER TABLE agent_market.org_settings DROP COLUMN IF EXISTS storage_warn_mb;
ALTER TABLE agent_market.org_settings DROP COLUMN IF EXISTS retention_years;
ALTER TABLE agent_market.org_settings DROP COLUMN IF EXISTS agent_models;
ALTER TABLE agent_market.org_settings DROP COLUMN IF EXISTS documents_may_leave;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS model_choice;
ALTER TABLE agent_market.agent_installs DROP COLUMN IF EXISTS model_choice;

DROP INDEX IF EXISTS agent_market.agent_runs_thread;
DROP INDEX IF EXISTS agent_market.agent_thread_items_thread;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS thread_id;
DELETE FROM agent_market.agent_thread_items WHERE thread_id IS NOT NULL;
ALTER TABLE agent_market.agent_thread_items DROP COLUMN IF EXISTS thread_id;
DROP TABLE IF EXISTS agent_market.agent_threads;

DELETE FROM agent_market.schema_migrations WHERE version = '004_simple';
