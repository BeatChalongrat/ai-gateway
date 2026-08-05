# FIX 6 — Restore web-chat + finish the running-stack fixes (4 bugs, verify END-TO-END)

Four bugs found on the RUNNING stack (not in code review — all four had green tests + correct HEAD). Fix them so they actually work when a user clicks, not just when pytest runs.

**NEW RULE — definition of done for this fix:** a change is NOT done until it's proven END-TO-END on the running stack, not just unit-tested. We've now hit "tests green + committed correct ≠ works" **three rounds in a row** (FIX 1 dormant, FIX 1b activated a broken path, FIX 5 fixed one dir but not the other). So each part below has a **live-proof** step that must actually run against the stack. If you cannot run a live proof, say so explicitly and mark that part unverified — do not claim done.

Backend + chat-edge + compose. Do NOT `git push`, do NOT commit secrets, do NOT edit source to force a test pass.

---

## BUG 1 — `__webchat__` phantom project = ALL web-chat messaging is 502 (P0)

**Verified live:** the FIX-1 service-JWT path (`deps.py:248`) returns a synthetic `project_id="__webchat__"`, but `__webchat__` exists nowhere else — never seeded/migrated/created — so `project_service.py:42` raises `NotFoundError` → every chat 502s `Project '__webchat__' not found`. This path was dormant (chat used the `gw_` fallback); FIX 1b activated it and surfaced that **the service-JWT path has never worked end-to-end.** Restoring the real `gw_` key produced the identical 404 — the key is not the cause; the phantom project is.

R0 (decides the fix): trace how `project_id` is used DOWNSTREAM of `get_resolved_api_key` on the completion path — specifically **does quota/credit/billing require a real project row, or does it charge the user/tenant?** Read `portal_credit_check.py` (`check_portal_credit_async` — does it take `project_id` or `portal_user_id`/`tenant_id`?), `project_service.py:42` (why it's called at all on this path), and `request_logs` (is `project_id` a NOT-NULL FK, or nullable/attribution-only?). This tells you seed-vs-skip.

**Fix — pick per R0, but the outcome is fixed (chat works on the service-JWT path):**
- **Preferred if billing charges the user/tenant (not the project):** the service-JWT (web-chat) path should NOT resolve/require a project at all — a user's chat isn't a developer project. Skip the project lookup for `internal_trusted` service-JWT callers (make `project_id` optional/None on this path, or use a sentinel that downstream treats as "user-scoped, no project"), so billing hits the user and no `project_service` lookup fires. This matches the ChatGPT model (chat = user identity, projects = a developer/API concept).
- **If a real project row is genuinely required downstream (FK constraints, project-scoped quota):** seed a real per-tenant "web-chat" project (a migration or a startup seed, like the admin/portal seed) and resolve to it, instead of a phantom id. Every tenant that can chat must get one.

Either way: NO phantom id that isn't backed by a row. **Live proof (required):** with the service-JWT path active (SERVICE_JWT_SECRET set on both sides), a real user chat returns 200 with a streamed reply — show it. And confirm billing still attributes to the user.

## BUG 2 — sandbox `in` dir is 0o700 root-owned → uid-65534 can't read job.json (Section 5 dead)

**Verified live:** `sandbox.go:293` creates the `in` dir `0o700` (root-owned); the container runs `--user 65534:65534`, which can't traverse a 0700 root dir even though `job.json` inside is 0644 → `Permission denied: '/job/in/job.json'` → 502. FIX 5 fixed the `out` dir (0o777 + Chmod) but missed `in`. Proven: `in` at 0700 → denied; at 0755 → `{"ok":1}`.

Fix: `sandbox.go:293` — create the `in` dir with a mode uid-65534 can traverse+read (`0o755`, matching the `out`-dir treatment; the job files inside stay read-only 0644). Keep it least-privilege (755 dir + 644 files is fine — the sandbox only needs to READ inputs). **Live proof (required):** `run_python` with `print(2+2)` returns 200 `4`; then a matplotlib chart returns an image (Section 5 + the C.1 inline-image case).

## BUG 3 — `docker-compose.webchat.yml:39` hardcodes SERVICE_JWT_SECRET → FIX 1b dormancy returns on rotation

**Verified:** FIX 1b parameterized the secret in `docker-compose.yml` + `.prod.yml`, but `docker-compose.webchat.yml:39` still hardcodes the dev-default literal instead of `${SERVICE_JWT_SECRET:-…}`. It matches today by coincidence; the moment someone sets `SERVICE_JWT_SECRET` in `.env` (which prod requires via `:?`, and which rotation is the whole point of), the backend takes the new value while webchat's chat-edge stays pinned → mismatch → silent fallback → FIX 1's dormancy is back.

Fix: `docker-compose.webchat.yml:39` → `${SERVICE_JWT_SECRET:-<same dev default>}` so every service that signs/verifies service JWTs reads the ONE `.env` value. Grep the whole repo for any OTHER hardcoded `SERVICE_JWT_SECRET` / `service-jwt` literal and parameterize them too (there must be exactly one source of truth). **Live proof (required):** set `SERVICE_JWT_SECRET=<a rotated value>` in `.env`, recreate backend + both chat-edge instances, and confirm chat STILL works (backend + chat-edge both took the new value; no mismatch/fallback) — this is the exact scenario that's broken today.

## BUG 4 — secret detector misses short-but-real secrets (`sk-test-abc123`, 14 chars leaked raw)

**Verified live:** `sk-test-abc123` (14 chars) egressed to OpenAI verbatim (`action=allow`, no secret category), while realistic 47–51-char keys are masked (`<r0>`, cats=secret). This is a length/entropy threshold — probably deliberate to avoid false positives on any `sk-`-prefixed token, but a short real secret leaks, and the checklist's own probe confusingly "passes through."

R0: read the secret/credential detector (the regex/entropy rules for `sk-`/API-key patterns in the DLP registry) and the threshold.

Fix (decision — state it): the `sk-`/`gw_`/known-prefix families are **structured** credentials — an `sk-`/`sk-proj-`/`sk-ant-`/`gw_` prefix followed by ANY token body is almost certainly a real key regardless of length, so match on the PREFIX pattern (not just length/entropy) for these known families, while keeping the entropy threshold for generic high-entropy strings (to avoid false positives on random-looking non-secrets). So `sk-test-abc123` → blocked as `secret` (it has the `sk-` credential prefix), but a random 14-char English word isn't. Also fix the **logging inconsistency** noted: a 50-char key masked correctly but logged empty `dlp_categories` — the audit category must be populated whenever a secret is masked. **Live proof (required):** the exact probe `sk-test-abc123` now → `<r0>` / blocked with `secret` category, AND a realistic key still masks, AND a benign short string is NOT falsely flagged.

---

## Cross-cutting: re-confirm DLP on the service-JWT path
The earlier DLP check ran on the `gw_` path because the service path 404'd (Bug 1). Once Bug 1 is fixed, **re-run the PII probe THROUGH the service-JWT path** and confirm masking holds identically (`<<PERSON_A>>` etc. + secret withheld) — proving FIX 1b's auth change didn't alter masking. This was explicitly unverified before.

## Final verification (END-TO-END is mandatory)
1. Tests: pytest (project-skip/seed + detector) + go test (sandbox dir mode) green — but tests are necessary, NOT sufficient here.
2. **Live-proof table (the real bar):** for each bug, the running-stack evidence —
   - Bug 1: user chat 200 on the service-JWT path (not 502); billing attributes to the user.
   - Bug 2: `run_python` 200 `4` + a chart returns.
   - Bug 3: after setting a rotated `SERVICE_JWT_SECRET` in `.env` + recreate, chat still works (no dormancy).
   - Bug 4: `sk-test-abc123` masked; realistic key masked; benign short string not flagged; audit category populated.
   - Cross-cutting: PII probe masked on the service-JWT path.
   If any live proof couldn't run, mark it UNVERIFIED explicitly — do not claim done.
3. Summarize changes file by file. State the Bug-1 decision (skip vs seed) + why, and confirm there are no remaining phantom ids / hardcoded secrets / permission-inverted dirs. This finally makes web-chat work end-to-end on the decoupled auth path — the thing that's been green-but-broken for three rounds.
