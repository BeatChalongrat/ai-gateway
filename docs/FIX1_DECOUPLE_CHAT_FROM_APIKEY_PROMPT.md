# FIX 1 — Decouple chat from the gateway API key (ChatGPT/Claude-style session auth)

**The problem (proven at runtime by the smoke test):** chat-edge authenticates to the gateway's `/v1/chat/completions` with a single shared `gw_` key (`gw_4be18…`). That key was revoked during security cleanup → **every web-chat send now 502s → chat-edge soft-deletes the conversation** (chats vanish). This is an architectural mistake: **the whole chat product depends on one API key.**

**The target (how ChatGPT/Claude actually work):** the end user logs in and chats **against their own identity + quota** — they never see or depend on an API key. An API key is a *separate developer credential* for plugging external apps into the API; rotating/revoking it must NOT affect the chat UI. So:

- **Chat path** → authenticated by the **user's session (portal JWT)**, quota/billing charged to the **user/tenant**. Works regardless of any `gw_` key's state.
- **`gw_` API keys** → external/developer use only (third-party apps hitting the API directly). Independent lifecycle; revoke/rotate freely; zero impact on chat.

This is code across **gateway (backend) + chat-edge**, not a config rotate. Do NOT `git push`, do NOT commit secrets. Security-sensitive — R0 is mandatory and the auth model must only get *safer*, never looser.

## R0 — Mandatory reconnaissance (answer ALL before changing anything)

The whole fix hinges on getting the current auth model exactly right. Read and report:

1. **Why chat-edge uses a `gw_` key today:** confirm the Phase-1 finding that gateway `/v1/chat/completions` **rejects portal JWTs** and requires a `gw_` key (`backend/app/api/deps.py` `get_resolved_api_key`, the `gw_` verification path). Exactly what identities does `/v1/chat/completions` accept today, and how is tenant/user + quota resolved from a `gw_` key vs from a portal JWT?
2. **chat-edge → gateway call:** how chat-edge calls the gateway for completions (`services/chat-edge-api` — the gateway client, where the `gw_` bearer is attached, where `GATEWAY_API_KEY`/`gw_` is configured). Does chat-edge already have the end-user's portal JWT in-request (it authenticates the user with it) that it could forward or exchange?
3. **Quota/billing today:** how is chat usage metered and charged — per `gw_` key, per project, or per user/tenant? (`portal_credit_check`/credit deduction, `request_logs`.) The target charges the **user/tenant**; confirm the data model supports user-level quota or what needs adding.
4. **The desktop app's `gw_` key (`AgentKeyProvider`):** it self-provisions a `gw_` key too. Decide: does desktop ALSO move to session auth, or keep its `gw_` (it's a different client)? Recommend based on what's consistent — but the web-chat path is the must-fix.
5. **Service-to-service trust:** is there an existing internal/service token or mTLS between chat-edge and the gateway (a `SERVICE_JWT_SECRET` was seen in prod compose)? That may be the cleanest mechanism (see design options).
6. **The soft-delete-on-failed-send behavior:** where chat-edge soft-deletes a conversation when the first send fails — this is a bad failure mode regardless; note it for the safety fix below.

Report the current auth flow end-to-end (browser → portal JWT → chat-edge → gateway → provider) with file:line before proposing the change.

## Design — pick the mechanism in R0, but the property is fixed

The invariant to achieve: **a completion request is authorized by the end user's identity, and no user-facing chat depends on a `gw_` API key.** Two viable mechanisms (R0 decides which fits the codebase; both are how real systems do it):

- **Option A — Gateway accepts the portal JWT (or a short-lived derived token) for completions.** chat-edge forwards the user's portal JWT (or exchanges it for a scoped, short-lived service token) to the gateway; the gateway validates it, resolves tenant/user, enforces user quota. `gw_` keys remain a *second* accepted credential for external API callers. This mirrors ChatGPT (session for the app, API keys for developers).
- **Option B — Service-to-service token + on-behalf-of user id.** chat-edge authenticates to the gateway with a rotating *service* credential (not a user-facing `gw_` key) and passes the authenticated end-user id as a trusted on-behalf-of claim (the gateway trusts chat-edge as an internal caller and charges that user). The desktop would use the same internal channel. `gw_` keys stay external-only.

**Recommend Option A if the gateway can cleanly validate portal JWTs** (least moving parts, closest to ChatGPT). Use Option B if internal service trust already exists (`SERVICE_JWT_SECRET`) and per-request user-JWT forwarding is awkward. **State the choice + why in the summary.** Either way: user quota enforced, `gw_` decoupled from chat.

## Implementation requirements (mechanism-agnostic)

1. **Gateway (`backend`):** `/v1/chat/completions` (and `/v1/search` if chat-edge uses it) accepts the chosen chat-path credential and resolves **tenant + user** from it, enforcing **user/tenant quota** (not key-scoped quota). Keep `gw_` acceptance intact for external callers — this is ADDITIVE, do not remove `gw_` support. Auth failures still fail closed. No endpoint that required auth becomes unauthenticated.
2. **chat-edge:** stop depending on a configured `gw_` key for the chat path; use the user's session credential (forward/exchange per the chosen option). Remove the hard dependency on `GATEWAY_API_KEY` for user chat (it may remain for any genuinely internal non-user call, if such exists).
3. **Quota/billing:** chat usage is metered to the user/tenant. R0 confirms whether the schema already supports this; if a column/table is needed, add it via migration (Alembic). Over-quota returns a clean, typed error the UI can show ("you've reached your limit"), not a 502.
4. **`gw_` keys become external-only:** confirm (and document) that a revoked/rotated `gw_` key no longer affects the chat UI — that's the whole point. The portal's key management (create/rotate/revoke from Phase 7) stays for developer keys.
5. **Fix the soft-delete-on-failed-send** (safety, regardless): a failed first send must NOT soft-delete the conversation — surface an error and keep the chat. R0 located it; fix it so a transient provider/auth error leaves the conversation intact with an error state.
6. **DLP/masking untouched:** the masking pipeline sits inside the gateway completion path — changing the *caller's auth* must not change what gets masked. Confirm the egress invariant + streaming credential guard still apply identically on the session-auth path.

## Tests
- Gateway: a completion authorized by the chat-path credential (portal JWT / service+user) succeeds, resolves the right tenant/user, and charges user quota; a `gw_` key still works for external callers (no regression); over-quota → typed 4xx not 502; auth failure → fail closed.
- chat-edge: a user send succeeds with NO `gw_` key configured for the chat path (prove the decoupling — set the old `gw_` env to empty/garbage and chat still works); a revoked `gw_` key does NOT break user chat.
- Soft-delete fix: a failed first send leaves the conversation present with an error, not deleted.
- DLP: seed-PII through the session-auth completion path is still masked before egress (the smoke test's exact check — `<<PERSON_A>>` etc. — must still hold), egress invariant tests green.

## Final verification
1. Per stack: what you ran, green (backend pytest, chat-edge go test). State what needed a live gateway+DB and how you covered it.
2. **Decoupling proof (the headline):** with the chat-path `gw_` key removed/garbage, a user still logs in and chats normally — show the request authorized by session identity, charged to the user, masked by DLP. And a revoked `gw_` key affects only external API callers, not the chat UI.
3. **Security statement:** the change only ADDED an accepted chat-path identity (didn't remove `gw_` or open any endpoint); user quota enforced; fail-closed preserved; DLP/egress invariant identical on the new path.
4. Summarize changes file by file (gateway + chat-edge + migration if any). Note whether desktop moved to session auth too or kept its `gw_` (R0.4 decision), and any follow-up (e.g. desktop decoupling as a separate pass). This makes chat behave like ChatGPT/Claude: login-and-use, quota-based, API keys are a separate developer concern.
