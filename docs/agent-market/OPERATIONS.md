# Agent Market — operations (AM1b)

What runs, where it is bounded, how you see it, and how to take it out.
Removal of the code: `REMOVAL.md`. Design: `ARCHITECTURE.md`.

## Exit plan — first, before anything else

If anything the Agent Market does affects a real user (chat errors or slows,
a smoke check other than `agent_market.*` goes red, the core pager fires, an
owner complaint): **take the exit first, report second.** Each step stands on
its own; stop at the first one that ends the problem.

```bash
cd "C:/Users/Beat Beat/Desktop/ai-gateway"
# 1. Flag off — chat-edge drops the routes and its pool; web-chat hides the rail (≈3 s recreate).
#    edit .env: AGENT_MARKET_ENABLED=false
docker compose -p ai-gateway -f docker-compose.webchat.yml up -d --no-deps chat-edge-api web-chat
# 2. Stop the runner (nothing in the core depends on it).
docker compose -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market down
# 3. Drop the data. Nothing outside the schema references it.
docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai -c 'DROP SCHEMA IF EXISTS agent_market CASCADE;'
docker run --rm -v ai-gateway_chat_edge_uploads:/v alpine rm -rf /v/agent-market
# 4. Revoke the runner's key: portal → project "Agent Market runner" → revoke; delete AGENT_RUNNER_* from .env.
# 5. Revert the code if needed: REMOVAL.md step 6 (git revert, rebuild, redeploy with rollback tags).
SMOKE_TARGET=live bash scripts/smoke.sh    # green, agent_market.flag → 404
```

## Sandbox per step (not per run — finer)

Every step (parse, checks, compute, render) runs in a **fresh** container
(`internal/sandbox`, launched by the runner through the Docker socket) and its
workspace directory is deleted when the step ends (`Executor.Cleanup`).

| Limit | Value | Where |
|---|---|---|
| network | `--network none` (no egress at all) | `internal/sandbox/sandbox.go` |
| user / capabilities | `--user 65534:65534`, `--cap-drop ALL`, seccomp profile | same |
| filesystem | `--read-only` rootfs, inputs mounted read-only, `/tmp` tmpfs 512 MB `noexec` | same |
| CPU | `--cpus 1.0` (`AGENT_SANDBOX_CPUS`) | `cmd/agent-runner/main.go` |
| memory | 1,536 MB, no swap (`AGENT_SANDBOX_MEMORY_MB`) | same |
| processes | `--pids-limit 256` | same |
| wall clock | 240 s per step (`AGENT_SANDBOX_WALL_SECONDS`), then the container is killed | same |
| output | 8 MB stdout, 256 MB per file (`ulimit fsize`), 256 open files | same |

The runner container itself: `cpus 1.0`, `memory 768m` (`docker-compose.agent-market.yml`),
one step at a time (`AGENT_RUNNER_SLOTS=1`).

**Orphans (AM1c).** Every sandbox container the runner starts is labelled
`sran.sandbox.owner=agent-runner`, `sran.agent_market.run_id`, `…runner` (the
runner id), `…instance` (one runner process), `…call` (one step) and
`sran.sandbox.deadline_unix` (start + wall + 30 s, when the host ceiling has
removed it). A runner killed between `docker create` and `start`, or mid-step,
leaves its container behind; the runner removes it at start and then every
`AGENT_RUNNER_SWEEP_SECONDS` (60): a container of this runner's previous
process, a container of its own whose step has returned, and any runner's
container `AGENT_RUNNER_SWEEP_GRACE_SECONDS` (60) past its deadline. Never a
step in flight, never another runner's before its deadline, never a container
without the owner label (chat's sandboxes). Step workspaces of removed
containers, and any older than 2 × (wall + 30 s) + 1 min, are deleted.
`docker ps -a --filter label=sran.sandbox.owner=agent-runner` lists them.

## Bulkheads, with numbers

| | Agent Market | Chat (core) |
|---|---|---|
| model-gateway identity | its own portal project "Agent Market runner" and API key (`.env AGENT_RUNNER_GATEWAY_API_KEY`) | chat-edge's service key |
| gateway rate limit | its own per-key bucket (measured in AM1b: see the report) | per user / per key |
| model concurrency | 1 call in flight; waits while any chat turn is generating or queued (`/ready`), up to 300 s | turn queue (FIX133) |
| DB pool | runner **4** (`agent-runner`), control plane **3** (`chat-edge-agent-market`) | chat-edge core pool, separate `pgxpool`; backend 10 async + 5 sync |
| budget | per install, 3,000,000 credits / month by default | platform credits |
| restarts | `restart: unless-stopped` only — **no `autoheal` label**, the core's autoheal never touches it | autoheal (FIX129) |
| compose | own project `sran-agent-market`, profile `agent-market` | `ai-gateway` |

## Observability

- **Metrics prefix `agent_market_`** (`runner /metrics`, scraped by Prometheus job
  `agent-runner`): `agent_market_runs{status}`, `agent_market_runs_finished_total{status}`,
  `agent_market_oldest_queued_seconds`, `agent_market_step_seconds_{sum,count}{step}`,
  `agent_market_credits_spent_total`, `agent_market_pool_{acquired,max}_conns`,
  `agent_market_sandbox_ready`. The run counts are read from the database, so a
  runner restart does not reset them.
- **Dashboard**: Grafana → "Agent Market" (`infra/grafana/provisioning/dashboards/agent_market.json`).
- **SLO and error budget**: 95 % of the agent runs that end, end succeeded
  (cancelled by the person excluded), rolling 14 days (Prometheus keeps 15).
  Budget = 5 %. Recording rule `agent_market:error_budget_remaining:ratio14d`
  (1 = untouched, 0 = spent, < 0 = SLO missed).
- **Alerts** (`infra/prometheus/agent_market.yml`, all `channel="agent-market"`):
  budget burning, budget spent, runner down, queue stuck > 10 min, sandbox not
  ready, pool saturated. Tested with `promtool test rules infra/prometheus/tests/agent_market_test.yml`.
- **Its own pager topic (AM1c)**: Prometheus here has no Alertmanager, so
  ops-watch reads the firing `channel="agent-market"` alerts from Prometheus
  (`OPS_WATCH_PROMETHEUS_URL`) every tick and pages them on
  `AGENT_MARKET_NTFY_URL` (`.env`; subscribe to it in the ntfy app) through a
  pager of their own (`backend/app/ops/agent_market_alerts.py`): one page, one
  "recovered", its own Redis namespace, the same maintenance window. Never the
  core channels; a core incident never reaches that topic. The daily smoke's
  `agent_market.*` checks stay ignored by ops-watch's smoke rule
  (`classify_smoke`), and the core `TargetDown` rule excludes
  `job="agent-runner"`. Test: `docker compose -p ai-gateway exec ops-watch
  python -m app.ops.agent_market_alerts test` (one `[TEST]` alert, agent topic only).

## Going live (AM1b order)

1. Latest desktop build installed. 2. Migration: `down` tested on the test
stack, a backup immediately before, a quiet hour, the only schema change in the
window. 3. Portal project + key → `.env` only. 4. Allowlist = the owner's email,
then the flag on. 5. Smoke green; a non-allowlisted account sees no agent surface.
