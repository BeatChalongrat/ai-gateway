# FIX 3 — Schema-drift guard: fail loud on stale schema, deterministic migrate (dev + prod)

**The problem (hit at runtime in the smoke test):** the DB was at Alembic `062` while the code was at head `064` → **every `/v1/chat/completions` returned 500** with `column tenants.search_pii_egress does not exist`, silently, for a long time. **Root cause:** the backend runs `alembic upgrade head` only at **container start** (`backend/Dockerfile:41` CMD; `docker-compose.override.yml:11`), but dev **hot-reloads the source** — so code advances to a new head while a long-lived container's schema stays behind. There is **no startup schema-version check**, so the mismatch surfaces as a generic 500 on every request instead of a clear, early failure. FIX 2 just added `065`, and more migrations will come — this WILL recur.

**The fix:** make schema drift **impossible to miss** — fail loud and early with an actionable message, and make migration deterministic in both modes (dev hot-reload vs prod immutable image). Backend-only. Do NOT `git push`. R0 first.

## R0 — Reconnaissance (answer all)

1. Confirm the migration triggers: `backend/Dockerfile:41` (prod CMD `alembic upgrade head && gunicorn …`), `docker-compose.override.yml:11` (dev `alembic upgrade head && uvicorn --reload`), and how dev mounts source (hot-reload) so the drift arises. Is there ANY startup schema check today (grep `main.py`/lifespan for alembic current / column checks — the earlier audit thought there was a fail-closed column check for some feature flags; confirm what actually exists).
2. How the app starts: `backend/app/main.py` — the FastAPI lifespan/startup hook (where a schema-version check would run before serving traffic). The seeding/`_import_env_provider_keys` startup work already lives here.
3. Alembic setup: `backend/migrations/env.py`, `alembic.ini`, how "head" is determined, and whether `alembic current` vs the code's head revision can be compared programmatically (Alembic's `ScriptDirectory.get_current_head()` + `MigrationContext.get_current_revision()`).
4. The generic 500: how does a missing-column `ProgrammingError` surface today (the middleware error handler) — so the new guard produces a CLEAR message, not the same opaque 500.

## Design — two coordinated mechanisms

### Part A — Startup schema-version guard (the "fail loud" piece — both modes)

In the app startup/lifespan (`main.py`), BEFORE serving traffic, compare the DB's current Alembic revision to the code's head:

- Read the DB's applied revision (`MigrationContext.get_current_revision()` against a connection) and the code's head (`ScriptDirectory.from_config(...).get_current_head()`).
- **If they match** → proceed normally.
- **If the DB is BEHIND** (stale schema — the exact smoke-test situation):
  - **Behavior is mode-configurable** via an env flag (e.g. `SCHEMA_DRIFT_MODE`):
    - **`autoupgrade` (recommended dev default):** log a loud WARNING, run `alembic upgrade head` in-process, then proceed. This fixes the hot-reload case automatically — a dev whose code moved ahead gets the migration applied on the next reload/restart without a manual step.
    - **`failclosed` (recommended prod default):** do NOT auto-migrate silently in a running app; **refuse to start** with a clear, actionable error naming the gap (e.g. `SCHEMA DRIFT: DB at 062, code needs 065 — run 'alembic upgrade head'`) so the deploy pipeline (which should migrate before boot) surfaces it, and no worker serves traffic against a schema it will 500 on. (Multi-worker safety: if autoupgrade runs, guard it with the existing `pg_advisory_lock` pattern — Phase-10 A1 — so 4 gunicorn workers don't race the migration.)
  - Pick sensible defaults per environment (dev override → `autoupgrade`; prod compose → `failclosed`), settable by env.
- **If the DB is AHEAD** of the code (rollback scenario) → loud warning, and refuse or proceed per a conservative choice (state it) — usually safe to proceed for additive migrations but worth flagging.

The point: a stale schema becomes an **immediate, named, actionable startup failure or an auto-fix** — never a silent per-request 500.

### Part B — Deterministic migration path (prod-before-boot)

- Confirm the prod CMD (`Dockerfile:41`) migrates before gunicorn — good, but with 4 workers it currently relies on the CMD running once before the workers fork (it does, since it's `&&` before gunicorn). Keep that, and ensure the Part-A guard is `failclosed` in prod so if someone bypasses the CMD (or the image is older than the DB expects vs newer code), it fails loud instead of 500ing.
- Provide a `make migrate` / documented one-liner (or confirm `make chat-edge-migrate*` has a backend analog) so an operator whose container outlived a migration can apply it deterministically without guessing.

### Part C — Better error if a missing-column 500 still slips through

As defense-in-depth: in the error handler (R0.4), detect a Postgres `UndefinedColumn`/`UndefinedTable` (SQLSTATE 42703/42P01) and return a clear `schema_out_of_date` error (500 is fine, but with a message pointing at "run migrations") instead of a generic `internal_error`. So even if the startup guard is disabled, the failure is diagnosable.

## Implementation requirements
- The startup guard runs once, early, before the app serves — using the existing DB pool/engine, not a second connection stack.
- Autoupgrade path is advisory-locked (multi-worker safe) and logged loudly.
- Failclosed path exits non-zero with a message a human can act on in 5 seconds.
- Env flags documented in `.env.example` with the recommended dev/prod values; the dev `docker-compose.override.yml` and prod compose set them appropriately (or rely on sane defaults).
- Zero behavior change when schema is in sync (the common case) — no added startup latency beyond one cheap revision read.

## Tests
- Guard logic is unit-testable: given (db_rev, code_head) → decision {ok / upgrade / fail / warn}, per mode. Test all combinations (in-sync, behind+autoupgrade, behind+failclosed, ahead).
- The advisory-lock wrapper around autoupgrade is exercised (or at least the lock is acquired — mirror Phase-10 A1's test).
- Error-handler mapping: a simulated `UndefinedColumn` → `schema_out_of_date` typed error, not generic 500.
- No regression: in-sync startup proceeds unchanged; existing startup/seed tests green.

## Final verification
1. `pytest` green (guard-decision tests + error-mapping + regressions). State what needed a live DB and how covered (the revision comparison can be unit-tested with a stubbed MigrationContext/ScriptDirectory).
2. **Drift-caught proof (the headline):** simulate the smoke-test situation — DB one revision behind code head — and show that instead of a silent per-request 500, the app either auto-upgrades (dev mode, logged) or refuses to start with a named, actionable error (prod mode). Show the message.
3. **Multi-worker safety:** confirm autoupgrade is advisory-locked so concurrent workers don't race.
4. Summarize changes file by file + the env flags and their dev/prod defaults. Note this closes the smoke-test's blocker #2 (schema drift → 500) permanently; the deploy story is now "migrate-before-boot in prod (fail loud if not), auto-catch in dev."
