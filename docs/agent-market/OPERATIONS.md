# Agent Market — operations (AM1b, AM2)

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

## Document converter (AM2)

PDF and workbook recalculation are LibreOffice in its own container,
`doc-converter` (`services/doc-converter`, image `ai-gateway-doc-converter`,
built by `scripts/agent-market/build-converter.sh`), in the same compose
project as the runner.

| Limit | Value | Where |
|---|---|---|
| network | only `converter`: an `internal` network shared with agent-runner alone — no route out, no port on the host | `docker-compose.agent-market.yml` |
| user / filesystem | `cap_drop ALL`, `no-new-privileges`, read-only root, `/tmp` tmpfs 256 MB | same |
| memory / CPU / processes | 768 MB, 1 CPU (`AM_CONVERTER_CPUS`), 128 pids | same |
| one conversion | a fresh LibreOffice profile and work directory each, removed after; 60 s wall clock (`AM_CONVERT_TIMEOUT_SECONDS`) then the whole process group is killed | `services/doc-converter/server.py` |
| queue | one conversion at a time, at most 4 waiting or running (`MAX_PENDING`), input ≤ 25 MB | same |
| fonts | Thai fonts baked into the image; no font or anything else is ever fetched | `services/doc-converter/Dockerfile` |

A PDF is only ever the converted .docx (the ReportLab path is gone). Every PDF
passes two gate checks before it is delivered: `pdf.pages` (every page is
rasterised; a page with no ink or all ink fails) and `pdf.thai` (every Thai
paragraph of the .docx is found in `pdftotext -raw` text). A customer's .xlsx
template is recalculated by LibreOffice and only the calculated values are
merged back into our own filled file — the customer never gets LibreOffice's
re-export. PDF is offered only by a runner with a converter configured
(`AGENT_MARKET_CONVERTER_URL` → capability `deliverable.pdf`). If a conversion
fails (converter down, timeout) or a gate check fails, the run that asked for
the PDF fails its quality gate (`quality_gate`, the reason in the workspace)
and nothing unchecked is delivered — the person can run it again without PDF.

## Which model sees a document (AM2)

Digital PDFs, e-Tax XML and spreadsheets are read by rules only (E2) — no
model. A scan or photo gets a second reading from a vision model **only when
that model is self-hosted**: `AGENT_MARKET_VISION_MODEL` (default
`qwen3.8-27b-fp8`) must be listed in `AGENT_MARKET_SELF_HOSTED_MODELS`, and
there is no fallback to another model. Its reading is only a proposal next to
E2's; a person decides. Every run records `model_used` and
`model_self_hosted`; the workbook lists which model saw which document.

## What waits on a person (AM2)

Questions are cards (`agent_run_clarifications`). What a rule found after
the numbers — an expense over its limit, a missing receipt, a duplicate, a
split purchase — is a run exception (`agent_run_exceptions`), open until a
person picks one of its own choices; nothing is rejected automatically, and
there is no hidden "reject". The per-agent dashboard
(`GET /installs/{id}/dashboard`) shows what is waiting and for how long.

## Retention (AM2)

The runner's hourly pass:

| What | Kept | Setting |
|---|---|---|
| an upload never attached to a run | 48 h | `AGENT_MARKET_STAGED_UPLOAD_HOURS` |
| a failed or cancelled run's scratch (its step blobs; the run row stays as its record) | 7 days | `AGENT_MARKET_FAILED_SCRATCH_DAYS` |
| a removed install (everything: runs, files, artifacts, index) | 30 days after removal, like a deleted chat | `AGENT_MARKET_REMOVED_INSTALL_DAYS` |
| a succeeded run's evidence and outputs | as long as its install | — |
| the duplicate index (invoice 730 days, expense 730 days) | the package's `document_index.retention_days` | the manifest |

A person's data export includes `agent-market/` (installs, runs, cards,
exceptions, files and outputs with their bytes); an account deletion stops
their runs at the request and erases everything at the purge
(`service.AccountDataProvider`).

## Versions (AM2)

An install is pinned to the version it installed, and its runs execute that
version's own engine (stored at `agent-market/engines/<build>.zip`, loaded by
any runner). A changed package must carry a new version or it is not
registered (the old one keeps running; the runner logs an error). Upgrade:
settings → upgrade shows the diff, runs the new version's self-test on this
install, and switches only if it passes and meets the package's own
expectations; rollback returns to the previous version. An install whose
pinned engine is missing fails its run with "upgrade this agent".

Versions released before AM2 recorded their engine by its AM1 checksum and
the engine itself was never stored. The AM2 deploy keeps it, once, before the
new runner takes over — from the running AM1c runner image:

```bash
docker cp <old runner container>:/app/agent_market.zip ./am1c_engine.zip
docker compose -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market \
  run --rm --no-deps -v "$PWD/am1c_engine.zip:/tmp/am1c.zip:ro" agent-runner store-engine /tmp/am1c.zip
# prints: engine checksum … build …, then per package "its engine is kept at agent-market/engines/<checksum>.zip"
```

It stores the zip only for a released version whose package checksum and
engine checksum both match it; the installs pinned to those versions keep
running their own engine, and settings offer the upgrade.

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
