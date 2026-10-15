-- AM21 (part 4): the pictures of a delivered file are made by one small job that hangs off the run
-- that delivered it — kind 'pages' — like a re-render or a reply. On 15 Oct 2026 the first request
-- for them on live was refused by this check (agent_runs_kind_check: SQLSTATE 23514) while every
-- unit and integration test had passed: none of them asked the database for a pages job.
ALTER TABLE agent_market.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_kind_check;
ALTER TABLE agent_market.agent_runs ADD CONSTRAINT agent_runs_kind_check
    CHECK (kind IN ('run', 'self_test', 'render', 'reply', 'mapping_preview', 'review', 'pages'));
