-- AM3 down: undoes all of 003 (an image rollback to AM2 reads none of it).
-- The catalog's sample installs and the sign-off jobs are AM3's own rows:
-- they go first (their runs, files and artifacts cascade), so the restored
-- CHECK constraints hold. Their stored objects stay under agent-market/
-- until the feature's removal (001's DROP SCHEMA + the prefix delete).
DELETE FROM agent_market.agent_runs WHERE kind = 'review';
DELETE FROM agent_market.catalog_samples;
DELETE FROM agent_market.agent_installs WHERE status = 'catalog';

DROP TABLE IF EXISTS agent_market.problem_reports;
DROP TABLE IF EXISTS agent_market.run_shares;
DROP TABLE IF EXISTS agent_market.run_reviews;
DROP TABLE IF EXISTS agent_market.catalog_samples;
DROP TABLE IF EXISTS agent_market.user_prefs;
DROP TABLE IF EXISTS agent_market.catalog_state;
DROP TABLE IF EXISTS agent_market.org_departments;
DROP TABLE IF EXISTS agent_market.org_members;
DROP TABLE IF EXISTS agent_market.org_settings;

ALTER TABLE agent_market.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_kind_check;
ALTER TABLE agent_market.agent_runs ADD CONSTRAINT agent_runs_kind_check
    CHECK (kind IN ('run', 'self_test', 'render', 'reply', 'mapping_preview'));
ALTER TABLE agent_market.agent_installs DROP CONSTRAINT IF EXISTS agent_installs_status_check;
ALTER TABLE agent_market.agent_installs ADD CONSTRAINT agent_installs_status_check
    CHECK (status IN ('active', 'removed'));
ALTER TABLE agent_market.agent_installs DROP COLUMN IF EXISTS last_opened_at;
ALTER TABLE agent_market.agent_installs DROP COLUMN IF EXISTS pinned_at;
ALTER TABLE agent_market.agent_installs DROP COLUMN IF EXISTS org_id;

DELETE FROM agent_market.schema_migrations WHERE version = '003_self_serve';
