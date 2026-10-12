# The smoke suite (FIX134 F)

`scripts/smoke.sh` hits every entry point of the system and exits 0 only when every check is
green. It is part of the deploy gate and runs daily (`scripts/smoke-daily.ps1`, alerted by
ops-watch). One line per check on stdout — `PASS|FAIL|SKIP <name> <detail>` — and a JSON
result in `scripts/probes/FIX134/results/smoke/smoke_<target>_<utc>.json` (gitignored).

```
SMOKE_TARGET=live bash scripts/smoke.sh                 # the live stack, read-mostly (default)
SMOKE_TARGET=test bash scripts/smoke.sh                 # the test stack (compose project sran-test)
SMOKE_ONLY=tls,jobs bash scripts/smoke.sh               # a subset: tls apps api jobs due alert desktop
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
| `SMOKE_MODEL` | the model every turn asks, and the only one an answer is accepted from (default `qwen3.8-27b-fp8`, free) | — |
| `SMOKE_TURN_EXPECT_MS` | how long a turn is expected to take (default 15000); a slower one is printed `SLOW` with its number | — |
| `SMOKE_DESKTOP_EXE`, `SMOKE_DESKTOP_CDP_PORT` | the installed app (default `%LOCALAPPDATA%\Programs\sran-desktop\SRAN Desktop.exe`, 9333) | — |

Every chat the suite creates is `[SMOKE] …`, memory off, on the free model, and is deleted by
id before the suite exits (also on a crash). On live nothing else writes: one login, one
test alert to the `[TEST]` topic, one 429 loop of empty bodies.

## Who answered

A check that makes a turn says which model it asked and which one answered (`lib.whoAnswered`), and
the result keeps it (`checks[].turns`: `asked`, `served`, `ms`, `expect_ms`):

- **an answer from another model is a FAIL that names both** — `asked qwen3.8-27b-fp8, answered
  deepseek/deepseek-r1, waited 35.4 s`. A rescue is right for a person in a hurry and wrong for a
  check: on 9 Oct 2026 the in-house model stopped answering, every turn waited thirty seconds for
  another model, and both live runs were green. The suite never asks a second model itself;
- **an answer that does not say who gave it is a FAIL**, and so is one out of the gateway's
  response cache (it keeps a non-stream answer for an hour: each run's API prompts carry a few
  characters no run before it sent);
- **a turn the model asked answered, slower than expected, is printed `SLOW`** with its number —
  a pass in the count, named in the summary line and in the result's `slow` list.

`node --test scripts/smoke/who.test.js` holds this with that day's own lines. **The proof that the
suite can fail** runs on the test stack, and the deploy gate runs both halves:

```
SMOKE_TARGET=test node scripts/smoke/dead_primary.js red     # the model taken away: the suite must be RED, each turn naming the model it asked
SMOKE_TARGET=test node scripts/smoke/dead_primary.js green   # the model back: GREEN, every turn answered by the model asked
```

`primary.js down|up|status` is the part that takes the model away (a container that accepts the
connection and never answers — the outage's own shape) and puts it back exactly; test stack only.

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
| `portal.me`, `portal.billing_api` | `/portal/me` with the session — answering 200 and naming the account that signed in; `/portal/billing/packs` → 200 | session |
| `portal.proxy_health`, `portal.proxy_me_unauth`, `portal.proxy_me_auth`, `portal.proxy_static`, `portal.proxy_billing`, `portal.billing_page` | the portal's proxied paths through https://localhost:3001 (`/api/*` → backend, `/static/*`) and the billing page — the rows the audit found broken (500/503 until FIX134 A2) | front door, session |
| `webchat.session` | chat-edge's session heartbeat: 204 with the portal token, 401 without | session |
| `webchat.turn` | a `[SMOKE]` chat, memory off, one turn on the free model → `message_complete` with content, **answered by that model**, deleted by id (one retry on the same model) | session |
| `admin.login` | admin sign-in + `/admin/requests?limit=1` with the admin session | SMOKE_ADMIN_* |
| `admin.requests_unauth` | `/admin/requests` is 401 without a session | — |
| `mcp.token` | `POST :3200/mcp/servers/filesystem/mcp` accepts the smoke token (200; one retry on 502 while the child starts) and refuses a bogus one (401) | mcp-gateway (profile desktop), session |
| `api.completion` | one non-stream `/v1/chat/completions` on the free model with the API key → 200 with an id and content, **answered now by that model** (not another one, not the cache); a call that ran out of its output budget is asked once more of the same model | key (the test stack: one made for the run and revoked) |
| `api.idempotency` | the same request twice with one `Idempotency-Key` → `Idempotent-Replayed: true`, same id; the first call is a turn like any other | key |
| `api.rate_limit_429` | chat-edge's send limit: empty bodies on a chat id that does not exist → 4xx until 429 `RATE_LIMIT_EXCEEDED` (nothing reaches a model; the smoke user is limited for up to a minute afterwards, so this runs last among the sends) | session |
| `jobs.backup` | `/backups/last_success.json`: < 26 h, drill pass, off-site pass or not configured | backup container |
| `jobs.retention` | chat-edge's `transcript retention: pass complete` in the last 26 h, errors 0 | — |
| `jobs.sweep` | chat-edge's `interrupted_turn_sweep` in the last 10 min | — |
| `jobs.ops_watch` | ops-watch's tick in the last 2 min | live |
| `jobs.maintenance` | no maintenance window left on (`python -m app.ops.maintenance status`) | — |
| `ops.old_audit_key` (area `due`) | a dated reminder: passes until 2027-01-06, then FAILS while `DLP_AUDIT_KEY_OLD` is still in `.env`, naming the one command (`python scripts/ops/rotate_dlp_audit_key.py drop-old`); passes once the line is gone. New dated items are entries in `scripts/smoke/due.js` | live |
| `alert.test_routing` | `python -m app.ops.fire_test_alert` reports `channels=["ntfy-test"]` and a delivered outcome — only the `[TEST]` topic, never the owner's | ALERT_TEST_NTFY_URL |
| `desktop.startup` | the installed app launched with `--remote-debugging-port`, CDP open, its startup over https clean in `shell.log` (no TLS error) | Windows, playwright-core |
| `desktop.session` | the window is the signed-in page — or the shell's signed-out/signing-in view (SKIP: sign-in is Google-only in the system browser; the suite never starts it) | — |
| `desktop.turn` | with a session: a `[SMOKE]` chat through the app's own session, one turn typed into the composer, the answer read back and the assistant row `completed` **and naming the model asked as the one that answered**, deleted by id | session in the app |
| `desktop.closed` | the app closed and port 9333 closed; relaunched without the port when it was running before | — |
| `agent_market.flag`, `agent_market.web_flag`, `agent_market.runner` | AM1: with `AGENT_MARKET_ENABLED` off the feature is absent (chat-edge `/api/v1/agent-market/*` → 404, web-chat `/api/agent-market` → `enabled:false`, no runner container); with it on, availability 200 and the runner's `/healthz` (docs/agent-market/) | — |
| `agent_market.not_allowlisted` | AM1b/AM3: the organisation's gate agrees with itself for the smoke account — not on the list (access `list`, not a member, not an admin) → availability `available:false` and catalog 403; let in (access `everyone`, a member or an admin) → `available:true` and catalog 200. The list lives in the database since AM3 (the admins keep it) | a session |
| `agent_market.model` | AM15: the runner's `/model` page — one word asked of the agents' model through the runner's own client, key and gateway, answered by that model. A FAIL names the model asked and who answered in its place, or that nobody did; a runner with no such page is a FAIL (`/healthz`'s `model: true` says only that a model is configured) | the runner |
| `agent_market.turn` | AM15: a sentence typed to the smoke's own copy of the bank reconciliation (installed once, with one finished run) → the reply's own record says the agent's model read it. A reply that waits for its model, words that came back unread, or another model's reading is a FAIL naming the model asked. SKIP where the account is not let into Agent Market — on live today, where `agent_market.model` is what watches the agents' model | a session the organisation lets in |
| `cleanup.chats` | every leftover chat deleted by id | — |

Test stack: no front door, portal, admin-ui, web-chat, ops-watch or mcp-gateway — those
checks SKIP with the reason; the seeded owner (`ts-owner@fix128.test`, no password) is minted
a token the FIX130 ts_lib way; the desktop is off (the installed app points at live). The two API
checks run there with a key the suite makes for the run and revokes (they were skipped on every
run of the test stack until 12 Oct 2026, for want of a key in the env file).

## The daily run and its alert

`scripts/smoke-daily.ps1` runs the live suite with the desktop on and copies the result to
`<BACKUP_DIR>/smoke_last.json` (`BACKUP_DIR` from `.env` the way compose reads it; default
`../ai-gateway-backups/daily`), the folder ops-watch mounts at `/backups`. Register it once:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke-register-task.ps1
```

ops-watch (`backend/app/ops/watch.py`, `classify_smoke`) pages incident `smoke` (degraded)
when that file says `ok: false` (naming the failed checks) or is older than / missing for
`OPS_WATCH_SMOKE_MAX_AGE_H` (26 h); `OPS_WATCH_SMOKE_FILE` overrides the path. The rule
ships with the backend image, so it is live after the next backend deploy.

**Desktop part of the daily run.** `SMOKE_DAILY_DESKTOP` in `.env`: `auto` (default) runs the desktop checks only when the app's own log (`%APPDATA%\SranDesktop-Electron\logs\shell.log`) shows it holds a session; with no session every launch of the app opens the Google sign-in in the owner's browser by itself, which an unattended 06:30 run must not do. `1` always, `0` never. The post-deploy gate runs the desktop part by hand (`SMOKE_DESKTOP=1`).
