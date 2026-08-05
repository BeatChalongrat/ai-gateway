# RE-VERIFY on the running stack — FIX 1b (service-JWT active) + FIX 5 (run_python 200)

FIX 1b + FIX 5 are committed and unit-tested, but the whole lesson of this round is **"tests green + committed HEAD correct ≠ works at runtime"** — three bugs got through exactly that way (config/wiring, not code). So prove these two on the RUNNING stack, not just in tests. Read-ish + operational: you MAY recreate containers, run migrations, hit endpoints, read logs. Do NOT edit source to force a pass, do NOT commit, do NOT push. If something's still broken, report it — that's the point.

## Step 0 — Recreate so the fixes actually load
The running backend/gateway predates FIX 1b (it won't have `SERVICE_JWT_SECRET` in its env until recreated), and the sandbox predates FIX 5's host-path fix.
1. Ensure `.env` has `SERVICE_JWT_SECRET` (one value; both backend + chat-edge read it now) and `REDIS_PASSWORD` (FIX 5 preflight should pass — run it: `scripts/preflight.sh` or `make up`'s preflight; report if it complains).
2. Recreate the affected services with the corrected compose: backend/gateway (picks up `SERVICE_JWT_SECRET`) and, for the sandbox, the correct layering (`docker-compose.yml` + `docker-compose.webchat.yml` + `docker-compose.sandbox.yml`) so `SANDBOX_HOST_WORKDIR` renders to the absolute host path. Report the `docker compose config` values for both (`SERVICE_JWT_SECRET` present on backend; sandbox bind-source == `SANDBOX_HOST_WORKDIR` == absolute host path).
3. Health-gate: gateway :8080, chat-edge :4000, backend, sandbox-enabled chat-edge all healthy before asserting.

## Step 1 — FIX 1b: prove the service-JWT path is ACTIVE (not the INTERNAL fallback)
The FIX-1 goal was chat auth via the **service JWT**, decoupled from `gw_` keys. It was dormant (falling back to `INTERNAL_SERVICE_SECRET`). Prove it's now the real path:
1. **Startup is clean / loud as designed:** with `SERVICE_JWT_SECRET` now set on the gateway, confirm the gateway boots WITHOUT the FIX-1b wiring warning/failure (i.e. `service_jwt_wiring_issue()` returns None). Then, as a negative, temporarily unset the gateway's `SERVICE_JWT_SECRET` (env only, no code) and confirm it now fails-loud/warns (prod refuses / dev warns) — then restore. Report both.
2. **Service JWT accepted end-to-end:** send a chat message through chat-edge and confirm in the gateway logs / request trace that the request authenticated via the **service-JWT path** (resolved user from `X-User-ID`, `internal_trusted`), not merely the header-secret fallback. If you can distinguish them in logs, show the line; if not, describe how you confirmed the service JWT validated (e.g. flip only `SERVICE_JWT_SECRET` to a MISMATCH on the gateway and confirm chat now 401s on the service-JWT path — proving it's the path in use — then restore).
3. **Decoupling still holds:** with `GATEWAY_API_KEY` set to garbage/revoked, a user chat still succeeds (auth is the service JWT + user identity, not the `gw_` key). This is the headline — show it works.

## Step 2 — FIX 5: prove run_python returns a result (Section 5 unblocked)
1. Call the code interpreter (`run_python`) with a trivial script — `print(2+2)` — through the desktop tool path or directly at chat-edge's `/api/v1/code/run`. Confirm **200 with the result** (`4`), NOT the 503 "no result sidecar" from the host-path bug.
2. Confirm the per-run dir under the host `SANDBOX_HOST_DIR` was created and the uid-65534 sandbox user wrote `__meta.json` (check the dir, or just that the result came back — a 200 proves the write worked).
3. A matplotlib chart script → confirm an image/artifact comes back (the Section-5 / C.1 inline-image case).

## Step 3 — quick DLP re-confirm (nothing regressed)
Send the PII probe (`John Smith, john@acme.com, 555-123-4567, sk-test-abc123`) through the now-service-JWT-authenticated path and confirm the trace still shows masking (`<<PERSON_A>>` etc.) + secret withheld — i.e. changing the caller's auth (FIX 1b) did not change what gets masked.

## Report
- Step 0: preflight result, `docker compose config` proof (backend has `SERVICE_JWT_SECRET`; sandbox host path absolute), services healthy.
- Step 1: gateway boots clean with secret set / fails-loud without it (both shown); service-JWT path confirmed as the one in use; `gw_`-garbage chat still works (decoupling proven live).
- Step 2: `run_python` 200 with `4`; chart returns; per-run dir written by uid-65534.
- Step 3: DLP masking still holds on the service-JWT path.
- Anything still broken → report honestly, don't patch. Restore any env you toggled. Note whether you left containers running.
