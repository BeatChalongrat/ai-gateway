# The smoke suite (FIX134 F)

`scripts/smoke.sh` hits every entry point of the system and exits 0 only when every check is
green. It is part of the deploy gate and runs daily (`scripts/smoke-daily.ps1`, alerted by
ops-watch). One line per check on stdout — `PASS|FAIL|SKIP <name> <detail>` — and a JSON
result in `scripts/probes/FIX134/results/smoke/smoke_<target>_<utc>.json` (gitignored).

```
SMOKE_TARGET=live bash scripts/smoke.sh                 # the live stack, read-mostly (default)
SMOKE_TARGET=test bash scripts/smoke.sh                 # the test stack (compose project sran-test)
SMOKE_ONLY=tls,jobs bash scripts/smoke.sh               # a subset: tls apps api jobs alert desktop
SMOKE_DESKTOP=0 bash scripts/smoke.sh                   # without the installed desktop app
```

Gate wording (CARD-RULES.md §4): **A card is not deployable while `SMOKE_TARGET=test bash
scripts/smoke.sh` is red on the test stack, and it is not closed until `SMOKE_TARGET=live
SMOKE_DESKTOP=1 bash scripts/smoke.sh` is green on live after the deploy; a FAIL that is a
known, named follow-up must be listed in the report with the check's name.**

## What runs where

Git Bash on Windows (the daily task and the gate), node 24 (`NODE_USE_SYSTEM_CA=1` makes
Node trust the Windows store; older Node needs the CA file, `SMOKE_CA` or
`infra/caddy/certs/rootCA.pem`), docker (container logs and records, the test alert),
PowerShell (the OS-store TLS check), and `apps/web-chat/node_modules/playwright-core` for the
desktop check (`SMOKE_PLAYWRIGHT_DIR` points elsewhere; SKIP when absent). No new dependency.

Credentials come from `.env` (`SMOKE_ENV_FILE` for a worktree, which has no `.env`) and are
never printed:

| variable | used by | without it |
|---|---|---|
| `SMOKE_EMAIL`, `SMOKE_PASSWORD` | portal sign-in, web-chat turn, 429 loop, MCP (live) | those checks SKIP |
| `SMOKE_TOKEN_MODE=login\|mint` | `mint` signs the smoke user's JWT locally (developing the checks while password login is off); default `login` | — |
| `SMOKE_API_KEY` or `GATEWAY_TEST_API_KEY` | API completion, Idempotency-Key (live) | SKIP |
| `SMOKE_TS_API_KEY` or `TS_GATEWAY_TEST_API_KEY` | the same on the test stack | SKIP |
| `SMOKE_ADMIN_EMAIL`, `SMOKE_ADMIN_PASSWORD` | admin-ui sign-in + request logs (never the owner's account) | SKIP; the 401 check still runs |
| `SMOKE_MODEL` | the chat model (default `qwen3.8-27b-fp8`, free) | — |
| `SMOKE_DESKTOP_EXE`, `SMOKE_DESKTOP_CDP_PORT` | the installed app (default `%LOCALAPPDATA%\Programs\sran-desktop\SRAN Desktop.exe`, 9333) | — |

Every chat the suite creates is `[SMOKE] …`, memory off, on the free model, and is deleted by
id before the suite exits (also on a crash). On live nothing else writes: one login, one
test alert to the `[TEST]` topic, one 429 loop of empty bodies.

## The checks

| check | what it proves | needs |
|---|---|---|
| `setup.containers` | the target's containers belong to its compose project (refuses otherwise) | docker |
| `pages.web_version` | https://localhost:3002/api/version answers and its build id is the web-chat image's revision | front door |
| `pages.portal_login`, `pages.admin_login` | the sign-in pages load over https | front door |
| `pages.chat_edge_health`, `pages.gateway_health`, `pages.backend_ready` | `/health`, `/__gw/health`, `/ready` (queue snapshot in the detail) | — |
| `tls.chain` | the five https ports are trusted by the OS store — PowerShell `Invoke-WebRequest` with no CA flag | Windows (Python's default context elsewhere) |
| `tls.plain_http` | plain http on 3000/3001/3002/4000/8080 is 400 (today) or a redirect to https (FIX134 A4) — the detail says which | front door |
| `tls.debug_doors` | the loopback doors 23000/23001/23002/24000/28080 answer | front door |
| `tls.docs_off` | `/docs` and `/openapi.json` are 404 on the gateway | — |
| `portal.login` | `POST /portal/auth/login` with the smoke account → 200 + session cookies (403 `password_login_disabled` until the FIX134 allowlist is deployed) | SMOKE_EMAIL/PASSWORD |
| `portal.me`, `portal.billing_api` | `/portal/me`, `/portal/billing/packs` with the session | session |
| `portal.proxy_health`, `portal.proxy_me_unauth`, `portal.proxy_me_auth`, `portal.proxy_static`, `portal.proxy_billing`, `portal.billing_page` | the portal's proxied paths through https://localhost:3001 (`/api/*` → backend, `/static/*`) and the billing page — the rows the audit found broken (500/503 until FIX134 A2) | front door, session |
| `webchat.session` | chat-edge's session heartbeat: 204 with the portal token, 401 without | session |
| `webchat.turn` | a `[SMOKE]` chat, memory off, one turn on the free model → `message_complete` with content, deleted by id (one retry on the same model) | session |
| `admin.login` | admin sign-in + `/admin/requests?limit=1` with the admin session | SMOKE_ADMIN_* |
| `admin.requests_unauth` | `/admin/requests` is 401 without a session | — |
| `mcp.token` | `POST :3200/mcp/servers/filesystem/mcp` accepts the smoke token (not 401; one retry on 502 while the child starts) and refuses a bogus one | mcp-gateway (profile desktop), session |
| `api.completion` | one non-stream `/v1/chat/completions` on the free model with the API key | key |
| `api.idempotency` | the same request twice with one `Idempotency-Key` → `Idempotent-Replayed: true`, same id | key |
| `api.rate_limit_429` | chat-edge's send limit: empty bodies on a chat id that does not exist → 4xx until 429 `RATE_LIMIT_EXCEEDED` (nothing reaches a model; the smoke user is limited for up to a minute afterwards, so this runs last among the sends) | session |
| `jobs.backup` | `/backups/last_success.json`: < 26 h, drill pass, off-site pass or not configured | backup container |
| `jobs.retention` | chat-edge's `transcript retention: pass complete` in the last 26 h, errors 0 | — |
| `jobs.sweep` | chat-edge's `interrupted_turn_sweep` in the last 10 min | — |
| `jobs.ops_watch` | ops-watch's tick in the last 2 min | live |
| `jobs.maintenance` | no maintenance window left on (`python -m app.ops.maintenance status`) | — |
| `alert.test_routing` | `python -m app.ops.fire_test_alert` reports `channels=["ntfy-test"]` and a delivered outcome — only the `[TEST]` topic, never the owner's | ALERT_TEST_NTFY_URL |
| `desktop.startup` | the installed app launched with `--remote-debugging-port`, CDP open, its startup over https clean in `shell.log` (no TLS error) | Windows, playwright-core |
| `desktop.session` | the window is the signed-in page — or the shell's signed-out/signing-in view (SKIP: sign-in is Google-only in the system browser; the suite never starts it) | — |
| `desktop.turn` | with a session: a `[SMOKE]` chat through the app's own session, one turn typed into the composer, the answer read back and the assistant row `completed`, deleted by id | session in the app |
| `desktop.closed` | the app closed and port 9333 closed; relaunched without the port when it was running before | — |
| `cleanup.chats` | every leftover chat deleted by id | — |

Test stack: no front door, portal, admin-ui, web-chat, ops-watch or mcp-gateway — those
checks SKIP with the reason; the seeded owner (`ts-owner@fix128.test`, no password) is minted
a token the FIX130 ts_lib way; the desktop is off (the installed app points at live).

## The daily run and its alert

`scripts/smoke-daily.ps1` runs the live suite with the desktop on and copies the result to
`<BACKUP_DIR>/smoke_last.json` (`BACKUP_DIR` from `.env` the way compose reads it; default
`../ai-gateway-backups/daily`), the folder ops-watch mounts at `/backups`. Register it once:

```
schtasks /Create /TN "SRAN smoke daily" /SC DAILY /ST 06:30 /F /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\Beat Beat\Desktop\ai-gateway\scripts\smoke-daily.ps1\""
```

ops-watch (`backend/app/ops/watch.py`, `classify_smoke`) pages incident `smoke` (degraded)
when that file says `ok: false` (naming the failed checks) or is older than / missing for
`OPS_WATCH_SMOKE_MAX_AGE_H` (26 h); `OPS_WATCH_SMOKE_FILE` overrides the path. The rule
ships with the backend image, so it is live after the next backend deploy.
