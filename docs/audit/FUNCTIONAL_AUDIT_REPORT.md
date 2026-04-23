# AI Gateway — Phase 1 Functional Audit Report

> **Status:** Static-analysis pass complete. Live functional testing (Phase 1.4) is **gated on Beat's approval** of these findings.
> **Scope:** Admin panel + end-user portal + backend API. Perf/baseline work is explicitly out of scope.
> **Date:** 2026-04-22
> **Git SHA:** `e0d7af0` (dirty — matches smoke baseline)

---

## Executive Summary

| Metric | Count |
|---|---|
| UI pages inventoried | **32** (admin: 18, portal: 14) |
| Backend HTTP endpoints inventoried | **~80** across 15 router files |
| Distinct entities | **~20** |
| P0 blockers (launch-blocking bugs / security holes) | **8** |
| P1 high-priority bugs | **11** |
| P2 medium-priority bugs | **12** |
| P3 low-priority / polish | **9** |
| Missing features for SaaS beta | **14** (4 table-stakes, 10 competitive) |

**Top-line verdict — NOT SaaS-launch-ready.**

Three structural findings make this the wrong state for beta customers *regardless* of the performance work already underway:

1. **Admin authentication is a client-side facade.** Admin-UI login calls no backend — it writes a plain, unsigned cookie (`sran_demo_role=admin`) and uses a `NEXT_PUBLIC_ADMIN_API_KEY` env var as a static Bearer token embedded in the browser JS bundle. Any visitor to the admin origin can read the admin token from devtools and impersonate the super admin. The `/auth/login` backend endpoint exists but is orphaned — **no page in the admin UI calls it**. This is a single P0 that invalidates every downstream admin-side control.
2. **Portal tenancy is collapsed to one tenant.** `POST /portal/projects` hard-codes `tenant_id = "demo-tenant-000"` for every portal user. The multi-tenant data model exists but the portal signup flow never creates a tenant — all customers end up sharing a single tenant row. Multi-tenant isolation is structurally not possible from the portal today. Any cross-customer isolation reasoning downstream is moot.
3. **Paid conversions do not work.** `POST /portal/billing/purchase` returns HTTP 501 "payment_not_implemented". `/pricing` marketing page is hand-written HTML (out of sync with `/billing` which fetches from DB). There is no Stripe integration anywhere. Revenue path is a dead end.

Beat — before approving any fix batch, please confirm the SaaS launch plan still assumes (a) a separate real auth system will replace the demo cookie, (b) portal signup will create a tenant-per-user or tenant-per-org, and (c) Stripe is a separate workstream. If yes to all, these three items and the rest below sort into a reasonable fix plan. If any assumption is wrong, scope needs to change.

---

## UI Inventory

### Admin panel (`admin-ui/`, 18 pages)

| Path | Purpose | Primary entity | Calls |
|---|---|---|---|
| `/login` | **Fake** client-side login (3 hardcoded demo users) | n/a | none (writes cookie only) |
| `/` | Dashboard — KPI cards, volume chart, top models, live feed | Dashboard agg | `/admin/dashboard/{overview,volume,top-models,recent-requests}` |
| `/tenants` | Tenant CRUD + region/status | Tenant | `GET/POST/PUT/DELETE /admin/tenants[/{id}]` |
| `/projects` | Project CRUD, policy assignment, tenant donut | Project | `GET/POST/DELETE /admin/projects[/{id}]` (no PUT UI — see gap) |
| `/providers` | Provider API-key mgmt with live test | ProviderKey | `GET/POST/DELETE /admin/providers/**/keys`, test endpoint |
| `/models` | Model registry browser (read only) | Model | `GET /admin/models` |
| `/policies` | DLP profiles + detectors + NER control (3 tabs) | PolicyProfile | `GET/PATCH /admin/policy-profiles/**`, `/admin/dlp/detectors`, `/admin/ner/status`, `/admin/settings` |
| `/routing` | Keyword routing rules + stats | KeywordRule | `GET/POST/PUT/DELETE /admin/keyword-rules[/{id}]`, `/admin/routing/stats` |
| `/api-keys` | API key CRUD grouped by project | ApiKey | `GET/POST/DELETE /admin/api-keys[/{id}]` |
| `/audit` | Request trail with CSV export | RequestLog | `GET /admin/requests?limit=1000`, `/admin/requests/{id}` |
| `/requests` | Request log with 24h sparkline, filters, detail drawer | RequestLog | `GET /admin/requests`, `/admin/requests/{id}` |
| `/usage` | Dual tabs: user traffic vs AI-review cost | RequestLog + AiReviewScanRun | `/admin/projects`, `/admin/usage/summary`, `/admin/ai-review/scan-runs/*` |
| `/alerts/events` | Alert event list with severity badges | AlertEvent | `GET /admin/alert-events?limit=50` (+ request detail) |
| `/dlp-coverage` | Detector catalog + quick-test | Detector | `GET /admin/dlp/coverage`, `POST /admin/dlp/test` |
| `/ai-review` | AI sensitive-data reviewer config + findings | AiReviewSettings/Finding | `GET/PATCH /admin/ai-review/*`, `POST scan/test-connection`, PATCH findings |
| `/portal-settings` | Portal config (General/Trial/Security-stub) | SystemSetting | `GET/PATCH /admin/system-settings[/{key}]` |
| `/portal-users` | Portal user list, credits, suspend/activate/top-up | PortalUser | `GET /admin/portal-users`, `POST suspend/activate/topup` |
| `/portal-token-packs` | Token pack CRUD | TokenPack | `GET/POST/PATCH/DELETE /admin/token-packs[/{id}]` |

### End-user portal (`portal/`, 14 pages)

| Path | Group | Purpose | Auth |
|---|---|---|---|
| `/` | marketing | Landing page | public |
| `/pricing` | marketing | Hard-coded pack tiers + FAQ | public |
| `/security` | marketing | Compliance page | public |
| `/privacy` | marketing | Privacy policy | public |
| `/terms` | marketing | TOS | public |
| `/docs` | marketing | **Stub** — "on the way" + snippet | public |
| `/login` | auth | Email/password + OAuth | public (redirects to `/dashboard` if logged) |
| `/signup` | auth | Email/password + OAuth | public |
| `/oauth-complete` | auth | OAuth callback exchange | public |
| `/dashboard` | app | Balance, stats, recent activity | portal JWT |
| `/projects` | app | Project list + CRUD | portal JWT |
| `/projects/[id]` | app | Project detail + API-key CRUD | portal JWT |
| `/billing` | app | Balance + token-pack purchase (**501**) + tx history | portal JWT |
| `/usage` | app | Full request log + filter + chart stub | portal JWT |
| `/settings` | app | Profile, avatar; password/MFA/delete **stubs** | portal JWT |

**No end-user portal beyond the tenant portal.** The user's prompt asked "End-user portal (ถ้ามี — check if exists)" — the portal is the only user-facing surface. There is no third UI.

---

## API Inventory (condensed)

Full per-endpoint detail was produced as part of the Phase 1.2 inventory and is preserved in the agent tool-result file; below is the condensed shape for the CRUD matrix.

### Router mount table (`backend/app/main.py`)

| Prefix | File | Auth dep |
|---|---|---|
| (none) | `health.py` | public |
| `/auth` | `auth.py` | public + bearer JWT for `/me` |
| `/v1` | `public_chat.py`, `public_models.py` | `get_resolved_api_key` (gw_xxx or admin JWT) |
| `/admin` | `admin.py` (+ sub-routers: `admin_dlp`, `admin_ai_review`, `admin_dashboard`) | `require_admin` |
| `/admin/portal-users` | `admin_portal_users.py` | `require_admin` |
| `/admin/system-settings` | `admin_system_settings.py` | `require_admin` |
| `/admin/token-packs` | `admin_token_packs.py` | `require_admin` |
| `/portal/auth` | `portal_auth.py` | public |
| `/portal/me` | `portal_me.py` | `get_portal_user` |
| `/portal/projects` | `portal_projects.py` | `get_portal_user` |
| `/portal/billing` | `portal_billing.py` | `get_portal_user` |

### Auth model in one picture

- **Admin JWT** — signed with `settings.jwt_secret`; issued by `POST /auth/login`. **Not used by admin-UI.**
- **Admin "key"** — `NEXT_PUBLIC_ADMIN_API_KEY` env var embedded in browser bundle; sent as `Authorization: Bearer …` by every admin-UI request. Treated as a gw_* API key with super-admin role, or as a JWT — both paths resolve to `require_admin`.
- **Portal JWT** — signed with `settings.portal_jwt_secret`; issued by `/portal/auth/{signup,login,refresh,oauth/callback}`.
- **API key (gw_xxxx)** — raw key issued once on create; stored as SHA-256 hash; resolved to `ResolvedApiKey{tenant_id, project_id, role}`.

---

## Entity CRUD Matrix

Legend: ✅ exists | ❌ missing | 🟡 partial/stub | 🔒 role-gated | 🔥 red flag

| Entity | Scope | C-UI | C-API | R-UI | R-API | U-UI | U-API | D-UI | D-API | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **AdminUser** | admin | ❌ | 🟡 (seed only) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | No UI to create/list/manage admins. Single seeded admin/admin1234 in code. |
| **Tenant** | admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ soft | ✅ | **Portal signup doesn't create a tenant — hard-coded `demo-tenant-000`.** |
| **Project (admin)** | admin | ✅ | ✅ | ✅ | ✅ | **❌** | ✅ (PUT) | ✅ | ✅ hard | 🟡 | Admin UI has no edit/rename flow — API exists but UI never exposes it. |
| **Project (portal)** | portal | ✅ | ✅ | ✅ | ✅ | ✅ rename | ✅ PATCH | ✅ | ✅ hard | ✅🔥 | `description` field sent but not persisted (silent drop). Hard-codes tenant_id. |
| **PortalUser (admin)** | admin | ❌ | ❌ | ✅ | ✅ | 🟡 suspend/activate/topup only | 🟡 | ❌ (no delete) | ❌ | 🟡 | No admin-side user creation, no email edit, no hard-delete. Suspend doesn't revoke keys. |
| **PortalUser (self)** | portal | ✅ signup | ✅ | ✅ | ✅ `/me` | 🟡 display_name + avatar only | 🟡 | ❌ (danger zone stub) | ❌ | 🟡 | Password change, MFA, email change, account delete all stubs. |
| **ApiKey (admin)** | admin | ✅ | ✅ | ✅ | ✅ | ❌ (TODO: regenerate) | ❌ | ✅ revoke | ✅ soft | 🟡 | No rotate/regenerate flow; revoke only. |
| **ApiKey (portal)** | portal | ✅ per-project | ✅ | ✅ | ✅ | ❌ rename | ❌ | ✅ revoke | ✅ soft | 🟡 | Same gap — no rotate, no rename. |
| **Provider** | admin | 🟡 read only | 🟡 | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | 🟡 | Provider list is implicit from ProviderKey aggregation — you can't "add a new provider", you can only add keys to a known provider name. |
| **ProviderKey** | admin | ✅ + live test | ✅ | ✅ masked | ✅ | ❌ (test-only) | 🟡 test | ✅ | ✅ soft | ✅ | **Env-var fallback** when DB has no key — silent shadow path. |
| **Model** | admin | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | 🟡 | Registry is read-only — no UI or API to add a new model entry. |
| **PolicyProfile (DLP)** | admin | ❌ profile create | ✅ | ✅ | ✅ | ✅ category-action only | ✅ PATCH | ❌ | ❌ | 🟡 | Can't create new profiles from UI, can't delete profiles. |
| **Policy (legacy)** | admin | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ hard | 🟡 | Old `/admin/policies` endpoints exist but no UI uses them (**orphan API**). |
| **KeywordRule** | admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | OK. |
| **AlertRule** | admin | ❌ | ✅ | ❌ list page | ✅ | ❌ | ✅ | ❌ | ✅ hard | ❌ | **Orphan API** — full CRUD exists, no UI at all. Only alert-events page exists, which is read-only. |
| **AlertEvent** | admin | n/a | n/a | ✅ | ✅ | ❌ ack/resolve | ❌ | ❌ | ❌ | 🟡 | No acknowledge / suppress workflow. |
| **RequestLog** | admin | n/a | n/a | ✅ 2 pages | ✅ | n/a | n/a | n/a | n/a | ✅🔥 | `GET /admin/requests` **has no tenant filter** — all admins see all tenants. |
| **Feedback** | API | n/a | ✅ via `/v1/feedback` | ❌ | ❌ | n/a | n/a | n/a | n/a | 🟡 | API exists; no UI surface anywhere. |
| **AiReviewSettings** | admin | n/a | n/a | ✅ | ✅ | ✅ | ✅ PATCH | n/a | n/a | ✅ | Singleton. |
| **AiReviewScanRun** | admin | ✅ trigger | ✅ | ✅ | ✅ | n/a | n/a | ❌ | ❌ | ✅ | OK read-only history. |
| **AiReviewFinding** | admin | n/a | n/a | ✅ | ✅ | ✅ status | ✅ | ❌ | ❌ | ✅ | Status transitions only. |
| **TokenPack** | admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ soft | ✅ soft | ✅ | OK. |
| **TokenPack (portal)** | portal | n/a | n/a | ✅ | ✅ | n/a | n/a | n/a | n/a | ✅ | Read-only to purchase. |
| **CreditTransaction** | both | via top-up / purchase | ✅ topup only | ✅ | ✅ | n/a | n/a | n/a | n/a | 🟡 | Purchase path returns 501 — see P0 #3. |
| **SystemSetting** | admin | ✅ whitelist | ✅ whitelist | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | Whitelist of 4 keys; good security. |
| **OrgSettings (ner_enabled)** | admin | ✅ toggle | ✅ | ✅ | ✅ | ✅ | ✅ | n/a | n/a | ✅ | OK. |

**CRUD gaps summary:**
- **Fully missing UI, full backend:** AlertRule CRUD, legacy Policy CRUD
- **Partial UI:** Admin Project edit (API exists, UI doesn't), DLP profile create/delete, ApiKey rotation, PortalUser password/MFA/delete, Model registry CRUD, Provider entity (only keys are editable)
- **Fully missing backend:** AdminUser CRUD, Feedback read view, Portal user deletion, Team member invitations, any billing Stripe flow

---

## Bugs Ranked

### P0 — Blocks SaaS launch / security-critical

| # | Title | Evidence | Impact |
|---|---|---|---|
| **P0-1** | **Admin UI auth is a client-side fake.** `/login` does `await setTimeout(400)` then writes `sran_demo_role` cookie; no `/auth/login` call. Middleware checks cookie presence only. | `admin-ui/app/login/page.tsx:20-37`, `admin-ui/middleware.ts:19-22` | Anyone who loads the admin URL sets `document.cookie = "sran_demo_role=admin"` and has full admin UI access. |
| **P0-2** | **Admin token leaks via public JS bundle.** API client reads `NEXT_PUBLIC_ADMIN_API_KEY` and sends it as Bearer to every request. `NEXT_PUBLIC_*` envs are compiled into the browser bundle. | `admin-ui/lib/api.ts:2,22-24` | Any visitor can extract admin API key from the admin-UI JavaScript bundle and call `/admin/*` directly. |
| **P0-3** | **Portal billing purchase returns 501.** `/portal/billing/purchase` rejects every purchase. No Stripe integration. | `backend/app/api/routes/portal_billing.py:53` | Zero revenue path. Users see "Contact support@sran.ai". |
| **P0-4** | **All portal users share one tenant.** `POST /portal/projects` hard-codes `tenant_id = "demo-tenant-000"`. | `backend/app/api/routes/portal_projects.py:93` (per inventory) | Multi-tenant isolation is structurally impossible via portal. Fixing this requires a data-model + signup-flow change. |
| **P0-5** | **`GET /admin/requests` has no tenant filter.** Any admin (or anyone with the leaked admin token) sees every request across every tenant. | `backend/app/api/routes/admin.py:545-605` | Cross-tenant PII leak via the `/requests` and `/audit` admin pages. |
| **P0-6** | **`GET /admin/projects` and `GET /admin/api-keys` also list all tenants** when filter omitted. | `admin.py:757,879` | Same cross-tenant exposure shape as P0-5. |
| **P0-7** | **Streaming DLP sends PII before masking.** Outbound DLP scan runs *after* SSE deltas are already on the wire. | Per `SMOKE_PERF_BASELINE_REPORT_…md` "Known caveats" line 111, confirmed in `public_chat.py` streaming path | PII generated by the upstream provider bypasses DLP on the streaming code path. |
| **P0-8** | **OAuth callback trusts the client.** `POST /portal/auth/oauth/callback` accepts `oauth_id, email, avatar_url` from the caller and issues portal JWTs without re-verifying the identity with the provider. | `portal_auth.py:85` | If the NextAuth-side logic can be bypassed or the endpoint is ever callable off-origin, anyone can assume any Google/GitHub identity. |

### P1 — High (test before sign-off)

| # | Title | Evidence |
|---|---|---|
| P1-1 | Admin UI has no Project edit form — API has PUT, UI has no trigger. | `admin-ui/app/projects/page.tsx` vs `admin.py:814` |
| P1-2 | Admin `TODO: regenerate API key` — UI commented out. | `admin-ui/app/api-keys/page.tsx:293` |
| P1-3 | AlertRule UI is entirely missing — orphan backend CRUD. | `admin.py:1004-1100` |
| P1-4 | `DELETE /admin/alerts` hard-deletes; AlertEvent FK becomes orphaned. | `admin.py:1077` |
| P1-5 | Hard-deleting a project doesn't revoke its API keys — keys remain valid. | `admin.py:839` |
| P1-6 | Suspending a portal user doesn't revoke their API keys — keys still work. | `admin_portal_users.py:192` |
| P1-7 | Portal dashboard + project detail show **all-user** usage, not per-project. Backend has no per-project filter on `GET /portal/me/usage`. | `portal/app/(app)/dashboard/page.tsx`, `portal_me.py:172` |
| P1-8 | Portal `PATCH /portal/projects/{id}` accepts `description` but never persists it (silent schema drift). | `portal_projects.py:120` |
| P1-9 | Seeded default admin creds (`admin` / `admin1234`) re-seeded on every startup. Even if you rotate, a DB re-init resets them. | `auth.py` startup seed + `_seed_admin_user()` |
| P1-10 | No API key expiry enforcement in hot path — `expires_at` column exists but isn't checked during resolution. | per inventory; worth verification |
| P1-11 | Avatar `avatar_url` accepted verbatim from OAuth callback — URL injection surface into portal UI. | `portal_auth.py:85` |

### P2 — Medium

- P2-1 Admin `GET /admin/requests` catches all exceptions and returns `error_code="query_failed"` with empty items — silent failure mode, no operator signal.
- P2-2 Hardcoded demo users (`admin / manager / viewer`) visible in committed source at `admin-ui/app/login/page.tsx:7-11`.
- P2-3 Admin-UI `/portal-settings` "Security" tab is "Coming soon" — configuration holes for portal security (signup guard, MFA toggles) go here.
- P2-4 Audit page uses N+1 pattern (`GET /admin/requests/{id}` per expand). Slow for large volumes.
- P2-5 `/pricing` marketing page is hand-written HTML; `/billing` fetches from DB. Inconsistency → stale pricing on marketing pages.
- P2-6 Pagination endpoints don't return `has_next`/`has_previous`. Clients compute from `total`.
- P2-7 Usage summary (admin) doesn't explicitly scope by tenant — depends on service layer.
- P2-8 Admin audit log is partial — `/admin/system-settings`, `/admin/policies`, etc. are not audited.
- P2-9 Portal "low credit" threshold is read server-side but also hardcoded client-side — drift on settings change.
- P2-10 `request_logs.dlp_findings_json` parse failure returns `None` silently — data-integrity loss possible.
- P2-11 Role-privacy gate hardcoded — no audit trail of who viewed masked PII.
- P2-12 `/admin/dashboard` health-check field returns `"ok"` unconditionally; never checks anything.

### P3 — Low / polish

- P3-1 Token packs: "Popular" tag inferred by `sort_order === 2` — undocumented convention.
- P3-2 Plan-label regex: `"free_trial" → "Free Trial"` works, but `"ipv4" → "Ipv4"`, etc.
- P3-3 Status bucketing on portal `/usage` is client-side regex on status strings — fragile.
- P3-4 `useCountUp` animates on every dashboard render even when user is null (no-op but wasteful).
- P3-5 `portal/app/(marketing)/docs/page.tsx` is a stub — "Documentation is on the way".
- P3-6 Portal `/usage` references `UsageChart` component that may be a stub/placeholder.
- P3-7 Admin UI has no empty-state CTA for `/ai-review` when unconfigured.
- P3-8 `/models` page has no "retry" when registry call fails — just blank.
- P3-9 Avatar upload succeeds on server but no client-side "replace" confirmation.

---

## Missing Features for SaaS Beta

### Table-stakes missing (bounce reason for beta customers)

| # | Feature | Exists? | Gap |
|---|---|---|---|
| F-1 | **Real admin authentication** (per-admin accounts, password reset, MFA) | Backend JWT exists; UI doesn't use it | Rebuild admin login flow; add `/auth/forgot` + `/auth/2fa` |
| F-2 | **Portal account deletion + password change** | Stubbed in `/settings` | Add endpoints + wire UI |
| F-3 | **Team member / org invitations** | Not present | Add `OrgMember` entity, invite flow, per-seat billing hook |
| F-4 | **Stripe billing integration** | 501 stub only | Stripe Checkout, webhook handling, invoice history |

### Competitive gaps (Portkey / Helicone / LiteLLM parity)

| # | Feature | Gap |
|---|---|---|
| F-5 | Per-user / per-project usage dashboard with charts | Backend aggregates exist, portal UI has only a chart stub |
| F-6 | Per-tenant cost alerts / budget caps visible in portal | Backend has `AlertRule`, portal has no UI |
| F-7 | API-key rotation / regeneration without revocation window | TODO'd in admin UI |
| F-8 | Audit log visible to tenant admins (not just super admin) | `admin_audit_log` not surfaced anywhere |
| F-9 | Webhook configuration UI (request events, usage alerts) | AlertRule has `webhook_url` field; no portal/admin UI config page |
| F-10 | SSO (SAML/OIDC) for enterprise tenants | Only Google/GitHub OAuth at the portal layer |
| F-11 | Rate-limit override UI per project / per tenant | Logic exists in service layer; no UI |
| F-12 | Request/response body logging toggles per key | Logged globally only; no per-key opt-in |
| F-13 | Bulk ops (bulk revoke keys, bulk delete projects, CSV export of users) | Single-record CRUD only |
| F-14 | Interactive API playground (curl + docs) | `/docs` is a stub |

---

## Data Integrity Concerns

1. `dlp_findings_json` parse failure → silent `None`. A corrupted blob is indistinguishable from "no findings". Fix: validate on write, log-and-alert on read failure.
2. Hard-delete policy / project / alert leaves dangling FKs in `request_logs` and `alert_events`. Reports may break or double-count.
3. Portal project `description` dropped on `PATCH` — data written by the client silently disappears.
4. `credit_balance` / `credit_used` updates are not wrapped in an explicit transaction boundary across `CreditTransaction` insert + user row update (needs verification; flagged for Phase 1.4).
5. Streaming handler re-opens `SessionLocal()` inside the generator because DI session is closed — any exception path risks an orphaned connection and slowly exhausts pool (ties into the existing `FINDING_DB_POOL_SATURATION.md`).

---

## Security Concerns

1. P0-1 / P0-2 above — admin auth + token leak.
2. P0-5 / P0-6 — missing tenant filters on admin list endpoints.
3. P0-8 — OAuth callback trusts caller.
4. Seeded default `admin / admin1234` credentials re-applied on startup.
5. `NEXT_PUBLIC_ADMIN_API_KEY` stored in plaintext env vars across admin-UI builds.
6. Admin-UI cookie `sran_demo_role` is the **role name** directly, not a signed token — client-set, trivially forgeable.
7. Avatar URL injection via OAuth callback (`avatar_url` written to DB, rendered as img src downstream).
8. No CSRF protection on portal mutating endpoints (cookie-auth design depends on CORS; worth verifying).
9. No audit log on admin config changes (settings, policies) — compliance concern for SOC 2.
10. No rate-limit or anti-brute-force on `/portal/auth/login` (needs verification on Phase 1.4).

---

## What's NOT in this report (deferred to Phase 1.4 live testing)

The following can only be validated by running the app against seeded data:

- Multi-tab sync / realtime update verification
- Upload → display → replace → delete avatar round-trip
- Permissions enforcement live (e.g., does `viewer` role actually get 403s?)
- Pagination on large datasets (>100 records)
- Long-string / emoji / Thai character handling
- Empty states + error toasts under real conditions
- Per-UI cascade-delete behavior (delete project → what happens to keys in UI)
- CSRF/CORS behavior
- Password policy enforcement at signup
- Rate-limit behavior under parallel requests

---

## Proposed Next Step

Beat — please triage the P0/P1 list and tell me:

1. **Which P0/P1 should land first?** My recommendation: P0-1 + P0-2 (real admin auth) must land before anything else because every other admin-side finding is gated on it. Tenant-filter fixes (P0-5 / P0-6) go next because they're surface-level query bugs and cheap.
2. **Authority to run Phase 1.4 live testing** on a fresh dev instance (backend :8090, admin-ui :3010, portal :3011). Baseline processes on 3000/3001/5432/6379/8080/9090 will not be touched.
3. **Scope call on the three structural items** (admin auth rework, portal tenancy, Stripe): are they in-scope for this audit's Phase 2/3, or do they get their own workstreams?

Once triaged, I'll produce `PHASE2_FIX_REPORT.md` in batches.
