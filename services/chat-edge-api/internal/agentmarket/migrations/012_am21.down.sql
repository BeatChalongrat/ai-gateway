-- AM21 down: the pages jobs go, with their steps (ON DELETE CASCADE); their pictures stay in storage
-- beside the files, harmless. The kinds a run may have are as before.
DELETE FROM agent_market.agent_runs WHERE kind = 'pages';
ALTER TABLE agent_market.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_kind_check;
ALTER TABLE agent_market.agent_runs ADD CONSTRAINT agent_runs_kind_check
    CHECK (kind IN ('run', 'self_test', 'render', 'reply', 'mapping_preview', 'review'));
DELETE FROM agent_market.schema_migrations WHERE version = '012_am21';
