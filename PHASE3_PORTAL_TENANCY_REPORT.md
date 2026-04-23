# Phase 3 — Portal Tenancy Refactor: Verification Report

**Status:** Complete. 10 commits (17–26) landed. P0-4 closed. Design doc
`PORTAL_TENANCY_MIGRATION_PLAN.md` fully implemented, all 5 §15 answers
applied, all 3 additional concerns addressed.

**Date:** 2026-04-23
**Branch:** main
**Launch mode:** internal tool (§14 SaaS-scale items deferred per plan).

---

## Commit log

| SHA | Title |
|---|---|
| `e8787e9` | 17 — Migration 029 (schema + backfill + rename demo-tenant → "Default Team") + design doc |
| `6c4e6bf` | 18 — Migration 030 (NOT NULL + request_logs FK + orphan pre-scan) |
| `a454638` | 19 — tenant_service + JWT tenant claims + token_version gate (Concern 1) |
| `16b1558` | 20 — Atomic signup: tenant creation OR invite redemption |
| `da2ca2a` | 21 — portal_projects: remove `_PORTAL_TENANT_ID`, add isolation tests |
| `be3c98b` | 22 — portal_team routes (members, invites, revoke, redeem) |
| `41b63b3` | 23 — Portal scope boot-check (Concern 2A) |
| `6829d1a` | 24 — Parameterized cross-tenant leak test (Concern 2C) |
| `aaf2aeb` | 25 — token_packs tenant-scoped (Q1 answer) |
| `49e46a5` | 26 — Portal UI: invite field + team page |

**New tests Phase 3:** 95 (across 8 new test files + additions to existing).
**Phase 2 + Phase 3 combined test surface:** 241 tests green.

---

## P0-4 closed

Pre-Phase-3 sites:
- `backend/app/api/routes/portal_projects.py:34` — `_PORTAL_TENANT_ID = "demo-tenant-000"` constant. **Deleted in C21.**
- `backend/app/api/routes/portal_projects.py:101` — `tenant_id=_PORTAL_TENANT_ID` on every project write. **Replaced with `tenant_id=_require_tenant_id(user)` in C21.**
- Migration 027 (demo-tenant seed) — **superseded** by migration 029's Option C rename. The row itself stays (same PK), but it's now a real "Default Team" tenant with a real owner.

Post-Phase-3 state:
- Every portal user has `tenant_id` + `tenant_role` on their row. DB enforces NOT NULL via migration 030.
- Portal JWT carries `tenant_id`, `tenant_role`, `token_version` claims. The get_portal_user dep rejects stale version tokens with code `token_version_stale` (the one-time re-login event on cutover).
- Every `/portal/*` route has an explicit scope declaration (C23). Boot crashes loud if any route ships without one.
- The `demo-tenant-000` value no longer appears anywhere in the production code.

---

## §15 answers — all executed

| Q | Answer | Commit that lands it |
|---|---|---|
| Q1 — Token pack scoping | Tenant-scoped | C25 (migration 031 + route filter) |
| Q2 — Invite TTL | 7 days hardcoded | C19 (`_DEFAULT_INVITE_TTL = timedelta(days=7)`) |
| Q3 — Orphan ownership | Superadmin reassigns; last-owner guard | C19 `remove_member` refuses to strip the last owner |
| Q4 — First-user-is-owner | Confirmed | C20 signup creates tenant + promotes user to owner atomically |
| Q5 — Demo rename target | "Default Team" | C17 migration 029 UPDATE statement |

---

## 3 additional concerns — all addressed

### Concern 1: JWT token_version bump

Implementation:
- `portal_users.token_version` column added in C17 (migration 029), default 1.
- Migration 029 bumps every existing row to version 2 — invalidates every pre-Phase-3 JWT on deploy.
- `create_portal_token` + `_mint_for_user` embed `token_version` in every new JWT (C19).
- `deps.get_portal_user` compares JWT's `token_version` against `user.token_version`; mismatch → 401 `token_version_stale`.
- Tokens without the claim (pre-Phase-3) are treated as version 0 → always mismatch after 029.
- Rotated automatically on: tenant create, invite redeem, member removal (one bump per state change that might alter user's auth context).

Test pins (`test_tenant_service.py`):
- `test_matching_token_version_accepted` — happy path.
- `test_stale_token_version_rejected` — mint with older version, DB has newer → 401.
- `test_absent_version_claim_treated_as_stale_after_bump` — forged pre-migration JWT → 401.

### Concern 2: Automated cross-tenant leak detection

**Option A (boot-check, C23):** Startup scans every `/portal/*` route and raises `RuntimeError` if any lacks a scope decorator. Decorators: `@scope_to_portal_tenant` / `@explicit_cross_portal_tenant(reason=...)` / `@public_portal_route`. 32 routes decorated across 5 router files. Boot fails loud listing every missing route — a forgotten decorator can't silently ship.

Test pins (`test_portal_scope_bootcheck.py`, 9 tests):
- Each decorator tags correctly.
- Undecorated route triggers RuntimeError; error enumerates every missing path.
- Production app.main passes the check at module-load.

**Option C (parameterized per-route leak test, C24):** Walks `app.routes` at test collection, enumerates every `/portal/*` route with a path parameter, parameterizes one test case per (method, path, params). Each probe runs as tenant A with a tenant B resource ID in the URL, asserts non-2xx. Mechanical coverage catches logic bugs inside the decorator even when the marker is present.

Current coverage (8 route/method combos auto-discovered):
```
GET    /portal/projects/{project_id}
PATCH  /portal/projects/{project_id}
DELETE /portal/projects/{project_id}
GET    /portal/projects/{project_id}/keys
POST   /portal/projects/{project_id}/keys
DELETE /portal/projects/{project_id}/keys/{key_id}
DELETE /portal/team/members/{target_user_id}
DELETE /portal/team/invites/{invite_id}
```

New routes with path params get picked up automatically on next test run — no per-route test file to maintain. Unknown param names pytest.skip with a message pointing at the fixture.

### Concern 3: Production-like migration test fixture

Test file `test_migration_029_portal_tenancy.py` fixture seeds:
- 3 portal users with distinct `created_at` values so "oldest user becomes owner" has a deterministic answer.
- 2 projects across 2 different users.
- 3 api_keys distributed across projects.
- 5 request_logs pointing at demo-tenant-000.

Scenarios covered (13 tests):
- Happy path upgrade: tenant rename, all users backfilled, oldest → owner, token_version bump, zero row rewrites on projects/api_keys/request_logs (Option C guarantee verified by snapshot compare).
- Fresh-install edge cases: empty portal_users table, missing demo-tenant row (027 didn't run) — upgrade still lands.
- Rollback (4 tests, separate-tx pattern — SQLite's batch_alter_table requires it): invites table drops, columns removed, data preserved, tenant name reverts.

Migration 030 (C18) adds 7 more tests including the belt-and-braces drift guard: manually NULL a tenant_id row, upgrade raises `RuntimeError` with the count.

Migration 031 (C25) adds 1 more: upgrade adds the tenant_id column on top of the full 001-030 chain.

---

## Tenant isolation replay — all BLOCKED

### Targeted tests (`test_portal_project_tenant_isolation.py`, 9 tests)

Two signed-up users in separate tenants exercise every write + read site:

| Scenario | Expected | Actual |
|---|---|---|
| Two signups → distinct tenants, both owners | ✓ | ✓ |
| Tenant B lists projects → A's project absent | ✓ | ✓ |
| Tenant B GETs A's project by id | 404 | 404 |
| Tenant B PATCHes A's project | 404 | 404 |
| Tenant B DELETEs A's project | 404 | 404 |
| Tenant B lists A's project keys | 404 | 404 |
| Tenant B creates key in A's project | 404 | 404 |
| Newly-created project lands in caller's own tenant | ✓ | ✓ (tenant_id ≠ "demo-tenant-000") |
| Invited member's project lands in owner's tenant | ✓ | ✓ (member joins via code → same tenant) |

### Mechanical (C24, 8 parameterized cases — see above)

### Billing isolation (`test_portal_billing_tenant_scoping.py`, 7 tests)

- Global pack (tenant_id NULL) visible to any caller.
- Tenant-private pack visible only to that tenant.
- Three-way mix: tenant A sees (global + A), tenant B sees (global + B).
- Purchase of foreign-tenant pack → 404 `pack_not_found` (no existence oracle).

### Team route isolation (`test_portal_team_routes.py`, 13 tests)

- Cross-tenant invite revoke → 404 `invite_not_found`.
- Member cannot generate invites (403 `owner_required`).
- Member cannot remove another member (403).

---

## Invitation flow

| Scenario | Result |
|---|---|
| Owner creates invite → code returned in response | ✓ (43-char urlsafe token) |
| Invite visible in GET /portal/team/invites (owner-only) | ✓ |
| Unauthenticated signup with code → user joins as member | ✓ |
| Used invite cannot be redeemed again | ✓ (`invite_already_used`, 409) |
| Expired invite rejected | ✓ (`invite_expired`, 409) |
| Invalid code rejected | ✓ (generic `invalid_invite`, 404 — no existence oracle) |
| Invite + tenant_name in signup body | ✓ (400 `invite_and_tenant_name_conflict`) |
| Revoked invite cannot be used | ✓ (marked `used_at = now, used_by = NULL`) |
| Authenticated orphaned user uses POST /portal/team/redeem | ✓ |

---

## Migration verification (snapshot + live chain)

### 001-028 → 029 → 030 → 031 chain

All four migrations run in strict sequence against an in-memory SQLite DB that's been upgraded through every preceding migration. The full chain runs in `test_migration_031` which executes 001-028 → 029 → 030 → 031 consecutively and inspects the resulting schema.

### Verification queries pass post-migration

- `SELECT tenant_id, COUNT(*) FROM portal_users WHERE tenant_id IS NULL GROUP BY 1` → 0 rows (migration 030 enforced).
- `SELECT COUNT(*) FROM portal_users WHERE tenant_role = 'owner'` → at least 1 per tenant.
- `SELECT name, slug FROM tenants WHERE tenant_id = 'demo-tenant-000'` → ('Default Team', 'default-team').
- `SELECT MIN(token_version) FROM portal_users` → 2 (every row bumped, pre-migration JWTs invalid).
- Every row in `projects`, `api_keys`, `request_logs` preserved (snapshot compare equal pre/post).
- `tenant_invites` table exists with 9 columns + 2 indexes + 3 FKs.
- `request_logs.tenant_id` now has an FK constraint.
- `token_packs.tenant_id` column added (nullable).

### Rollback

`alembic downgrade 028` restores:
- Drops `tenant_invites`, `portal_users.tenant_id/tenant_role/token_version`, `request_logs` FK, `token_packs.tenant_id`.
- Reverts tenant name "Default Team" → "Demo Tenant".
- Preserves every row in `projects`, `api_keys`, `request_logs`, `portal_users`, `tenants`.

Tests: `TestMigration029Downgrade` (4), `TestMigration030Downgrade` (3).

---

## Regression integrity

Full Phase 2 auth surface + Phase 3 tenancy surface:

```
$ pytest \
    tests/test_portal_auth.py \
    tests/test_portal_credit.py \
    tests/test_portal_signup_tenancy.py \
    tests/test_tenant_service.py \
    tests/test_email_verification.py \
    tests/test_oauth_flow.py \
    tests/test_portal_project_tenant_isolation.py \
    tests/test_portal_team_routes.py \
    tests/test_portal_scope_bootcheck.py \
    tests/test_portal_cross_tenant_leak_template.py \
    tests/test_portal_billing_tenant_scoping.py \
    tests/test_migration_029_portal_tenancy.py \
    tests/test_migration_030_portal_tenancy_finalize.py \
    tests/test_migrations.py \
    tests/test_admin_bearer_rejection.py \
    tests/test_csrf_middleware.py \
    tests/test_client_id_rejection.py \
    tests/test_delete_with_dependent_keys.py \
    tests/test_input_validation_hardening.py \
    tests/test_login_rate_limit_config.py \
    tests/test_admin_auth_decorators.py \
    tests/test_tenant_isolation_p0_5_6.py \
    tests/test_admin_audit_extensions.py \
    tests/test_secret_validation.py \
    tests/test_sse_dlp_ordering.py \
    -q

241 passed in 67.76s
```

Pre-existing `/v1/chat/completions` 503 failures (147) unchanged — all from `provider_unavailable` in the perf-baseline uncommitted work (same as Phase 2C).

`test_session_manager::test_tampered_cookie_rejected` continues to show the pre-existing cross-test isolation flake when bundled with `test_admin_session_routes`; passes in isolation. Documented at Phase 2C Commit 16, not caused by Phase 3.

Startup boot-checks (admin + portal) pass on every app import. If C23's boot-check finds a gap, worker crashes loud — already covered by the `TestProductionAppPassesBootCheck` test.

---

## Known limitations / deferrals (per design doc §14)

1. **Real email for invitations.** Invite codes are copy-paste via the portal UI. Real SMTP / transactional mailer → Phase 3+ post-launch.
2. **Tenant settings page.** Name / logo / billing address editable post-signup: deferred. Owner can rename via admin-side DB edit in the meantime.
3. **Rich role hierarchy.** Only owner + member today. billing_admin / viewer / custom roles → post-launch.
4. **Cross-tenant user membership.** One user → multiple tenants. Currently blocked with `user_has_tenant` on redeem. Post-launch.
5. **Tenant transfer / archive / soft-delete.** Not supported.
6. **Per-tenant branded portal URLs** (custom subdomains) — post-launch.
7. **Orphan-ownership auto-promote.** If the sole owner deletes their account, no other owner exists. Current behavior: admin-side DB fix. Auto-promote "oldest remaining member → owner" is tracked as a nice-to-have (§15 Q3 default).
8. **Streaming cadence** from Phase 2C P0-7 — still emits single-chunk. Rolling-buffer incremental DLP is a separate track.
9. **Per-action audit writer** — Phase 2A schema is in place, wired only to `@cross_tenant` today. Generalising to `@require_role` and portal routes → post-launch.

All 9 items behind design-doc deferrals (§14); none block internal-tool launch.

---

## Recommendation

**Approve Phase 3 → ship internal tool launch.**

- P0-4 closed. Migration 027 duct-tape retired.
- All 3 additional concerns (token_version, boot-check + parameterized leak test, production-like migration fixture) implemented and verified.
- All 5 §15 answers executed.
- 95 new tests (241 combined with Phase 2). Zero regressions to pre-existing passing tests.
- Portal UI has the minimum viable surface for invite-based team signup + team management.

Remaining SaaS-launch-blocking tracks (deferred per project_scope_split memory):
- `STRIPE_INTEGRATION_PLAN.md` — billing/payments. Still needed before external-customer SaaS.
- Phase 2C post-launch items (streaming cadence, admin UI polish, audit writer generalisation, real mailer).

---

*Commit 26 is the last of Phase 3. Revert path for the whole phase: `git revert e8787e9..49e46a5`.*
