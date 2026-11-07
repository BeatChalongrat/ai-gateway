-- 035_create_cloud_tasks.sql
--
-- FIX13 — CLOUD SCHEDULED TASKS (server-side, unattended runs on chat-edge).
--
-- This is the UNATTENDED-RUN STORE: a user schedules a prompt to run on
-- an interval, and the scheduler fires it with NO human present. Because
-- nobody is at the keyboard when a run happens, every run is
--   1) AUTHORIZED first (a revoked user, or one with no balance, is
--      refused BEFORE any turn/gateway call), and
--   2) AUDITED — every fire writes exactly one cloud_task_runs row
--      recording the outcome (ok | refused_revoked | refused_no_balance
--      | error).
--
-- THE ONE INVARIANT: a task run MUST NOT reach the turn runner unless the
-- authorization gate returned Allow. The runner enforces the ordering
-- structurally (authorize() -> only on Allow do we run the turn); this
-- table is the durable record that proves it happened for each fire.
--
-- Every user-facing read/write is hard-scoped by (tenant_id, user_id) in
-- the repo; a task is private to the user who created it. The due-scan
-- (ListDue) is the ONLY cross-user read and it is scheduler-internal.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.cloud_scheduled_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    conversation_id UUID NOT NULL,

    -- Human-facing label for the management surface.
    name VARCHAR(120) NOT NULL,

    -- The prompt fired verbatim as the user turn each run. Plain text
    -- handed to the turn runner exactly like a typed message — it is
    -- NEVER executed as a command. Size-capped defensively.
    prompt TEXT NOT NULL,

    -- Only 'interval' is supported in the safe core. The column exists so
    -- a future cron kind is an additive change, not a migration break.
    schedule_kind VARCHAR(16) NOT NULL DEFAULT 'interval',

    -- 1-hour floor: an unattended task can fire at most once per hour so a
    -- misconfigured client can't turn a scheduled task into a hot loop
    -- that drains a user's balance.
    interval_seconds INT NOT NULL CHECK (interval_seconds >= 3600),

    next_run_at TIMESTAMPTZ NOT NULL,
    enabled     BOOLEAN     NOT NULL DEFAULT true,

    -- Optional lifetime cap: after run_count reaches max_runs the task is
    -- paused (paused_reason='max_runs'). NULL = unbounded.
    max_runs  INT,
    run_count INT NOT NULL DEFAULT 0,

    last_run_at  TIMESTAMPTZ,
    -- Why an enabled=false task was auto-paused: 'revoked' | 'max_runs'.
    -- NULL for a user-disabled or still-running task.
    paused_reason VARCHAR(40),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT cloud_task_prompt_len CHECK (length(prompt) BETWEEN 1 AND 4000),
    CONSTRAINT cloud_task_name_len   CHECK (length(name)   BETWEEN 1 AND 120)
);

-- The scheduler's due scan: cheap lookup of enabled tasks whose time has
-- come. Cross-user by design (scheduler-internal only).
CREATE INDEX IF NOT EXISTS idx_cloud_tasks_due
    ON chat.cloud_scheduled_tasks(enabled, next_run_at);

-- The per-user management surface.
CREATE INDEX IF NOT EXISTS idx_cloud_tasks_owner
    ON chat.cloud_scheduled_tasks(tenant_id, user_id);

-- Auto-maintain updated_at on every write.
CREATE OR REPLACE FUNCTION chat.update_cloud_tasks_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS cloud_tasks_updated_at_trigger ON chat.cloud_scheduled_tasks;
CREATE TRIGGER cloud_tasks_updated_at_trigger
    BEFORE UPDATE ON chat.cloud_scheduled_tasks
    FOR EACH ROW EXECUTE FUNCTION chat.update_cloud_tasks_updated_at();


-- AUDIT TABLE — one row per fire, append-only. This is the durable proof
-- that every unattended run was authorized before it ran. A
-- refused_revoked / refused_no_balance row means the turn runner was
-- NEVER called for that fire.
CREATE TABLE IF NOT EXISTS chat.cloud_task_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    task_id         UUID NOT NULL,
    tenant_id       UUID NOT NULL,
    user_id         UUID NOT NULL,
    conversation_id UUID,

    fired_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- ok                 — authorized and the turn ran.
    -- refused_revoked    — authorize() denied (user not active); NO turn.
    -- refused_no_balance — authorize() denied (no balance);     NO turn.
    -- error              — authorized, but the turn itself failed.
    outcome VARCHAR(24) NOT NULL,

    -- The authorizer's reason enum on a refusal ("revoked"|"no_balance"),
    -- or a short skip note. NULL on the ok path.
    skip_reason VARCHAR(80),

    -- Set only on outcome=ok: the assistant message the run produced.
    assistant_message_id UUID,
    -- The DLP policy action the turn reported, when available (else '').
    policy_action VARCHAR(24),
    tokens        INT,
    -- Bounded error text on outcome=error. Never carries a secret/token.
    error_detail VARCHAR(300),

    CONSTRAINT cloud_task_runs_outcome_chk
        CHECK (outcome IN ('ok', 'refused_revoked', 'refused_no_balance', 'error'))
);

-- Per-task audit timeline (newest first).
CREATE INDEX IF NOT EXISTS idx_cloud_task_runs_task
    ON chat.cloud_task_runs(task_id, fired_at DESC);

-- Per-user audit lookup (newest first), hard-scoped display.
CREATE INDEX IF NOT EXISTS idx_cloud_task_runs_owner
    ON chat.cloud_task_runs(tenant_id, user_id, fired_at DESC);
