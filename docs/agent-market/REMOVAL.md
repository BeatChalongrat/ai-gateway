# Agent Market — removal

Agent Market is a bolt-on. Removing it is five boring steps, and the core is
left exactly as it was before the feature arrived. This file is the drill the
AM1 card ran for real (its output is in the AM1 report).

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

## Steps

Run from the main checkout (`C:\Users\Beat Beat\Desktop\ai-gateway`).

```bash
# 1. Stop the execution plane (nothing else depends on it).
docker compose -p sran-agent-market -f docker-compose.agent-market.yml --profile agent-market down
docker image rm $(docker images -q ai-gateway-agent-runner) 2>/dev/null || true

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
#    then delete AGENT_MARKET_*, AGENT_RUNNER_*, AM_* from .env.

# 6. Revert the code (one squashed commit), rebuild and redeploy as usual.
git revert --no-edit <AM1 squash sha>
REVISION=$(git rev-parse --short HEAD) bash scripts/build-images.sh chat-edge-api web-chat
#    tag :rollback-<label>, swap, measure (CARD-RULES §4)

# 7. Prove it.
SMOKE_TARGET=live bash scripts/smoke.sh          # green, including the agent_market-absent check's successor: GET /api/v1/agent-market/* → 404
```

## Expected end state

- `GET /api/v1/agent-market/availability` → **404** (no route), web-chat shows no Agent Market item.
- `SELECT to_regclass('agent_market.agent_runs')` → **NULL**; `\dn` lists no `agent_market`.
- No container of project `sran-agent-market`, no image `ai-gateway-agent-runner`.
- No `agent-market/` folder in the uploads volume.
- `git grep -n -i "agent.market\|agentmarket\|agent-runner"` → only CHANGELOG/history mentions.
- Every test lane green: backend, Go (chat-edge), web (tsc, vitest, lint:i18n, check:classes, next build), desktop.
