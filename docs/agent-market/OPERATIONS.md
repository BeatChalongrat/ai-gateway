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

## Where a run's documents go, and whether they are masked (AM16)

Two switches per agent in the admin app (Agent Market → Models), both **off by default**, each change
of either in the audit log (`agent_market.agent.documents_may_leave`, `agent_market.agent.masked`):

| documents may leave (the organisation's switch AND the agent's own) | masked | what happens |
|---|---|---|
| **off** (default) | **off** (default) | in-house models only, no web; sent **as typed** |
| off | on | in-house models only; personal data in the text is masked first |
| **on** | **off** | may go to a model outside the organisation, **as typed — not masked** |
| on | on | text may go outside, masked first; **a page is read inside only** |

Until AM16 there was one switch and masking followed it ("may leave" meant masked). A row that was
open before the migration (`010_am16`) is kept masked, so nothing changed for any agent by deploying it.

**What masking covers: text.** The image of a page — a scanned invoice read by a model — cannot be
masked, on any path, and never was. That is why a masked agent's pages are read by an in-house model
only, and why its model menu has no outside model.

**What a run says.** A run whose documents went to a model outside the organisation as typed — text
that was not masked, or a page — says so in one sentence on the face of its card, in the reader's
language, as soon as it has happened and whatever the run then comes to
(`progress.sent_out_unmasked`, `outputs.model.sent_out_unmasked`). It is noted from what happened to
each model call, not from the switches. The older line (`outputs.model.masking` `off` / `on` / `mixed`)
says what the gateway did with the text.

The runner tells the gateway per model call (`X-Sran-Masking: off | on`; `X-Sran-Egress: closed` when
the data may not leave), and the gateway decides (`backend/app/services/dlp/in_house.py`;
`docs/DLP_ARCHITECTURE.md`, "A named caller's own word on masking"). The gateway believes these
headers only from a project the operator named:

    DLP_IN_HOUSE_PROJECTS=<the id of the portal project "Agent Market runner">     # .env, then restart the backend

Empty (the default) switches the whole rule off: every run is masked, whatever the runner sends, and
its card says "masked". The id is not a secret; it is the `project_id` on any `request_logs` row the
runner's key made.

In the request log: `dlp_input_action = 'in_house_unmasked'` on a row whose findings reached the
in-house model as typed, `'sent_as_typed'` on a row whose findings reached a provider outside as typed;
`input_masked` / `output_raw` / `output_final` hold the masked copy either way. To list what left:

    select created_at, model, provider, dlp_input_findings from request_logs
    where dlp_input_action = 'sent_as_typed' order by created_at desc;

(A page sent outside leaves no finding — the scan does not read images — so it is not in that list;
the run's own steps carry `sent_out_unmasked`.)

**To whom (AM16).** Under the two switches the admin names who may receive: admin app → Agent Market
→ Models → "Who outside the organisation may receive documents" is the organisation's list, and each
agent's row has its own ticks among them. Every recipient is shown with both names — "Google via
OpenRouter", "Amazon Bedrock via OpenRouter", "DeepSeek (direct)" — the company being the one that
runs the model, which is not always the one that made it. **An agent whose documents may leave and
that has nobody ticked reaches nobody**, and its row says so. To open an agent to one company:
tick the organisation's switch, the agent's "Allowed", the company in the organisation's list, and the
same company in the agent's row — four ticks, each its own audit line. To stop everything going to one
company at once, untick it in the organisation's list.

What can be ticked comes from the gateway's catalogue. Behind OpenRouter that is, today: Google (its
own models, and Claude on Vertex), Amazon Bedrock and Anthropic (Claude), Microsoft Azure and OpenAI
(GPT). The OpenRouter account in use lets only endpoints that keep nothing through (its "ZDR"
guardrail), so **ticking "Anthropic via OpenRouter" or "OpenAI via OpenRouter" gives a run that fails
at the router** — it is not sent to Amazon or Microsoft instead; tick those if that is who should read
it, or give the gateway a direct key. DeepSeek's and Meta's models behind OpenRouter, and OpenRouter's
own "auto", are run by whoever hosts them that day: they have no recipient and cannot be opened.
Who really answers is asked of OpenRouter with `scripts/agent-market/openrouter_pin.py`; the gateway's
rule is asked with `scripts/agent-market/recipients_probe.py`.

**Before an agent that reads pages is opened to outside models, its installs must be on the version
that names the reader:** tax-invoice 1.10.0, expense audit 1.9.0, invoice ↔ PO matching 1.7.0. An
install on an older version still asks "the in-house model read … from the image" whoever read the
page (a version keeps the engine it was registered with); the upgrade is offered in the agent's room
as for any version. The catalogue shows the new guide to anyone who opens it after the deploy.

What does not change: a block still blocks; a request holding a key or a secret keeps its mask on the
way out; nothing unmasked is cached, logged or sent to moderation; no other model is tried with typed
text. Ordinary chat is not part of this — it has no such switches and is masked whichever model answers.

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
  `agent_market_runs_waiting_budget`; AM15: `agent_market_runs_waiting_model`. The run counts are
  read from the database, so a runner restart does not reset them.
- **Who answers the runner's model calls (AM15)**: `GET /model` on the same port asks the primary
  model one word — through the client, key and gateway every step uses, in words no call before it
  sent — and says `asked`, `answered`, `served`, `ms`, `error`. `/healthz`'s `"model": true` says only
  that a model is configured: it said so through the hours of 9 Oct 2026 when the in-house model
  answered nobody. The smoke suite reads `/model` (`agent_market.model`).
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
  closed), runs waiting for budget over an hour; AM15: runs waiting for a model that cannot be
  reached, after ten minutes. Tested with `promtool test rules infra/prometheus/tests/agent_market_test.yml`
  (`scripts/gate.sh lanes` runs it).
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
range (`scripts/agent-market/test_stack_web.sh`), so every refusal and every page
is the fetcher's real code against a web that is a fixture.

## The gate, one heavy thing at a time (AM11b)

The host is a 32 GB laptop that also runs live (`docs/HOST_MEMORY.md`). On 7 Oct 2026 a gate's lanes,
test stack, image builds and desktop build ran as each came to mind; the host went to 1.1 GB free five
times and the integration lane was cut twice with no verdict. Run alone, that lane takes 41 minutes and
passes. A gate's heavy phases are now started through one script and nothing beside it:

```
bash scripts/gate.sh preflight          # live's memory guard, free memory, the lock
bash scripts/gate.sh lanes              # engine · go · backend · web · admin · desktop, test stack stopped
bash scripts/gate.sh it                 # the integration lane, alone, each chunk's verdict as it ends
bash scripts/gate.sh build <sha>        # the five images; the VM's cache is handed back after
bash scripts/gate.sh stack <sha>        # test stack + stand-in web + built clients; red when an image is missing or a service is not healthy
bash scripts/gate.sh stack-stop         # stop the test stack (nothing is removed)
bash scripts/gate.sh walk -- <command>  # one walk
bash scripts/gate.sh smoke <sha>        # flag on, flag off, back on
bash scripts/gate.sh drill              # removal drill + gitleaks on the unpushed commits
bash scripts/gate.sh backup             # a live backup with its restore drill (--no-deps)
```

Every phase:

- is refused on battery (AM16): `REFUSED`, exit 3, before the lock, having started nothing — and a run
  of lanes asks again before each lane, so a charger pulled an hour in stops the next one;
- takes a lock, and a second phase is refused while one holds it (exit 3, saying who holds it);
- stops the test stack first when it does not need it (lanes, the integration lane, builds);
- waits for 3,000 MB free on the host (`GATE_MIN_FREE_MB`), handing the VM's cache back once, and
  after ten minutes (`GATE_WAIT_S`) stops saying how much the host had — having started nothing (exit 4);
- checks live's memory settings against compose and puts them right in place if a container was
  restarted since (`scripts/ops/live_memory.py`);
- writes one file under `GATE_RESULTS` and prints one verdict line.

A card sets `GATE_RESULTS` and `GATE_IT` (its own integration lane, run after the two standing
chunks; the default is the newest card's, `scripts/gate/it/am16.sh`).
`scripts/tests/test_am11b_gate.py` holds the lock and the stop, without Docker.

**On battery nothing heavy starts (AM16).** On 12 Oct 2026 the power was printed in front of
`gate.sh lanes backend_lane`; it said "battery" and the lane ran. The check is now in the tools
(`scripts/ops/power.sh`, described in `docs/ops/monitoring.md`): every phase above but `preflight` and
`stack-stop`, the swap of a live service (`scripts/ops/swap.sh` — the runner too, asked through
its health check: `swap.sh agent-runner health:sran-agent-runner -p sran-agent-market -f
docker-compose.agent-market.yml --profile agent-market`), the runner's, fetcher's and converter's image
builds, the engine's Python lane, bringing the test stack up and building its clients. To run one of
them on battery anyway: `RUN_ON_BATTERY=yes` on that command — it says on its output that it did.
`scripts/tests/test_am16_gate_on_battery.py` holds it, with six ways of breaking the rule shown red.

**A step passes on what is true, not on what it intended (AM19b).** On 14 Oct 2026 `gate.sh stack` was
given an 8-character sha: Compose tried to pull a tag that does not exist, the chat-edge stayed down, and
the step said ok. Now the test stack's `up` refuses, before Compose is asked anything, when an image it
would run is not on the host (exit 6, the images named), and ends with `test_stack.sh check <tag>`: red,
naming the container, when one the stack started is not running or not healthy, or when the edge, the
runner or the backend runs an image other than the one asked (exit 7); the test clients' `up` is red when
a door does not answer. The swap of a live service is not started when its measurement cannot be written
(exit 5), says `NOT MEASURED` when its poll recorded nothing (exit 8), and is red when the service never
answered 200 ten times in a row after the swap (exit 9) — the swap of the web chat on 14 Oct had printed
"0 requests, 0 not 200" as if it were a pass. `scripts/tests/test_am19b_steps_assert.py` holds all of it
with stand-ins for Docker, curl and sleep.

**The backend lane is the suite, not a list (AM15).** A card used to name the backend files its lane
ran (`GATE_BACKEND_TESTS`); a test in any other file could fail for months with no lane to say so.
That setting is gone. `lanes` runs every backend test not marked `integration` (`backend_lane`), then
every one that is (`backend_marked`) — 661 tests no lane had run, 91 of them failing on 12 Oct 2026.
The ones that still fail are not hidden and not green: they are on a list by name with why
(`scripts/gate/backend_marked_failing.txt`), and `scripts/gate/marked.py` holds each run to it — a
failure not on the list is red, a name on the list that no longer fails is red (the list only gets
shorter; `--write` takes names off and refuses to add one), an unfinished run is red, and what
stands on the list and what the lane skipped is printed in the gate's own output on every run. The
lanes' last line does not say "ALL GREEN" while a name is on it. `lanes` also runs what only its
author ran before: `scripts/tests/test_*.py` (`host_tests`), `scripts/smoke/*.test.js`
(`smoke_units`) and the alert rules' own tests (`alert_rules`).

**The integration lane is a list of names too** (`scripts/gate/it/*.sh`, in chunks): a
sandbox test that no chunk names runs nowhere, and a renamed one leaves a chunk that matches nothing
and passes. `runner/am15_lane_test.go`, in the unit lane, fails for either.

**The lane's file keeps every chunk's verdict (AM22).** Each card's lane prints the lane it wraps to
stderr as well, and the gate collects stdout and stderr into one file. From AM19 to AM22 that was
`tee /dev/stderr`, which opens the file afresh and truncates it: on 17 Oct 2026 the file was emptied the
moment the card's piece started, taking two failures of the AM10 chunks with it, and the nested lanes
wrote over each other's lines. Every lane tees with `-a` now; `scripts/tests/test_am22_lane_output_kept.py`
fails on a lane that does not.

**A green that means green (AM24).** A check that says it passed must have looked at something and been able to
fail. One helper, `scripts/gate/ledger.py`, holds every lane, every chunk of the integration lane and the smoke to
three rules: a selector that matched nothing is red (a lane whose output names no check that ran; a chunk that ran
no test, or in which a name its selector lists ran as no test — `scripts/gate/it/lib.sh run_chunk`, which every lane
script uses); a check over nothing is a skip, never a pass (a lane whose every check was skipped; the smoke's
`portal.billing_api` when the organisation sells no pack); and a count that falls on its own is red — each run's
count is held to the lane's last green run, and a check removed on purpose is removed with a line in
`scripts/gate/removed_checks.txt` saying what replaced it, which is what lets the count fall. The smoke holds its
checks by name the same way (`smoke.count`), so a check gone is named.

**One verdict, from pieces (AM24).** The host cannot hold the whole gate at once, so it runs in pieces — and from
AM19 to AM23 nobody saw every lane green at one commit. Every lane now writes, when it ends, the commit it ran at,
when, its count and its result to the gate's ledger (`scripts/gate/results/ledger.jsonl`); a lane run with
uncommitted changes under it is recorded as such and never counts. `bash scripts/gate.sh verdict` (and the last line
of every `lanes` run) is green only when every lane, every chunk, the build, the test stack and the smoke have a
green result at HEAD; otherwise it names each one that has not, with the commit its last result carries. And as
the full run has said since AM15, it is not ALL GREEN while a backend test still fails by name on
`scripts/gate/backend_marked_failing.txt`.
`scripts/tests/test_am24_ledger.py` holds each rule with a fixture shown red, and a run with a lane left out.

**A swap that swapped nothing is not a pass, and a slow one says so (AM24).** On 16 Oct 2026 the admin's swap was run
against the web chat's compose file: Compose said "no such service", nothing was recreated, and the tool printed a
pass. The swap now asks the compose files which services they hold before it touches anything (a service they do not
name is red, exit 6, naming both), is red when Compose refuses the swap (exit 7), and red when the service still runs
the container it ran before (exit 10). Every measured swap is told against a ceiling — 5 s without a 200 for a door,
20 s for a health check (`SWAP_CEILING_S`): above every ordinary swap measured since AM11b (doors 0.5–4.6 s, health
checks 5.8–6.7 s) and, for a door, half of the 10 s the front door holds a request before it fails it — and over it
says `SLOW`, with where the time went from Docker's events (the old container's stop, the new one's start and health),
and still exits 0: a slow swap is not a failed one, and is something to tell the owner. Every swap adds a line to
`scripts/ops/results/swap_series.tsv` (seeded with the gaps the reports recorded since AM11b), so a gap that grows is
seen at 2×, not at 23×. `scripts/tests/test_am24_swap.py`.

**A failed Agent Market smoke check pages (AM24).** The core pager leaves the `agent_market.*` checks out (AM1b:
Agent Market never pages the core channel), and until AM24 nothing else read them. Agent Market's own pager
(`app/ops/agent_market_alerts.py`) now reads the same smoke record as the core and pages its own topic
(`AGENT_MARKET_NTFY_URL`) with the failed agent checks; a green run closes it. It reads the record only on a tick
Prometheus answers — while Prometheus is down Agent Market's pager is still blind (AM15 §10). Test:
`backend/tests/test_am24_agent_smoke_pages.py`.

**The smoke can fail, and the gate proves it each time**: `gate.sh smoke` takes the model away on
the test stack (`scripts/smoke/dead_primary.js red`) and demands a RED suite in which every turn
names the model it asked, then puts it back and demands GREEN (`green`).

What the gate starts gives way to live: the test stack's services have no floor and turn 500 (the
generator), the test project's runner, converter and fetcher are put at 500 by
`scripts/agent-market/test-web.yml`, the built test clients and stand-in sites are started with
`--oom-score-adj 500` and a ceiling. On live the runner keeps −500, the converter and the fetcher −300:
after the databases, the front door and the applications
(`backend/tests/test_am11b_memory_guard.py`).
