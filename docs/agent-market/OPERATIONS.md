# Agent Market — operations (AM1b, AM2, AM3)

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

## The organisation (AM3)

Who may use Agent Market, what they may spend, and what the catalog offers
are the organisation's decisions, kept in the database by its admins
(web: Agent Market → "จัดการ Agent Market"; API `/api/v1/agent-market/admin/*`).

| What | Where | Notes |
|---|---|---|
| The switch | `org_settings.access`: `off` (admins only) · `list` (the people on the list) · `everyone` (every signed-in user) | first start: `list` seeded from `AGENT_MARKET_ALLOWED_EMAILS`, or `everyone` when that is empty (AM1's meaning) — once; the admins' settings win afterwards |
| The people | `org_members`: email, name, department, role `member` · `publisher` · `admin`, personal monthly budget | the first admins: `AGENT_MARKET_ADMIN_EMAILS` (always admins, break-glass), or the pilot's allowed emails when unset; the last admin cannot be removed |
| The organisation's email domains | `org_settings.domains` (`AGENT_MARKET_ORG_DOMAINS` seeds it) | a run can be shared only with someone at these domains or on the list |
| Budgets | per person (the org default `user_monthly_credits`, `AGENT_MARKET_USER_BUDGET_CREDITS` seeds it; or the person's own), per department (`org_departments`, none = no limit), per install (AM1) — Bangkok month | a run that hits one **waits** (`awaiting_input`, `budget_user` / `budget_department` / `budget_install`) with a notice naming who to ask (the department's contact, else the organisation's); it goes on from where it stopped when an admin raises the limit (at once) or the hourly pass finds room (a new month) |
| The catalog | `catalog_state` per agent: `published` · `unpublished` (only publishers and admins see it; every new package starts here) · `disabled` (admins only, with a reason people read) | disabling stops the agent's queued runs with a notice and refuses new installs, runs, answers and sign-offs, naming who disabled it; every run, file and decision stays |
| Samples | the runner runs every released version's self-test once, under an install nobody owns (`status = 'catalog'`, nil tenant/user) | the catalog serves only those rows' files (`/sample-artifacts`, `/sample-files`); "สร้างตัวอย่างใหม่" makes one again; their spend shows apart on the spend page |
| Data sheet | `GET /catalog/{slug}/datasheet` — the manifest (what it reads, `guide.model_inputs`) + what the runner binds (models, self-hosted or not, storage and retention: the `platform.storage` binding) | the same page for procurement and for the person clicking install |
| Sign-off | `run_reviews` + a `review` job: the runner stamps the newest docx/pptx/xlsx with the reviewer's name, time and note (its own engine — the stamp changes no number), converts the PDF again, keeps the unsigned versions | a finished run with no open exception; its owner, or a colleague it was shared with "may review" |
| Sharing | `run_shares`: a named colleague at the organisation's domains or on its list, signed in as that email; read only or may review; expiry; revocable; opens counted | never public (chat's share link is; a run's files are accounting data) |
| Problems | `problem_reports`: the person's sentence + the run, its status, the step it stopped at and that step's error, the page | admins' page "ปัญหาที่แจ้ง"; the runner's `agent_market_problem_reports_open` gauge → alert `AgentMarketProblemReported` → the agent pager topic |

A second organisation later is a row in `org_settings` whose `domains` its
people sign in with — every AM3 table carries `org_id`; the people of the
first organisation are resolved by default.

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
  `agent_market_sandbox_ready`; AM3: `agent_market_problem_reports_open`,
  `agent_market_runs_waiting_budget`. The run counts are read from the database, so a
  runner restart does not reset them.
- **Dashboard**: Grafana → "Agent Market" (`infra/grafana/provisioning/dashboards/agent_market.json`).
- **SLO and error budget**: 95 % of the agent runs that end, end succeeded
  (cancelled by the person excluded), rolling 14 days (Prometheus keeps 15).
  AM6: a run that finished open — its draft delivered, its package's gate not
  passed — is `status="completed_open"`: it ended, and it is not a failure.
  Budget = 5 %. Recording rule `agent_market:error_budget_remaining:ratio14d`
  (1 = untouched, 0 = spent, < 0 = SLO missed).
- **Alerts** (`infra/prometheus/agent_market.yml`, all `channel="agent-market"`):
  budget burning, budget spent, runner down, queue stuck > 10 min, sandbox not
  ready, pool saturated; AM3: a problem someone reported (at once, until it is
  closed), runs waiting for budget over an hour. Tested with `promtool test rules infra/prometheus/tests/agent_market_test.yml`.
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

## Reminders, notifications and e-mail (AM6)

- **Nothing runs without a person.** A reminder only opens a round that waits
  for files and tells its owner; the round runs when that person sends the
  files. The runner's minute pass does it (`runner/reminders.go`): with the
  runner stopped, nothing falls due, and the first pass after it starts
  catches up (one round per reminder, never one per missed minute).
- **Who is told, and how**: in the app (the web's banner strip, a browser
  notification if the person allowed it) and, while the desktop app runs, as a
  Windows notification. Nothing reaches a person who has neither open: e-mail
  delivery is off in production (`EMAIL_DELIVERY=disabled`), so no reminder is
  e-mailed.
- **What to look at**: `agent_market.reminders` (`next_due_at`, `fired_period`,
  `waiting_run_id`, `followup_sent_at`), `agent_market.notifications`
  (`shown_web_at`, `shown_desktop_at`, `dismissed_at`). The runner logs each
  pass that did something (`reminders pass`).
- **E-mail**: drafts only. No mail connector is configured, so "ส่ง" answers
  that the draft is to be copied (`NO_MAIL_CONNECTOR`). A send, once a
  connector exists, is logged in `agent_market.email_sends` (who approved it,
  to whom, which files); the log stays with its round, and a person's account
  deletion blanks their id and address in it.

## Going live (AM1b order)

1. Latest desktop build installed. 2. Migration: `down` tested on the test
stack, a backup immediately before, a quiet hour, the only schema change in the
window. 3. Portal project + key → `.env` only. 4. Allowlist = the owner's email,
then the flag on. 5. Smoke green; a non-allowlisted account sees no agent surface.

## The web fetcher and agents on the web (AM12)

**What runs.** One more container in the Agent Market project: `web-fetcher`
(image `ai-gateway-web-fetcher:<sha>`, built by
`bash scripts/agent-market/build-fetcher.sh [REV]`; ~1.2 GB, a headless
Chromium). Read-only root, `/tmp` in memory, no capability, no volume, no
published port. It is on two networks of the project and no other: `fetch`
(internal — the runner asks it for pages) and `web` (its way out). It is not
on the core network: it cannot name or reach the gateway, the databases or
any other service. Idle it holds a Node process (~60 MB); a browser exists
only for the length of one fetch.

**Turning it on for an agent.** Admin app › Agent Market › Models: the
organisation's switch, then the agent's own. An agent on the web is off
until both are on. Admin app › Agent Market › Web shows whether a search
provider and the fetcher are there, and holds the ceilings of one run, the
sites never opened, the organisation's suppliers and where each run went.

**Search.** The runner searches through the gateway's `/v1/search` with its
own gateway key; the provider (and its key) is the platform's, set on the
admin app's Search page. With none set, an agent opens only the links a
person pastes, and says so. Each search is on the run's ledger at
`AGENT_MARKET_SEARCH_USD_PER_QUERY` (default 0.008) and on the Spend tab as
model `search:<provider>`.

**Settings** (`.env`; all optional):

| Setting | Default | What |
|---|---|---|
| `AGENT_MARKET_FETCHER_URL` | `http://web-fetcher:8097` | where the runner asks for pages; empty = no fetcher, no run on the web starts |
| `AGENT_MARKET_FETCH_TIMEOUT_SECONDS` | 75 | the runner's wait for one page |
| `AGENT_MARKET_SEARCH_USD_PER_QUERY` | 0.008 | what one search costs, for the ledger |
| `AM_FETCHER_OWN_HOSTS` | (none) | the organisation's own public site names, comma-separated: never opened |
| `AM_FETCHER_MAX_BYTES` | 10485760 | the most one page may transfer |
| `AM_FETCHER_TIMEOUT_MS` | 45000 | the fetcher's own limit for one page |
| `AM_FETCHER_MEMORY` / `AM_FETCHER_CPUS` | 1536m / 1.0 | the container's limits |
| `AM_FETCHER_TAG` | latest | the image tag |

**Checks.**

```bash
docker inspect -f '{{.State.Health.Status}}' sran-agent-market-web-fetcher-1                      # healthy (the image's own check of /health)
docker network inspect sran-agent-market_web --format '{{range .Containers}}{{.Name}} {{end}}'   # the fetcher alone
docker network inspect ai-gateway_default --format '{{range .Containers}}{{.Name}} {{end}}' | tr ' ' '\n' | grep -c fetcher   # 0
```

The runner's heartbeat lists `web.search` and `web.fetch` when it has them;
without `web.fetch` the workspace says the part that saves pages is not
available and no run starts.

**What is kept, and for how long.** Saved pages are files of the run's steps
(object prefix `agent-market/…/runs/<run>/steps/…`). The runner's
housekeeping deletes the pages of sources that did not become a candidate
after the admin's number of days (Web tab; 7 by default) and marks the call
`purged`. Pages of candidates go with the run's own retention. `web_calls`
rows stay with the run.

**When something is refused.** A refusal is not an incident: the reason is
on the run page ("pages opened") and on the admin's Web tab, in words. A run
in which every source was refused still finishes, with a table of no
candidates and each source's reason. The fetcher never retries past a
challenge, a sign-in or a `robots.txt` rule; do not add a site-specific
exception for one — put a site that should not be asked on the never-open
list instead.

**When a saved page is not read.** A page's text goes to a model through the
gateway like every other request, so the gateway's own rules apply to it. A
page the content guardrail refuses (403 `guardrail_blocked` — a page that
carries instructions to its reader is one) is saved, read by no model, and
listed in the run's file among the sources not used with that reason
(`read_refused`); when no model answered for another reason the reason is
`not_read`. Neither is an incident, and neither is retried. A run that ends
with nothing to compare says so in the thread, with what to try next; its
file is the record of what was searched and opened, and what it used is
charged like any delivered run.

**The answer's shape is not in the system message.** If a page is "read by no
model" on every run while the gateway log shows the model answering, look at
the prompt first: the platform's prompt-echo guard removes from an answer any
run of 40 characters that repeats the system message, and a prompt that prints
its JSON shape among the rules loses pieces of every faithful answer
(ARCHITECTURE.md, "A prompt is two parts"; `backend/tests/test_am12_prompt_echo.py`).

**The test stack** has no route to the real web: its fetcher's `web` network
is replaced by an internal network of stand-in sites on a public address
range (`scripts/probes/AM12/test_stack.sh`), so every refusal and every page
is the fetcher's real code against a web that is a fixture.
