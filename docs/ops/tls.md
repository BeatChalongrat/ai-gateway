# TLS and production mode (FIX133 B)

Every browser-facing port of the stack is served over **https** by the Caddy
front door (compose service `edge`, `infra/caddy/Caddyfile`), and every
service runs in **production mode**. The port numbers did not change:

| URL (before)            | URL (now)                | behind it              | plain-http debug door |
|-------------------------|--------------------------|------------------------|-----------------------|
| http://localhost:3002   | https://localhost:3002   | web-chat (the page)    | http://127.0.0.1:23002 |
| http://localhost:4000   | https://localhost:4000   | chat-edge-api          | http://127.0.0.1:24000 |
| http://localhost:8080   | https://localhost:8080   | gateway (the API)      | http://127.0.0.1:28080 |
| http://localhost:3001   | https://localhost:3001   | portal                 | http://127.0.0.1:23001 |
| http://localhost:3000   | https://localhost:3000   | admin-ui               | http://127.0.0.1:23000 |

The debug doors are loopback-only and exist for scripts and probes (they
skip the proxy). Nothing else listens on the old plain ports: an old
`http://localhost:3002` bookmark is redirected to the https one (see "Plain
http on the https ports" below).

## Plain http on the https ports (FIX134 A4)

Caddy's `http_redirect` listener wrapper (global `servers` block of the
Caddyfile) answers a plain-http request on any of the five https ports with
a **308** to `https://<same host>:<same port><same path>`, instead of the bare
400 "Client sent an HTTP request to an HTTPS server" it gave before:

* browser ports (3000, 3001, 3002): an old `http://` bookmark or typed
  address lands on the https page;
* API ports (4000, 8080): an API client gets the same redirect, so a script
  that still speaks `http://localhost:8080` must follow redirects
  (`curl -L`, `requests` does by default; a POST body is re-sent by a 308)
  or, better, use the plain loopback debug door (`http://127.0.0.1:28080`,
  `:24000`), which skips the proxy.

The redirect keeps the port, so it never sends anyone to `:443`. There is
still no HSTS (see above).

## Health checks

The front door probes every upstream every 5 s (`(balanced)` snippet):
web-chat `/api/version`, chat-edge `/health`, gateway `/__gw/health`, and
since FIX134 C1 portal and admin-ui `/healthz` — a route in each Next app
that answers `{"ok":true,"service":"…"}` from the Node process alone (no
auth, no database, no backend call; the app's middleware lets it through
without a session). Before, those two were probed on `/login`, a full page
render (plus the portal's session check against the backend) with a 3 s
timeout, which logged `context deadline exceeded` and pulled the service out
of rotation whenever the box was busy (deploys, the 03:15 backup). The
timeout is 5 s now and an upstream is marked down only after two consecutive
failures (`max_fails 2`), for 15 s.

## The certificate

`infra/caddy/certs/` (gitignored, this machine's own):

* `local.pem` / `local-key.pem` — the server certificate, issued for
  `localhost`, `127.0.0.1`, `::1`, the machine's hostname and its LAN address,
  valid ~27 months (mkcert default);
* `rootCA.pem` — the **public** root of the local certificate authority that
  issued it (the private key stays in mkcert's own store,
  `%LOCALAPPDATA%\mkcert\rootCA-key.pem`, never in the repo).

Renew (from the repo root, mkcert installed with `winget install
FiloSottile.mkcert`):

```
mkcert -cert-file infra/caddy/certs/local.pem -key-file infra/caddy/certs/local-key.pem \
  localhost 127.0.0.1 ::1 <hostname> <hostname>.local <lan-ip>
docker compose -p ai-gateway restart edge
```

## Trusting the root (one step per machine)

Browsers trust the certificate once the root CA is in the OS store. This is a
one-time step **per machine and per person**, and it needs one click on the
Windows "install this root certificate?" dialog:

```
certutil -user -addstore Root infra\caddy\certs\rootCA.pem
```

(or `mkcert -install` on the machine that made the CA). Until then Chrome
shows "Your connection is not private" for the https ports; the site works
after "Advanced → proceed", but the padlock is red.

**The desktop app does not need that step.** Its config pins the root
(`trustedCaFile`, shipped beside the exe as `resources/trusted-ca.pem` by
`scripts/dist.js`); `src/main/local-ca.ts` accepts a server certificate only
when Chromium's sole complaint is "issuer unknown", the host is one of the
app's own, and the leaf is signed by that root, valid now and names the host.
Everything else keeps Chromium's verdict. Since FIX134 this is the app's
default config (`apps/desktop/config/desktop.config.json`), so `npm run dist`
with no `--config` builds the https app and `npm start` runs it; both need
`infra/caddy/certs/rootCA.pem` to exist (the validator refuses the config
otherwise). `config/desktop.config.plain-http.json` is the twin for a stack
that runs without Caddy.

For colleagues on the LAN (a pilot): they open `https://<hostname>:3002` and
must import `rootCA.pem` once (the same `certutil` line); the desktop
installer built from the default config already carries it.

## Production mode

`.env` states it: `APP_ENV=production`, `COOKIE_SECURE=true`,
`CHAT_EDGE_DEV=` (empty). With that the backend's `validate_secrets()` and
chat-edge's `Validate()` refuse to boot on any dev-default secret, so every
secret in `.env` is real: `JWT_SECRET`, `MASTER_ENCRYPTION_KEY`,
`PORTAL_JWT_SECRET`, `SERVICE_JWT_SECRET`, `USER_JWT_SECRET`, `CSRF_SECRET`,
`CHAT_EDGE_ADMIN_API_KEY`, `SESSION_SIGNING_SECRETS`, `DLP_AUDIT_KEY`,
`PROVIDER_KEY_MASTER_KEY`, `NEXTAUTH_SECRET`.

What production mode changes:

* auth cookies are `Secure` (portal, chat-edge, NextAuth's `__Secure-` names);
* `/docs`, `/redoc`, `/openapi.json` are off (`API_DOCS_ENABLED=true` turns
  them back on);
* the dev token is no longer echoed in auth responses, seeds and the
  `admin1234` auto-admin never run, the mock search provider is gone;
* email: `EMAIL_DELIVERY=disabled` is the stated choice for a deployment
  without a mail sender (sign-in is Google OAuth; verification and reset mails
  are reported as not delivered, never silently dropped). Set a Resend key and
  `EMAIL_DELIVERY=auto` to send real mail;
* admin-ui and portal run their built images (`next start`), not `next dev`;
  the hot-reload shapes are opt-in in `docker-compose.dev.yml`.

Rotating `PROVIDER_KEY_MASTER_KEY` (the key that encrypts provider, search
and connector secrets at rest) needs every stored blob rewritten first:

```
docker compose -p ai-gateway exec backend python -m scripts.reencrypt_master_key --old <old|dev> --new <new>            # dry run
docker compose -p ai-gateway exec backend python -m scripts.reencrypt_master_key --old <old|dev> --new <new> --apply
```

## Behind the proxy

The backend trusts `X-Forwarded-Proto` / `-For` from the compose network
(`FORWARDED_ALLOW_IPS=*`, gunicorn.conf.py), so `url_for()` builds https
callback URLs — the Google sign-in redirect URI registered in the Google
console must be the https one. Caddy sends no HSTS on purpose: HSTS is per
host, and `localhost` also serves the plain debug doors and the test stack.

## Rollback

`APP_ENV=development`, `COOKIE_SECURE=false`, `CHAT_EDGE_DEV=1` in `.env` and
recreate backend, chat-edge-api, web-chat, portal, admin-ui; the `edge`
service can stay (https keeps working in dev mode) or be stopped after
restoring the `ports:` lines. Secrets rotated for production keep working in
dev mode — never put the dev defaults back.
