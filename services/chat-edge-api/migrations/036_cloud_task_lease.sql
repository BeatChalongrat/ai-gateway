-- 036_cloud_task_lease.sql
--
-- FIX16 — single-flight LEASE for cloud scheduled tasks.
--
-- Problem: two scheduler instances (or one restarted mid-tick) must never
-- fire the same task twice. FIX13's ListDue + AdvanceAfterRun had no claim,
-- so two ticks racing on the same due task would both run it.
--
-- Mechanism: a claim-lease. The scheduler CLAIMS a due task with one atomic
-- UPDATE (see repo ClaimDue) that stamps locked_at/locked_by; the run then
-- happens OUTSIDE any transaction; on completion the lease is cleared. A
-- second tick/instance cannot claim a row whose locked_at is fresh.
--
-- Crash recovery: a holder that dies mid-run leaves locked_at set and
-- next_run_at NOT advanced. After the lease TTL elapses (the claim predicate
-- allows locked_at < now - TTL), another tick re-claims and RETRIES it — a
-- crash costs one delayed run, never a task that never runs again.
--
-- Idempotent — safe to re-run.

ALTER TABLE chat.cloud_scheduled_tasks
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS locked_by TEXT;

-- The claim scan reads enabled + due + (unlocked OR stale-lease); index the
-- shape it filters on so the claim stays cheap as tasks accrue.
CREATE INDEX IF NOT EXISTS idx_cloud_tasks_claim
    ON chat.cloud_scheduled_tasks(enabled, next_run_at, locked_at);
