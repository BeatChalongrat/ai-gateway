-- AM3: self-serve — the organisation's access list and budgets, the
-- catalog's owner, the left rail's pins, sample outputs in the catalog,
-- sign-off, sharing a run inside the organisation, and "report a problem".
--
-- Same isolation contract as 001/002: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.
-- A second organisation is a row in org_settings (its email domains), not a
-- rewrite: every table below carries org_id.

-- The organisation's own settings. access: off (admins only) | list (the
-- people in org_members) | everyone (every signed-in user of the org).
CREATE TABLE IF NOT EXISTS agent_market.org_settings (
    org_id               text PRIMARY KEY,
    name                 text NOT NULL DEFAULT '',
    access               text NOT NULL DEFAULT 'list' CHECK (access IN ('off', 'list', 'everyone')),
    domains              text[] NOT NULL DEFAULT '{}',
    user_monthly_credits numeric(14, 2) NOT NULL DEFAULT 3000000,
    contact_name         text NOT NULL DEFAULT '',
    contact_email        text NOT NULL DEFAULT '',
    updated_by           text NOT NULL DEFAULT '',
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Who may use Agent Market (access = list), their department and role.
-- role: member | publisher (publishes agents to the org's catalog) | admin
-- (everything: access, budgets, spend, disabling an agent for everyone).
-- monthly_credits NULL = the organisation's per-person default.
CREATE TABLE IF NOT EXISTS agent_market.org_members (
    org_id          text NOT NULL REFERENCES agent_market.org_settings (org_id) ON DELETE CASCADE,
    email           text NOT NULL,
    user_id         uuid,
    display_name    text NOT NULL DEFAULT '',
    department      text NOT NULL DEFAULT '',
    role            text NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'publisher', 'admin')),
    monthly_credits numeric(14, 2),
    added_by        text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, email)
);
CREATE INDEX IF NOT EXISTS org_members_user ON agent_market.org_members (user_id) WHERE user_id IS NOT NULL;

-- A department's monthly budget (NULL = no department limit) and the person
-- to ask when it is used up.
CREATE TABLE IF NOT EXISTS agent_market.org_departments (
    org_id          text NOT NULL REFERENCES agent_market.org_settings (org_id) ON DELETE CASCADE,
    name            text NOT NULL,
    monthly_credits numeric(14, 2),
    contact_name    text NOT NULL DEFAULT '',
    contact_email   text NOT NULL DEFAULT '',
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, name)
);

-- The catalog's owner decides what the organisation sees. A slug with no
-- row is unpublished. disabled = no new install, no new run, no resume, for
-- everyone at once; every run, file and decision already made is kept.
CREATE TABLE IF NOT EXISTS agent_market.catalog_state (
    org_id     text NOT NULL,
    slug       text NOT NULL,
    status     text NOT NULL CHECK (status IN ('published', 'unpublished', 'disabled')),
    reason     text NOT NULL DEFAULT '',
    changed_by text NOT NULL DEFAULT '',
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, slug)
);

-- Installs: which organisation (budgets), the rail's pin and "recently used".
-- status 'catalog' is the runner's own install that makes an agent's sample
-- outputs for the catalog (tenant and user are the nil uuid: nobody's data).
ALTER TABLE agent_market.agent_installs ADD COLUMN IF NOT EXISTS org_id text NOT NULL DEFAULT 'default';
ALTER TABLE agent_market.agent_installs ADD COLUMN IF NOT EXISTS pinned_at timestamptz;
ALTER TABLE agent_market.agent_installs ADD COLUMN IF NOT EXISTS last_opened_at timestamptz;
ALTER TABLE agent_market.agent_installs DROP CONSTRAINT IF EXISTS agent_installs_status_check;
ALTER TABLE agent_market.agent_installs ADD CONSTRAINT agent_installs_status_check
    CHECK (status IN ('active', 'removed', 'catalog'));

-- A reviewer's sign-off re-issues the files with their name on them: a job of its own.
ALTER TABLE agent_market.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_kind_check;
ALTER TABLE agent_market.agent_runs ADD CONSTRAINT agent_runs_kind_check
    CHECK (kind IN ('run', 'self_test', 'render', 'reply', 'mapping_preview', 'review'));

-- Per-person preferences (the rail's collapsed state), kept per user, not per browser.
CREATE TABLE IF NOT EXISTS agent_market.user_prefs (
    user_id    uuid PRIMARY KEY,
    prefs      jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- The catalog's sample: the agent's own self-test, run once per released
-- version on its bundled sample data, so a stranger can open real outputs
-- before installing.
CREATE TABLE IF NOT EXISTS agent_market.catalog_samples (
    definition_id uuid PRIMARY KEY REFERENCES agent_market.agent_definitions (id),
    install_id    uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    run_id        uuid NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Sign-off: a person's name on a finished run (and on its files).
CREATE TABLE IF NOT EXISTS agent_market.run_reviews (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    org_id            text NOT NULL,
    reviewer_user_id  uuid NOT NULL,
    reviewer_email    text NOT NULL,
    reviewer_name     text NOT NULL,
    note              text NOT NULL DEFAULT '',
    via_share_id      uuid,
    stamp_run_id      uuid,
    artifact_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_reviews_run ON agent_market.run_reviews (run_id, created_at);

-- A run shared with a named colleague in the organisation. They must sign
-- in as that person; nothing is public and nothing leaves the organisation.
CREATE TABLE IF NOT EXISTS agent_market.run_shares (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    org_id            text NOT NULL,
    owner_user_id     uuid NOT NULL,
    recipient_email   text NOT NULL,
    recipient_user_id uuid,
    can_review        boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz,
    revoked_at        timestamptz,
    last_opened_at    timestamptz,
    opens             integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS run_shares_run ON agent_market.run_shares (run_id);
CREATE INDEX IF NOT EXISTS run_shares_recipient ON agent_market.run_shares (recipient_email) WHERE revoked_at IS NULL;

-- "Report a problem": the person's sentence and what we need to find it.
CREATE TABLE IF NOT EXISTS agent_market.problem_reports (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      text NOT NULL,
    user_id     uuid NOT NULL,
    email       text NOT NULL DEFAULT '',
    install_id  uuid,
    run_id      uuid,
    slug        text NOT NULL DEFAULT '',
    version     text NOT NULL DEFAULT '',
    run_status  text NOT NULL DEFAULT '',
    run_reason  text NOT NULL DEFAULT '',
    step        text NOT NULL DEFAULT '',
    step_error  text NOT NULL DEFAULT '',
    page        text NOT NULL DEFAULT '',
    text        text NOT NULL,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    admin_note  text NOT NULL DEFAULT '',
    closed_by   text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    closed_at   timestamptz
);
CREATE INDEX IF NOT EXISTS problem_reports_open ON agent_market.problem_reports (org_id, created_at DESC) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS problem_reports_user ON agent_market.problem_reports (user_id);

-- The agents released before AM3 stay published for the default
-- organisation, so every install keeps working the moment this applies.
-- (The organisation row itself is created by the control plane at start,
-- from AGENT_MARKET_ALLOWED_EMAILS / AGENT_MARKET_ADMIN_EMAILS: org.go.)
INSERT INTO agent_market.catalog_state (org_id, slug, status, changed_by)
    SELECT DISTINCT 'default', slug, 'published', 'migration 003' FROM agent_market.agent_definitions
    ON CONFLICT DO NOTHING;
