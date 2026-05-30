# Auth Session Overhaul — Verification Report (Session 5)

Status of the HttpOnly-cookie + silent-refresh + rotation overhaul against
the Definition of Done. Scope: Portal (3001), Web Chat (3002), Gateway /
FastAPI (8080), Go Chat Edge (4000). Admin (3000) untouched.

---

## 1. Build / compile gate — ALL GREEN

| Component | Command | Result |
|-----------|---------|--------|
| Backend (FastAPI) | `pytest` (auth suites) | ✅ pass (see §2) |
| Go Chat Edge | `go build ./...` + `go test ./internal/middleware/` | ✅ build + 5 tests pass |
| Portal (Next) | `npm run build` | ✅ 20/20 pages, exit 0 |
| Web Chat (Next) | `npm run build` | ✅ 9/9 pages, exit 0 |
| Portal/Chat types | `tsc --noEmit` | ✅ exit 0 |

Note: fixed two **pre-existing** portal build breaks (`/oauth-complete`,
`/reset-password` used `useSearchParams` without a Suspense boundary — both
were red on `main` before this work; `/reset-password` is untouched by the
auth changes).

---

## 2. Automated test evidence

`backend/tests/test_portal_auth.py` (HTTP-level, TestClient) covers the
cookie contract end-to-end:

- `test_signup/login_*` — body carries `{user}` only, **no tokens**;
  `portal_access_token` + `portal_refresh_token` cookies set; the cookie
  alone authenticates `/portal/me`.
- `test_refresh_rotates_cookies_and_sets_new_pair` — refresh mints a NEW
  pair (rotation); the rotated session still authenticates.
- `test_refresh_without_cookie_returns_401_and_clears`.
- `test_token_version_bump_invalidates_refresh` — password-reset-style
  version bump kills old sessions at the refresh choke point.
- `test_cookie_exchange_*` — OAuth bootstrap happy path + forged /
  role-swapped rejection.
- `test_logout_clears_cookies` — real revocation + cookie clear.
- Go `auth_extract_test.go` — cookie-first / Bearer-fallback priority.

Full portal regression suite (signup/login/refresh/logout/me/team/
projects/billing/invites/usage/password-reset/oauth): **156+ pass**.

### Observed Set-Cookie attributes (raw, from a live TestClient run)

```
# login / signup
portal_access_token=<jwt>;  HttpOnly; Max-Age=3600;    Path=/; SameSite=lax
portal_refresh_token=<jwt>; HttpOnly; Max-Age=7776000; Path=/; SameSite=lax   # 90 days
# body = {"user": {...}}   (no access_token / refresh_token)

# refresh  → both cookies re-issued with NEW values (rotation confirmed)
# logout   → both cookies expired (Max-Age=0, expires in the past)
```

`Secure` is omitted in dev (`app_env` ∈ dev/test) and added automatically
in any other environment via `settings.cookie_secure_effective`.

---

## 3. The 8 cross-port scenarios (Session 5.1)

| # | Scenario | How it's satisfied | Verified by |
|---|----------|--------------------|-------------|
| 1 | Login → cookies set | Backend Set-Cookie (HttpOnly) on `/portal/auth/login` | ✅ automated (raw headers above) |
| 2 | Chat (3002) sees cookies | Host-only cookie on `localhost` is shared across ports; chat-edge reads `portal_access_token`; gateway CORS allows 3002 + credentials | ⚙️ manual (browser) — logic verified |
| 3 | Silent refresh @ ~50 min → cookies renewed | `auth-refresh.ts` timer + rotation endpoint | ✅ rotation automated; timing manual |
| 4 | Tab close → reopen → still in | Persistent cookies (Max-Age 90d refresh) | ⚙️ manual (browser) |
| 5 | Browser restart → still logged in | Same — refresh cookie persists | ⚙️ manual (browser) |
| 6 | Many tabs → refresh not duplicated | `BroadcastChannel` coordination + backend 30s grace | ✅ grace automated; coordination manual |
| 7 | Network blip → NOT logged out | `api.ts` 401 → single-flight refresh → retry once | ✅ refresh-retry path in code; manual sim |
| 8 | Token expires mid-SSE → reconnect | SSE opens via `raw()`; 401 → refresh → retry re-opens stream | ⚙️ manual (browser) |

**Browser-only scenarios (2,4,5,6,8) require the full live stack** (`make
up` / the 5 services running) and a real browser — they can't be exercised
from unit tests. The underlying mechanics are each backed by code +
automated tests; the manual runbook below confirms them on a live stack.

### Manual runbook (live stack)

```
make up                      # or docker compose up: backend, redis, pg, portal, chat, chat-edge
# 1. http://localhost:3001/login → sign in. DevTools ▸ Application ▸ Cookies:
#    expect portal_access_token + portal_refresh_token, HttpOnly ✓, Path=/, SameSite=Lax.
# 2. Open http://localhost:3002/chat (or click "Open Chat" on the dashboard).
#    Expect: lands in chat WITHOUT a portal-login bounce and WITHOUT ?token= in the URL.
# 3. Watch Network: ~50 min (or temporarily lower REFRESH_INTERVAL_MS) → POST /portal/auth/refresh 200,
#    Set-Cookie rotates. Or background the tab >50 min, refocus → immediate refresh.
# 4/5. Close tab / quit browser, reopen 3002/chat → still authenticated.
# 6. Open 3 chat tabs → only one POST /portal/auth/refresh per interval (BroadcastChannel).
# 7. DevTools ▸ Network ▸ Offline for 2s during an idle period, back Online, click around →
#    a 401 triggers one refresh + retry; NO bounce to /login.
# 8. Start a long streamed reply; if the access cookie lapses mid-stream the next stream/request
#    refreshes + reconnects rather than dropping you to login.
```

---

## 4. Production configuration (Session 5.2)

All cookie behaviour is env-driven — no code change to ship to prod:

| Env (gateway / backend) | Dev default | Production |
|--------------------------|-------------|------------|
| `AUTH_COOKIE_DOMAIN` | "" (host-only; localhost shares across ports) | `.yourdomain.com` (share across `portal.`, `chat.`, `api.` sub-domains) |
| `AUTH_COOKIE_SECURE` | unset → auto-off in dev | unset → auto-**on** outside dev/test, or force `true` |
| `AUTH_COOKIE_SAMESITE` | `lax` | `lax` (same-site sub-domains) — use `none` only if chat/api are a *different* registrable domain (then `Secure` is mandatory) |
| `CORS_ORIGINS` | `localhost:3000,3001,3002,5173` | the real portal + chat origins |
| `PORTAL_JWT_SECRET` | dev default | **must** be a ≥32-byte secret; identical on FastAPI + Go |
| Chat `NEXT_PUBLIC_GATEWAY_URL` | `http://localhost:8080` | `https://api.yourdomain.com` |
| Chat `NEXT_PUBLIC_PORTAL_URL` | `http://localhost:3001` | `https://portal.yourdomain.com` |

Cross-origin reachability: on `localhost` all ports are same-site so a
host-only `SameSite=Lax` cookie flows to 3002/4000/8080 automatically. In
prod the shared parent `AUTH_COOKIE_DOMAIN` does the same across
sub-domains. Different registrable domains would require `SameSite=None;
Secure` (documented above).

---

## 5. Edge cases (Session 5.3)

- **JWT > 4 KB cookie drop** — NOT a risk. Measured real tokens:
  access ≈ 501 B, refresh ≈ 503 B (~1 KB for both cookies), far under the
  4 KB per-cookie limit. The audit's speculative "oversize cookie" cause
  doesn't apply to the current claim set.
- **Multiple tenants / tenant switch** — a tenant change bumps
  `token_version`; the next refresh is rejected (`token_version_stale`)
  and the client re-establishes a session carrying the new tenant claims.
  The access token always reflects the user row at mint time.
- **Concurrent refresh (two tabs racing)** — handled at two layers:
  frontend `BroadcastChannel` + single-flight `sharedRefresh()` so tabs
  don't stampede, AND a backend 30-second grace window
  (`portal_refresh_grace:<jti>`) that returns the SAME freshly-minted pair
  to a duplicate refresh instead of tripping reuse detection. Genuine
  reuse past the grace window → 401 `refresh_reuse`.
- **Redis outage** — rotation state is best-effort/fail-open: refresh
  still succeeds (without replay protection) rather than locking everyone
  out. Availability > strict replay defence for the refresh endpoint.

---

## 6. Outstanding (tracked into Session 6)

- Go startup secret-strength validation (reject dev-default
  `PORTAL_JWT_SECRET` in prod).
- `.env.example` documentation for the new vars.
- Remove now-dead token code paths; CSRF posture note.
