# AI Gateway — Phase 2A: Admin Auth & Authorization Redesign

> **Historical design reference (clarified 2026-11-12).** The draft/approval
> status below belongs to the April design proposal. Admin session auth and
> RBAC are now implemented; use [ADMIN_API.md](ADMIN_API.md) and
> [SECURITY_MODEL.md](SECURITY_MODEL.md) for current behavior. This document is
> retained for design rationale, not as a claim that admin auth is unfinished.

> **Status:** Draft. **Stops for Beat review** before any implementation.
> **Scope:** P0-1, P0-2, P0-5, P0-6, P0-8. Portal-user auth is explicitly NOT touched.
> **Locked decisions from Beat (2026-04-22):** hybrid session model (Redis-backed for admin, keep portal JWT) • retain role hierarchy, add decorator enforcement • OAuth moves server-side via authlib.
> **Date:** 2026-04-22

---

## 1. Executive Summary

Replace the admin-UI's client-side fake login and bundled API key with a real server-rendered authentication layer: login issues a signed session cookie backed by Redis, every admin endpoint enforces role + tenant scope via declarative decorators (default-deny), and OAuth callbacks are verified server-side with state + PKCE. Portal JWT auth is untouched. The P0s from Phase 1 + 1.4 (P0-1, P0-2, P0-5, P0-6, P0-8) all close in this phase; the P0-9/P0-10 API-shape bugs and P0-11 cascade bug are explicitly Phase 2B.

---

## 2. Current State — what's broken

Evidence references use `file:line` format. See `FUNCTIONAL_AUDIT_REPORT.md` and `PHASE1.4_LIVE_TEST_REPORT.md` for full context.

| P0 | Evidence | One-line |
|---|---|---|
| **P0-1** Fake login | `admin-ui/app/login/page.tsx:20-37` + `admin-ui/middleware.ts:19-22` | `/login` does `await setTimeout(400)`, writes a plain cookie; middleware only checks presence. |
| **P0-2** Leaked token | `admin-ui/lib/api.ts:2,22-24` | `NEXT_PUBLIC_ADMIN_API_KEY` compiled into browser bundle → sent as Bearer on every admin call. Devtools-readable. |
| **P0-5** No tenant filter on `/admin/requests` | `backend/app/api/routes/admin.py:545-605` | Confirmed live (PHASE1.4 T1): returns all tenants. |
| **P0-6** No tenant filter on `/admin/projects`, `/admin/api-keys` | `admin.py:757,879` | Confirmed live (PHASE1.4 T2/T3): single admin token returns both acme + globex data. |
| **P0-8** OAuth callback trusts caller | `backend/app/api/routes/portal_auth.py:85` | `POST /portal/auth/oauth/callback` accepts `oauth_id, email, avatar_url` verbatim and mints portal JWTs with no provider re-verification. |

Secondary rolled into this phase:
- **P1-14** No brute-force protection on login (Phase 1.4 E7) — ten rapid wrong passwords all 401, no lockout. Lives here because login-hardening is part of the same surface.
- **Seeded default `admin` / `admin1234`** re-applied on every startup (`auth.py` `_seed_admin_user`) — must deprecate.

Not here (explicit out-of-scope): P0-3 Stripe, P0-4 portal tenancy, P0-7 SSE DLP, P0-9/10/11 API contract, P0-12 (already duct-taped).

---

## 3. Target Architecture

```
                                             +---------------------+
                                             |   Redis             |
                                             |                     |
                                             |  session:<uuid>     |
                                             |  session:user:<id>  |
                                             |  oauth_state:<s>    |
                                             |  login_attempts:<e> |
                                             +----------^----------+
                                                        |
Browser (admin-ui)     Browser (portal)                 |
    |                     |                             |
    |                     |                             |
    | HttpOnly cookie     | Authorization: Bearer JWT   |
    |  session_id=...     |                             |
    |  csrf_token=... (non-HttpOnly, for double-submit) |
    v                     v                             |
+-----------------+    +-------------------+            |
|  Next admin-ui  |    |  Next portal      |            |
|  (rewrite)      |    |  (unchanged)      |            |
|                 |    |                   |            |
| - no more env   |    |  existing JWT     |            |
|   API key       |    |  flow stays put   |            |
| - fetch(..., { |    |                   |            |
|   credentials:  |    |                   |            |
|   'include' }) |    |                   |            |
+--------+--------+    +---------+---------+            |
         |                       |                      |
         |  POST /admin/auth/login                      |
         |  GET  /admin/* (cookie)                      |
         |                       |  POST /portal/auth/*|
         |                       |  GET  /portal/*      |
         v                       v                      |
+------------------------------------------+            |
|  FastAPI (backend)                       |            |
|                                          |            |
|  +-----------------------------------+   |            |
|  |  Admin auth middleware            |<--+------------+
|  |  - verify session cookie          |                |
|  |  - load session from Redis        |                |
|  |  - populate request.state.admin   |                |
|  +-----------------+-----------------+                |
|                    |                                  |
|                    v                                  |
|  +-----------------------------------+                |
|  |  Route handler                    |                |
|  |  @require_role(TENANT_ADMIN)      |                |
|  |  @scope_to_tenant                 |                |
|  +-----------------+-----------------+                |
|                    |                                  |
|                    v                                  |
|  +-----------------------------------+                |
|  |  Service / repo layer             |                |
|  |  (receives tenant_id kwarg)       |                |
|  +-----------------------------------+                |
|                                          |            |
|  Portal auth middleware (existing)       |            |
|  - verify JWT                            |            |
|  - load PortalUserORM                    |            |
|                                          |            |
|  OAuth flow (new)                        |            |
|  /admin/auth/oauth/start                 |<-----------+
|  /admin/auth/oauth/callback              |  (state,
|                                          |   PKCE)
+------------------------------------------+
```

Key change shapes:
- **Admin path:** cookie in → session lookup → role/tenant enforcement → handler (same handler code, different dep).
- **Portal path:** unchanged end-to-end.
- **OAuth:** only used for admin login in this phase; portal OAuth is P0-8 fix (same flow applied to portal's existing OAuth surface).

---

## 4. Session Model (per Decision 1)

### 4.1 Storage

```python
# Redis keys
session:<uuid>          # JSON blob; TTL = sliding 24h, absolute max 7d
session:user:<user_id>  # SET of session UUIDs; for "revoke all my sessions"
```

### 4.2 Session payload

```json
{
  "session_id": "9f6...uuid",
  "user_id": "adminuser-uuid",
  "role": "super_admin",
  "tenant_id": null,            // null for super_admin; set for scoped admins
  "created_at": "2026-04-22T16:00:00Z",
  "last_active": "2026-04-22T16:05:12Z",
  "absolute_expires_at": "2026-04-29T16:00:00Z",
  "ip": "203.0.113.7",
  "user_agent": "Mozilla/5.0 ..."
}
```

### 4.3 Cookie

| Flag | Value | Justification |
|---|---|---|
| Name | `admin_session` | distinct from portal |
| Value | `itsdangerous.URLSafeSerializer(secret).dumps(session_id)` | signed; tampering detected on deserialize |
| `HttpOnly` | `True` | blocks JS access → mitigates XSS token theft |
| `Secure` | `True` | HTTPS-only (admin panel never served over HTTP in non-dev) |
| `SameSite` | `Lax` | allows the cookie on OAuth-callback redirects (Strict would block them); non-navigation CSRF is handled by the double-submit token below |
| `Path` | `/` | full-domain |
| `Max-Age` | not set | session cookie; Redis TTL is the source of truth |

### 4.4 Lifecycle

| Event | Action |
|---|---|
| Login success | Generate new session UUID; SET Redis `session:<uuid>` with TTL 86400; SADD `session:user:<id>` uuid; set cookie; write `admin_login` audit event (outcome=success). |
| Any authenticated request | Refresh `last_active`; re-set Redis TTL to 86400 if `(now - created_at) < 7d`; otherwise reject + force re-login. |
| Logout | DEL `session:<uuid>`; SREM `session:user:<id>` uuid; clear cookie; audit `admin_logout`. |
| Revoke-all-for-user | For each uuid in `session:user:<id>`, DEL `session:<uuid>`; DEL the set. |
| Role/tenant change on user | Revoke-all-for-user (forces re-login with updated claims). |

### 4.5 Failure modes

- **Redis down:** Admin panel hard-fails with a clear 503 "auth store unavailable." No silent fall-back to the old fake cookie. The perf baseline already treats Redis as required for rate limits, so this is the same availability class.
- **Secret rotation:** `SESSION_SIGNING_SECRETS` is a comma-separated list (first = active, rest = accepted for verification only). Weekly rotation via config reload; old secret removed after one TTL max (7d).

---

## 5. Role & Enforcement Layer (per Decision 2)

### 5.1 Keep the hierarchy

`USER < PROJECT_ADMIN < TENANT_ADMIN < ADMIN` (from `backend/app/domain/rbac.py`). No renames, no additions in this phase.

### 5.2 Decorators — default-deny

```python
from app.auth.decorators import require_role, scope_to_tenant, cross_tenant, public
from app.domain.rbac import Role

@router.get("/admin/projects")
@require_role(min_role=Role.TENANT_ADMIN)
@scope_to_tenant
async def list_projects(
    session: AdminSession = Depends(get_admin_session),
    tenant_id: str = Depends(current_tenant),  # injected by @scope_to_tenant
):
    return await project_service.list(tenant_id=tenant_id)
```

Rules enforced by a FastAPI startup hook that introspects all routes:

| Rule | Enforcement |
|---|---|
| Every `/admin/*` endpoint has exactly one of `@require_role`, `@public`, `@cross_tenant` | Startup hook fails boot if missing — crash early, never silently insecure. |
| Every data-returning `/admin/*` endpoint has `@scope_to_tenant` OR `@cross_tenant` | Same startup hook. |
| `@cross_tenant` is super-admin-only AND every call emits an audit event | Built into the decorator. |
| `@public` allowed only on well-known paths (health, login, OAuth callbacks) | Whitelist — boot fails if used elsewhere. |

### 5.3 `@scope_to_tenant` semantics

Reads `session.tenant_id`. If non-null, injects `tenant_id` as a dependency consumed by the service. If null (super_admin), handler still gets `tenant_id=None` — but the **service layer receives `None` as a sentinel and must filter accordingly**. Services that call `filter_by_tenant(tenant_id=None)` return empty, not all-tenants; super admins needing cross-tenant data must use `@cross_tenant`.

This makes "see all tenants" an explicit, audited, reviewable choice — not an accidental missing WHERE.

### 5.4 `@cross_tenant` semantics

```python
@router.get("/admin/tenants")
@require_role(min_role=Role.ADMIN)   # super-admin only
@cross_tenant(reason="tenant roster is platform-wide by design")
async def list_tenants(...):
    ...
```

The `reason` string is captured into the audit event so reviewers can evaluate intent.

### 5.5 Existing handlers — migration

Every current handler in `admin.py`, `admin_dlp.py`, `admin_ai_review.py`, `admin_dashboard.py`, `admin_portal_users.py`, `admin_system_settings.py`, `admin_token_packs.py` is tagged with the right decorator set. Grep-assisted pass; boot-time check catches missing tags.

---

## 6. OAuth Flow (per Decision 3)

### 6.1 Library

`authlib==1.3.2` — batteries-included, owns PKCE, state, JWKS caching, id_token verification. Light enough that we're not locking into heavyweight middleware.

### 6.2 Flow

```
┌──────────┐                     ┌─────────────┐                    ┌──────────┐
│  Browser │                     │  FastAPI    │                    │ Provider │
└────┬─────┘                     └──────┬──────┘                    └────┬─────┘
     │ GET /admin/auth/oauth/start?provider=google   │                   │
     ├────────────────────────────────────────────►  │                   │
     │                            │ SET oauth_state:<state> in Redis    │
     │                            │ {verifier, return_to, exp=now+5min} │
     │ 302 → provider authorize URL (with code_challenge, state)        │
     │ ◄────────────────────────  │                                      │
     │                            │                                      │
     │  GET provider/authorize    │                                      │
     ├────────────────────────────┼────────────────────────────────────► │
     │                            │                                      │
     │  302 → callback?code=&state=                                      │
     │ ◄────────────────────────────────────────────────────────────────┤
     │                            │                                      │
     │  GET /admin/auth/oauth/callback?code=&state=                     │
     ├────────────────────────────►│                                      │
     │                            │ GET oauth_state:<state>             │
     │                            │ - must exist → CSRF check           │
     │                            │ DEL oauth_state:<state> (one-shot)  │
     │                            │                                      │
     │                            │ POST provider/token                  │
     │                            │  code + code_verifier                │
     │                            ├────────────────────────────────────► │
     │                            │ ◄── id_token + access_token ────────┤
     │                            │                                      │
     │                            │ Verify id_token signature (JWKS)     │
     │                            │ Verify aud, iss, exp, nonce          │
     │                            │ Find-or-create AdminUser in DB       │
     │                            │ Create session (§4)                  │
     │                            │                                      │
     │ 302 → return_to (+ Set-Cookie: admin_session=...)                 │
     │ ◄────────────────────────  │                                      │
```

### 6.3 Key choices

| Choice | Value |
|---|---|
| PKCE | Required. `code_verifier = secrets.token_urlsafe(64)`; `code_challenge = base64url(sha256(verifier))`; method=S256. |
| State TTL | 300 s (5 min), one-time use, deleted on first callback. |
| Nonce | Set in authorize request, verified in id_token. |
| JWKS | `authlib` defaults — respects provider `Cache-Control`, default TTL 10 min. |
| id_token `aud` | Must equal our client_id. |
| id_token `iss` | Must match expected provider issuer. |
| `return_to` | URL-whitelisted — only `ADMIN_UI_ORIGIN` prefixes. Blocks open-redirect. |

### 6.4 Provider matrix

| Provider | Endpoint config | Env vars |
|---|---|---|
| Google | OpenID discovery doc | `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` |
| GitHub | hand-specified URLs (no OIDC) — `authorize_url`, `access_token_url`, `userinfo_endpoint` | `OAUTH_GITHUB_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_SECRET` |

Both flows land in the same `/admin/auth/oauth/callback` handler, branching on `provider` from the state payload.

### 6.5 Portal OAuth (P0-8)

Same flow applied to `/portal/auth/oauth/*` — portal keeps JWT issuance but adds state+PKCE verification at the callback. This is an additive fix; the flat `POST /portal/auth/oauth/callback` that currently trusts caller-supplied `oauth_id` is deprecated and removed.

---

## 7. Cookie & Security Strategy

Addresses the 12 security-discipline items Beat listed. Each is either implemented below or explicitly declined with reason.

| # | Item | Status | Where |
|---|---|---|---|
| 1 | HttpOnly, Secure, SameSite=Lax cookie flags (Lax vs Strict justified) | ✅ | §4.3 |
| 2 | CSRF for state-changing admin endpoints | ✅ | Double-submit cookie: server sets non-HttpOnly `csrf_token` at login (random 32-byte URL-safe); middleware requires `X-CSRF-Token` header on POST/PUT/PATCH/DELETE to match the cookie. Rejected (=401) if missing or mismatched. Middleware bypass list: `/admin/auth/login`, `/admin/auth/oauth/*`. |
| 3 | Login rate limit + account lockout | ✅ | Redis key `login_attempts:<email>` — increment on failed attempt, TTL 900 s (15 min). After 5 failures, return 429 with `Retry-After`. On successful login, DEL the key. Per-IP additional limit via existing `RateLimitService` bucket `admin_login_ip:<ip>` (20/min). |
| 4 | Password hashing | 🟡 Keep bcrypt | bcrypt cost 12 already in use (`api_key_service.py`, `portal/auth_service.py`). argon2id considered; migration risk + backfill cost outweigh benefit for this phase. Documented in risk register (§16) so Phase 2A+N can revisit. |
| 5 | Session fixation prevention | ✅ | New session UUID generated on every login. No pre-auth session reused. Logout fully deletes server-side entry (§4.4). |
| 6 | Logout invalidates server-side | ✅ | §4.4 — DEL the Redis key, not just cookie clear. |
| 7 | Timing-attack mitigation on login | ✅ | Always run bcrypt.checkpw even when user not found (use a cached dummy hash); uniform 401 "Invalid email or password" for wrong-pw-vs-nonexistent; no branching response time longer than ~1 ms. |
| 8 | No secrets in logs | ✅ | `structlog` processor redacts keys matching `(?i)(session|password|token|secret|authorization|cookie)` from the extra dict before emit. Session IDs never logged raw — only a hash prefix for correlation. |
| 9 | JWKS caching | ✅ | authlib default (provider Cache-Control respected, 10 min floor). |
| 10 | OAuth state TTL + one-time use | ✅ | §6.3: 5 min TTL, DEL on callback. |
| 11 | Admin audit log captures failed auth | ✅ | Outcome field added to `admin_audit_log` (§10). Failed logins emit `admin_login_failed` with email, ip, user_agent. |
| 12 | No rolling own crypto | ✅ | `itsdangerous` for cookie signing, `bcrypt` existing, `authlib` for OAuth + JWKS. No hand-written HMAC/AES. |

---

## 8. Tenant Scoping Middleware

Implementation sketch:

```python
# backend/app/auth/decorators.py
from functools import wraps
from fastapi import Request

def scope_to_tenant(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        request: Request = kwargs["request"]  # FastAPI injects
        session = request.state.admin_session
        # Super admins must use @cross_tenant explicitly to see all tenants
        kwargs["tenant_id"] = session.tenant_id  # may be None
        return await fn(*args, **kwargs)
    wrapper.__scope_to_tenant__ = True
    return wrapper

def cross_tenant(reason: str):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs["request"]
            session = request.state.admin_session
            if session.role != "super_admin":
                raise AuthorizationError("cross-tenant access denied")
            emit_audit(
                actor=session.user_id,
                action="cross_tenant_access",
                resource_type=fn.__qualname__,
                details={"reason": reason, "path": request.url.path},
            )
            return await fn(*args, **kwargs)
        wrapper.__cross_tenant__ = True
        return wrapper
    return decorator
```

**Startup enforcement** — in `app/main.py` after router registration:

```python
for route in app.routes:
    if route.path.startswith("/admin"):
        fn = route.endpoint
        has_scope = getattr(fn, "__scope_to_tenant__", False)
        has_cross = getattr(fn, "__cross_tenant__", False)
        has_public = getattr(fn, "__public__", False)
        if not (has_scope or has_cross or has_public):
            raise RuntimeError(
                f"SECURITY: admin route {route.path} lacks @scope_to_tenant / "
                f"@cross_tenant / @public. Refusing to start."
            )
```

Boot fails loudly on any untagged admin route. This is the whole point — accidental "missing WHERE" cannot ship.

### Service-layer change

Every admin service method that takes optional `tenant_id: str | None` must interpret `None` as "empty filter → return empty result set," not "no filter." One-line audit across `project_service.py`, `tenant_service.py`, `api_key_service.py`, `request_log_service.py`, `usage_service.py`. Unit tests (§11) pin the `None`-means-empty contract.

---

## 9. Migration Path

### 9.1 Existing admin "sessions"

The current `sran_demo_role` cookie is client-set and trivially forgeable. On the Phase 2A deploy:

1. Admin auth middleware attempts to read the new `admin_session` cookie.
2. If absent → 401 → admin UI redirects to `/admin/login` (new).
3. Any stale `sran_demo_role` cookie is explicitly deleted on the first response.

No "seamless migration" is offered — every admin must re-authenticate. Because today's "authentication" is fictitious, no actual security posture is degraded by forcing re-login.

### 9.2 Removing `NEXT_PUBLIC_ADMIN_API_KEY`

| Step | Action |
|---|---|
| 1 | `admin-ui/lib/api.ts` — delete the `ADMIN_KEY` read and the Bearer header set; replace `fetch(url, opts)` with `fetch(url, {...opts, credentials: 'include'})` and add `X-CSRF-Token` header from the `csrf_token` cookie. |
| 2 | `admin-ui/next.config.js` / Dockerfile build args — remove `NEXT_PUBLIC_ADMIN_API_KEY` and the corresponding build arg. |
| 3 | `docker-compose.yml` — same. |
| 4 | Grep the whole repo for `NEXT_PUBLIC_ADMIN_API_KEY` — delete every remaining reference. |
| 5 | Add CI check: fail build if any `NEXT_PUBLIC_*` env var name contains `KEY`, `SECRET`, `TOKEN`, or `PASSWORD`. |

Step 5 closes the door for good — no future bundle can accidentally leak a key.

### 9.3 Rollout

| Environment | Strategy |
|---|---|
| Dev | Hard cutover. Docs updated. Engineers re-authenticate. |
| Staging | Hard cutover. Smoke test suite runs — must include the Phase 1.4 replay probes (see §11). |
| Prod | **Not applicable yet** — no production customer is on the admin panel (SaaS launch is 12 months out; admin-UI today is internal + demo). Whenever prod exists, follow the staging pattern. Because today's auth is fictitious, there is no prior session to preserve. |

Because there is no prior real auth, there is no prior session state to migrate forward. This is a one-way door; document as such.

### 9.4 Rollback plan

There is no meaningful rollback to "old real auth" because there was no real auth before this. Rollback options in order of preference:

1. **Forward-fix** (always preferred): if a bug is found, patch the new code.
2. **Revert the Phase 2A commits** — admin panel is fully down (authentication is impossible) until forward-fix. Data is safe.
3. **Emergency "pin open"** — feature flag `ADMIN_AUTH_BYPASS=1` (dev-only) that allows a well-known bearer token. Never on for prod. Implement as a sealed env-var check that logs a loud warning every request.

Option 3 documented here so it exists if needed; flag it for deletion once Phase 2A is stable.

---

## 10. Audit Logging

### 10.1 Schema — extend `admin_audit_log`

The existing table (migration 007) has: `id, timestamp, actor_key_id, tenant_id, action, resource_type, resource_id, details`.

Migration 028 adds four columns:

| Column | Type | Why |
|---|---|---|
| `outcome` | VARCHAR(20) | `success` / `failure` / `denied` — Phase 2A needs failed-login tracking (checklist #11). |
| `ip_address` | INET | Forensics. |
| `user_agent` | TEXT | Forensics. |
| `session_id_prefix` | VARCHAR(16) | First 16 chars of session UUID — correlates audit rows to a session without leaking the full ID. |

### 10.2 Events captured

| Event | `action` | Trigger |
|---|---|---|
| Successful login | `admin_login` | POST /admin/auth/login 200 |
| Failed login | `admin_login_failed` | POST /admin/auth/login 401/429 (outcome=failure) |
| Logout | `admin_logout` | POST /admin/auth/logout |
| Session revoked (by admin action) | `admin_session_revoke` | Admin revokes own or another user's session |
| Cross-tenant access | `cross_tenant_access` | `@cross_tenant` decorator |
| Role change | `admin_role_change` | Admin updates another admin's role |
| OAuth start | `admin_oauth_start` | /admin/auth/oauth/start (with provider) |
| OAuth callback success | `admin_oauth_callback` | Issues session |
| OAuth callback failure | `admin_oauth_callback_failed` | Bad state / PKCE / id_token |
| Every CUD on admin resources | Existing per-resource actions (unchanged) | Existing code, now always with `outcome` set |

### 10.3 Retention

Out of scope for Phase 2A beyond "don't delete." Retention policy is a SOC 2 design decision for a later phase. Table has no retention script today.

---

## 11. Test Plan

### 11.1 Unit (fast, pytest, no Redis)

- `tests/auth/test_session_manager.py` — create, read, extend, revoke, revoke-all-for-user, expire-after-absolute-max, with `fakeredis`.
- `tests/auth/test_decorators.py` — `@require_role` at each role level; `@scope_to_tenant` injects correct tenant; `@cross_tenant` rejects non-super-admin; `@public` bypass; startup boot-check fails on untagged route.
- `tests/auth/test_csrf.py` — double-submit accept/reject matrix; bypass list respected.
- `tests/auth/test_oauth_flow.py` — state creation+verification, PKCE challenge/verifier, JWKS verification (mocked), open-redirect guard on `return_to`.
- `tests/auth/test_password_timing.py` — 100 runs of "nonexistent user" vs "wrong password": response-time CV < 10%, same error message.

### 11.2 Integration (medium, pytest, real Postgres + real Redis via audit stack)

Replay every Phase 1.4 exploit against the new stack. Each must flip from PASS (exploit works) to FAIL (exploit blocked):

| Phase 1.4 finding | New assertion |
|---|---|
| P0-1 | `Cookie: sran_demo_role=admin` on `/admin/tenants` → 401 (old fake cookie ignored). |
| P0-2 | Scan `admin-ui/.next/static/**` for `NEXT_PUBLIC_ADMIN_API_KEY` usage → 0 matches. |
| P0-5 | Login as tenant_admin of acme-001 → `GET /admin/requests` — returns only acme-001 rows. |
| P0-6 | Same as P0-5 for `/admin/projects` + `/admin/api-keys`. |
| P0-8 | `POST /portal/auth/oauth/callback` with tampered state → 401; with missing PKCE verifier → 401. |
| P1-14 | 10 rapid wrong-password logins from same IP → 6th returns 429 with `Retry-After`. |

### 11.3 E2E (slow, optional Playwright; if unavailable, curl sequences)

- Full admin login → action → logout cycle. New cookie set, redis session exists after login, deleted after logout.
- OAuth login (against a mocked provider at http://localhost:9091) → session created → subsequent /admin/tenants works.
- Session expires after 24h idle → next request redirects to login.
- Revoke session → next request → 401.

### 11.4 Coverage gate

No new admin route ships without at least one test that exercises the full cookie → handler path. CI task in `Makefile` runs `pytest -m auth` before merge.

---

## 12. Dependencies Added

| Package | Version | Purpose |
|---|---|---|
| `itsdangerous` | `2.2.0` | sign session UUIDs into cookie value |
| `authlib` | `1.3.2` | OAuth client with PKCE + JWKS + id_token verify |
| `fakeredis` | `2.26.1` | unit-test Redis fake (dev-dep only) |

`redis`, `bcrypt`, `structlog`, `httpx` — all already present.

---

## 13. Config / Env Vars

Added to `.env.example`:

```dotenv
# ---- Admin Auth (Phase 2A) ----
# Comma-separated list: first = active signing secret, rest = accepted for verification.
# Rotate weekly; old secret removed after one absolute-session-max (7d).
# Minimum 32 random bytes; generate with: python -c 'import secrets;print(secrets.token_urlsafe(48))'
SESSION_SIGNING_SECRETS=dev-admin-session-signing-secret-not-for-prod

# Absolute origin of the admin UI; used for CORS + cookie Domain + OAuth return_to whitelist.
ADMIN_UI_ORIGIN=http://localhost:3000

# OAuth provider credentials.
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_GITHUB_CLIENT_ID=
OAUTH_GITHUB_CLIENT_SECRET=

# Session-manager tuning (seconds).
ADMIN_SESSION_IDLE_TTL=86400       # 24h
ADMIN_SESSION_ABSOLUTE_TTL=604800  # 7d
ADMIN_LOGIN_LOCKOUT_THRESHOLD=5
ADMIN_LOGIN_LOCKOUT_WINDOW=900     # 15m
```

`NEXT_PUBLIC_ADMIN_API_KEY` is **removed** from `admin-ui/.env.example` and `docker-compose.yml`. Removal enforced by CI check (§9.2 step 5).

---

## 14. Backward Compat + Breaking Changes

**Breaking for anyone currently using the admin panel:**

1. Old `sran_demo_role` cookie ignored — all admins re-login.
2. Old `NEXT_PUBLIC_ADMIN_API_KEY` env var removed — build breaks if it's still referenced. Error message points at this doc.
3. `/auth/login` JWT endpoint — kept for now (no-op deprecation), because it's the only working login until the new `/admin/auth/login` ships. After Phase 2A lands the admin UI uses only `/admin/auth/login`, and `/auth/login` is marked `@deprecated` in OpenAPI with removal date in the next phase.

**Non-breaking:**

1. Portal users — zero change. JWT flow unchanged; same cookies, same headers, same endpoints.
2. Public `/v1/*` API-key auth — unchanged; SDKs / gateway clients unaffected.
3. Existing admin audit log rows — columns added as nullable; historical queries still work.

---

## 15. Effort Estimate

Beat's phase plan allotted 2-3 days. My honest estimate is higher.

| Subsystem | Hours |
|---|---|
| Session manager + Redis store + unit tests | 6 |
| `/admin/auth/login` endpoint + rate-limit + audit | 4 |
| Cookie middleware + CSRF double-submit | 5 |
| Role + tenant-scope decorators + startup boot-check | 6 |
| Apply decorators across all 40+ admin handlers | 4 |
| OAuth flow (Google + GitHub) + state/PKCE/JWKS + audit | 10 |
| Portal OAuth callback hardening (P0-8) | 3 |
| Admin-UI rewrite (login page, remove NEXT_PUBLIC key, cookie-based requests, CSRF header) | 8 |
| Integration tests replaying Phase 1.4 exploits | 6 |
| Migration 028 for audit-log columns + code wiring | 2 |
| Docs, `.env.example`, runbook, CI checks | 3 |
| Contingency / debugging | 10 |
| **Total** | **~67h** |

**That's ~4 calendar days at 16h/day, or ~8 days at an 8h/day cadence.** The "2-3 days" target is likely undershooting the OAuth + admin-UI-rewrite cost. Two ways to fit it:

- **Option A (recommended):** accept ~5-day scope. Phase 2A ships complete. Phase 2B waits one extra day.
- **Option B:** split into **2A.1** (session + decorators + tenant scoping + login + admin-UI cookie wiring — ~35h) and **2A.2** (OAuth server-side + portal OAuth hardening + full exploit replay test suite — ~30h). P0-1, P0-2, P0-5, P0-6, P1-14 close in 2A.1; P0-8 closes in 2A.2.

**My recommendation: Option A.** Splitting the auth surface across two PRs doubles the integration risk for ~0 saved days. If Beat prefers Option B because of the 2-3 day cadence, it's viable — just costlier overall.

Flagging this here instead of silently adjusting scope. If Beat says "just do 2-3 days," I'll cut scope to 2A.1 only and re-scope 2A.2 as a follow-up — not hide the OAuth gap.

---

## 16. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Redis outage makes admin panel unusable | Low (Redis uptime is high in our stack) | Medium (admin actions blocked) | Hard-fail with clear 503; add Redis to status dashboard; document runbook: if Redis truly down, use DB-direct emergency console |
| R2 | CSRF double-submit misimplemented → admin UI can't mutate | Medium during dev | High (admin actions return 401) | Integration test specifically covers the CSRF happy path on every mutation verb; rollout has a smoke-test gate |
| R3 | CORS misconfig + cookie `Secure` flag on localhost:3000 | Medium (local dev quirk) | Low (only blocks dev) | `APP_ENV=development` turns `Secure` off; documented in `RUNBOOK.md` |
| R4 | bcrypt kept, argon2id desired later for SOC 2 | Low immediate | Low-medium long-term | Documented as Phase 2A+N; migration path: `password_hash` column stays TEXT so algorithm can be swapped per-row |
| R5 | Staged vs prod deploy out-of-sync → admins can't log in | Medium | High (admin down) | Admin-UI + backend release as a single coordinated deploy; release checklist lists both artifacts |
| R6 | `return_to` whitelist too strict blocks legit redirects | Low | Low (users hit login page twice) | Whitelist is env-driven; accepts subpath prefixes under `ADMIN_UI_ORIGIN` |
| R7 | `authlib` version regression | Low | Medium | Pin exact version; upgrade path documented in `requirements.txt` comment |
| R8 | Boot-time decorator check rejects existing endpoint accidentally | Medium during rollout | High (service won't start) | CI runs boot on a fresh container before merge; failure prints the offending route clearly |
| R9 | Session hijacking via XSS if admin UI ever slips a `dangerouslySetInnerHTML` | Low | High | `HttpOnly` cookie blocks session theft; CSP header on admin UI caps the damage; document as a standing review item |
| R10 | Feature flag `ADMIN_AUTH_BYPASS` accidentally on in prod | Low | Catastrophic | Env-var gating requires `APP_ENV=development` AND the flag; each request logs a loud warning; flag deleted after Phase 2A stable |

---

## Out of Scope for Phase 2A (flagged as "Phase 2A+N")

As Beat specified:

- SAML / OIDC enterprise SSO (SOC2-required later)
- 2FA / TOTP
- Magic-link login
- Session management UI (list active sessions, revoke individual) — Phase 3 feature
- API key rotation UI — Phase 3 feature
- Password reset flow — verify whether an endpoint exists (PHASE1.4 N5 showed `POST /portal/me/password` → 404; admin `/auth/forgot` not present). If missing, Phase 2C.
- argon2id migration — see R4.
- Audit-log retention policy + GDPR erasure — SOC 2 deliverable, later.

---

## Decisions Required from Beat Before I Write Code

1. **Effort option (§15):** Option A (~5 days, ship complete) or Option B (split 2A.1 + 2A.2)?
2. **Password reset flow:** bundle into Phase 2C, or earlier? My vote — Phase 2C, since admin passwords can be rotated by super-admin in DB for the duration.
3. **Emergency bypass flag (§9.4 option 3):** implement it as a safety net, or not? Having it as documented-and-deletable is my preference; zero-chance-of-accidental-use is Beat's call.
4. **Drop `/auth/login` in this phase or later?** Current plan: keep as deprecated, remove in Phase 2B or later. Alternative: remove now since nothing in admin-ui uses it after 2A — but external tooling might.

Reply with answers to these four plus a global approve/reject on the design. On approval I start with the session-manager unit tests and work outward (TDD), keeping each subsystem in its own commit so a single problem is reversible.

---

**Audit stack still running.** Will leave it up through Phase 2A implementation so the integration tests in §11.2 have a real Postgres + Redis to hit.
