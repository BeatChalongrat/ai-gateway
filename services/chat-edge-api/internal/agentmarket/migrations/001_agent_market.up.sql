-- Agent Market (AM1) — the whole feature's data, in its own schema.
--
-- Isolation contract (docs/agent-market/REMOVAL.md):
--   * every table lives in schema agent_market, never in public or chat;
--   * no foreign key points from here into an existing table, and none back:
--     tenant_id / user_id are plain indexed columns;
--   * no ALTER TABLE on any existing table.
-- Removal is `DROP SCHEMA agent_market CASCADE;` (this file's .down.sql).
--
-- Applied by agent-runner (and the control plane) only when
-- AGENT_MARKET_ENABLED is on; never by the core migration chain.

CREATE SCHEMA IF NOT EXISTS agent_market;

CREATE TABLE IF NOT EXISTS agent_market.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- Installable agent packages (one row per slug + version).
CREATE TABLE IF NOT EXISTS agent_market.agent_definitions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL,
    version      text NOT NULL,
    name         text NOT NULL,
    manifest     jsonb NOT NULL,
    instructions text NOT NULL DEFAULT '',
    checksum     text NOT NULL,
    engine_checksum text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);

-- One user's installed agent.
CREATE TABLE IF NOT EXISTS agent_market.agent_installs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL,
    user_id             uuid NOT NULL,
    definition_id       uuid NOT NULL REFERENCES agent_market.agent_definitions (id),
    slug                text NOT NULL,
    definition_version  text NOT NULL,
    checksum            text NOT NULL,
    title               text NOT NULL,
    capability_bindings jsonb NOT NULL DEFAULT '{}'::jsonb,
    binding_version     integer NOT NULL DEFAULT 1,
    budget_credits      numeric(14, 2) NOT NULL DEFAULT 20000,
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    self_test_run_id    uuid,
    idempotency_key     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    removed_at          timestamptz
);
CREATE INDEX IF NOT EXISTS agent_installs_owner ON agent_market.agent_installs (tenant_id, user_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS agent_installs_idem ON agent_market.agent_installs (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Answers asked once: at install, or remembered from a run's card.
CREATE TABLE IF NOT EXISTS agent_market.agent_install_defaults (
    install_id  uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    key         text NOT NULL,
    value       jsonb NOT NULL,
    source      text NOT NULL DEFAULT 'install' CHECK (source IN ('install', 'answer', 'settings')),
    label       text NOT NULL DEFAULT '',
    note        text NOT NULL DEFAULT '',
    from_run_id uuid,
    from_card_id uuid,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (install_id, key)
);

-- Theme registry: one theme per tenant, versioned.
CREATE TABLE IF NOT EXISTS agent_market.agent_themes (
    tenant_id  uuid NOT NULL,
    version    integer NOT NULL,
    theme      jsonb NOT NULL,
    logo_key   text,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, version)
);

-- Runs (and the small jobs that hang off a run: re-render, reply, mapping preview).
CREATE TABLE IF NOT EXISTS agent_market.agent_runs (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id         uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    parent_run_id      uuid REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    tenant_id          uuid NOT NULL,
    user_id            uuid NOT NULL,
    kind               text NOT NULL DEFAULT 'run' CHECK (kind IN ('run', 'self_test', 'render', 'reply', 'mapping_preview')),
    status             text NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'awaiting_input', 'succeeded', 'failed', 'cancelled')),
    status_reason      text NOT NULL DEFAULT '',
    period             text,
    message            text NOT NULL DEFAULT '',
    params             jsonb NOT NULL DEFAULT '{}'::jsonb,
    definition_version text NOT NULL,
    checksum           text NOT NULL,
    engine_checksum    text NOT NULL DEFAULT '',
    binding_version    integer NOT NULL DEFAULT 1,
    formats            jsonb NOT NULL DEFAULT '[]'::jsonb,
    answers            jsonb NOT NULL DEFAULT '{}'::jsonb,
    assumptions        jsonb NOT NULL DEFAULT '[]'::jsonb,
    idempotency_key    text,
    cancel_requested   boolean NOT NULL DEFAULT false,
    lease_owner        text,
    lease_until        timestamptz,
    attempts           integer NOT NULL DEFAULT 0,
    credits_spent      numeric(14, 4) NOT NULL DEFAULT 0,
    model_used         text,
    model_self_hosted  boolean,
    awaiting_since     timestamptz,
    awaiting_ms        bigint NOT NULL DEFAULT 0,
    created_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    finished_at        timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_queue ON agent_market.agent_runs (created_at) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS agent_runs_install ON agent_market.agent_runs (install_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_idem ON agent_market.agent_runs (install_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Every step writes a row. A step is identified by its idempotency key:
-- a retry after a crash finds the row and never produces a second output.
CREATE TABLE IF NOT EXISTS agent_market.agent_run_steps (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    name            text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('ingest', 'check', 'clarification', 'compute', 'ai', 'deliverable', 'publish')),
    status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped', 'awaiting')),
    idempotency_key text NOT NULL,
    input           jsonb NOT NULL DEFAULT '{}'::jsonb,
    output          jsonb NOT NULL DEFAULT '{}'::jsonb,
    script_id       text NOT NULL DEFAULT '',
    model_id        text NOT NULL DEFAULT '',
    error           text NOT NULL DEFAULT '',
    attempt         integer NOT NULL DEFAULT 1,
    credits         numeric(14, 4) NOT NULL DEFAULT 0,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    duration_ms     bigint,
    UNIQUE (run_id, idempotency_key),
    UNIQUE (run_id, seq)
);

-- Files a user gave a run (bytes in object storage under agent-market/).
CREATE TABLE IF NOT EXISTS agent_market.agent_files (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL,
    user_id      uuid NOT NULL,
    install_id   uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    run_id       uuid REFERENCES agent_market.agent_runs (id) ON DELETE SET NULL,
    name         text NOT NULL,
    content_type text NOT NULL DEFAULT '',
    size         bigint NOT NULL,
    sha256       text NOT NULL,
    storage_key  text NOT NULL,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('staged', 'active', 'pending_decision', 'next_round', 'removed')),
    client_id    text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_files_run ON agent_market.agent_files (run_id);
CREATE INDEX IF NOT EXISTS agent_files_install ON agent_market.agent_files (install_id, created_at DESC);

-- Question cards. card id = row id. Matched by finding key, never by position.
CREATE TABLE IF NOT EXISTS agent_market.agent_run_clarifications (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    install_id   uuid NOT NULL,
    tenant_id    uuid NOT NULL,
    user_id      uuid NOT NULL,
    moment       text NOT NULL CHECK (moment IN ('install', 'preflight', 'postcompute', 'run')),
    seq          integer NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('questions', 'request_files', 'late_file', 'output_format')),
    blocking     boolean NOT NULL,
    supersedes   uuid,
    questions    jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'interpreting', 'answered', 'defaulted', 'superseded', 'cancelled')),
    answer       jsonb,
    answer_text  text,
    answered_via text,
    answered_by  uuid,
    answered_at  timestamptz,
    attempts     integer NOT NULL DEFAULT 0,
    last_error   text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);
CREATE INDEX IF NOT EXISTS agent_clar_run ON agent_market.agent_run_clarifications (run_id, status);

-- Delivered files, versioned per run and format.
CREATE TABLE IF NOT EXISTS agent_market.agent_output_artifacts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    install_id      uuid NOT NULL,
    tenant_id       uuid NOT NULL,
    user_id         uuid NOT NULL,
    format          text NOT NULL,
    kind            text NOT NULL,
    name            text NOT NULL,
    version         integer NOT NULL,
    storage_key     text NOT NULL,
    size            bigint NOT NULL,
    sha256          text NOT NULL,
    theme_id        text NOT NULL DEFAULT '',
    theme_version   integer NOT NULL DEFAULT 1,
    citations       jsonb NOT NULL DEFAULT '[]'::jsonb,
    validation      jsonb NOT NULL DEFAULT '{}'::jsonb,
    chart_bindings  jsonb NOT NULL DEFAULT '[]'::jsonb,
    produced_by_step text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, format, version),
    UNIQUE (produced_by_step, format)
);

-- The agent workspace thread. rev is the stream cursor: every insert and
-- every update takes a new value, so a reconnect resumes from its last rev.
CREATE SEQUENCE IF NOT EXISTS agent_market.thread_rev;
CREATE TABLE IF NOT EXISTS agent_market.agent_thread_items (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    tenant_id  uuid NOT NULL,
    user_id    uuid NOT NULL,
    run_id     uuid,
    kind       text NOT NULL CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice')),
    payload    jsonb NOT NULL,
    client_id  text,
    rev        bigint NOT NULL DEFAULT nextval('agent_market.thread_rev'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_thread_rev ON agent_market.agent_thread_items (install_id, rev);
CREATE UNIQUE INDEX IF NOT EXISTS agent_thread_client ON agent_market.agent_thread_items (install_id, client_id) WHERE client_id IS NOT NULL;

-- Credits spent, per step. The per-install budget is the sum this month.
CREATE TABLE IF NOT EXISTS agent_market.agent_budget_ledger (
    id         bigserial PRIMARY KEY,
    install_id uuid NOT NULL,
    run_id     uuid NOT NULL,
    step_key   text NOT NULL,
    credits    numeric(14, 4) NOT NULL,
    model      text NOT NULL DEFAULT '',
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_key)
);
CREATE INDEX IF NOT EXISTS agent_budget_install ON agent_market.agent_budget_ledger (install_id, created_at);

-- Runner heartbeat: the control plane reads health and capabilities here.
CREATE TABLE IF NOT EXISTS agent_market.runner_heartbeats (
    runner_id    text PRIMARY KEY,
    version      text NOT NULL,
    started_at   timestamptz NOT NULL,
    seen_at      timestamptz NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    running      integer NOT NULL DEFAULT 0,
    slots        integer NOT NULL DEFAULT 0
);

-- Created now, unused in AM1 (durable approvals and the exception queue
-- arrive with write actions).
CREATE TABLE IF NOT EXISTS agent_market.agent_run_approvals (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    step_id    uuid,
    policy     text NOT NULL,
    prompt     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    actor      uuid,
    decided_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_market.agent_run_exceptions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES agent_market.agent_runs (id) ON DELETE CASCADE,
    step_id         uuid,
    severity        text NOT NULL,
    reason_code     text NOT NULL,
    source_ref      jsonb NOT NULL DEFAULT '{}'::jsonb,
    proposed_action text NOT NULL DEFAULT '',
    choices         jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          text NOT NULL DEFAULT 'open',
    assigned_to     uuid,
    resolved_by     uuid,
    resolved_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO agent_market.schema_migrations (version) VALUES ('001_agent_market') ON CONFLICT DO NOTHING;
