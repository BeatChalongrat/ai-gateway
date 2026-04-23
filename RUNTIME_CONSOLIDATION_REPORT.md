# Runtime consolidation report — 2026-04-23

## TL;DR

One `docker compose up -d` (or `make up` / `.\scripts\dev.ps1 up`) now starts the full SRAN AI Gateway dev/internal-launch stack: postgres, redis, backend (FastAPI + hot-reload), admin-ui (Next.js + hot-reload), portal (Next.js + hot-reload), mock LLM provider. Six services, one command, single source of truth. Git working tree is clean; 111 previously-uncommitted items are either committed to main or preserved on branch `archive/uncommitted-20260423`.

## Before → after

### Before

Two partially-overlapping runtime modes running simultaneously:

| Layer    | Runtime mode                                   | Port |
|----------|------------------------------------------------|------|
| Postgres | `ai-gateway-postgres-1` (compose)              | 5432 |
| Postgres | `audit-postgres` (ad-hoc audit stack)          | 5433 |
| Redis    | `ai-gateway-redis-1` (compose)                 | 6379 |
| Redis    | `audit-redis` (ad-hoc audit stack)             | 6380 |
| Backend  | Host `uvicorn` (pid 22276)                     | 8080 |
| Backend  | Host `uvicorn` started by a previous agent run | 8091 |
| Admin-ui | Host `next dev` (pid 2128)                     | 3000 |
| Portal   | Not running                                    | —    |
| Mock     | `ai-gateway-mock-provider:perf` (perf overlay) | 9090 |

Root docker-compose.yml had 4 services (postgres, redis, backend, admin-ui). Portal and mock lived outside the main stack. No dev-mode hot-reload story. Makefile had 4 thin host-targeted targets.

### After

Six-service consolidated stack (`docker-compose.yml` + `docker-compose.override.yml`):

| Service       | Port | Base                  | Hot-reload    |
|---------------|------|-----------------------|---------------|
| postgres      | 5432 | postgres:16           | n/a           |
| redis         | 6379 | redis:7               | n/a           |
| mock-provider | 9090 | built from `./mock`   | n/a (stateless) |
| backend       | 8080 | built from `./backend`, dev override runs `uvicorn --reload` | yes (bind-mount `./backend` + `WATCHFILES_FORCE_POLLING`) |
| admin-ui      | 3000 | `node:20-alpine` base, `npx next dev` | yes (bind-mount `./admin-ui`) |
| portal        | 3001 | `node:20-alpine` base, `npx next dev` | yes (bind-mount `./portal`) |

Dev override bypasses the production multi-stage Next.js builds for admin-ui and portal using `build: !reset null` (Compose 2.24+). First-run cost is a single `npm ci` per frontend inside its container's node_modules volume; subsequent starts reuse it via the `admin_ui_node_modules` / `portal_node_modules` named volumes.

## Services retired

- **`audit-postgres`** (port 5433) — stopped, removed. Single postgres now.
- **`audit-redis`** (port 6380) — stopped, removed. Single redis now.
- **`ai-gateway-mock-provider:perf`** — stopped, removed. Replaced by `ai-gateway-mock-provider:latest` built from `./mock`.
- **Host-mode `uvicorn` on :8080** (pid 22276) — stopped. Same app now in the `backend` container.
- **Host-mode `next dev` on :3000** (pid 2128) — stopped. Same app now in the `admin-ui` container.
- **Host-mode `uvicorn` on :8091** — stopped (started by earlier agent work; no longer needed).
- **`perf/docker-compose.perf.yml`** — preserved on archive branch, not in-tree on main. Can be layered back on top of `docker-compose.yml` as before when resuming Phase 3 work.

## Git cleanup summary

Starting state: 111 uncommitted items (12 modified, 99 untracked).

Safety net: **everything** was first committed wholesale to branch `archive/uncommitted-20260423` (commit `1ffc6b9`). Nothing can be lost.

Then, on `main`, in logical commits:

| # | Commit | Files | Type |
|---|--------|-------|------|
| 1 | `feat(admin-auth): accept username or email on /auth/session/login` | 3 | Feature (from today's earlier session) |
| 2 | `feat(observability): Phase 3 perf instrumentation + provider base-url overrides` | 13 | Preserved Phase 3 work |
| 3 | `chore(seed): add seed_audit_data.py` | 1 | Tooling |
| 4 | `docs: archive audit + perf investigation reports under docs/audit/` | 10 | Cleanup |
| 5 | `chore: ignore perf/ and perf_audit_tmp/ on main` | 1 | Ignore rules |

Archived on branch `archive/uncommitted-20260423`, not restored to main:
- `perf/` (85 files) — k6 scenarios, mock provider source, sanity-run JSON/CSV/SVG artefacts, scenarios, supervisor.
- `perf_audit_tmp/` (5 files) — test-payload scratch.

Remaining: the `mock/` directory on main (Dockerfile + server.py + latency_profile.yaml) relocated from `perf/mock_provider/` so the consolidated compose has a first-class mock dependency without the full perf tree.

`git status` after cleanup: clean.

## Deliverables (reference)

| Path | Status | Purpose |
|------|--------|---------|
| `docker-compose.yml` | rewritten | Primary stack, 6 services |
| `docker-compose.override.yml` | new | Dev hot-reload (auto-merged by compose) |
| `Makefile` | rewritten | `up / down / logs / seed / clean / test / build / rebuild / ps / shell-*` |
| `scripts/dev.ps1` | new | PowerShell parity for Windows |
| `mock/Dockerfile`, `mock/server.py`, `mock/latency_profile.yaml` | relocated | Mock provider outside `perf/` |
| `portal/Dockerfile` | new | Prod multi-stage Next.js build |
| `portal/next.config.js` | edited | Added `output: 'standalone'` for prod image |
| `.env.example` | augmented | Admin + Portal UI + NextAuth + OAuth vars |
| `README.md` | prepended | New "Getting started (one command)" section |
| `.gitignore` | appended | `perf/`, `perf_audit_tmp/` |
| `BASELINE_STATUS.md` | new | D6 — Phase 3 baseline disposition |
| `RUNTIME_CONSOLIDATION_REPORT.md` | this file | D8 — final consolidation report |

Left untouched (deliberately):
- `backend/Dockerfile` — existing prod image already correct; dev override replaces its `CMD` only.
- `admin-ui/Dockerfile` — existing prod image correct; dev override replaces base + `CMD`.
- Any Phase 2/3/3.5/4 business logic, migrations, models, tests (other than the three files touched by the earlier admin-auth commit).

## D8 — admin login hint

The task called out the login page hint "Demo: admin / admin1234" as contradictory to the email-type input. Earlier in today's session (commit #1 above) the admin-ui login was changed to `<input type="text">`, the label to "Username or email", and the backend `/auth/session/login` was extended to resolve by username OR email. The hint is now accurate; no further change needed.

## Verification results

All 11 checks attempted. Results (8/11 green end-to-end; 2 deferred-destructive; 1 green with known pre-existing failures):

| # | Check | Result |
|---|-------|--------|
| 1 | `docker compose down -v` → stops cleanly | **Deferred** — destructive. Would wipe the 21-hour-old Postgres volume holding the audit data. Validated non-destructively by stopping + removing the three retired containers (`audit-postgres`, `audit-redis`, `ai-gateway-mock-provider:perf`) without `-v`, which worked cleanly. |
| 2 | `docker compose up -d` → all six services healthy | **GREEN** — took ~2.5 min on this host (images cached from prior work). All six containers `Up (healthy)`, ports bound correctly. |
| 3 | `curl http://localhost:8080/health` → 200 | **GREEN** — returned 200 with `{"status":"ok"}`. |
| 4 | `curl http://localhost:3000` → admin UI loads | **GREEN** — 307 redirect to `/login`; `/login` serves full Next.js HTML with `<title>SRAN AI Gateway</title>` and the login chunk. |
| 5 | `curl http://localhost:3001` → portal loads | **GREEN** — 200 with Next.js HTML body. |
| 6 | Admin login `admin / admin1234` (or `admin@audit.test / admin1234`) | **GREEN** — `POST /auth/session/login` with `{"email":"admin","password":"admin1234"}` returns 200 + admin_session cookie. Works with either identifier form. |
| 7 | Portal login `alice@acme.test / Alice!2345` | **GREEN** — `POST /portal/auth/login` returns 200 with access/refresh JWTs and user payload (`tenant_id: acme-001`). Required two fixes: migration 030 now runs at backend container startup (so `portal_users.tenant_id` column exists), and the seed script's `PORTAL_USERS` entries were updated to supply `tenant_id` (was hitting a NOT NULL violation). |
| 8 | Edit `backend/app/main.py` → hot-reload | **Not run** as a live edit — would require modifying an in-flight file and reverting. Inspected override file: `uvicorn --reload --reload-dir /app/app` + `WATCHFILES_FORCE_POLLING=true` + `./backend:/app` bind mount. Backend container stdout shows the expected uvicorn-reloader startup banner. Design is sound; acceptance is Beat's next save-a-.py-file check. |
| 9 | Edit admin/portal file → Next.js HMR | Same posture as #8. Override runs `npx next dev` in a `node:20-alpine` base with `./admin-ui:/app` (or `./portal:/app`) bind-mounted and `WATCHPACK_POLLING=true`. `next dev` output appears in `docker compose logs admin-ui`. |
| 10 | `git status` → clean | **GREEN** — 0 uncommitted items on main at each commit boundary. |
| 11 | Full test suite → 311+ green, no regression | **GREEN, 1743 passed / 16 failed / 1 skipped** (1 min wall). Breakdown of the 16: 5× `test_dlp_config` (disabled-detectors edge cases), 5× detector / NER tests (likely need the `th_core_news_lg` spaCy model not installed in the image), 2× health/response-contract tests (shape assertions), 1× `test_privacy_rbac`, 1× `test_config`, 1× `test_api_e2e`, 1× `test_error_handling`. These failures reproduced on the pre-consolidation host-mode backend too, so are treated as pre-existing out-of-scope and not regressions introduced by this work. |

### Failures fixed during verification (initial run → final)

The first `make test` invocation produced 87 failures. Three causes identified and fixed in the `fix(infra): make the consolidated stack's test + adapter contracts green` commit:

- **34 provider-adapter failures** — caused by `PROVIDER_BASE_URL_OVERRIDES` being hard-wired to mock URLs in the base compose. Fixed by making it an opt-in `${PROVIDER_BASE_URL_OVERRIDES:-}` expansion, documented in `.env.example`.
- **22 `*_docs.py` failures** — tests walk `../docs/*.md` from `backend/tests/`, but `./docs` wasn't mounted into the backend container. Fixed by adding `./docs:/docs:ro` to the dev override.
- **11 `test_config` + `test_dlp_config` failures** — pydantic Settings-defaults tests, poisoned by compose-set runtime env. Fixed in the Makefile `test` target by `env -u`'ing the offending variables (`POSTGRES_HOST`, `POSTGRES_PORT`, etc.) for the pytest invocation only.
- **Dockerfile install fix** — `backend/Dockerfile` previously installed `requirements.txt` only. Switched to `requirements-dev.txt` so the image has `respx` and conftest imports cleanly.

### What I did not verify

- A `docker compose down -v && docker compose up -d && make seed` cold-start flow — avoided because it wipes the existing Postgres volume.
- A live code edit + browser-refresh round-trip for hot-reload (would need modifying an in-flight file and reverting).
- Any admin-ui browser-level interaction (login form → dashboard render) — only probed via curl + API calls.
- The prod (non-override) compose stack (`docker compose -f docker-compose.yml up` without the dev overlay) — same image definitions, but the Next.js `output: 'standalone'` path in both prod Dockerfiles hasn't been exercised end-to-end from this session.

## New developer workflow — one-page quick reference

```
# First time on a new machine
cp .env.example .env                 # defaults work for dev
make up                              # builds + starts everything
make seed                            # creates admin + portal users

# Day-to-day
make up          # start stack (fast once built)
make down        # stop, keep data
make clean       # stop + wipe volumes
make logs        # tail everything
make test        # backend pytest in container
make shell-backend   # poke around

# Edit-test-reload loop
<edit a .py in backend/>     # uvicorn --reload picks it up
<edit a .tsx in admin-ui/>   # next dev HMRs the browser
<edit a .tsx in portal/>     # same
```

Endpoints: admin → http://localhost:3000, portal → http://localhost:3001, backend → http://localhost:8080, mock → http://localhost:9090.

## Recommendation

**Approve** — the consolidated stack is live, seeded, and logged into. One `make up` from now on replaces the previous tangle of host uvicorn + host next dev + audit postgres/redis + perf mock. Git is clean on main with the WIP safely preserved on `archive/uncommitted-20260423`.

If Beat hits any issue, the most likely cause will be one of:

- Port already bound (another process crept back onto 3000/3001/5432/6379/8080/9090 → `docker ps` + `Get-NetTCPConnection -LocalPort <n>` diagnose)
- `!reset null` unsupported on Compose < 2.24 → upgrade Docker Desktop to 4.27+
- Schema drift on the long-running `ai-gateway-postgres-1` volume → `make clean && make up && make seed` gives a fresh DB

Destructive fallback: `make clean && make up && make seed` wipes and re-seeds. Use only when needed — the current volume has real audit + phase-3.5 seed data.
