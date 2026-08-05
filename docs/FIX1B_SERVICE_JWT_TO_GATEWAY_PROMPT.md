# FIX 1b — Actually wire SERVICE_JWT_SECRET to the gateway + fail loud on half-config

**FIX 1 shipped correct code but the config never reaches the gateway** — proven on the running stack. `_resolve_service_jwt` (backend `deps.py`) is right, but `SERVICE_JWT_SECRET` is set ONLY under the **chat-edge-api** service in compose (`docker-compose.yml:307`, `docker-compose.prod.yml:116`) — NOT under **backend/gateway** (the backend block at `docker-compose.yml:257` has `JWT_SECRET`, `PORTAL_JWT_SECRET`, `INTERNAL_SERVICE_SECRET`, but no `SERVICE_JWT_SECRET`). So `settings.service_jwt_secret == ""` on the gateway → `deps.py` returns None ("feature disabled") → **chat-edge mints service JWTs the gateway can never verify.** Chat still works only because it silently falls back to `INTERNAL_SERVICE_SECRET` — so FIX 1's decoupling goal is unmet AND it fails *silently*, the exact failure mode FIX 3 was built to kill.

This is backend/config, not a rewrite. Do NOT `git push`, do NOT commit secrets. R0 first.

## R0 — Confirm the exact state
1. Confirm `SERVICE_JWT_SECRET` appears ONLY under `chat-edge-api` in `docker-compose.yml` (:307) and `docker-compose.prod.yml` (:116), and is ABSENT from the `backend`/gateway service block (:257 area). Confirm both must hold the SAME value for the service-JWT path to validate (chat-edge signs, gateway verifies with `service_jwt_secret`).
2. Confirm the current fallback: with the gateway's `service_jwt_secret` empty, chat-edge's service JWT is rejected and the call succeeds only via `INTERNAL_SERVICE_SECRET` + `X-User-ID` (the older trust path). So today the intended FIX-1 mechanism is dormant.
3. Confirm chat-edge REQUIRES `SERVICE_JWT_SECRET` (Go `config.go`) — it does (from FIX 1 R0). So the two sides can be made to match.

## Fix

### Part A — Wire the secret to the gateway (both compose files)
Add `SERVICE_JWT_SECRET: ${SERVICE_JWT_SECRET:-<same dev default as chat-edge>}` to the **backend/gateway** service env in `docker-compose.yml` (and `docker-compose.prod.yml` with the `${VAR:?}` hard-required form matching how prod treats other secrets). Use the SAME env var so one `.env` value feeds both services and they always match. Verify: after this, gateway `settings.service_jwt_secret` is non-empty and equals chat-edge's `SERVICE_JWT_SECRET`.

### Part B — Fail loud on half-configuration (the real lesson)
The silent dormancy is the danger. Add a startup consistency check on the **gateway** (`main.py` lifespan / `config.validate_secrets()` — the existing secret-validation seam):

- The gateway can't see chat-edge's env directly, so the check is: if the service-JWT feature is *expected* to be on (there's a signal it should be — see below) but `service_jwt_secret` is empty → **fail loud** (refuse to start with a named error, mirroring FIX 3's philosophy), OR at minimum log a prominent WARNING at startup that "service-JWT auth is DISABLED; chat-edge service tokens will be rejected and fall back to INTERNAL_SERVICE_SECRET."
- Simplest robust signal: since chat-edge always mints service JWTs when it has the secret, and both are meant to be configured together, treat "empty `service_jwt_secret`" as a misconfiguration warning by default (it's the migration target; empty means the decoupling isn't active). If you want it strictly fail-closed, gate that behind a flag so a genuinely service-JWT-less deployment can still boot — but the DEFAULT should make the dormancy impossible to miss (loud log or refuse), never silent.
- Decide + justify: WARN-loud vs fail-closed. Given FIX 1's whole point was "no silent auth surprises," lean toward **fail-closed when a service-JWT secret mismatch/absence is detected in a deployment that has chat-edge wired** — but a plain gateway-only deploy (no chat-edge) must not be bricked. State how you distinguish.

### Part C — Prove the decoupling for real this time
The headline FIX-1 proof was a chat-edge unit test (it mints a service JWT) + a gateway unit test (it accepts one) — but nothing checked they share a secret in a running config. Add an integration-style test or a config-validation test that asserts: **the gateway's configured `service_jwt_secret` is non-empty in the compose/env defaults, and a service JWT signed with it validates end-to-end** (sign with the configured secret → `_resolve_service_jwt` accepts → resolves the user). And a negative: empty gateway secret → the startup warning/failure fires.

## Tests
- Gateway config: with `SERVICE_JWT_SECRET` set (as compose now does), `_resolve_service_jwt` validates a token signed with that same secret and resolves the user; with it empty, the startup guard warns/fails per the chosen behavior.
- Round-trip: a service JWT minted the way chat-edge mints it (iss/aud/HS256 with the shared secret) is accepted by the gateway (this is the check that was missing — the two halves using the SAME secret).
- No regression: `INTERNAL_SERVICE_SECRET` path still works; gw_/admin/portal auth unchanged; DLP unchanged.

## Final verification
1. `pytest` green. State what needed compose/live and how covered (the secret-matching is checkable from the config defaults + a sign/verify round-trip unit test).
2. **Decoupling-for-real proof:** with `SERVICE_JWT_SECRET` now on the gateway, a chat send authenticates via the **service JWT path** (not the INTERNAL_SERVICE_SECRET fallback) — show the gateway accepting the service JWT and resolving/billing the user. Then the headline still holds: a revoked `gw_` key doesn't affect chat.
3. **Fail-loud proof:** an empty/mismatched gateway `service_jwt_secret` in a chat-edge-wired deployment now produces a loud, named startup event — not a silent fallback.
4. Summarize changes (both compose files + the gateway startup check + tests). Note the deploy requirement is now self-enforcing (misconfig is loud), and that this finally makes FIX 1's decoupling active rather than dormant.
