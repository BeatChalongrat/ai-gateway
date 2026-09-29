-- AM6.
--
-- Same isolation contract as 001–004: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.
-- Each section of the card adds its statements under its own header.

-- ── Charging follows delivery (section F) ────────────────────────────────
--
-- The rule: charge for work delivered, not for whether the numbers agreed.
-- The ledger stays what the platform paid, row by row. A row of a round that
-- delivered nothing (a crash, a cancel) is kept and voided — when, and why —
-- and no budget counts it.
ALTER TABLE agent_market.agent_budget_ledger ADD COLUMN IF NOT EXISTS voided_at timestamptz;
ALTER TABLE agent_market.agent_budget_ledger ADD COLUMN IF NOT EXISTS void_reason text NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS agent_budget_charged ON agent_market.agent_budget_ledger (install_id, created_at)
    WHERE voided_at IS NULL;

-- A run: credits_spent is what the person was charged, credits_cost what the
-- platform paid (equal until a row is voided). For the runs that exist the
-- two are the same: what was charged before this rule stays charged.
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS credits_cost numeric(14, 4) NOT NULL DEFAULT 0;
UPDATE agent_market.agent_runs SET credits_cost = credits_spent WHERE credits_cost = 0 AND credits_spent <> 0;

-- gate_open: the run ended on its package's completion gate with its draft
-- delivered — a completed round that stays open, not a failure. The runner
-- sets it when it ends the run; it is never worked out from status_reason, so
-- a reason that only looks like a gate's code is not taken for delivered work
-- (and a round that ended "not_tied" before this rule stays what it was).
-- delivered_at: when the run last delivered its work (it ended succeeded, or
-- with its gate open); what a later pass of the same run costs is voided from
-- there if that pass delivers nothing.
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS gate_open boolean NOT NULL DEFAULT false;
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
UPDATE agent_market.agent_runs SET delivered_at = finished_at WHERE delivered_at IS NULL AND status = 'succeeded';

-- ── Reminders and notifications (section B) ──────────────────────────────
--
-- Nothing runs without a person. A reminder that falls due opens a round that
-- waits for files (awaiting_input, with a request_files card) and tells its
-- owner; the round starts only when the person sends the files. There is no
-- agent principal and no unattended execution: the runner's minute pass
-- writes rows, it never starts a step.
--
-- rule: {"every":"month","day":1..28,"time":"HH:MM"} or
-- {"every":"week","weekday":1..7,"time":"HH:MM"}, Thai time (UTC+7).
-- next_due_at is the occurrence the rule falls due at next (its period is
-- worked out from it, never from when the pass ran); snoozed_until holds the
-- firing (or, while a round waits, its one follow-up) until then.
-- fired_period / fired_at / waiting_run_id / followup_sent_at are the last
-- firing: its period, when, the round it opened, and its one follow-up.
CREATE TABLE IF NOT EXISTS agent_market.reminders (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id       uuid NOT NULL,
    tenant_id        uuid NOT NULL,
    user_id          uuid NOT NULL,
    label            text NOT NULL,
    rule             jsonb NOT NULL,
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'stopped')),
    next_due_at      timestamptz NOT NULL,
    snoozed_until    timestamptz,
    skipped_period   text,
    fired_period     text,
    fired_at         timestamptz,
    waiting_run_id   uuid,
    followup_sent_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reminders_due ON agent_market.reminders (next_due_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS reminders_install ON agent_market.reminders (install_id, created_at);
CREATE INDEX IF NOT EXISTS reminders_waiting ON agent_market.reminders (waiting_run_id) WHERE waiting_run_id IS NOT NULL;

-- A notification is written once (dedupe_key: a restart or a second runner
-- never doubles one) and shown once per surface; dismissed is the end of it.
CREATE TABLE IF NOT EXISTS agent_market.notifications (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    user_id          uuid NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('reminder_due', 'reminder_followup', 'waiting', 'waiting_followup')),
    title            text NOT NULL,
    body             text NOT NULL DEFAULT '',
    link             text NOT NULL,
    install_id       uuid NOT NULL,
    run_id           uuid,
    reminder_id      uuid,
    card_id          uuid,
    dedupe_key       text NOT NULL UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    shown_web_at     timestamptz,
    shown_desktop_at timestamptz,
    dismissed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS notifications_user ON agent_market.notifications (user_id, created_at DESC) WHERE dismissed_at IS NULL;
CREATE INDEX IF NOT EXISTS notifications_run ON agent_market.notifications (run_id) WHERE run_id IS NOT NULL;

-- ── E-mail drafts and sends (section C) ──────────────────────────────────
--
-- A draft is built by the server from the round (one per run, rebuilt when
-- asked again) and never has a recipient. A send is the person's approval of
-- that exact message: who approved, to whom, the hash of the body, what was
-- attached (the round's own artifacts), and how it went — a failed send too.
CREATE TABLE IF NOT EXISTS agent_market.email_drafts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid NOT NULL UNIQUE,
    install_id  uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    user_id     uuid NOT NULL,
    subject     text NOT NULL,
    body        text NOT NULL,
    attachments jsonb NOT NULL DEFAULT '[]',
    body_sha256 text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_market.email_sends (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id            uuid NOT NULL,
    run_id              uuid NOT NULL,
    to_addrs            text[] NOT NULL,
    cc_addrs            text[] NOT NULL DEFAULT '{}',
    subject             text NOT NULL,
    body_sha256         text NOT NULL,
    attachments         jsonb NOT NULL DEFAULT '[]',
    approved_by_user_id uuid NOT NULL,
    approved_by_name    text NOT NULL DEFAULT '',
    approved_by_email   text NOT NULL DEFAULT '',
    approved_at         timestamptz NOT NULL DEFAULT now(),
    status              text NOT NULL CHECK (status IN ('sent', 'failed')),
    error               text NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_sends_draft ON agent_market.email_sends (draft_id, created_at);
-- The draft is also a thread item (schema.go declares what of it is sent).
ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice', 'intent', 'email_draft'));

-- ── What a round records, and a receipt confirmed in this system (section A) ──
--
-- detail: what a package keeps of a document beyond the four values the
-- duplicate check compares (an invoice's lines and the PO lines they billed,
-- a receipt a person confirmed in this system — which has no total and no
-- seller's tax ID). NULL for the entries recorded before this column.
ALTER TABLE agent_market.document_index ADD COLUMN IF NOT EXISTS detail jsonb;
