# Deferred Work — tracked backlog

This file is the **tracked** (git-committed) home for deliberate deferrals so
they are findable in code review and don't live only in a gitignored task
board (`TASK_BOARD.md` is `.gitignore`d). Each entry records the **current
posture**, **why it is deferred** (not merely forgotten), and a concrete
**revisit-condition** that promotes it back to active work.

Deferring is a decision, not a gap. A deferral here means: the risk is
understood and bounded today, and the cost/risk of doing it now outweighs the
benefit. Do not silently "just start" a deferred item — a half-ship of any of
these is worse than the current state (see each entry's no-half-ship note).

---

## 1. Portal double-submit CSRF (defence-in-depth consistency)

**Status:** DEFERRED — Option 2 (defer correctly), Ecosystem Fix Phase 16.
**Owner surface:** `backend/app/services/portal/cookies.py` (canonical note),
`backend/app/middleware/csrf.py` (the admin mechanism this would reuse),
`backend/app/core/config.py` `validate_secrets()` (the fail-close guard).

### Current posture — the vector is already blocked

- Portal/chat session cookies are set `SameSite=Lax` (see
  `cookies._common_kwargs`). Lax withholds the cookie on cross-**site**
  state-changing requests (programmatic POST/PUT/DELETE from another
  registrable domain) — which is exactly the CSRF vector. Same-site
  cross-port calls in this stack (portal/chat → gateway) are trusted and do
  carry the cookie.
- `config.validate_secrets()` (Phase 11) **fail-closes production startup** if
  `AUTH_COOKIE_SAMESITE=none` is set without `AUTH_COOKIE_DOMAIN` + Secure. So
  there is **no silent-exposure window**: Lax cannot be voided by a
  misconfigured deploy without the process refusing to boot.
- The admin domain already uses a double-submit token (`CsrfMiddleware`, scoped
  to `/admin/*` mutations): a JS-readable `csrf_token` cookie echoed as an
  `X-CSRF-Token` header, constant-time compared. Portal parity would reuse this
  exact mechanism.

Therefore adding a portal CSRF token is **defence-in-depth consistency** with
admin-ui, **not** closing an open hole.

### Why deferred (Option 2 justification)

The change **cannot be contained to the portal** — it spans four clients and
must land atomically or it breaks installs in the field:

- **SRAN Desktop** (`AgentKeyProvider.cs`) calls mutating portal routes
  (`/portal/projects`, `/portal/projects/{id}/keys`) authenticated by
  `Authorization: Bearer <portal-JWT>` (`AuthTokenHandler`) with **no CSRF
  header**. `get_portal_user` accepts cookie-first then Bearer, so desktop is a
  Bearer caller. A naive middleware enforcing on all `/portal/*` mutations would
  **403 every desktop install**.
- **web-chat** (:3002) calls the gateway's `/portal/auth/refresh` +
  `/portal/auth/logout` cross-origin; a JS-readable `portal_csrf` cookie scoped
  to the gateway host is not readable from the web-chat origin unless
  `AUTH_COOKIE_DOMAIN`-style scoping is added for it too.

Option 1 (full atomic four-client rollout now) carries field-breakage risk that
is not justified while Lax + fail-close already block the vector. Hence Option 2:
defer, but track it as a first-class task with an explicit atomic checklist.

### Atomic-rollout checklist (all five in ONE change, else desktop 403s)

- (a) Mint a JS-readable `portal_csrf` cookie in `cookies.set_auth_cookies`
  (same Domain/Secure attrs, `httponly=False`).
- (b) Widen `CsrfMiddleware` to `/portal/` mutations, **exempting**
  `/portal/auth/*` (bootstrap — no cookie yet) and requests authenticated by
  non-cookie means (`Authorization: Bearer` / internal-trust headers), so
  desktop + service callers keep working.
- (c) Echo the cookie as `X-CSRF-Token` in `portal/lib/api.ts`.
- (d) Echo it in `apps/web-chat/src/lib/api.ts` (+ `lib/auth.ts`).
- (e) Echo it in the **desktop** client (`AgentKeyProvider` / the portal HTTP
  path) — or keep desktop on the Bearer exemption from (b) — **plus a desktop
  regression test proving it still authenticates**.

**No half-ship:** enabling any of (a)-(e) alone either does nothing (no
enforcement) or 403s live clients (enforcement without the echo). Ship all five
together with the desktop regression test, or ship none.

### Revisit-condition

Promote to active work when the portal and API move to a **cross-site topology**
(different registrable domains, requiring `AUTH_COOKIE_SAMESITE=none`). At that
point Lax no longer protects the session and a CSRF token becomes
**mandatory** — `validate_secrets()` already refuses to boot such a deploy, and
that guard message points here.

---

## 2. Local (offline) PDF generation on the desktop

**Status:** DEFERRED. **Surface:** SRAN Desktop document/export path.

### Current posture

Desktop document export uses the **server** generation path, which already
handles PDF correctly — including Thai/Sarabun font embedding (bundled fonts in
`scripts/fonts/`, per the Phase 4 Doc workspace). The server fallback works
today; there is no user-facing gap.

### Why deferred

A client-side PDF path would need a **new .NET PDF dependency** plus Thai
(Sarabun) font embedding re-implemented on the desktop — duplicating logic the
server already gets right. Low ROI: added dependency weight and a second font
pipeline to maintain, for a capability users already have via the server.

### Revisit-condition

When **offline PDF export becomes a demanded feature**, or a customer requires
**air-gapped** PDF generation where the document may not reach the server.

---

## 3. Client-side Pyodide (pure-local desktop file analysis)

**Status:** DEFERRED — the strongest defer. **Surface:** SRAN Desktop file
analysis / code execution.

### Current posture

The desktop's file analysis is served by the **server sandbox** (Phase 13),
which delivers real CPython with the full package set **and** the DLP masking
property. Files uploaded for analysis already go to chat-edge, so they are
already leaving the machine under the current design.

### Why deferred (strongest case)

- The **original premise** for Pyodide — "data never leaves the machine" — is
  already undercut: files upload to chat-edge today, so local execution does not
  actually keep data on-device in the current flow.
- The **server sandbox already delivers more**: real CPython, full packages, and
  DLP masking on egress — a strictly stronger and broader execution surface than
  Pyodide.
- Pyodide adds a **~10–200 MB WASM payload**, and requires **CSP relaxation
  (COOP/COEP cross-origin isolation)** that risks the cross-origin chat-edge
  calls the app depends on. It is a **weaker, narrower** execution surface for a
  large cost.

### Revisit-condition

A genuine **air-gapped / offline-desktop requirement** where **nothing** may
reach the server — at which point pure-local execution stops being redundant
with the server sandbox and becomes the only option.

## Google SSO cutover (Phase 3, 2026-08-10) — known items / follow-ups

- **M3 (Phase 0 review) — RESOLVED BY CUTOVER**: "a password user on a
  domain outside the allowlist can still log in" was a password-path
  hole; with `PASSWORD_LOGIN_ENABLED=false` the whole surface (login /
  signup / password-reset) answers 403, so the hole is closed as long
  as the flag stays off. If passwords are ever re-enabled, M3 must be
  re-examined (add the domain gate to password LOGIN, not just signup).
- chat-edge validates portal JWTs statelessly → a revoked user
  (token_version bump) stays valid on chat-edge until the access token
  expires (≤60 min lag). Acceptable for the internal tool; revisit for
  SaaS.
- Redis outage during refresh fails open on the rate-limit side
  (outage-conservative limiter) — the refresh itself still requires a
  valid signed token; documented, not a hole.
- CSRF double-submit on /portal/** mutations: still the deliberate,
  ATOMIC 5-client deferral documented in services/portal/cookies.py.
- Desktop OAuth callback error (403 domain_not_allowed etc.) renders
  raw JSON in the system browser — cosmetic; make a small HTML page.

### RESEND_API_KEY is mandatory in production even with email features off

`Settings.validate_secrets()` (`backend/app/core/config.py`, "Phase 3.5 —
Resend is mandatory in production") requires a non-empty `RESEND_API_KEY`
whenever `APP_ENV` is not dev/test. The check is unconditional, so after
the Phase 3 cutover — `PASSWORD_LOGIN_ENABLED=false`, meaning no
verification or password-reset mail can be sent at all — a deployment
that legitimately has no email provider still gets `STARTUP BLOCKED`.

Not a blocker (set a real key, or a throwaway one on a domain you own),
and deliberately NOT changed as part of the cutover: relaxing a
fail-closed startup check is a security-posture change that deserves its
own review rather than riding along in a docs/flag commit.

Possible follow-up: make the requirement conditional on the features
that actually send mail — roughly "required unless
`password_login_enabled` is false AND email verification is off" — with
a startup WARNING (not a hard failure) in that case so the operator
still knows outbound mail is unavailable. Whoever picks this up should
also check `request_verification_email` and the invite-link flow, which
send mail independently of the password surface.

---

## Global cross-worker/pod provider concurrency cap → Rust rate-limiter

**Deferred from:** Batch A provider-resilience (feat/provider-resilience, 2026-08-10)

### What shipped instead

`PROVIDER_CONCURRENCY_CAPS` — a per-provider `asyncio.Semaphore` bulkhead in
`app/services/providers/concurrency.py`, held per gunicorn worker process.
Effective ceiling a provider sees = **cap × GUNICORN_WORKERS (× pods)**; the
`.env.example` entry documents the sizing formula and operators size the cap
accordingly.

### What is deferred

A cluster-true cap (one number regardless of workers/pods) needs shared
state — a Redis token/lease keyed `provider:{name}` with TTL'd slots to
survive worker crashes without leaking permits. The natural home is the
Rust rate-limiter sidecar (it already owns the Redis sliding windows), by
adding a provider dimension to its proto + engine.

### Why deferred (per the configurability audit, artifact 642d0557)

- The per-process cap covers the actual motivating case (one small
  self-hosted box behind a known worker count on one host).
- Cross-worker leasing brings real failure modes (leaked slots on
  worker death, added RTT on the hot path, fail-open semantics to
  design) that are not worth carrying before evidence the simple cap is
  insufficient.

### Revisit-condition

Horizontal scaling of the backend (multiple pods) in front of capacity-
sensitive self-hosted boxes, or measured cap overshoot causing box
degradation despite correctly-sized per-worker caps.
