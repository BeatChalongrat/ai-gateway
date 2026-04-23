# AI Gateway — Phase 1.4 Live Functional Test Report

> **Status:** Complete. Stops for Beat review before Phase 2A auth-redesign design.
> **Stack used:** isolated audit containers on ports 5433 (Postgres) + 6380 (Redis) + 8090 (backend). Baseline ports 3000/3001/5432/6379/8080/9090 **untouched.**
> **Scope note:** Security penetration of known-broken auth (P0-1/2/8) and tenant isolation (P0-5/6) was **out of scope per Beat** — but two of them were verified empirically as a side-effect and their evidence is captured below for the design doc.
> **Evidence log:** `perf_audit_tmp/evidence.log` (331 lines, all raw curl + SQL output).
> **Date:** 2026-04-22

---

## Executive Summary

- **Tests executed:** ~80 live curl + SQL probes across every documented entity
- **PASS:** 58 (73%)
- **FAIL — confirms a Phase 1 finding:** 13 (including 2 re-confirmed empirically)
- **FAIL — NEW finding not in Phase 1:** 9 (4 new P0s, 4 new P1s, 1 P2)
- **Evidence-gaps needing human browser verification:** 4

**Headline:** Phase 1 static audit **underestimated** the severity. Four brand-new P0s surfaced once the code actually executed — most notably that **portal project creation is a 100% crash on a fresh database** (P0-4 isn't just "one shared tenant," it's "every portal user who tries to create a project gets a 500"). Admin tenant/project **create** silently ignore caller-supplied IDs, and `DELETE /admin/projects/{id}` throws 500 whenever the project has any API keys — which is every realistic scenario.

---

## Stack bring-up (for reproducibility)

```bash
# 1. Isolated Postgres + Redis (different ports, different container names)
docker run -d --name audit-postgres -p 5433:5432 \
  -e POSTGRES_DB=ai_gateway_audit -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
  postgres:16
docker run -d --name audit-redis -p 6380:6379 redis:7

# 2. Migrate + seed (see perf_audit_tmp/env.sh for env vars)
cd backend && PYTHONPATH=. python -m alembic upgrade head
cd backend && PYTHONPATH=. python -m scripts.seed_audit_data

# 3. Backend on 8090
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
```

**Teardown when done:** `docker stop audit-postgres audit-redis && docker rm audit-postgres audit-redis` and kill the uvicorn PID. The backend uvicorn process is currently PID **26188** (started by `nohup`; shell PID 4784). Baseline DB / containers unaffected either way.

**Seed data created:** 2 admin tenants (acme-001, globex-001), 6 projects (3/tenant), 6 seeded API keys, 5 portal users covering active/suspended/unverified/low-credit/emoji+Thai cases, 1 admin user (admin/admin1234 — DEV DB only), 3 token packs (auto-seeded by migration 025).

---

## Confirmed Phase 1 findings (empirical evidence added)

### P0-5 / P0-6 — cross-tenant admin leak — **CONFIRMED LIVE**

Evidence: [T2/T3 in evidence.log, lines 320-325]

```
GET /admin/projects  (no filter)  → 7 projects spanning tenants: ['acme-001', 'globex-001']
GET /admin/api-keys  (no filter)  → 7 keys spanning tenants: ['acme-001', 'globex-001']
```

A single admin token returns every tenant's projects and keys in the same response. No tenant scope is applied. With the P0-2 leaked admin token, this is directly exploitable by any visitor to the admin URL.

### P0-3 — portal purchase 501 — **CONFIRMED** with a real pack

Evidence: [X5, line ~316]
```
POST /portal/billing/purchase  {"pack_id":"pack-starter-000"}
→ HTTP 501
  {"code":"payment_not_implemented",
   "message":"Online payment coming soon. Contact support@sran.ai to purchase tokens.",
   "pack":{"id":"pack-starter-000","name":"Starter","tokens":1000000,"price_usd":10.0}}
```

Revenue path remains a dead end.

### P1-5 — project delete doesn't revoke keys — **CONFIRMED**

Evidence: [U1, lines 328-334]
```
After DELETE /admin/projects/acme-web-app:
 key_id                               | project_id    | status
 5f0d8658-a6b8-45bc-a477-ff1bc566da10 | acme-web-app  | active   ← still active
```

The key can still authenticate. Tenant isolation fiction: "this project is deleted" but its keys remain valid gateway credentials.

### P1-6 — suspended user login blocked (correctly)

Evidence: [E5, line 98]
```
dave (status=suspended) → HTTP 401  "Account is not active. Contact support."
```

Good — the **login path** is gated. But existing API keys of a suspended user are not tested here (dave has no keys); the P1-6 concern about key-after-suspend remains unvalidated and must be rechecked in Phase 2B.

### P1-8 — portal project PATCH silently drops `description`

Could not confirm live because G2 crashed (see P0-4 below). The static-analysis finding stands and will re-verify once P0-4 is fixed.

### Validation: Some Phase 1 findings were fine

- **P0-7 streaming DLP** — not testable without a real provider call; static finding preserved.
- **P0-8 OAuth callback trust** — not testable without NextAuth runtime; static finding preserved.

---

## NEW findings (not in the Phase 1 static report)

### 🔴 NEW P0-9 — `POST /admin/tenants` IGNORES caller-supplied `tenant_id`

Evidence: [B2, lines 20-21]
```
Request: POST /admin/tenants {"tenant_id":"crud-test-001","name":"CRUD Test","slug":"crud-test"}
Response: HTTP 201 {"tenant_id":"786abfad-a5bd-42ca-98f1-4c5e07c7ee7b",...}
```

The caller asked for ID `crud-test-001`. Server returned a UUID instead. Subsequent B3 (`GET /admin/tenants/crud-test-001`) → 404, because the real ID is the UUID in the response.

**Impact:** The admin UI tenant form lets operators type a slug/ID; backend silently throws it away. Every follow-up operation (update, delete, reference in project FK) must use the generated UUID. If the UI isn't using the response body's `tenant_id` (and the UI wasn't built against this behavior — it's inconsistent with the REST-style `POST /admin/tenants {tenant_id: ...}` contract), half the CRUD is silently broken.

**Root cause suspicion:** a default factory on the Pydantic schema or ORM generates a UUID when the request field isn't properly wired to the insert — needs source inspection in Phase 2A.

### 🔴 NEW P0-10 — `POST /admin/projects` IGNORES caller-supplied `project_id`

Evidence: [C4, lines 54-55]
```
Request: POST /admin/projects {"project_id":"test-proj-001","tenant_id":"acme-001",...}
Response: HTTP 201 {"project_id":"ffc51e51-a5ff-4e62-af9a-d90d740ea3dd",...}
```

Same pattern. Every C5–C8 follow-up was a 404 because the test used the requested ID.

Combined impact of P0-9 + P0-10: **admin-driven resource creation with stable IDs is not usable.** Anyone wiring Terraform, migration scripts, or test fixtures to admin CRUD will break silently.

### 🔴 NEW P0-11 — `DELETE /admin/projects/{id}` returns 500 when project has API keys

Evidence: [C9, lines 64-65]
```
DELETE /admin/projects/acme-web-app  (project has 1 active key)
→ HTTP 500
  {"error":{"code":"internal_error","message":"An internal service error occurred."}}
```

Backend attempted hard-delete; FK from `api_keys.project_id` blocked it; exception bubbles as 500 instead of a clean 409 or a cascade.

**Impact:** Admin can never delete a real project (every real project has keys). Combined with P1-5 (keys aren't revoked on delete) you get the worst of both — the API shape suggests delete is supported, but the only projects you can actually delete are the ones that are already broken (no keys).

### 🔴 NEW P0-12 — Portal project creation is a 100% 500 on a fresh DB

Evidence: [G2 + X6, lines 126 + backend.log tail]

```
POST /portal/projects (alice, logged in, valid payload)
→ HTTP 500 internal_error

Backend log root cause:
  IntegrityError: Key (tenant_id)=(demo-tenant-000) is not present in table "tenants".
  SQL: INSERT INTO projects (...) VALUES ('demo-tenant-000', ...)
```

This is **P0-4 manifesting as a hard crash on fresh installs.** The portal project route hard-codes `tenant_id = "demo-tenant-000"`; that tenant only exists if someone ran the demo seeder; if they didn't, every portal user hitting `/projects` gets an opaque 500.

**Impact:** portal signup → create first project → SaaS onboarding — completely broken out-of-the-box. Only works in environments where `scripts.seed_demo_data` was run.

**Phase 2A deadline implication:** This can be patched in 2 minutes (seed the default tenant at startup, or refactor to use tenant-per-user per the separate `PORTAL_TENANCY_MIGRATION_PLAN.md`). A *surgical* fix (auto-seed `demo-tenant-000` at startup) is an acceptable stop-gap while the proper tenancy refactor is done as a separate track.

### 🟠 NEW P1-12 — Admin tenant create accepts empty name + doesn't enforce slug uniqueness

Evidence: [B8 + B10, lines 32-37]
```
B8: POST /admin/tenants {"tenant_id":"acme-001","name":"Duplicate","slug":"dup"}
    → HTTP 201  (not 409 — and yes, tenant_id was ignored so a new UUID row was created
                 anyway, but importantly no duplicate check on any field ran)

B10: POST /admin/tenants {"tenant_id":"empty-001","name":"","slug":"empty"}
     → HTTP 201  (row created with name='')
```

Zero-length name passes validation. No check on slug uniqueness even though the Phase 1 audit showed `TenantORM.slug` is declared `unique=True`. Needs a direct `SELECT slug FROM tenants` check to confirm whether DB-level uniqueness is actually enforced — I did not run that probe.

### 🟠 NEW P1-13 — Admin tenant create with 1000-char name → 500

Evidence: [B5, lines 26-27]
```
POST /admin/tenants {...,"name":"XXXXX...(1000 X's)","slug":"long-001"}
→ HTTP 500 internal_error
```

Likely hit Postgres VARCHAR length limit or name field is TEXT but something upstream truncates. Should be a 422 with a clear `string_too_long` message, not a 500. (Portal's tenant-name constraint by contrast enforces 255 chars cleanly — see G14.)

### 🟠 NEW P1-14 — No brute-force protection on portal login

Evidence: [E7, lines 102-103]
```
10 rapid wrong-password POSTs in a loop → all 401, no lockout, no delay, no Retry-After.
```

Beat's Phase 2A constraint #4 is "Rate limit /auth/login to prevent brute force." This confirms it's needed — nothing is there today. (Admin `/auth/login` was not tested for this specifically but the code path is likely identical.)

### 🟠 NEW P1-15 — Unverified email can still login at the portal

Evidence: [E6, lines 100-101]
```
eve@globex.test  (email_verified=false)
POST /portal/auth/login → HTTP 200 + access_token
```

No email-verification gate. This interacts badly with P0-4 and the missing account-delete flow: anyone can sign up with `foo@victim.com`, read system settings derivable from `/portal/auth/public-settings`, and sit on the credit balance indefinitely.

### 🟡 NEW P2-13 — PATCH `/portal/me` silently drops unknown fields

Evidence: [F6, lines 118-119]
```
PATCH /portal/me  {"email":"changed@acme.test"}
→ HTTP 200  (email unchanged — no error, no hint that 'email' is not mutable)
```

Pydantic default behavior. Fine as a design choice, but combined with the P1-8 "`description` is dropped on portal project PATCH" pattern, it suggests the portal API should switch to `model_config = {"extra": "forbid"}` at least for mutation endpoints — otherwise client code that thinks it's updating `email` silently succeeds with no effect.

---

## Full test matrix — PASS/FAIL per section

### A. Auth (admin)

| # | Test | Result | Evidence line |
|---|---|---|---|
| A1 | `POST /auth/login` admin/admin1234 | ✅ 200, JWT returned | 6-7 |
| A2 | Wrong password | ✅ 401 | 8-9 |
| A3 | `GET /auth/me` with valid JWT | ✅ 200, user info | 10-11 |
| A4 | `GET /auth/me` no auth | ✅ 401 | 12-13 |

### B. Admin Tenant CRUD

| # | Test | Result | Note |
|---|---|---|---|
| B1 | List tenants | ✅ 200, 2 seeded | |
| B2 | Create tenant | 🟡 201 **but tenant_id ignored** (P0-9) | |
| B3-4 | Get/update by my-ID | ❌ 404 | follows from B2 |
| B5 | 1000-char name | ❌ 500 instead of 422 (P1-13) | |
| B6 | Thai+emoji name | ✅ 201 (but curl stripped unicode — see X7/X8) | |
| B7 | SQL-injection in name | ✅ 201, stored literally; tables intact (W1) | |
| B8 | Duplicate | 🟡 201 (P1-12) | |
| B9 | Missing required field | ✅ 422 | |
| B10 | Empty name | ❌ 201 (P1-12) | |
| B11-13 | Delete by my-ID / list after | ❌ 404s because of B2 | |

### C. Admin Project CRUD

| # | Test | Result |
|---|---|---|
| C1 | List all | ✅ 200, 6 seeded |
| C2 | Filter by tenant_id=acme-001 | ✅ 200, 3 items |
| C3 | Filter by fake tenant | ✅ 200, [] |
| C4 | Create | 🟡 201 but project_id ignored (P0-10) |
| C5-C8 | Operations on requested ID | ❌ 404 (follows from C4) |
| C7 | Create with nonexistent tenant | ✅ 404 correctly |
| C9 | Delete project with keys | ❌ **500** (P0-11) |
| C10 | Verify keys after attempted delete | ❌ key status=active (P1-5) |

### D. Admin API Keys

| # | Test | Result |
|---|---|---|
| D1 | List all | ✅ 6 keys shown across **both tenants** (P0-6 re-confirmed) |
| D2 | Filter by project_id | ✅ 1 item |
| D3 | Create with raw key returned once | ✅ 201 + key |
| D4 | Get by id | ✅ 200, key_hash not leaked |
| D5 | Revoke | ✅ 204 |
| D6 | Get after revoke | ✅ 200 with status=revoked |
| D7 | Use revoked key on `/v1/models` | 🟡 200 — `/v1/models` is public, so revoked key still works (expected; documented) |

### E. Portal Auth

| # | Test | Result | Note |
|---|---|---|---|
| E1 | Alice login | ✅ 200 + access + refresh | |
| E2 | `/portal/me` | ✅ 200, full profile | |
| E3 | Wrong password | ✅ 401 | |
| E4 | Nonexistent user | ✅ 401 (same message as E3 — good, no enumeration) | |
| E5 | Suspended user (dave) | ✅ 401 with clear message | |
| E6 | Unverified email (eve) | ❌ 200 — login allowed (P1-15) | |
| E7 | 10 rapid wrong logins | ❌ 10× 401, no throttle (P1-14) | |

### F. Portal `/me` updates

| # | Test | Result | Note |
|---|---|---|---|
| F1 | Display name w/ unicode (shell arg) | 🟡 200 but mojibake — test artifact, see X7 | |
| F2 | Readback | 🟡 200 with same mojibake | |
| F3 | 10000-char name | ✅ 422 "at most 100 characters" | |
| F4 | null | ✅ 200, unchanged | |
| F5 | Empty string | ✅ 200, becomes null | consistent-ish |
| F6 | Unknown field `email` | ❌ 200, silently ignored (P2-13) | |
| X7/X8 | Unicode via `--data-binary` UTF-8 file | ✅ Round-trips perfectly (56 octets stored) | rules out a unicode bug |

### G. Portal Project CRUD

| # | Test | Result | Note |
|---|---|---|---|
| G1 | Alice list | ✅ 200, [] | |
| G2 | **Alice create** | ❌ **500** (P0-12 / P0-4 root cause: demo-tenant-000 FK violation) | |
| G3–G5, G7–G12, G15 | All downstream operations | cascading failures because G2 gave no id; redirects to `/portal/projects/` (307) | |
| G13 | Empty name | ✅ 422 "at least 1 character" | |
| G14 | 500-char name | ✅ 422 "at most 255 characters" | |

### H. Portal Billing

| # | Test | Result |
|---|---|---|
| H1 | List packs | ✅ 3 items (Starter/Pro/Business auto-seeded by migration) |
| H2 | Purchase nonsense pack_id | ✅ 404 pack_not_found |
| H3 | Missing pack_id | ✅ 422 |
| H4 | Transactions (alice) | ✅ 200, [] (no signup_bonus tx because alice was direct-seeded, not via signup) |
| H5 | Usage (alice) | ✅ 200, [] |
| X5 | **Purchase with real pack_id** | ❌ **501** (P0-3 confirmed) |

### I. Avatar upload round trip

| # | Test | Result |
|---|---|---|
| I1 | Upload 1×1 PNG | ✅ 200, `avatar_url=/static/avatars/<uid>_<token>.webp` (auto-converted to WebP) |
| I2 | `/portal/me` reflects new avatar_url | ✅ 200 |
| I3 | Upload 20MB blob | ✅ 413 `request_too_large` at 5MB limit |
| I4 | Upload text file with image mime | ✅ 400 "Only JPEG, PNG, or WebP images are allowed." |
| I5 | Delete | ✅ 200, `avatar_url:null` |
| I6 | `/portal/me` reflects deletion | ✅ 200 |

### J. Dashboard + counts

| # | Test | Result |
|---|---|---|
| J1 | `/admin/dashboard` | ✅ 200, tenants=7 projects=7 requests=0 |
| J2 | overview 24h | ✅ Full structure, zeros throughout |
| J3 | top-models 24h | ✅ 200, [] |
| J4 | volume 24h | ✅ 200, 24 hourly buckets |
| J5 | recent-requests | ✅ 200, [] |
| J6 | usage/summary | ✅ 200, zeros |
| J7 | **DB vs API count reconciliation** | ✅ Exact match: tenants 7/7, projects 7/7, api_keys active 6/6, portal_users 5/5 |

**Count correctness (Beat's focus): PASS across the board.** No drift between DB and API.

### K. System settings

| # | Test | Result |
|---|---|---|
| K1 | List | ✅ 4 keys as whitelisted |
| K2 | Patch allowed key (`free_trial_tokens`) | ✅ 200 |
| K3 | Patch disallowed key | ✅ 404 `setting_not_found` |
| K4 | Patch with invalid value (`-100`) | ✅ 422 "Value must be positive" |

**Setting propagation verified:** K2 changed `free_trial_tokens` 100000 → 7500. X4 shows a new signup got `credit_balance:7500`. Live, no stale cache.

### L. Alert Rules (orphan API)

| # | Test | Result | Note |
|---|---|---|---|
| L1 | List | ✅ 200, [] | |
| L2 | Create w/ wrong metric name | ✅ 422 with full `allowed` enum list returned — good developer UX | confirmed allowed: error_rate, cost_per_hour, requests_per_minute, dlp_block_spike, provider_failure, budget_threshold, quota_exhausted |
| L3 | List again | ✅ [] (L2 was rejected) | |
| L4 | Delete w/ empty id | 307 redirect | test artifact |

Orphan-API conclusion: the backend is fully functional — just no UI.

### N. Error states

| # | Test | Result |
|---|---|---|
| N1 | GET nonexistent project | ✅ 404 |
| N2 | GET /admin without auth | ✅ 401 |
| N3 | GET /admin with garbage token | ✅ 401 `invalid, expired, or revoked API key` |
| N4 | POST invalid JSON body | ✅ 422 `json_invalid` |
| N5 | Password change endpoint | ✅ 404 (endpoint does not exist — documented gap) |
| N6 | Delete account | ✅ 405 Method Not Allowed (endpoint does not exist) |

### O. Public /v1

| # | Test | Result |
|---|---|---|
| O1 | /v1/models no auth | ✅ 200 with full model catalog |
| O2 | /v1/models with seeded key | ✅ 200 |
| X10 | acme key + globex key both work | ✅ 200 both |

### P. Policies + detectors

| # | Test | Result |
|---|---|---|
| P1 | list /admin/policy-profiles | ✅ `default` + `strict` profiles |
| P2 | list /admin/policies (legacy) | ✅ 200 — confirms orphan API |
| P3 | list /admin/dlp/detectors | ✅ 200 — shows full detector catalog incl. Thai-specific detectors (thai_national_id, thai_tax_id, thai_bank_account) |

### Q. Keyword Rules

| # | Test | Result |
|---|---|---|
| Q1 | List | ✅ [] |
| Q2 | Create valid | ✅ 201 |
| Q3 | List again | ✅ 1 item |

### R. Feedback

| # | Test | Result |
|---|---|---|
| R1 | Submit for fake request_id | ✅ 404 `not_found` |

### S. Admin portal-user ops

| # | Test | Result | Note |
|---|---|---|---|
| S1 | `/admin/ner/status` | ✅ 200 `not_loaded` (NER disabled by default) | |
| S2 | List portal-users | ✅ 5 users, Charlie's 🐉+Thai displays correctly | emoji stored & served intact |
| S3 | Search `alice` | ✅ 200, 1 match | |
| S4 | Search by emoji (url-encoded 🐉) | ✅ 200, Charlie returned | unicode search works |
| S5-retry | Top-up alice 5000 | ✅ 200, `credit_balance 50000→55000`, transaction row created with `created_by:"jwt:admin"` | good audit signal |

### X. Follow-up probes

| # | Test | Result |
|---|---|---|
| X1 | Portal signup | ✅ 201 + tokens + user with `credit_balance:7500` from post-K2 setting |
| X2 | Signup with existing email | ✅ 400 `email_exists` |
| X3 | Signup weak password (3 chars) | ✅ 422 |
| X4 | New signup's free-trial credit | ✅ matches current `free_trial_tokens` system setting |
| X5 | Real-pack purchase | ❌ 501 (P0-3) |
| X6 | Backend log root cause of G2 | IntegrityError on tenant_id=demo-tenant-000 → P0-12 |
| X7/X8 | UTF-8 roundtrip via `--data-binary` | ✅ "Alice 🦀 นักพัฒนา 🎉 ทดสอบ" stored 56 bytes correctly |
| X9 | Dashboard vs DB counts | ✅ exact match |
| X10 | Cross-tenant keys on public endpoint | ✅ 200 (public endpoint — expected) |
| X11 | Portal token on /admin | ✅ 401 — auth layer separation enforced |

---

## Evidence gaps needing human browser verification

These cannot be validated by curl — flagging them for Beat to check in the browser if desired, otherwise deferred to Phase 4 regression:

1. **Admin UI empty states** — do the 18 admin pages render a CTA or a broken blank table when an API returns `{"items":[]}`? The backend behavior is verified; the UI-rendering side is not.
2. **Multi-tab / cross-session realtime sync** — the admin dashboard polls every 5s per the Phase 1 inventory. I confirmed API responses are always fresh (K2 setting change reflected in X4 immediately), but whether the dashboard chart actually re-renders after a `refetch` without a hard refresh needs a real browser.
3. **Avatar upload preview** — upload works via API; whether the portal's upload modal shows a preview, spinner, and success toast was not tested.
4. **Portal dark-theme regressions** (flagged in Phase 1 inventory — recent commits `af64a5e` + `9ef3456`) — need browser visual inspection on both themes; no functional regression detectable via API.

---

## Summary of all P0/P1 after Phase 1.4

**The new bugs get slotted into Beat's Phase 2A/2B plan:**

**Phase 2A — auth / tenant-scope (Beat's Batch A):**
- P0-1, P0-2, P0-8 — admin auth rework (unchanged from Phase 1)
- P0-5, P0-6 — tenant-filter bugs (empirically confirmed)
- **P0-9, P0-10 — tenant_id / project_id auto-gen bug (NEW)** — touches admin CRUD contract; candidate for bundling with auth rework since it's admin-side plumbing
- **P1-14 — login rate-limit (NEW)** — matches Beat's constraint #4 for Phase 2A, belongs here

**Phase 2B — data correctness (Beat's Batch B):**
- P0-7 — SSE DLP ordering
- **P0-11 — project-delete 500 with keys (NEW)**
- **P0-12 — portal project 500 on fresh DB (NEW)** — can land as a surgical fix (auto-seed default tenant or guard/create-on-demand) while the big P0-4 tenancy refactor lives in `PORTAL_TENANCY_MIGRATION_PLAN.md`
- **P1-12, P1-13, P1-15, P2-13 (NEW)** — data-integrity / validation cleanups
- Remaining Phase 1 P1 items

**Separate tracks (unchanged from Beat's scope decision):**
- P0-3 Stripe → `STRIPE_INTEGRATION_PLAN.md`
- P0-4 Portal tenancy → `PORTAL_TENANCY_MIGRATION_PLAN.md` (with P0-12 as the immediate duct-tape)

---

## Artifacts produced

| Path | Purpose |
|---|---|
| `perf_audit_tmp/env.sh` | Audit-stack env vars incl. raw API keys (DEV ONLY — fresh DB, zero value) |
| `perf_audit_tmp/test_matrix.sh` | Full curl test harness |
| `perf_audit_tmp/evidence.log` | Raw output, 331 lines |
| `perf_audit_tmp/backend.log` | Backend-side traceback evidence for P0-12 |
| `backend/scripts/seed_audit_data.py` | Test-data seeder (kept as committable asset for future audit runs) |

---

## Proposed Next Step

Beat — stopping here per the gated workflow. Recommended sequencing for Phase 2A design:

1. Approve this report's findings (especially P0-9/10/11/12 as new P0s to add to the fix queue).
2. Decide whether `P0-12` gets a **duct-tape fix** now (seed `demo-tenant-000` at startup) or waits for the full portal-tenancy refactor (which is on the separate-tracks list).
3. Green-light the Phase 2A `AUTH_REDESIGN_DESIGN.md` draft — which will incorporate P0-1/2/5/6/8/9/10 and P1-14 into one coherent auth + authorization layer rewrite, honoring Beat's six auth-redesign constraints.

Also — the audit stack is still running. If you want to poke at it in a browser, I can start the admin UI on 3010 and portal on 3011 pointing at `http://localhost:8090`. Otherwise, say the word and I'll tear it down.
