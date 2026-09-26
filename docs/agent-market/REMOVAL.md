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

The only lines in existing files (the "seams") — all removed by the revert:

- `services/chat-edge-api/cmd/server/main.go` — one import, one flag-gated block
- `services/chat-edge-api/internal/router/router.go` — one field, one guarded mount
- `docker-compose.webchat.yml` — the flag env lines on chat-edge and web-chat
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
#    AGENT_MARKET_CONVERTER_URL and the retention windows), AGENT_RUNNER_*, AM_* from .env.

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
