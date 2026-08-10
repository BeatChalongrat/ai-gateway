# Google SSO — production go-live runbook

Step-by-step for taking the Google-only sign-in stack (portal + web-chat +
desktop) to production. Everything here is derived from the committed
config: `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`,
and `backend/app/core/config.py` (`validate_secrets`).

Read the whole document before starting — two steps (the portal/web-chat
image rebuild, and the secrets that block startup) will waste a deploy
window if you discover them halfway through.

Placeholders used below:

| Placeholder | Meaning | Example |
|---|---|---|
| `<portal>` | Portal hostname | `portal.sran.net` |
| `<gateway>` | Gateway/API hostname | `gateway-api.sran.net` |
| `<chat>` | Web-chat hostname | `chat.sran.net` |
| `<parent>` | Registrable parent domain shared by the three | `sran.net` |

---

## A. Google Cloud — production OAuth client

Identity for all three surfaces comes from **one** Google OAuth client;
the backend pins the id_token `aud` to `OAUTH_GOOGLE_CLIENT_ID`, so the
portal and the backend MUST use the same client id.

1. **Consent screen → Internal.** Workspace-only. Switch to External
   only if you later open sign-in beyond the company domain (that is an
   `ALLOWED_EMAIL_DOMAINS` change too — see §B).
2. **Authorized redirect URIs** — add BOTH; each surface uses a different
   one and the desktop app breaks silently without the second:
   - `https://<portal>/api/auth/callback/google` — portal (NextAuth)
   - `https://<gateway>/portal/auth/oauth/callback` — desktop PKCE
     (`GET /portal/auth/oauth/start?client=desktop` sends the browser here)
3. **Authorized JavaScript origin**: `https://<portal>`
4. **⚠️ Rotate the client secret.** The development secret has been
   shared in chat/tooling and must never reach production. Either rotate
   the secret on the existing client (dev then needs the new value too),
   or create a separate production client — in which case set BOTH
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to the new pair.

The secret lives **only** in the backend environment. It is never
shipped in the desktop binary or any browser bundle — the backend
performs the desktop authorization-code exchange itself.

---

## B. Production environment variables

### B.1 SSO-specific

| Var | Value | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` | from §A | Feeds portal (NextAuth) **and** backend (`OAUTH_GOOGLE_CLIENT_ID`, `aud` pin) from one variable |
| `GOOGLE_CLIENT_SECRET` | from §A (rotated) | Backend only (`OAUTH_GOOGLE_CLIENT_SECRET`) |
| `GOOGLE_HOSTED_DOMAIN` | `gbtech.co.th` | `hd` account-chooser hint — UX only, never enforcement |
| `ALLOWED_EMAIL_DOMAINS` | `gbtech.co.th` | Server-side allowlist. **Empty = allow every domain** (that is how you would open up to gmail later) |
| `PASSWORD_LOGIN_ENABLED` | `false` | Kill-switch: login / signup / password-reset answer `403 password_login_disabled` |
| `NEXT_PUBLIC_OAUTH_ENABLED` | `true` | Renders the portal's Google button — **BUILD-time**, see §E |
| `NEXTAUTH_URL` | `https://<portal>` | Must match the browsed origin exactly |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` | The `dev-portal-nextauth-secret` default must not reach prod |

### B.2 Secrets that BLOCK STARTUP if missing or weak

`docker-compose.prod.yml` sets `APP_ENV: production`, which turns on
`Settings.validate_secrets()`. It raises `RuntimeError: STARTUP BLOCKED`
— the process refuses to serve a single request — when any of these is
empty, a known placeholder, or under 32 bytes:

| Var | Generate with |
|---|---|
| `JWT_SECRET` | `openssl rand -base64 48` |
| `PORTAL_JWT_SECRET` | `openssl rand -base64 48` |
| `MASTER_ENCRYPTION_KEY` | `openssl rand -base64 48` |
| `SESSION_SIGNING_SECRETS` | `openssl rand -base64 48` (comma-separated rotation list) |
| `SERVICE_JWT_SECRET` | `openssl rand -hex 32` — must be the SAME value in backend and chat-edge |
| `DLP_AUDIT_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `PROVIDER_KEY_MASTER_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` (exactly 32 bytes / 64 hex chars) |
| `RESEND_API_KEY` | real Resend key — **see the note below** |

> **`RESEND_API_KEY` is mandatory in production even with password login
> disabled.** `validate_secrets()` requires it unconditionally
> (`config.py`, "Phase 3.5 — Resend is mandatory in production"). It is
> intuitive to think it is optional while `PASSWORD_LOGIN_ENABLED=false`
> — no verification or reset mail is sent in that mode — but the check
> does not know about the flag, so an empty value blocks startup. Set a
> real key (or relax that check deliberately in a separate change).

Additionally, `docker-compose.prod.yml` refuses to even render without
these (compose `${VAR:?}`):

`CORS_ORIGINS`, `CHAT_CORS_ORIGINS`, `PORTAL_PUBLIC_URL`,
`WEB_CHAT_PUBLIC_URL`, `MCP_PUBLIC_BASE`, `REDIS_PASSWORD`,
`SERVICE_JWT_SECRET`, `USER_JWT_SECRET`, `CSRF_SECRET`,
`PORTAL_JWT_SECRET`, `CHAT_EDGE_ADMIN_API_KEY`.

`INITIAL_ADMIN_PASSWORD` is only read by `make seed` on a fresh database
(admin UI login) — not by the SSO path, but set it before seeding.

---

## C. Auth cookie scope

The portal mints HttpOnly `portal_access_token` / `portal_refresh_token`
cookies. Web-chat has **no Bearer fallback** — every authed fetch (files,
media, SSE) depends on those cookies reaching the chat-edge and gateway
origins. A wrong scope does not error loudly; it 401s everything.

```
AUTH_COOKIE_DOMAIN=.<parent>     # e.g. .sran.net — omit the leading host
AUTH_COOKIE_SAMESITE=lax         # default; keep unless you go cross-site
```

- **`docker-compose.prod.yml` does NOT set these** (a comment in the base
  compose says otherwise — it is stale). They must come from `.env`.
- Leaving `AUTH_COOKIE_DOMAIN` empty gives host-only cookies. That is
  correct for localhost (ports share a host) and **wrong** for a
  subdomain deployment, where the portal's cookie would never reach
  `<gateway>` or `<chat>`.
- `Secure` needs no configuration: `cookie_secure_effective` turns it on
  automatically outside dev/test.
- `SameSite=Lax` is the load-bearing CSRF defence for the portal. If you
  ever move the apps and the API to **different registrable domains**,
  `SameSite=None` becomes necessary — and `validate_secrets()` then
  fail-closes unless `AUTH_COOKIE_DOMAIN` and Secure are both set. Note
  the warning it prints: `SameSite=None` removes the CSRF defence, so a
  double-submit token must land first (see `docs/DEFERRED_WORK.md`).

---

## D. CORS

Both lists must be the real production origins — no `localhost`.

```
CORS_ORIGINS=https://<portal>,https://<chat>,https://admin.<parent>   # gateway (backend)
CHAT_CORS_ORIGINS=https://<chat>                                      # chat-edge (CORS_ALLOW_ORIGINS)
```

The portal and web-chat call the gateway cross-origin **with
credentials** (silent refresh, `/portal/me`), so a missing origin here
looks exactly like a broken login.

---

## E. Deploy order

Two of these images bake configuration in at **build** time. Restarting
the container does not pick up a changed value — you must rebuild.

1. **postgres + redis** — up first.
2. **backend** — runs `alembic upgrade head` on start. Verify the schema
   the SSO work depends on:
   ```bash
   docker compose exec backend python -m alembic current          # expect: head
   docker compose exec postgres psql -U postgres -d ai_gateway \
     -c "SELECT is_nullable FROM information_schema.columns \
         WHERE table_name='portal_users' AND column_name='tenant_id';"
   # expect: NO   (migration 030 — OAuth users cannot be inserted tenant-less)
   ```
3. **gateway** (Go proxy, `:8080`).
4. **chat-edge** — pulled SQL migrations are applied by the init hook only
   on a fresh volume; on an existing one run
   `bash scripts/apply_chat_edge_migrations_main.sh`.
5. **portal — REBUILD, do not just restart.** `NEXT_PUBLIC_OAUTH_ENABLED`
   (the Google button) and `NEXT_PUBLIC_WEB_CHAT_URL` (the chat link plus
   three redirect allowlists) are inlined into the bundle at build time:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml build portal
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d portal
   ```
6. **web-chat — REBUILD too.** `NEXT_PUBLIC_API_URL`,
   `NEXT_PUBLIC_GATEWAY_URL` and `NEXT_PUBLIC_PORTAL_URL` come from
   `WEB_CHAT_API_URL` / `WEB_CHAT_GATEWAY_URL` / `WEB_CHAT_PORTAL_URL`
   as build args; unset, the image bakes in localhost and auth breaks
   off-box.

---

## F. Post-deploy smoke test

Non-browser checks first — they isolate backend problems from browser ones.

```bash
# 1. Password surface is closed (all three must be 403 password_login_disabled)
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<gateway>/portal/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"x@gbtech.co.th","password":"whatever12"}'
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<gateway>/portal/auth/signup \
  -H 'Content-Type: application/json' -d '{"email":"x@gbtech.co.th","password":"whatever12"}'
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<gateway>/portal/auth/password-reset/request \
  -H 'Content-Type: application/json' -d '{"email":"x@gbtech.co.th"}'

# 2. UI truth flag the login screens read
curl -s https://<gateway>/portal/auth/public-settings     # password_login_enabled: false

# 3. Google client is wired (must NOT say "Google OAuth not configured")
curl -s -X POST https://<gateway>/portal/auth/oauth/verify \
  -H 'Content-Type: application/json' -d '{"provider":"google","id_token":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}'
# expect: oauth_verification_failed / "Invalid input segments length" — it reached the verifier

# 4. Desktop start redirects to Google with the prod client id
curl -s -o /dev/null -w '%{redirect_url}\n' \
  "https://<gateway>/portal/auth/oauth/start?provider=google&client=desktop&nonce=$(openssl rand -hex 16)"
```

Then the browser checks a human has to do:

- **Portal**: `https://<portal>/login` shows "Continue with Google" and
  **no password form** → sign in with an `@gbtech.co.th` account → lands
  on the dashboard.
- **Web-chat**: open `https://<chat>` in the same browser → loads without
  bouncing back to login (it inherits the portal cookie).
- **Desktop**: "Sign in with Google" → system browser → success landing
  page → the app receives the session.
- **Off-domain**: sign in with a non-allowlisted Google account →
  rejected with `403 domain_not_allowed`, no session anywhere.

---

## G. Rollback

If SSO has to be abandoned mid-incident, re-open the password surface —
the code was never deleted:

```bash
# in .env
PASSWORD_LOGIN_ENABLED=true
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d backend
```

That immediately restores `POST /portal/auth/login`, `/signup` and
`/password-reset/*`, and both login UIs re-render the password form on
their next read of `/portal/auth/public-settings` (the portal needs no
rebuild for this — the form is hidden at runtime, not at build).

Users created through Google have no password. To let one in during a
rollback, send them through the reset flow (needs `RESEND_API_KEY` to
actually deliver, which §B.2 already requires).

Reverting the whole cutover commit is **not** required and not
recommended — the flag is the intended control surface.

---

## Known limitations

Carried deliberately into production; details and rationale in
[`docs/DEFERRED_WORK.md`](./DEFERRED_WORK.md):

- chat-edge validates portal JWTs statelessly — a revoked user keeps
  access there for up to the access-token lifetime (≤ 60 min).
- A Redis outage fails open on the refresh **rate limiter** only; the
  refresh itself still requires a validly signed token.
- Double-submit CSRF on `/portal/**` mutations is deferred (it must land
  atomically across four clients); `SameSite=Lax` is the active defence.
