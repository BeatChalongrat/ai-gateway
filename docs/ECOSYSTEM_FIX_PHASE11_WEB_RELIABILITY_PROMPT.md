# ECOSYSTEM FIX — PHASE 11: Web reliability (+ ci.yml restore)

Starts with a one-line git hygiene fix, then the web-reliability phase: the three Next.js apps (`apps/web-chat`, `portal`, `admin-ui`) have **zero automated tests** and a few real bugs, plus one residual masking-heart gap in the backend (streaming DLP tail window). Do not regress Phases 1–10. Do NOT `git push` — leave that to the user.

---

## STEP 0 — Restore the truncated ci.yml (do this first, standalone)

The committed CI workflow (`be5cb23`) is complete and correct, but the WORKING-TREE copy of `.github/workflows/ci.yml` was truncated after commit — 52 lines deleted (the dependency-audit job tail), verified via `git diff --ignore-all-space --stat` showing real deletions, not EOL. Restore the working tree to the good committed version:

```
git checkout -- .github/workflows/ci.yml
```

Then confirm: `git status` is clean, and `git show HEAD:.github/workflows/ci.yml` still contains the `mcp-gateway-test`, `desktop-test` (windows-latest), and DLP egress-gate jobs. Do NOT re-edit the workflow in this phase beyond what PART B requires. Report that the tree is clean before proceeding.

---

## Ground rules (Phase 11 proper)

- Read every file you touch IN FULL; do each PART's R0 recon first and answer it in the summary.
- The three web apps have NO test tooling today — PART B establishes it. Prefer **Vitest + @testing-library/react** for unit/component and **Playwright** for one e2e happy-path (confirm in R0 what, if anything, exists; Next 14 App Router).
- Do not weaken DLP/auth. The masking heart stays intact; PART D only tightens it.
- Every bug fix ships with a test that fails before / passes after (now that there's a harness).
- Wire the new web tests into the CI jobs (the web-chat lane already runs typecheck — extend it to run the unit tests; keep Playwright as a separate, non-blocking-or-clearly-marked job since it needs a built app).
- Finish state per stack: state what you ran and that it's green.

---

## PART A — web-chat P0 correctness (does-it-work-in-prod)

### A1 — Remove shipped debug logging
`apps/web-chat/src/stores/chatStore.ts:565-571` — the `[DEBUG delete]` `console.log` (logs conversation id + all folder ids on every delete; comment says "remove after diagnosis"). Delete it. Grep the whole `apps/web-chat/src` for other stray `console.log`/`console.debug` in hot paths and remove or downgrade the accidental ones (keep intentional `console.error` in catch blocks). Report what you found.

### A2 — Production cross-origin cookie (the "works in prod" fix)
**Verified:** web-chat has NO Bearer fallback — every authed fetch (`lib/api.ts` `raw()`, `credentials:"include"`) AND every blob fetch (`AuthedImage`, `VideoPlayer`) depends entirely on the `portal_*` cookies reaching the chat-edge origin (`:4000`, a different origin in prod). Comments across `AuthedImage.tsx`, `VideoPlayer.tsx`, `MessageAttachments.tsx`, `ConversationFiles.tsx`, `api.ts:358-372` falsely claim a "Bearer token" is used — factually wrong and hides this dependency.

R0: read how cookies are SET (which service sets `portal_access_token`/`portal_refresh_token`, the `SameSite`/`Domain`/`Secure` attributes, and `.env*.example` for `AUTH_COOKIE_DOMAIN`). Confirm the prod topology (are web-chat and chat-edge same parent domain?).

Fix: (a) **Correct the false comments** to describe the actual cookie-based cross-origin model — they actively mislead. (b) Ensure the cookie-setting code path emits `SameSite=None; Secure; Domain=<shared parent>` in production (driven by an env var, not hardcoded), and document the required prod env (`AUTH_COOKIE_DOMAIN` etc.) in `.env.production.example` or the deploy doc. (c) Add a startup/config assertion (or a clear doc-checked note) so a misconfigured cookie domain in prod fails loudly rather than silently breaking image/file/media rendering. This is a config + docs + comment-correctness fix; do not invent a Bearer path.

### A3 — Media-gen playback bypasses auth
**Verified:** `VideoGenForm.tsx:290` and `AudioGenForm.tsx:212` set `<video src={job.output_url}>` / `<audio src=...>` directly to a raw gateway URL (flagged `PROXY-PENDING`), unlike `AuthedImage`/`VideoPlayer` which fetch a blob through the authed client. These will 403/404 whenever the media route requires auth (and definitely under the cross-origin cookie regime).

Fix: route media playback through the same authenticated blob-fetch pattern as `VideoPlayer`/`AuthedImage` (fetch → object URL → `src`). Remove the stale `PROXY-PENDING` and the incorrect `api.ts:115` "these will 404" comment (the media endpoints DO exist — `router.go:197-200`). Test: the media player component requests its bytes via the authed client, not a bare `<video src>`.

---

## PART B — Web test harness (the biggest quality gap)

Establish tooling in all three apps and cover the highest-risk logic first. R0: confirm each app's `package.json` has no test runner today; pick Vitest (fast, Vite/Next-friendly) unless one already leans jest.

Priority coverage (web-chat, the riskiest):
1. **`lib/stream.ts` SSE parser** — frame splitting on `\n\n`, partial frames across chunks, the `[DONE]` sentinel, and (see E4) CRLF `\r\n\r\n`.
2. **`chatStore.consumeStream`** (~350 lines of SSE event handling) — `message_start` id-swap, `content_delta` accumulation + RAF flush, `reasoning_delta`, `fallback_info`, cancel, error terminal, `message_complete`. Mock the stream; assert store transitions.
3. **`lib/api.ts` + `portal/lib/api.ts` single-flight 401→refresh→retry** — concurrent 401s trigger exactly one refresh; retry replays the original request; refresh failure logs out cleanly.
4. **Optimistic-update/rollback** in `chatStore` mutations (send/delete/rename) — success path and server-error rollback.
5. **One Playwright happy-path** — login handoff portal→web-chat, send a message, assert streamed tokens render. Mark it clearly (needs a running/built app + mock backend); keep it out of the required fast lane.

portal/admin-ui: at minimum smoke tests for their `lib/api.ts` refresh/auth and the CSRF header attach (admin-ui) — depth can be lighter than web-chat.

Wire the unit suites into CI (extend the existing web-chat lane; add portal/admin-ui lanes or a shared web job). Report the coverage added and the exact CI change.

---

## PART C — Cross-service contract tests (catch endpoint drift)

The audit found real drift risks: desktop sends `video_mode` that the server "reads image_mode only"; several desktop calls are wrapped in tolerant 404/503 handling because routes have been unstable; web-chat had a stale "will 404" comment about media routes that DO exist.

Goal: a test layer that FAILS when a client and the server disagree on an endpoint's existence/shape.

R0: decide the cheapest effective mechanism — options: (a) a shared JSON/OpenAPI-ish contract snapshot that both the chat-edge router registration and the client method lists are checked against; (b) a test that asserts every path string the web-chat/desktop clients call is a registered route in `services/chat-edge-api/internal/router/router.go` (parse the router, parse the client call sites); (c) lightweight consumer contract tests hitting a mock server built from the router's route table. Pick one, justify, and implement it for the **web-chat ↔ chat-edge** surface at minimum (desktop ↔ chat-edge is a bonus — its client is C#, harder to introspect from a JS/Go test; a documented path-list check is acceptable there).

Concretely catch: a client calling a path the router doesn't register → fail; the `video_mode` mismatch → at least a documented, tested assertion of which fields the server actually honors. Report exactly which drifts this would now catch.

---

## PART D — Backend: streaming DLP tail window (masking-heart residual)

**Verified:** `backend/app/api/routes/public_chat.py:1183` `MIN_TAIL_CHARS = 120`. During streaming, the outbound DLP scan holds back only a 120-char tail before emitting; a sensitive pattern LONGER than 120 chars whose leading fragment isn't individually a detector match can be emitted before the full match forms. PEM private keys, long DB connection strings, long secrets are exactly this shape — and these are the credential class the system must never leak, even on the intentional-raw channels.

R0: read the streaming DLP buffering logic around `public_chat.py:1173-1200` (how the tail is held/flushed, how it interacts with token restore) and the longest credential-class detector pattern length in the DLP registry.

Fix: make the held-back tail window **adaptive** — at least the length of the longest credential/secret-class pattern (not a fixed 120), OR gate the credential/secret categories to full-buffer-until-complete on the streaming path (never emit a partial that could be the head of a credential). Identity-class latency behavior can stay as-is; this is specifically about not leaking a boundary-split SECRET. Keep the streaming UX acceptable for normal text.

Tests: a streamed response containing a >120-char PEM key / long connection string split across SSE chunks → the secret is NEVER partially emitted (assert no chunk contains a raw fragment of it before it's masked). Add to the existing streaming-DLP test file.

---

## PART E — Web hardening (cheap, real, from the audit)

1. **Stale Bearer comments** (beyond A2's) — sweep `AuthedImage.tsx`, `VideoPlayer.tsx`, `MessageAttachments.tsx`, `ConversationFiles.tsx`, `api.ts` and correct every comment claiming a Bearer/Authorization header is used (there is none). Delete the stale `api.ts:115` "will 404" media comment.
2. **Collision-proof optimistic ids** — `chatStore.ts` `` `tmp-user-${Date.now()}` ``/`tmp-asst-...` (~:797-798) can collide on rapid double-send. Use `crypto.randomUUID()` (or append `Math.random()` as `addPendingFile` already does). Test: two sends in the same tick get distinct ids.
3. **OAuth token in JS** — `portal/app/api/auth/[...nextauth]/authOptions.ts` + `oauth-complete/page.tsx` expose `portal_access_token`/`portal_refresh_token` to client JS via `getSession()` before the cookie-exchange. R0: confirm whether the backend `/oauth/verify` could set the HttpOnly cookies directly, eliminating the JS-visible window. If feasible, do it; if it's a larger change, document the risk precisely and leave a tracked note (don't half-migrate).
4. **CRLF in the SSE parser** — `lib/stream.ts` splits on `\n\n` only; handle `\r\n\r\n` too (valid SSE). Covered by the PART B parser tests.
5. **Portal CSRF** — portal relies solely on `SameSite=Lax` (admin-ui has double-submit CSRF). Adding a CSRF token to portal mutations is defense-in-depth consistency. R0: assess effort; implement if contained, else document as a tracked follow-up (don't leave it half-done).

For E3 and E5, a *documented, tracked* deferral is an acceptable outcome if the change is large — but say so explicitly; don't silently skip.

---

## Final verification checklist

1. STEP 0: `git status` clean, ci.yml intact (all jobs present). PART B/C/D: state exactly what you ran per stack and that it's green; the new web unit suites run in CI.
2. **Prod-readiness statement (written):** walk the web-chat auth path and confirm what a correctly-configured prod deployment needs (cookie domain/SameSite/Secure), and that image/file/media rendering now works under cross-origin cookies (A2/A3) — or precisely what env the operator must set.
3. **Masking statement:** confirm PART D means a boundary-split credential can no longer be partially emitted on the streaming path; identity-class behavior unchanged.
4. Summarize changes file by file per stack; list what you deferred (E3/E5 if applicable) as tracked notes. List Phase 12 (feature depth) follow-ups WITHOUT implementing: web LaTeX/math + code-interpreter sandbox; desktop local Office generation, sub-agents, sandboxed code execution, MCP stdio.
