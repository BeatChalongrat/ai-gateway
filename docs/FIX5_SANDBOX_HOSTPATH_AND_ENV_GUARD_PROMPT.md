# FIX 5 — Sandbox host-path bug + .env completeness guard (two runtime bugs found in prep)

Two config bugs found while preparing the manual test — both proven on the running stack, both a bad experience for a normal operator. Config/ops, not a rewrite. Do NOT `git push`, do NOT commit secrets. R0 first.

---

## Bug A — Sandbox `SANDBOX_HOST_WORKDIR` is a container path, not a host path (Section 5 / code interpreter is dead)

**Verified:** `docker-compose.sandbox.yml` sets `SANDBOX_WORKDIR: "/sandbox-runs"` (:43) AND `SANDBOX_HOST_WORKDIR: "/sandbox-runs"` (:49), while the volume mounts the host dir `${SANDBOX_HOST_DIR:-./data/sandbox-runs}:/sandbox-runs` (:58). But the sandbox code's own comment says `HostWorkDir` must be "the same staging root **AS THE DAEMON sees it** for `-v`" — i.e. the **host** path, not the in-container path. Setting it to `/sandbox-runs` makes chat-edge tell the Docker daemon to `-v /sandbox-runs:...` — the daemon creates an empty root-owned `/sandbox-runs` on the host, the unprivileged sandbox user can't write `__meta.json` → `PermissionError` → "no result sidecar" → the 503 you'd see on every `run_python`.

R0: read `services/chat-edge-api/internal/sandbox/sandbox.go` (how `SANDBOX_WORKDIR` vs `SANDBOX_HOST_WORKDIR` are used — the in-container staging path vs the `-v` host source), the `docker-compose.sandbox.yml` mount, and confirm exactly which value must be the host path. Reproduce the empty-root-owned-dir symptom to be sure (a marker-file check like the prep did).

Fix:
1. `SANDBOX_HOST_WORKDIR` must equal the **host** side of the bind mount — i.e. `${SANDBOX_HOST_DIR:-./data/sandbox-runs}` (an absolute host path the daemon can resolve; a relative `./data/...` may need resolving to an absolute path depending on how the daemon interprets `-v` — confirm and use the form that works, likely `${SANDBOX_HOST_DIR}` as an absolute path). `SANDBOX_WORKDIR` (`/sandbox-runs`, the in-container staging) stays as-is.
2. Ensure the host dir exists with permissions the unprivileged sandbox user (uid 65534) can write, OR that the per-run subdir is created world-writable/owned appropriately — R0 how the current per-run dirs get their ownership; the fix must let uid 65534 write `__meta.json` and the result sidecar.
3. Document in the sandbox compose header the host-vs-container path distinction so this isn't re-broken (it's a subtle, easy-to-invert config).
4. Verify: after the fix, `run_python` with a trivial script (`print(2+2)`) returns a result (not 503), and a matplotlib chart comes back — the Section-5 smoke items work.

## Bug B — `.env` missing `REDIS_PASSWORD` bricks `docker compose up` (the stale-container root cause)

**Verified:** the compose requires `REDIS_PASSWORD` (`${REDIS_PASSWORD:?}`), but a fresh/old `.env` didn't have it → `docker compose up` fails outright → nobody could recreate the stack → the backend sat 3 weeks stale at an old schema (that's the real origin of the smoke-test schema-drift blocker #2, and redis ran with NO `--requirepass` until this recreate). The `make bootstrap` (Phase 15) generates it, but stacks created before bootstrap — or `.env` files copied from `.env.example` — lack it.

R0: confirm `.env.example` ships `REDIS_PASSWORD` as a `changeme-*` placeholder (Phase 15 added it) and whether the placeholder value actually passes `${REDIS_PASSWORD:?}` (a non-empty placeholder does pass the `:?` guard but leaves a weak password). Confirm `make bootstrap` generates a strong one.

Fix (make the failure impossible-to-miss and self-healing):
1. **Preflight check:** add a `make` target (or a tiny `scripts/preflight.sh` invoked by `make up`/`make bootstrap`) that, before `docker compose up`, verifies `.env` exists and contains the hard-required vars (`REDIS_PASSWORD`, and any other `${VAR:?}` the chosen profile needs) with non-placeholder values — and prints a clear "run `make bootstrap` / set REDIS_PASSWORD" message instead of letting `docker compose` fail with a cryptic error. This turns the "3-weeks-stale" silent trap into an immediate, actionable message.
2. **Bootstrap idempotency:** confirm `make bootstrap` (Phase 15) fills a missing `REDIS_PASSWORD` into an EXISTING `.env` (not only when creating a fresh one) — if it only generates on fresh-create, extend it to backfill missing required vars into an existing `.env` (append, never overwrite existing values). So an operator with an old `.env` can `make bootstrap` and get unstuck.
3. **Doc:** the README/RUNBOOK setup section notes `REDIS_PASSWORD` is mandatory and that `make bootstrap` handles it — coordinate with the Phase-15 docs.
4. Do NOT commit any generated `.env` or password.

## Tests / verification
- Bug A: `run_python` returns a result on the running sandbox after the path fix (state the before-503 / after-200 evidence); the per-run dir is writable by uid 65534.
- Bug B: the preflight check fails loudly with a named message when `REDIS_PASSWORD` is absent, and passes when present; `make bootstrap` backfills a missing `REDIS_PASSWORD` into an existing `.env` without touching other values.
- No regression: a correctly-configured `.env` + sandbox still come up normally.

## Final verification
1. What you ran (compose config validate, a `run_python` smoke, a preflight dry-run with/without `REDIS_PASSWORD`).
2. **Sandbox proof:** `run_python` now returns output + a chart (Section 5 unblocked).
3. **Env-guard proof:** a missing `REDIS_PASSWORD` now yields an immediate actionable error, not a cryptic compose failure or a silently-stale stack.
4. Summarize changes (sandbox compose host path + comment, preflight/bootstrap, docs). Note these two close the root causes behind the smoke-test's stale-container class and the dead code interpreter — the deploy story is now self-diagnosing.
