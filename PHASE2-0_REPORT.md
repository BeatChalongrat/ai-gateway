# AI Gateway — Phase 2-0: P0-12 Duct-Tape

> **Status:** Complete. Stops for Beat verification before Phase 2A design.
> **Scope:** Emergency-only. Fresh-install onboarding must not crash on `POST /portal/projects`.
> **Not included:** The root-cause fix for P0-4 (portal tenancy refactor) remains on the separate track — `PORTAL_TENANCY_MIGRATION_PLAN.md` when it lands will replace this duct-tape.
> **Date:** 2026-04-22

---

## What was broken

`POST /portal/projects` hard-codes `tenant_id="demo-tenant-000"`. On any freshly migrated DB that tenant does not exist, so the portal-side `INSERT` into `projects` fails with an `IntegrityError`, which the error middleware surfaces as HTTP 500. Every single new portal user's first "create a project" click is a crash.

Backend log evidence (Phase 1.4, X6):

```
IntegrityError: Key (tenant_id)=(demo-tenant-000) is not present in table "tenants".
SQL: INSERT INTO projects (project_id, tenant_id, name, ...) VALUES (..., 'demo-tenant-000', ...)
```

---

## The fix

One new migration that idempotently seeds the tenant row. Nothing else in the backend changes; the hard-coded `tenant_id` in `portal_projects.py` is left alone on purpose so the separate tenancy refactor track can own that file cleanly.

### `backend/migrations/versions/027_seed_demo_tenant.py` — NEW

```python
def upgrade() -> None:
    op.execute("""
        INSERT INTO tenants (tenant_id, name, slug, status, region, ner_enabled)
        VALUES ('demo-tenant-000', 'Demo Tenant', 'demo', 'active', 'global', false)
        ON CONFLICT (tenant_id) DO NOTHING
    """)
```

Top-of-file docstring matches Beat's required TEMPORARY comment format: P0-12 reference, root-cause P0-4 reference, removal trigger (`PORTAL_TENANCY_MIGRATION_PLAN.md`), creation date.

`ON CONFLICT DO NOTHING` keeps it idempotent — safe to re-run, safe to coexist with `scripts.seed_demo_data` which also tries to insert the same tenant with a pre-check.

### `backend/tests/integration/test_fresh_db_onboarding.py` — NEW

Regression test pinning the fix. Each run:

1. Creates a throwaway Postgres database on the admin connection.
2. Runs `alembic upgrade head` against it as a subprocess.
3. Asserts `demo-tenant-000` exists (pinpoints migration 027 if it's dropped later).
4. Boots a fresh uvicorn on a free port pointed at the throwaway DB.
5. `POST /portal/auth/signup` → new portal user.
6. `POST /portal/projects` → **this is the exact call that used to 500.**
7. Asserts `status < 500` (hard failure if the P0-12 shape comes back), and `status in (200, 201)` (positive-path confirmation).
8. Asserts returned `tenant_id == "demo-tenant-000"` — acts as a second alarm when the P0-4 refactor finally lands and this test needs updating.
9. Tears down uvicorn and drops the throwaway DB.

Gated on `INTEGRATION_PG_URL` env var — CI without Postgres skips cleanly. The existing in-memory SQLite test harness is not touched.

---

## Verification evidence

### 1. Migration applied against audit DB

```
INFO [alembic] Running upgrade 026 -> 027, TEMPORARY WORKAROUND for P0-12 (onboarding crash on fresh DB).
```

### 2. `demo-tenant-000` seeded

```
docker exec audit-postgres psql -U postgres -d ai_gateway_audit -c \
  "SELECT tenant_id, name, slug, status, region FROM tenants WHERE tenant_id='demo-tenant-000';"

    tenant_id    |    name     | slug | status | region
-----------------+-------------+------+--------+--------
 demo-tenant-000 | Demo Tenant | demo | active | global
```

### 3. Replay the exact Phase 1.4 G2 failure — now succeeds

```
POST /portal/projects  (alice logged in, name="phase2-0-smoke")
HTTP 200
{"id":"32dba36a-a5d9-4e6e-9731-0091766f6155",
 "name":"phase2-0-smoke",
 "tenant_id":"demo-tenant-000",
 "created_at":"2026-04-22T16:28:18.629397+00:00", ...}
```

(Pre-fix: HTTP 500 `internal_error`.)

### 4. Project row persisted

```
 project_id                           | tenant_id       | name
--------------------------------------+-----------------+----------------
 32dba36a-a5d9-4e6e-9731-0091766f6155 | demo-tenant-000 | phase2-0-smoke
```

### 5. Regression test passes

```
$ INTEGRATION_PG_URL=postgresql://postgres:postgres@localhost:5433 \
  pytest backend/tests/integration/test_fresh_db_onboarding.py -v

tests/integration/test_fresh_db_onboarding.py::test_fresh_db_signup_then_create_project_does_not_500 PASSED
======================== 1 passed, 5 warnings in 8.38s ========================
```

### 6. Without env var: clean skip, CI-safe

```
$ pytest backend/tests/integration/ -v

======================= 1 skipped, 5 warnings in 0.06s ========================
```

### 7. Existing suite not broken

```
$ pytest backend/tests/test_seed_demo.py backend/tests/test_tenant_model.py backend/tests/test_portal_auth.py -q
33 passed, 5 warnings in 2.81s
```

---

## Files changed

| File | Status | LoC | Notes |
|---|---|---|---|
| `backend/migrations/versions/027_seed_demo_tenant.py` | NEW | 45 | The fix + docstring |
| `backend/tests/integration/__init__.py` | NEW | 0 | package marker |
| `backend/tests/integration/conftest.py` | NEW | 9 | empty init / local notes only |
| `backend/tests/integration/test_fresh_db_onboarding.py` | NEW | 148 | The regression test |

No backend code changed — this is intentional. The P0-4 structural fix will rewrite `portal_projects.py` later; duct-tape here only augments the data layer.

---

## Commit hygiene (pending Beat go-ahead)

Suggested single commit:

```
fix(portal): seed demo-tenant-000 to unblock fresh-DB onboarding (P0-12)

Portal project creation hard-codes tenant_id="demo-tenant-000" but
that tenant doesn't exist on fresh installs, so the first signup →
create-project click 500s. Add migration 027 to idempotently seed
the row, plus an integration regression test that boots a throwaway
DB + uvicorn and replays the failing curl.

This is a TEMPORARY WORKAROUND. Root cause is P0-4 (portal users share
a single hardcoded tenant) — removed by PORTAL_TENANCY_MIGRATION_PLAN.md
when that design doc lands.

Regression: tests/integration/test_fresh_db_onboarding.py
Phase ref: PHASE2-0_REPORT.md
```

---

## Audit-stack status

Still running:
- `audit-postgres` on 5433
- `audit-redis` on 6380
- Backend (PID 26188) on 8090, now with migration 027 applied

Baseline processes on 3000/3001/5432/6379/8080/9090 untouched.

---

## Proposed Next Step

Beat — approving this unlocks Phase 2A. Three things I'll need from you before I draft `AUTH_REDESIGN_DESIGN.md`:

1. **Session model**: server-side session table in Postgres, or stateless JWT-in-HttpOnly-cookie? My instinct is stateless JWT in an `HttpOnly; Secure; SameSite=Lax` cookie signed with `itsdangerous` — avoids a session-store round-trip, plays well with the existing portal JWT pattern — but I'll defer if you have a stronger preference.
2. **Role taxonomy**: the backend already has `USER < PROJECT_ADMIN < TENANT_ADMIN < ADMIN`. Do we keep this hierarchy, or collapse/rename for Phase 2A? I'm assuming we keep it as-is and just actually *enforce* it via dep injection.
3. **OAuth callback**: for P0-8 — keep NextAuth in the portal and add state/PKCE verification on our side, or move the OAuth flow server-side entirely (FastAPI does the exchange)? The former is minimal-change; the latter is more defensible.

Once those three decisions are in, I'll produce the design doc and stop again for your approval before touching code.
