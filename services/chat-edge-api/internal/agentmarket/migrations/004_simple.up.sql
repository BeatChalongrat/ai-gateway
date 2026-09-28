-- AM4: make it simple — threads and rounds, the round-boundary question,
-- the model a round reads documents with, how long the organisation keeps
-- its accounting records, and an admin's per-run access with a reason.
--
-- Same isolation contract as 001–003: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.

-- A thread is one conversation with an installed agent. The install's first
-- thread is implicit (thread_id NULL on its items and runs); "แชทใหม่กับ agent
-- นี้" starts another. Settings and remembered answers belong to the install
-- and are shared by all its threads; a round's files, figures and answers
-- stay in that round.
CREATE TABLE IF NOT EXISTS agent_market.agent_threads (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    tenant_id  uuid NOT NULL,
    user_id    uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_threads_install ON agent_market.agent_threads (install_id, created_at DESC);

ALTER TABLE agent_market.agent_thread_items ADD COLUMN IF NOT EXISTS thread_id uuid
    REFERENCES agent_market.agent_threads (id) ON DELETE CASCADE;
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS thread_id uuid
    REFERENCES agent_market.agent_threads (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS agent_thread_items_thread ON agent_market.agent_thread_items (install_id, thread_id, rev);
CREATE INDEX IF NOT EXISTS agent_runs_thread ON agent_market.agent_runs (install_id, thread_id, created_at DESC);

-- The question asked at a round boundary: files or an instruction arrived
-- after a round finished — start a new round, or ask about the last one.
ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice', 'intent'));

-- Which model reads the documents: 'auto' (from the capability the agent
-- declares) or one of the organisation's curated choices. The install keeps
-- the person's default; a round records what it ran with.
ALTER TABLE agent_market.agent_installs ADD COLUMN IF NOT EXISTS model_choice text NOT NULL DEFAULT 'auto';
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS model_choice text;

-- The organisation decides whether documents may go to a model outside it
-- (off: only models that run inside the organisation see them), which
-- models its people may pick, how long accounting records are kept (years
-- from the round's finish), and when a person's storage use gets a warning.
ALTER TABLE agent_market.org_settings ADD COLUMN IF NOT EXISTS documents_may_leave boolean NOT NULL DEFAULT false;
ALTER TABLE agent_market.org_settings ADD COLUMN IF NOT EXISTS agent_models jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agent_market.org_settings ADD COLUMN IF NOT EXISTS retention_years integer NOT NULL DEFAULT 5;
ALTER TABLE agent_market.org_settings ADD COLUMN IF NOT EXISTS storage_warn_mb integer NOT NULL DEFAULT 2048;
ALTER TABLE agent_market.org_settings DROP CONSTRAINT IF EXISTS org_settings_retention_years_check;
ALTER TABLE agent_market.org_settings ADD CONSTRAINT org_settings_retention_years_check
    CHECK (retention_years BETWEEN 1 AND 30);

-- A finished round's evidence (the files sent in, the files made) is the
-- organisation's record: kept for retention_years, removed earlier only by
-- an admin who says why.
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS evidence_removed_at timestamptz;
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS evidence_removed_by text NOT NULL DEFAULT '';
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS evidence_removed_reason text NOT NULL DEFAULT '';

-- An admin opening someone else's run: one row per opening, with the reason
-- they gave. It is written before anything is shown, it expires, and the
-- run's owner is told (a notice in their thread; the run page lists it).
CREATE TABLE IF NOT EXISTS agent_market.run_access_log (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    org_id        text NOT NULL,
    admin_user_id uuid NOT NULL,
    admin_email   text NOT NULL,
    admin_name    text NOT NULL DEFAULT '',
    reason        text NOT NULL,
    opened_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS run_access_log_run ON agent_market.run_access_log (run_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS run_access_log_admin ON agent_market.run_access_log (admin_user_id, expires_at);
