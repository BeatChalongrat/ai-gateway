# DESKTOP FIX — PHASE 7: Real OAuth connectors (server broker + desktop deep-link)

Cross-stack phase: **backend (FastAPI, `backend/`)**, **mcp-gateway (Node, `services/mcp-gateway/`)**, and **SranDesktop (WPF, `SranDesktop/`)**. Phases 1–6 + hotfixes are merged; do not regress them.

## What exists today (all verified — build on it, don't reinvent)

- **Desktop connector flow is a stub:** `ConnectorMarketplaceViewModel.ToggleInstallAsync` (`ConnectorMarketplaceViewModel.cs:92-119`) pops an `InputDialog` asking the user to PASTE a raw token for both `api_key` and `oauth2` (comment at line 105: "OAuth deep-link infra doesn't exist yet"). Tokens go to DPAPI key `connector:{id}` and ride as a static `Authorization: Bearer` on every `POST {server_url}/mcp` (`McpRemoteClient.cs:32-34`). No refresh, no expiry.
- **Backend already has OAuth machinery — for portal LOGIN only:** `backend/app/services/portal/oauth_flow_service.py` — Redis one-shot state store (`oauth_state:*`, GETDEL, 300s TTL, lines 62-132), `generate_pkce()` S256 (307-318), code-exchange helpers; providers hardcoded to google/github (`_provider_config`, 272-299). `authlib==1.3.2` in requirements. Routes pattern: `portal_auth.py` `GET /oauth/start` (442-498) / `GET /oauth/callback` (501-542).
- **Encryption to reuse:** `backend/app/core/security.py` `encrypt_provider_key`/`decrypt_provider_key` (66-94, AES-256-GCM, versioned wire format) with the `provider_key_service.py` `_encode`/`_decode` usage pattern (62-70). There is NO per-user token table today — net-new.
- **Portal auth dependency to reuse:** `get_portal_user` (`backend/app/api/deps.py:428`) — cookie or Bearer; JWT carries `sub`, `tenant_id`, `token_version`. Mount pattern per `portal_team.py:31`.
- **Catalog service exists:** `services/mcp-gateway/server.js` — serves `GET :3200/catalog.json` (62-76), spawns stdio MCP servers behind `supergateway --streamableHttpPath /mcp` (filesystem :3201, memory :3202; all `auth_type:"none"`). docker-compose service `mcp-gateway` (`docker-compose.yml:346-368`) with `MCP_PUBLIC_BASE`/`MCP_PUBLIC_MODE`. Catalog fields match the desktop's `McpConnector` exactly: `id,name,description,category,auth_type,server_url,tools`.
- **Desktop deep-link gaps (both must be built):** the Inno installer (`SranDesktop/build/SranDesktop.iss` [Registry], 59-64) registers NO URI protocol; `SingleInstance.cs` forwards only the literal `"activate"` over the pipe (line 31 write, line 61 check) — a second instance cannot deliver a callback URL to the running one.

## Architecture (decided — implement this, not an alternative)

**Broker model: provider tokens NEVER touch the desktop.**

1. Desktop (signed in, has portal JWT) asks the backend for a one-time **connect ticket** for connector X.
2. Desktop opens the system browser at `{gateway}/portal/connectors/oauth/start?ticket=...` — the ticket authenticates the browser session (the browser has no portal cookies; the ticket is the bridge).
3. Backend validates the ticket (single-use, short TTL), then runs the standard OAuth dance with the provider (state + PKCE, reusing the existing machinery), receives tokens on ITS callback, encrypts and stores them server-side, and finally 302-redirects the browser to **`sran://oauth/callback?connector=X&status=ok`** — the deep link carries ONLY the connector id and status, never tokens.
4. The OS routes `sran://` to the desktop; if an instance is running, the second instance forwards the URI over the named pipe; the app refreshes connector status.
5. Tool calls: for `oauth2` connectors the desktop calls the mcp-gateway with its **portal JWT** as Bearer; the mcp-gateway validates the JWT, fetches the user's provider token from the backend, and runs a **per-(user, connector) MCP server instance** with the token injected via env. The desktop's `McpRemoteClient` protocol is unchanged.

## Ground rules

- Read every file you touch IN FULL. Follow each codebase's existing patterns (FastAPI routes/deps/migrations; Node service style in server.js; C# conventions incl. Dict EN/TH parity and atomic writes).
- Security invariants (verify in the final review): provider tokens exist ONLY encrypted in the backend DB and in the memory/env of the per-user MCP server instance; never in a URL, never in desktop storage, never logged. `state` is one-shot; PKCE S256 everywhere; tickets single-use with ≤5 min TTL; the token-fetch endpoint is portal-JWT-authenticated and never returns refresh tokens.
- Tests per part (pytest / node:test or the repo's existing JS test approach — check first / xunit).
- Finish state: backend pytest green on touched files; mcp-gateway starts and serves the catalog; desktop `dotnet build` 0/0 + `dotnet test` all green.

---

# PART A — Backend: connector OAuth broker (FastAPI)

### A1 — Data model
Alembic migration (follow `backend/migrations/versions/` conventions): table `connector_tokens` — `id`, `tenant_id`, `portal_user_id` (FK), `connector_id` (str), `provider` (str), `access_token_encrypted`, `refresh_token_encrypted` (nullable), `expires_at` (nullable), `scopes` (str), `created_at`, `updated_at`; unique `(portal_user_id, connector_id)`. Encrypt/decrypt via the `security.py` helpers using the same master-key pattern as `provider_key_service._encode/_decode`.

### A2 — Provider registry
Extend the oauth provider config concept (do NOT bolt onto `_provider_config`'s login flow — a separate `CONNECTOR_PROVIDERS` map in a new `connector_oauth_service.py`): per provider — auth URL, token URL, scopes, client id/secret from settings (new settings fields, `.env.example` updated), refresh support flag. Ship configs for: `google` (used by gmail/gdrive/gcal connectors — scopes differ per connector, so the map is keyed by CONNECTOR id, not provider), `slack`, `github`. Adding a connector must be config-only.

### A3 — Routes (`backend/app/api/routes/portal_connectors.py`, auth = `get_portal_user` where applicable)
- `GET /portal/connectors` — the user's connection state: `[{connector_id, provider, connected, expires_at, scopes}]`.
- `POST /portal/connectors/{connector_id}/ticket` — (portal-JWT) mints a single-use connect ticket (random 256-bit, Redis `SETEX` ≤300s, value = user+tenant+connector), returns `{ticket, start_url}`.
- `GET /portal/connectors/oauth/start?ticket=...` — (no cookie auth; the ticket IS the auth) GETDEL the ticket, mint state+PKCE bound to the ticket payload (reuse `OAuthStateStore` semantics with a distinct key prefix `connector_oauth_state:`), 302 to the provider.
- `GET /portal/connectors/oauth/callback` — GETDEL state, exchange code (PKCE verifier), encrypt + upsert into `connector_tokens`, then 302 to `sran://oauth/callback?connector={id}&status=ok`. On any failure → `sran://oauth/callback?connector={id}&status=error&reason={coarse_code}` (no provider error text in the URL). Also render a minimal HTML fallback page ("You can close this window") since some browsers block custom-scheme redirects — the page contains a clickable `sran://` link.
- `DELETE /portal/connectors/{connector_id}` — (portal-JWT) delete the row; best-effort provider revocation if the provider supports it.
- `POST /portal/connectors/{connector_id}/token` — (portal-JWT; used by mcp-gateway on behalf of the user, forwarding the user's own JWT) returns `{access_token, expires_at}` ONLY — refreshing first via the refresh token when expired/near-expiry (60s skew). NEVER returns the refresh token. Rate-limit reuse: follow whatever per-route limiting exists in the portal routes (check; if none, a simple Redis counter — note what you did).

### A4 — Tests (pytest, follow existing route-test patterns)
Ticket single-use (second GETDEL fails → 400); state one-shot; callback stores encrypted tokens (assert ciphertext != plaintext, decrypts back); token endpoint refreshes when expired (mock provider), never leaks refresh token; cross-user access → 404/403; disconnect deletes.

# PART B — mcp-gateway: OAuth-aware per-user MCP instances (Node)

### B1 — Catalog additions
Add `oauth2` connectors to `SERVERS` in `server.js` — at minimum `github` (`@modelcontextprotocol/server-github`, token env `GITHUB_PERSONAL_ACCESS_TOKEN`) and `slack` (`@modelcontextprotocol/server-slack`, `SLACK_BOT_TOKEN`-style env — verify each package's actual env contract before wiring; if a package is impractical, document and pick an equivalent). Catalog entries get `auth_type:"oauth2"` and `server_url` pointing at this gateway's per-connector route.

### B2 — Per-user instance manager
New request path `POST /mcp/servers/{connectorId}/mcp` (align with `MCP_PUBLIC_MODE=path` conventions):
1. Require `Authorization: Bearer <portal JWT>`; validate signature with `PORTAL_JWT_SECRET` (new env, same secret the backend signs with — add to docker-compose) and extract `sub`/`tenant_id`.
2. Resolve the user's provider token: `POST {BACKEND_BASE}/portal/connectors/{connectorId}/token` forwarding the SAME bearer. Cache in-memory until `expires_at` − 60s.
3. Get-or-spawn the per-`(sub, connectorId)` child (stdio server + supergateway or an in-process bridge — follow how server.js already spawns things) with the token in env; proxy the JSON-RPC body through; keep `Mcp-Session-Id` semantics working.
4. Lifecycle: LRU with idle timeout (10 min) and a global cap (e.g. 20 children); kill + evict on token refresh failure (401 from backend → return a JSON-RPC error the desktop shows as NotConfigured).
5. `none`-auth servers (filesystem/memory) keep working exactly as today.

### B3 — Tests
Whatever test setup exists in the service (check `package.json`; if none, add minimal `node:test`): JWT rejected/accepted; instance reuse for same user, isolation across users (two subs → two children); idle eviction; catalog shape still matches the desktop contract (`id,name,description,category,auth_type,server_url,tools`).

# PART C — Desktop: deep link + broker flow (C#)

### C1 — `sran://` URI scheme
- Inno (`SranDesktop.iss` [Registry]): `HKCU\Software\Classes\sran` → `URL Protocol` + `shell\open\command` = `"{app}\SranDesktop.exe" "%1"` (per-user install, no elevation).
- Dev fallback: on startup, if the scheme is missing, register the same HKCU keys pointing at the current exe (best-effort, logged).

### C2 — Second-instance forwarding
Extend `SingleInstance.cs`: the second instance writes its args joined (or `"activate"` when none); the listener parses — `"activate"` →今日's behavior; a string starting with `sran://` → raise a new `DeepLinkReceived` event (plus still activating the window). Keep the protocol backward-compatible (an old second instance sending `"activate"` still works). `App.xaml.cs`: handle the URI both on cold start (in `e.Args`) and via the pipe event.

### C3 — OAuth connect flow
`ConnectorMarketplaceViewModel.ToggleInstallAsync`, `oauth2` branch (replace the paste dialog):
1. Call new `IChatEdgeApiClient`-style method on the PORTAL client (decide placement — the portal-authenticated HttpClient; recon how `AgentKeyProvider`'s client is wired): `POST /portal/connectors/{id}/ticket` → `{start_url}`.
2. `Process.Start` the system browser at `start_url`. Set connector status to "Connecting…" (new localized state).
3. On `sran://oauth/callback?connector=X&status=ok` → mark installed WITHOUT a DPAPI token (this is the key change: `InstalledConnector` gains an `auth_mode` so oauth2 installs carry no local secret), refresh status via `GET /portal/connectors` + a `PingAsync`. `status=error` → localized error message with the coarse reason.
4. `api_key` connectors keep the existing paste flow untouched.

### C4 — Calling oauth2 connectors
`McpConnectorService.GetOrCreateClientAsync`: for `auth_mode=oauth2`, the Bearer is the CURRENT portal access token (from `TokenSession` — recon how to reach it from Infrastructure; it must pick up refreshed JWTs, so resolve per-call or subscribe to session changes, don't snapshot once). `connector:{id}` DPAPI entries are neither written nor read for oauth2. Disconnect (uninstall) also calls `DELETE /portal/connectors/{id}` best-effort.

### C5 — UI + strings
Marketplace card states: NotConnected / Connecting / Connected / Error + a Disconnect action for oauth2. Dict keys (EN + natural TH): `connector.connecting`, `connector.connectFailed`, `connector.disconnect`, `connector.connectedAs` (optional), plus whatever the flow needs. Remove the now-dead oauth2 paste strings ONLY if unused elsewhere (`DictParityTests` must stay green).

### C6 — Desktop tests
SingleInstance protocol parse (pure part); deep-link URI parse (`connector`, `status`, `reason` — pure helper + tests incl. malformed/hostile input: wrong scheme, missing params, oversized values); ToggleInstall oauth2 path with mocked portal client (ticket→browser-open invoked via injected launcher, no InputDialog); GetOrCreateClient picks portal JWT for oauth2 and DPAPI token for api_key.

---

## Final verification checklist

1. All three stacks build/test green (state exactly what you ran per stack).
2. **Security review paragraph:** walk the full flow and confirm each invariant from Ground Rules — where tokens live at every step, what's in every URL, what a malicious app registering `sran://` first could learn (answer should be: connector id + status only), and why a stolen ticket or replayed state fails.
3. E2E trace (written, since no live run): user clicks Connect on Slack → … → tool call succeeds — list every hop with the auth artifact used at each.
4. Summarize changes file by file per stack + follow-up risks for Phase 8 (UI polish & leftovers: sidebar virtualization, toast system, unstyled controls, AutomationProperties, search-hit pull, agent feedback ids) WITHOUT implementing them.
