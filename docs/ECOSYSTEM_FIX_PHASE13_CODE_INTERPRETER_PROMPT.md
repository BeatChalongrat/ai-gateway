# ECOSYSTEM FIX — PHASE 13: Sandboxed code interpreter (Claude/GPT-level, DLP-consistent)

The capstone. A real code interpreter — Python data-analysis the model can run on the user's files — matching ChatGPT Advanced Data Analysis / Claude's code execution in capability, but built to THIS system's DNA: **the gateway masks all provider egress, so the sandbox must be an extension of the gateway's trust boundary, not a hole in it.** This is the most security-critical phase in the program. Do not regress Phases 1–12. Do NOT `git push`.

## The architecture decision (decided — implement this; R0 validates it against constraints)

**Server-side hardened sandbox is the primary model** (what ChatGPT/Claude actually do), for reasons the recon proved:

1. **Data adjacency:** the authoritative copy of user files already lives server-side (chat-edge local disk / S3, materialized via `Backend.OpenLocalCopy` — `services/chat-edge-api/internal/storage/storage.go:70`). A client-side (Pyodide/WASM) interpreter would have to download already-uploaded files back to the browser — awkward, and web-chat has no worker/WASM/COOP-COEP today (greenfield, and COEP `require-corp` risks breaking the cross-origin chat-edge calls). Server-side has natural data adjacency and one implementation serves web AND desktop.
2. **DLP-consistency (the crown jewel):** the sandbox sits INSIDE the gateway trust boundary, like the NER sidecar. Raw user data enters the sandbox but **never reaches a third-party provider**. The model AUTHORS the code (masked in/out on the completion like any turn) and SEES the results (masked before they reach the provider on the next turn). So a code interpreter here is arguably MORE private than ChatGPT's — the analyzed data is behind a masking layer that ChatGPT's sandbox has no equivalent of. State and preserve this property explicitly.
3. **Capability:** full CPython + real pandas/numpy/matplotlib/openpyxl, real file sizes, pre-installed packages — genuine parity, not the Pyodide subset.

**Critical precondition the recon surfaced:** the EXISTING plan-exec (`excel_executor.py` etc.) runs **as root, in the chat-edge container, inheriting every secret (`DATABASE_URL`, `GATEWAY_API_KEY`, JWT/S3 creds), with full network** (`Dockerfile` no `USER`; `docker-compose.yml:297-341` no `user`/`read_only`/`cap_drop`/`security_opt`/network isolation). That is already a serious exposure. **Phase 13's first job is building a real isolated sandbox, and BOTH the existing plan-exec and the new free-form interpreter run inside it** — nothing model-influenced runs in the secret-bearing chat-edge process ever again.

Client-side Pyodide (pure-local-file analysis on desktop, data never leaving the machine at all) is a genuine future enhancement but is explicitly **out of scope here** — note it as a possible Phase 14, do not half-build it.

## Ground rules

- Read every file you touch IN FULL; do R0 first and answer it in the summary.
- Security is the deliverable. A code interpreter that isn't truly isolated is worse than none. Every isolation claim must be demonstrated by an actual break-out attempt that FAILS (see the verification section) — not asserted.
- Do not weaken DLP/masking; extend the egress-invariant CI gate to cover the sandbox.
- Tests + a written threat model are required. Finish state per stack: what you ran, green.

## R0 — Reconnaissance + decision validation (answer all)

1. Confirm the recon: chat-edge exec runs as root / inherits secrets / has network (`Dockerfile`, `docker-compose.yml:297-341`, `excel_service.go:157-176` `runScript`). Confirm `OpenLocalCopy` (`storage.go:70`, `local.go:155`, `s3.go:184`) is the file-materialization primitive to reuse.
2. The plan-exec dispatch surface: every place chat-edge shells to python (`excel_service.go`, `doc_service.go`) — these all move behind the sandbox.
3. Validate the server-sandbox decision against real constraints: expected data sizes, package needs, concurrency. If anything argues for a different model, say so with evidence BEFORE building — but the default is server-side sandbox.
4. Deployment: how services are built/composed (Dockerfiles, `docker-compose*.yml`, any k8s under `infra/`) — the sandbox is a new isolation boundary; determine whether it's a sidecar container chat-edge calls, or a per-run ephemeral container, or a locked-down subprocess with namespaces. Recommend based on what the infra supports.

---

## PART A — The sandbox (the security core)

Build a real isolation boundary. NOTHING model-influenced may run with secrets, network, or host access.

1. **Isolation model** (R0.4 picks the concrete mechanism; the REQUIRED properties are non-negotiable):
   - **Non-root**, `cap_drop: [ALL]`, `no-new-privileges`, a **seccomp** profile (default-deny syscalls beyond a compute allowlist).
   - **No network** — the exec must run in a network-denied context (`--network none` / separate netns). It must not be able to reach postgres/redis/gateway/internet. Prove it (a test that a python `socket.connect`/`urllib` inside the sandbox fails).
   - **Read-only rootfs** + a per-run **ephemeral tmpfs scratch**; the ONLY writable/readable user data is the specific input file(s) bind-mounted read-only (or copied into scratch), never the whole uploads volume.
   - **Stripped environment** — the child gets NONE of chat-edge's env (no `DATABASE_URL`, `GATEWAY_API_KEY`, JWT/S3 secrets). Prove it (a test that `os.environ` inside the sandbox contains no secret).
   - **Resource caps** — CPU time, wall-clock (hard kill), memory, PIDs, output size, file size. A fork bomb / infinite loop / 10 GB alloc / 10 GB stdout must all be contained, not crash the host.
   - **Ephemeral** — each run starts clean; no state leaks between runs or between users/conversations.
2. **Migrate the existing plan-exec into it** — `excel_executor.py`/`docx_generator.py`/etc. now run inside the sandbox via the new dispatch path, not in-process-with-secrets. This is a security upgrade to shipped functionality; its outputs (generated files) must still surface through the existing workspace/versioning path unchanged (regression-test the excel/doc generation end-to-end).
3. **Interface:** a narrow `execute(code_or_plan, input_files[], limits) -> {stdout, stderr, exit, artifacts[], truncated}` contract chat-edge calls. Timeouts/limits are server-enforced, never client-supplied beyond a capped request.

## PART B — The `run_python` code-interpreter tool

The model-facing capability, on top of PART A's sandbox.

1. **Tool contract** — a `run_python` (a.k.a. code interpreter) tool exposed to the model on both paths: the desktop agent loop (`IMcpTool` in the orchestrator) and the web/chat-edge tool path. Args: `{ "code": "...", "files"?: [file_ids] }`. The code runs in the sandbox with the named conversation files mounted read-only; captures stdout/stderr, the value of the last expression, and **matplotlib figures / generated files** (png charts, output xlsx/csv).
2. **Results back to the model** — text output + images (charts) returned as tool results, rendered in the UI like the existing tool-execution cards / inline media, and any generated files surfaced through the workspace/generated-files path (reuse Phase-5 versioning where it fits). The model iterates (write code → see result/plot → refine) — the core ADA/analysis loop.
3. **Wiring:**
   - **web-chat:** a tool the model calls during a completion; results stream into the message (reuse the tool-call/`fallback_info`/artifact rendering seams). R0: how tool calls surface in `chatStore.consumeStream`.
   - **desktop:** register the tool in `AppHostBuilder`; it runs through the SAME consent flow (`PermissionService`). Because the actual execution is server-side and sandboxed (no host access), its consent posture can differ from `run_command` (which touches the real machine) — but default to **Ask** on first use and let the user Allow-Always per conversation. State the chosen posture and why.
   - **gateway:** the model's code is authored and returned like any completion content — it flows through DLP mask/restore normally. Confirm nothing special-cases it around masking.
4. **Files:** reuse `OpenLocalCopy` to materialize input files into the sandbox; never mount the whole store. A run only sees files from ITS conversation, owned by ITS user (tenant/user scoped — test cross-user file access is denied).

## PART C — DLP / egress-invariant integration

1. The sandbox is INTERNAL trust boundary (like the NER sidecar) — it is NOT a third-party egress. Update the Phase 9 egress-invariant gate (`backend/tests/test_dlp_egress_invariant.py` / the `EGRESS_CLASSIFICATION` map, or a chat-edge equivalent) to classify the sandbox call path as `no_user_content` egress to third parties (it egresses to OUR sandbox, which has no network out). Add a test asserting the sandbox has no third-party network path (ties to PART A's no-network proof).
2. **State the DLP property in the summary and in `docs/DLP_ARCHITECTURE.md`:** the code interpreter runs on raw user data INSIDE the trust boundary; results reach a provider only via the masked completion pipeline; the sandbox has no network egress; therefore raw user data never reaches a third party through code execution. This is the differentiator — document it as a first-class privacy guarantee.

## PART D — Portal login rate limiter (bundled small fix)

**Verified:** `backend/app/api/routes/portal_auth.py` login/signup/password-reset have NO rate limiting; admin login DOES (`admin_session.py:175-223`) via the reusable `LoginRateLimiter` (`login_service.py:41`, sliding-window Redis + in-process fallback). Portal already has `_portal_redis_client()` (`portal_auth.py:51`).

Fix: wire a portal-scoped `LoginRateLimiter` (new key prefix `portal_login_attempts:`) as a `Depends(...)` on the `login` route mirroring admin — `check(email, ip)` before `auth_service.login`, `record_failure` on `AuthenticationError`, `record_success` on success — and extend to signup + password-reset/request. Honor the Phase 12 A4 posture (conservative on Redis outage for these credential routes). Tests: N+1 failed logins in-window → 429; success resets; Redis-down still throttles conservatively.

---

## Final verification checklist

1. Per stack: what you ran, green. The migrated plan-exec (excel/doc generation) still works end-to-end (regression). New sandbox + `run_python` + portal-limiter tests pass.
2. **Threat model + break-out proof (the headline deliverable):** a written threat model, and a test suite that ATTEMPTS and FAILS to break out — each of: (a) network egress from inside the sandbox (socket/urllib → blocked); (b) reading a secret from `os.environ` (empty of secrets); (c) reading a file outside the mounted input (e.g. `/etc/shadow`, another conversation's upload → denied); (d) resource exhaustion (fork bomb / infinite loop / huge alloc / huge stdout → contained, host survives, run killed); (e) writing outside scratch (read-only rootfs → denied); (f) persistence between runs (state does not leak). List each attempt and that it's contained.
3. **DLP statement:** confirm raw user data processed by the interpreter never reaches a third party (no-network sandbox + masked completion path), and the egress-invariant gate now covers the sandbox path.
4. **Claude/GPT parity statement:** what a user can now do (upload a CSV/xlsx, ask for analysis, get computed results + charts, iterate) and any honest gaps vs ChatGPT ADA / Claude (e.g. package set, session persistence, max data size) — as tracked notes, not silent omissions.
5. Summarize changes file by file per stack. This is the FINAL planned phase — end with a program-wide statement: all Phase 1–13 suites green, the full ecosystem test count, and confirm the masking invariant holds across every egress path including the new interpreter.
