# Agent Market — removal

Agent Market is a bolt-on. Removing it is five boring steps, and the core is
left exactly as it was before the feature arrived. This file is the drill the
AM1 card ran for real (its output is in the AM1 report); AM2 ran it again
after touching core export/delete and the shared sandbox scripts (output in
the AM2 report).

## What exists, and where

| Piece | Where | Removed by |
|---|---|---|
| Control plane (HTTP) | `services/chat-edge-api/internal/agentmarket/` (mounted only when `AGENT_MARKET_ENABLED=true`) | revert |
| Execution plane | `services/chat-edge-api/cmd/agent-runner/` + `internal/agentmarket/runner/`, image `ai-gateway-agent-runner:*`, compose project `sran-agent-market` (file `docker-compose.agent-market.yml`, profile `agent-market`) | step 1 + revert |
| Python engine + agent packages | `backend/agent_market/` (runs only inside the sandbox; the backend app never imports it) | revert |
| Web UI | `apps/web-chat/src/features/agent-market/`, `apps/web-chat/src/app/chat/agents/`, `apps/web-chat/src/app/api/agent-market/route.ts` | revert |
| Data | Postgres schema `agent_market` in the chat database (`sran_ai`) — no foreign key into or out of it | step 3 |
| Files | object storage prefix `agent-market/` (local: the folder of that name in volume `ai-gateway_chat_edge_uploads`) | step 4 |
| Runner's gateway identity | portal project "Agent Market runner" and its API key; `.env` `AGENT_RUNNER_GATEWAY_API_KEY` | step 5 |
| Settings | `.env`: `AGENT_MARKET_*`, `AGENT_RUNNER_*`, `AM_*` | step 5 |
| Scratch | `data/agent-sandbox-runs/` | step 4 |
| Observability (AM1b) | Prometheus job `agent-runner` + `infra/prometheus/agent_market.yml` (+ its test), Grafana `infra/grafana/provisioning/dashboards/agent_market.json` | revert, then `docker kill -s HUP ai-gateway-prometheus-1` |
| Agent pager (AM1c) | `backend/app/ops/agent_market_alerts.py` (ops-watch reads Agent Market's alerts from Prometheus and pages its own topic only), `.env` `AGENT_MARKET_NTFY_URL` | revert + step 5 |
| Document converter (AM2) | `services/doc-converter/` (LibreOffice: .docx → PDF, .xlsx recalculation), image `ai-gateway-doc-converter:*` (`scripts/agent-market/build-converter.sh`), service `doc-converter` and the internal network `converter` in `docker-compose.agent-market.yml` | step 1 + revert |
| Stored engines (AM2) | every runner's engine zip under `agent-market/engines/<build>.zip` (a pinned install runs the engine it was released with) | step 4 (same prefix) |
| Duplicate index, versions (AM2) | migration `002_document_index`: table `agent_market.document_index`, `agent_runs.scratch_purged_at`, `agent_installs.previous_definition_id` — inside the schema | step 3 |
| The organisation, catalog, rail, sign-off, sharing, problems (AM3) | migration `003_self_serve`: tables `org_settings`, `org_members`, `org_departments`, `catalog_state`, `user_prefs`, `catalog_samples`, `run_reviews`, `run_shares`, `problem_reports`; `agent_installs.org_id / pinned_at / last_opened_at`; run kind `review`, install status `catalog` (the runner's sample installs) — inside the schema; its down script undoes all of it | step 3 |

The only lines in existing files (the "seams") — all removed by the revert
(AM3 adds no new seam file: it changes lines inside the seams below — the
Sidebar element, the compose env block, the smoke area and the Agent Market
alert file; it reads the core's `identity.users` name and email by user id
for display, and changes nothing there):

- `services/chat-edge-api/cmd/server/main.go` — one import, one flag-gated block
- `services/chat-edge-api/internal/router/router.go` — one field, one guarded mount
- `docker-compose.webchat.yml` — the flag env lines on chat-edge and web-chat
  (AM3: four more `AGENT_MARKET_*` seed settings on chat-edge)
- `apps/web-chat/src/components/chat/Sidebar.tsx` — one import, one element
- `apps/web-chat/tailwind.config.js` — one `content` path
- `scripts/smoke/run.js` + `scripts/smoke/README.md` — the `agent_market` area
- `docker-compose.test-stack.yml` — regenerated (it mirrors the flag env line)
- AM1b: `infra/prometheus/prometheus.yml` (the `agent-runner` job and rule file),
  `infra/prometheus/alerts.yml` (`TargetDown` leaves `job="agent-runner"` to its own alert),
  `backend/app/ops/watch.py` (`classify_smoke` never pages on `agent_market.*` checks)
- AM1c: `backend/app/ops/watch.py` + `backend/app/services/alert_channels.py` +
  `alert_pager.py` + `backend/app/core/config.py` (the agent-market channel and its
  topic), `docker-compose.yml` (two env lines on ops-watch),
  `services/chat-edge-api/internal/sandbox/sandbox.go` (labels on every step
  container so a restarted runner reaps what a killed one left)
- AM2, core export and deletion: `services/chat-edge-api/internal/service/account_providers.go`
  is a feature-agnostic registry (`AccountDataProvider`: Name, Export, Stop, Purge);
  `account_export.go` and `account_deletion.go` call every registered provider at
  their three moments (the export ZIP, the deletion request, the purge). Agent
  Market registers itself with **one line** inside the flag-gated block of
  `cmd/server/main.go`. With the flag off nothing registers and export and
  deletion do exactly what they did before; the revert removes the registry too.
- AM2, shared sandbox scripts (also used by chat's code interpreter, so the
  sandbox image is rebuilt after a revert, with a rollback tag):
  `services/chat-edge-api/scripts/extract_text.py` (keeps each OCR word's box and
  confidence), `excel_executor.py` (fills a workbook's named ranges, input cells
  only), `docx_generator.py` (markers, repeating rows, content controls). Each
  change is additive: chat's calls and outputs are unchanged.
- AM5, the chat's own components (the agent workspace stopped being a copy of
  the chat's surface: what both show is one component, so these files of the
  chat were edited, and the revert puts each back as it was):
  - `apps/web-chat/src/components/chat/ChatInput.tsx` — its shell moved to the new
    `ComposerFrame.tsx` (the text box, its growth, Enter, the attach button, the
    send and stop button, the keyboard inset); ChatInput fills the frame's slots.
  - `apps/web-chat/src/components/chat/MessageBubble.tsx` — its parts moved to the
    new `MessageParts.tsx` (the person's bubble and its collapse, the assistant's
    prose, copy, the time, the failed-turn card, the note card).
  - `apps/web-chat/src/components/chat/MessageAttachments.tsx` — its file card moved
    to the new `AttachmentCard.tsx`.
  - `apps/web-chat/src/components/chat/ModelSelector.tsx` — exports its pill, its
    popover and its picker; the popover opens to the left of its pill when
    opening to the right would leave the window.
  - `apps/web-chat/src/components/chat/QuestionCard.tsx` — an optional typed
    field per question (`others`), and `data-typed`.
  - `apps/web-chat/src/components/chat/CatIndicator.tsx` — the label's key is optional.
  - `apps/web-chat/src/lib/i18n/index.ts` — `LocaleScope` / `useLocale`: a part of
    the page may read the dictionary in a language of its own (Agent Market is
    Thai whatever the app's language).
  - tests that read a class by its file: `apps/web-chat/src/lib/contrast.test.ts`,
    `apps/web-chat/src/components/chat/ChatInput.stopButton.test.tsx` (they name
    the file the class now lives in).
  The chat's behaviour is what it was (its own tests pass unchanged apart from
  the two paths above); with the flag off the shared components are simply the
  chat's. One thing the chat gained with them and loses with the revert: the
  composer no longer reads a difference of under one pixel between the window
  and what is visible of it as a keyboard (the desktop app at 125 %), so its
  menus are not drawn under the credits badge.
- AM5 adds no table, no column and no migration: the matching engine
  (`backend/agent_market/engine/e3/`) and the bank reconciliation
  (`backend/agent_market/packages/bank_reconciliation/`) are files of the
  engine; the runner's completion gate is a step of a run (`kind = 'check'`)
  and a reason (`not_tied`) on a failed run; the install's tolerances are rows
  of `agent_defaults`. All inside the schema, all gone with steps 3 and 6.
- AM6, migration `005_am6` (inside the schema; its down script undoes all of
  it): ledger `voided_at` / `void_reason` and index `agent_budget_charged`;
  runs `credits_cost`, `gate_open`, `delivered_at`; `document_index.detail`;
  tables `reminders`, `notifications`, `email_drafts`, `email_sends`.
- AM6, the chat's own components (the agent workspace uses the chat's; the
  revert puts each back as it was):
  - `apps/web-chat/src/components/chat/ChatInput.tsx` — dictation moved to the new
    `components/chat/Dictation.tsx`, which the workspace composer uses too.
  - `apps/web-chat/src/components/chat/ModelSelector.tsx` — optional `quietGate`,
    `notice`, `hidden` / `hiddenLine`; `shortLabel` exported. Without them the chat's
    menu is what it was.
  - `apps/web-chat/src/components/chat/KBSelector.tsx` — optional `standalone`.
  - `apps/web-chat/src/components/chat/UpdateBanner.tsx` — its strip exported as
    `BannerStrip` (same markup), which Agent Market's notifications use.
  - `apps/web-chat/src/components/ui/Button.tsx` — `size="sm"` and `ButtonLink`;
    `components/ui/Modal.tsx` — an optional `testId`. (Agent Market's own copies of a
    button kit and a dialog were deleted: the feature uses the app's.)
  - `apps/web-chat/src/app/chat/layout.tsx` — one element: Agent Market's
    notification host, rendered only when Agent Market is available.
- AM6, the desktop app: `apps/desktop/src/main/agent-notify.ts` (new; polls
  Agent Market's notifications while the app runs, and shows them as Windows
  notifications that open a page of the app's own origin),
  `apps/desktop/src/main/chat-edge-client.ts` (two methods),
  `apps/desktop/src/main/index.ts` (start, stop, the sign-in hook, the toast),
  `apps/desktop/docs/agent-notifications.md`. With the flag off the poll gets
  404 and shows nothing; the revert removes it, and the desktop app is rebuilt.
- AM6, the core's knowledge base: `services/chat-edge-api/cmd/server/main.go`
  passes the chat's knowledge retrieval to Agent Market inside the flag-gated
  block (one closure); nothing in the knowledge service changed.
- AM6, observability: `infra/prometheus/agent_market.yml` (+ its test) counts a
  run that finished open (`status="completed_open"`) as delivered.
- AM8 (the gate opens the finished file): no migration and nothing outside the
  feature's own trees — `backend/agent_market/` (gates, citations, print setup,
  item reading, templates, packages 1.4.0 / 1.4.0 / 1.3.0 / 1.2.0 / 1.1.0),
  `services/chat-edge-api/internal/agentmarket/` (the narrative's figure keys,
  the template's model proposal and draft delivery, the quality line, the
  formats a card offers) and `apps/web-chat/src/features/agent-market/`. The
  desktop app only took a new version number. Gone with step 6.
- AM9: migration `007_am9` in schema `agent_market` only (table `agent_egress`,
  column `email_sends.transport`; gone with step 3). Outside the feature's own
  trees, all in commits named AM9 and reverted with step 6:
  the backend's admin-ui accounts API (`backend/app/api/routes/admin_accounts.py`
  and its line in `main.py`) and the `mail` connector provider (scope
  `gmail.send`) with the `available` list in `/portal/connectors`; admin-ui's
  Accounts page, its role landing and the per-agent egress card; the MCP
  gateway's `mail` server (`services/mcp-gateway/servers/mail-send.js`, its
  entry in `lib.js` and the internal-token gate in `server.js`); the desktop's
  notification memory file (`agent-notifications-shown.json` in its user
  data) and its refusal to offer a `mail` connector; the `MCP_INTERNAL_TOKEN`
  line of `.env` (delete it with step 5). The Mermaid canvas viewer of the
  same card is a chat-app feature in its own commit ("Canvas: …") and stays.

## Steps

Run from the main checkout (`C:\Users\Beat Beat\Desktop\ai-gateway`).

```bash
# 1. Stop the execution plane and the converter (nothing else depends on them).
docker compose -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market down
docker image rm $(docker images -q ai-gateway-agent-runner) $(docker images -q ai-gateway-doc-converter) 2>/dev/null || true

# 2. Turn the flag off and recreate chat-edge + web-chat on their current images.
#    (flag off = no route, no pool, no query, no nav item; verify with step 6)
#    edit .env: AGENT_MARKET_ENABLED=false
docker compose -p ai-gateway -f docker-compose.webchat.yml up -d --no-deps --force-recreate chat-edge-api web-chat

# 3. Drop the data. Nothing outside the schema references it.
docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai -c 'DROP SCHEMA IF EXISTS agent_market CASCADE;'

# 4. Delete the files and the scratch folder.
docker run --rm -v ai-gateway_chat_edge_uploads:/v alpine rm -rf /v/agent-market
rm -rf data/agent-sandbox-runs
#    (S3 backend instead: delete every object under the prefix agent-market/)

# 5. Revoke the runner's key (portal → project "Agent Market runner" → revoke),
#    then delete AGENT_MARKET_* (including AGENT_MARKET_NTFY_URL, AGENT_MARKET_VISION_MODEL,
#    AGENT_MARKET_CONVERTER_URL, the retention windows and AM3's ADMIN_EMAILS / ORG_DOMAINS /
#    USER_BUDGET_CREDITS / THB_PER_USD), AGENT_RUNNER_*, AM_* from .env.

# 6. Revert the code — every Agent Market commit, newest first — rebuild and redeploy as usual.
git revert --no-edit $(git log --format=%h --grep='^AM[0-9]' main)
REVISION=$(git rev-parse --short HEAD) bash scripts/build-images.sh chat-edge-api web-chat
#    the shared sandbox scripts changed back too: rebuild ai-gateway-sandbox (tag the
#    current :latest :rollback-<label> first), then swap and measure (CARD-RULES §4)

# 7. Prove it.
SMOKE_TARGET=live bash scripts/smoke.sh          # green, including the agent_market-absent check's successor: GET /api/v1/agent-market/* → 404
```

## Expected end state

- `GET /api/v1/agent-market/availability` → **404** (no route), web-chat shows no Agent Market item.
- `SELECT to_regclass('agent_market.agent_runs')` → **NULL**; `\dn` lists no `agent_market`.
- No container of project `sran-agent-market`, no image `ai-gateway-agent-runner` or `ai-gateway-doc-converter`.
- A data export ZIP has no `agent-market/` folder and its README no "Also included" line;
  an account deletion runs the core steps only.
- No `agent-market/` folder in the uploads volume.
- `git grep -n -i "agent.market\|agentmarket\|agent-runner"` → only CHANGELOG/history mentions.
- Every test lane green: backend, Go (chat-edge), web (tsc, vitest, lint:i18n, check:classes, next build), desktop.
