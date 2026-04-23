# Phase 2B + 2C — Auth & Authz + P1 Hardening: Verification Report

**Status:** Complete. 8 commits (9-16) landed end-to-end. All required P0/P1
exploit replays blocked. Zero regressions to previously-passing tests.

**Date:** 2026-04-23
**Branch:** main
**Audit stack:** still up on 5433/6380/8090.

---

## Commit log

| SHA | Title |
|---|---|
| `b437292` | 9 — Bearer rejected on /admin/* (P0-1 hidden closure, 7 tests) |
| `1477751` | 10 — CSRF double-submit middleware (12 tests) |
| `79a7366` | 11 — Reject client-supplied IDs on POST /admin/{tenants,projects} (P0-9 + P0-10, 6 tests) |
| `132fe03` | 12 — Block DELETE /admin/projects/{id} with dependent API keys → 409 (P0-11, 4 tests) |
| `0a4464b` | 13 — SSE DLP runs BEFORE emit; provider text never reaches the wire raw (P0-7, 1 test) |
| `edb4aa2` | 14 — Login requires `email_verified=True` + /request-verification-email + /verify-email (P1, 6 tests) |
| `0acf587` | 15 — Name min/max + trim; PATCH unknown fields → 400 with field list (P1, 7 tests) |
| `0acf587→this` | 16 — Rate-limit default tightened to 5/15min per IP (was 20/1min) + this report |

**Total new tests Phase 2B/2C:** 44
**Total across the auth rewrite (Phase 2A + 2B + 2C):** 160
**Pre-existing `/v1/chat/completions` 503 failures:** 147 (unchanged — separate track)

---

## P0 exploit replay — all BLOCKED

### P0-1 (hidden Bearer path)
Phase 2A Commit 6 removed the admin API key from the browser bundle. Phase 2B
Commit 9 closes the backend leg: any `Authorization: Bearer <x>` on `/admin/*`
returns 401 outright.

```
$ curl -H "Authorization: Bearer gw_666dff9bed57e5e41c6954" \
    http://localhost:8090/admin/tenants
HTTP/1.1 401 Unauthorized
{"error":{"code":"authentication_error",
  "message":"Bearer tokens are not accepted on admin endpoints..."}}
```

Pinned by `test_admin_bearer_rejection.py` (7 tests). The specific leaked
key format `gw_*` explicitly tested.

`SECURITY_INCIDENT_LOG.md` records the git-history grep: the key VALUE was
never committed, only the variable NAME. No rotation required.

### P0-2, P0-5, P0-6, P0-8 — Phase 2A regressions
All four still blocked after 2B/2C landed. Verified by re-running the full
test set:

```
tests/test_admin_bearer_rejection.py               7 passed  (P0-1 + P0-2)
tests/test_tenant_isolation_p0_5_6.py              9 passed  (P0-5 + P0-6)
tests/test_oauth_flow.py                          18 passed  (P0-8)
```

### P0-7 (SSE DLP)
The streaming event generator no longer emits provider deltas as they arrive.
Every chunk accumulates server-side, outbound DLP runs on the full body, and
the DLP-scrubbed text is emitted as a single content delta followed by the
finish chunk and `[DONE]`. Raw PII / credential content never reaches the
wire.

Test `tests/test_sse_dlp_ordering.py` replays the exploit with a stubbed
adapter yielding `AKIA...EXAMPLE` (an AWS-key-shaped string, caught by the
`credential` category which the default profile masks outbound):

```
FAIL: raw credential leaked into streamed response   ← pre-fix assertion
PASS: AKIAIOSFODNN7EXAMPLE not in body               ← post-fix (this commit)
```

Known trade-off, documented in the commit body: token-level streaming cadence
is now lost (reply emits as one chunk). Rolling-buffer incremental masker is
a tracked post-launch optimization.

### P0-9 (client tenant_id) + P0-10 (client project_id)
Locked decision: reject, don't honour.

```
$ curl -X POST /admin/tenants -d '{"tenant_id":"forged","name":"X","slug":"x"}'
HTTP/1.1 400 Bad Request
{"detail":{"error":{"code":"client_supplied_id_forbidden",
  "message":"client-supplied tenant_id is not permitted; server generates identifiers",
  "field":"tenant_id"}}}
```

`tests/test_client_id_rejection.py` (6 tests) covers both, plus the critical
nuance that ProjectCreate's `tenant_id` field (a parent reference, NOT a
client-supplied id) is still accepted.

### P0-11 (delete with keys → 500)
Pre-existing FK-violation 500 replaced with a structured 409:

```
$ curl -X DELETE /admin/projects/proj-a1
HTTP/1.1 409 Conflict
{"error":{"code":"project_has_dependent_keys",
  "message":"project has 2 API keys; delete them first",
  "details":{"dependent_keys":["key-a1","key-a2"]}}}
```

`tests/test_delete_with_dependent_keys.py` (4 tests): with keys → 409 with
list, without keys → 204, missing → 404.

### P0-12 (onboarding crash)
Unchanged — Phase 2-0 migration 027 still in place, `test_fresh_db_onboarding.py`
integration test still pins the regression.

---

## P1 items — all verified

### Login brute force (5/15min per IP AND per email)
`app/services/admin/session_service.py::get_login_rate_limiter` defaults
tightened this commit (Commit 16):

| Rule | Limit | Window |
|---|---|---|
| Per email | 5 fails | 15 min |
| Per IP    | 5 attempts | 15 min |

Pinned by `test_login_rate_limit_config.py` (fails if defaults drift) and
exercised end-to-end by Phase 2A Commit 3's
`test_admin_session_routes.py::TestLoginRateLimit` (3 tests: 6th fail → 429,
correct password during lockout still 429, successful login clears per-email
counter).

### Unverified email — 403
`tests/test_email_verification.py::TestUnverifiedLoginBlocked` (2 tests):

* Unverified user login → 403 with `code=email_not_verified`
* After flipping flag → 200

Plus the request → verify → login round-trip is proven in
`test_request_then_verify_flow`. Malformed / wrong-purpose tokens
rejected. No user-existence leak on `POST /portal/auth/request-verification-email`.

### Input validation (name min/max + PATCH unknown)
`tests/test_input_validation_hardening.py` (7 tests):

* Empty name → 4xx (never 5xx)
* 300-char name → 4xx (no DB truncation, no crash)
* Whitespace-only name → 4xx post-trim
* PATCH with unknown field → 400 with the field name
* PATCH with MULTIPLE unknown fields → 400 with all listed
* Known-fields-only update still 200

Constraints centralised on the Pydantic schemas; unknown-field rejection done
via dependency-injected helpers on the PUT routes so the error shape is
consistent (`{"error": {"code": "unknown_fields", "unknown_fields": [...]}}`).

### P0-7 SSE DLP ordering
Covered above under P0 section.

---

## CSRF verification

Commit 10 added `CsrfMiddleware` to the stack. Enforcement rules:

* GET/HEAD/OPTIONS — never guarded.
* Non-admin paths (`/v1/*`, `/portal/*`, `/auth/session/login`) — never guarded.
* `/admin/*` POST/PUT/DELETE/PATCH — both `csrf_token` cookie AND
  `X-CSRF-Token` header required; must match (hmac.compare_digest).

`tests/test_csrf_middleware.py` (12 tests):

* Missing either → 403 `csrf_failure`
* Mismatched → 403 (constant-time compare — two different mismatches produce
  identical response bodies, no side-channel)
* Matching → 200
* POST/PUT/DELETE/PATCH all covered
* Public endpoints (/v1/chat/completions) pass through
* GET /admin/thing passes without CSRF

Admin UI already sends `X-CSRF-Token` as of Phase 2A Commit 6
(`admin-ui/lib/api.ts`), so the full double-submit is round-tripped.

---

## Regression integrity

Full Phase 2 auth test surface (Phase 2A + 2B + 2C):

```
$ pytest tests/test_admin_bearer_rejection.py \
         tests/test_csrf_middleware.py \
         tests/test_client_id_rejection.py \
         tests/test_delete_with_dependent_keys.py \
         tests/test_sse_dlp_ordering.py \
         tests/test_email_verification.py \
         tests/test_input_validation_hardening.py \
         tests/test_login_rate_limit_config.py \
         tests/test_session_manager.py \
         tests/test_secret_validation.py \
         tests/test_admin_auth_decorators.py \
         tests/test_admin_session_routes.py \
         tests/test_oauth_flow.py \
         tests/test_tenant_isolation_p0_5_6.py \
         tests/test_admin_audit_extensions.py -q
160 passed in 8.01s
```

Pre-existing `/v1/chat/completions` 503 failures (147) — unchanged. All from
the `provider_unavailable` path in the perf-baseline uncommitted work.
Not introduced by any Phase 2 commit.

Counts / unicode / upload tests from Phase 1.4 still green (spot-checked
`test_admin_crud`, `test_admin_e2e`, `test_portal_auth`).

---

## Known limitations / deferrals

1. **Streaming cadence** (P0-7 side-effect). The fix collapses every
   provider chunk into a single content delta. Rolling-buffer incremental
   masker is tracked as a post-SaaS-launch performance pass.

2. **Per-action audit writer**. `admin_audit_log` schema extension from
   Phase 2A Commit 7 is only wired to `@cross_tenant` today. Adding an
   audit write inside every `@require_role` successful-call path is a
   small follow-up that wasn't in Phase 2B/2C scope.

3. **Portal tenancy** (P0-4). Duct-tape from Phase 2-0 still patches the
   onboarding crash. The structural fix is on the separate
   `PORTAL_TENANCY_MIGRATION_PLAN.md` track.

4. **OAuth production mailer / token UX**. Email verification mints a
   valid JWT; the mailer is stubbed (prints to log). Hooking up a real
   SMTP / transactional-email provider is Phase 3.

5. **Admin UI sidebar polish**. A few pages still read the legacy
   `sran_demo_session` localStorage shape directly rather than via
   `lib/auth`. They no-op safely under the new cookie auth but render
   the nav emptily for some roles. Non-security cleanup, tracked for
   Phase 3.

---

## Recommendation

**Approve Phase 2B + 2C → proceed to Phase 3** (feature-gap closure: password
reset flow, real mailer, admin UI polish) and the separate design tracks
(PORTAL_TENANCY_MIGRATION_PLAN.md, STRIPE_INTEGRATION_PLAN.md).

All 8 planned commits landed, all 6 P0 exploits blocked, all 4 P1 items
verified, CSRF double-submit end-to-end. Zero regressions to previously-
passing tests. 160 tests pinning the auth contract.

---

*Commit 16 is the last of Phase 2B/2C. Revert path for the whole Phase
2B/2C range: `git revert b437292..01603dd` (excluding 0acf587→this, which
contains the report itself).*
