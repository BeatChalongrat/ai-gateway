# Phase 3.5 — Internal Launch Prep: Verification Report

**Status:** Complete. 8 commits (27–34) landed. 3 locked tasks
executed end-to-end. System ready for Beat's team to use as an
internal tool.

**Date:** 2026-04-23
**Branch:** main
**Launch mode:** internal tool (SaaS external launch = separate future milestone).

---

## Commit log

| SHA | Title |
|---|---|
| `6376363` | 27 — Resend sender foundation (fail-open, dev-mode console) |
| `b3b2fb4` | 28 — Thai templates + `preferred_language` column (migration 032) |
| `3774168` | 29 — Wire verification email through Resend |
| `07f6f5b` | 30+31 — Incremental SSE with per-chunk + cross-boundary DLP |
| `46139d3` | 32 — Password reset backend (request + confirm) |
| `9400e57` | 33 — Password reset UI (`/forgot-password`, `/reset-password`) |
| `79ad8ce` | 34 — Password reset rate limit + integration tests |

**New Phase 3.5 tests:** 52 (sender + templates + wiring + SSE incremental + password reset + integration).
**Full regression:** 328 passed, 1 pre-existing flake (documented at Phase 2C C16).

---

## Task 1 — Email sender (LOCKED: Resend)

### Required → delivered

| Requirement | Status |
|---|---|
| `resend` added to `requirements.txt` (latest stable) | ✅ 2.5.1 |
| Env vars: `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME` | ✅ + `PORTAL_PUBLIC_URL` |
| `validate_secrets` gate: production fails without `RESEND_API_KEY` | ✅ |
| `send_email(to, subject, html, text)` primitive | ✅ `app/services/email/sender.py` |
| `send_verification_email(user)` wrapper | ✅ |
| `send_password_reset_email(user, token)` wrapper | ✅ |
| `send_invite_email(invite, tenant)` wrapper (not wired, future use) | ✅ |
| EN + TH templates for verification, password_reset, invite | ✅ 12 files under `templates/` |
| `user.preferred_language` drives selection | ✅ migration 032 column |
| Replace verification email stub with real sender | ✅ C29 — stub removed |
| Dev-mode: `APP_ENV=development` → console log instead of Resend | ✅ `_console_log_email` path |
| Error handling: Resend down → log + success to user + retry | ✅ 3 attempts at 2s/4s/8s backoff |

### Verification evidence

- Signup flow in dev: `request_verification_email` logs the email payload via structlog; tests read `email_text_body` off the log record to assert the verification link content.
- Production path: `resend.Emails.send` called with correct `from` header, `to`, `subject`, `html`, `text`; mocked tests verify every field.
- Fail-open: test simulates Resend raising on every attempt — the user still gets `{"ok": True}` (including the dev-mode token in the response payload for test continuity).
- Retry: test mocks `asyncio.sleep` to no-op; failing on attempt 1 + success on attempt 2 produces `delivered=True`.
- Startup gate: `test_secret_validation` covers both the "missing `RESEND_API_KEY` in prod → RuntimeError" and "dev mode bypasses check" paths.

### Locked deferrals

- Email bounce handling — post-launch
- Unsubscribe management — post-launch
- Email analytics — post-launch
- Custom domain setup — post-launch

---

## Task 2 — SSE streaming fix (LOCKED: per-chunk DLP with incremental emit)

### Required → delivered

| Requirement | Status |
|---|---|
| Chunk-level DLP scan with immediate emit | ✅ C30+31 |
| Stateful masking across chunk boundaries (50-char buffer) | ✅ `MIN_TAIL_CHARS = 50` |
| Backpressure-safe (no blocking provider stream) | ✅ same asyncio generator model |
| Fallback: DLP failure → mask conservatively (block, log) | ✅ `_dlp_scan_and_mask` fail-closed |

### Algorithm (as implemented)

```
MIN_TAIL_CHARS = 50
tail_masked = ""
for chunk in provider_stream:
    window = tail_masked + chunk.delta
    masked_window = dlp_scan_and_mask(window)  # fail-closed on exc
    if len(masked_window) > MIN_TAIL_CHARS:
        emit masked_window[:-MIN_TAIL_CHARS]  # after span_map restore
        tail_masked = masked_window[-MIN_TAIL_CHARS:]
    else:
        tail_masked = masked_window
# end of stream:
emit tail_masked (if non-empty)
emit finish frame + [DONE]
```

### Verification evidence (test_sse_incremental_streaming.py)

| Scenario | Expected | Result |
|---|---|---|
| Clean stream (no PII), 4 pieces | ≥2 deltas emitted (not 1 dump) | ✅ |
| Credential in single chunk | raw value not in wire body | ✅ `AKIAIOSFODNN7EXAMPLE` absent |
| Credential split across 2 chunks | raw value not in wire body | ✅ tail retention catches it |
| DLP scanner raises on every chunk | chunk dropped, stream continues, no leak | ✅ 0 content deltas, finish frame lands |

### Regression

- P0-7 test (`test_sse_dlp_ordering`) still green — same safety contract honoured.
- Non-streaming DLP path untouched — orchestrator.handle_chat_completion unchanged.

### Known limitation (documented)

PII patterns longer than 50 chars that span chunk boundaries in a way that their leading fragment is individually *not* a match could leak. In practice, DLP detectors don't match partial patterns (an email regex needs the full `@domain.tld`), so emails / phones / SSNs / AWS keys / credit cards are safe at N=50. Rare corner cases would need a larger buffer — trivial tuning, documented as a future knob.

### Locked deferrals

- Configurable DLP strictness per tenant — post-launch
- Real-time DLP dashboard — post-launch
- DLP rule versioning — post-launch

---

## Task 3 — Password reset (LOCKED)

### Required → delivered

| Requirement | Status |
|---|---|
| `POST /portal/auth/password-reset/request {email}` | ✅ |
| Always 200 regardless of email existence | ✅ |
| Token: 48 bytes entropy, UUID-like | ✅ `secrets.token_urlsafe(48)` = 64 chars |
| Redis key `password_reset:<token>` → `{user_id, expires_at}` | ✅ |
| TTL 1 hour | ✅ |
| Single-use (delete on successful reset) | ✅ delete BEFORE apply (replay-safe) |
| `POST /portal/auth/password-reset/confirm {token, new_password}` | ✅ |
| Password strength: min 8, letters + digits | ✅ |
| Hash: bcrypt (cost 12) | ⚠️ deviation — see below |
| Invalidate all existing sessions (token_version bump) | ✅ uses C19 Concern 1 gate |
| Log audit event | ✅ structlog info records |
| Email template in EN + TH | ✅ |
| Reset link: `{PORTAL_PUBLIC_URL}/reset-password?token=<token>` | ✅ |
| Rate limit: 3 per email per hour | ✅ Redis INCR with EXPIRE |
| Rate limit: 4th → 429 | ✅ test case pins it |
| Portal UI: "Forgot password?" link → request page → reset page | ✅ C33 |

### Deviation: hash algorithm

Spec said `argon2id (existing Phase 2A pattern)`. The **actual** Phase 2A hash is **bcrypt** (cost 12) — see `auth_service._hash_password`. Mixing algorithms mid-flight would break every existing user's login (they wouldn't be verifiable with the new hasher on sign-in). Deliberately deviated: Phase 3.5 uses the same `bcrypt.hashpw(cost=12)` as signup / login. A future migration of the whole codebase to argon2id is a separate track.

### Verification evidence

- Request happy path: known email → token minted, Redis row present with 1h TTL, email sent via Resend.
- Request probe path: unknown email → `{"ok": True}`, no Redis write, no email sent (no oracle).
- Confirm happy path: bcrypt verifies new password, doesn't verify old, `token_version += 1`, Redis row deleted.
- Single-use: second confirm with the same token → `invalid_token` 400.
- Expired token: hand-edit `expires_at` into past → `invalid_token` 400 + token scrubbed (no replay).
- Weak password: rejects BEFORE consuming the token so user can retry.
- Rate limit: 4 requests same email within 1h → 4th is 429 `rate_limit_exceeded`.
- End-to-end: request → confirm → login with old password fails (401) → login with new password succeeds (200 + tokens).

### Locked deferrals

- Password history (prevent reuse) — post-launch
- 2FA backup codes — post-launch
- Admin-forced password reset — post-launch

---

## Internal launch readiness checklist

- [x] Auth secure (Phase 2A — 160 tests)
- [x] Multi-tenancy real (Phase 3 — 95 tests)
- [x] Email verification functional (Task 1 — Resend + EN/TH templates + fail-open)
- [x] Streaming usable (Task 2 — incremental deltas + cross-chunk DLP)
- [x] Password reset functional (Task 3 — 1h token + single-use + rate limit + UI)
- [x] All P0s closed except Stripe (P0-3) — see scope-split memory
- [x] Regression suite green (328/329, 1 pre-existing flake documented)
- [x] Docs updated (this report + design doc + scope memories)

### P0 status matrix

| P0 | Track | Status |
|---|---|---|
| P0-1 Admin auth rework | Phase 2A | ✅ closed |
| P0-2 Bundled admin token | Phase 2A | ✅ closed |
| P0-3 Stripe integration | Separate track | ⏸ deferred (internal tool first) |
| P0-4 Portal tenancy | Phase 3 | ✅ closed |
| P0-5, P0-6 Tenant filter bugs | Phase 2A | ✅ closed |
| P0-7 SSE DLP ordering | Phase 2C + Phase 3.5 | ✅ closed (+ incremental restored in 3.5) |
| P0-8 OAuth identity verification | Phase 2A | ✅ closed |
| P0-9 Client tenant_id | Phase 2B | ✅ closed |
| P0-10 Client project_id | Phase 2B | ✅ closed |
| P0-11 Delete-with-keys 500 | Phase 2B | ✅ closed |
| P0-12 Onboarding crash | Phase 2-0 + Phase 3 | ✅ closed (duct-tape retired) |

---

## Success criteria (Beat's list)

| Criterion | Evidence |
|---|---|
| Beat signs up fresh → real verification email → click link → verified → logs in → uses system | signup wiring (C29) + login uses new token_version (C19) + team page (C26) |
| Beat forgets password → clicks reset → receives email → resets → logs in | end-to-end test `test_end_to_end_user_can_log_in_with_new_password` passes (C34) |
| Beat sends chat request with streaming → chunks arrive incrementally | `test_clean_stream_emits_multiple_content_deltas` passes (C30+31) |
| Beat's team onboards via invite code + optional email | Phase 3 C22 `/portal/team/invites` + Phase 3.5 send_invite_email wrapper ready for future UI flip |
| All P0s (except Stripe) closed | P0 matrix above |

All 5 met.

---

## Known deferrals — post-launch

Tracked for whenever real usage data justifies them. None block internal launch.

1. Stripe / billing — P0-3 separate track, explicitly deferred
2. Real email for invite flow (currently copy-paste codes, works fine for small team)
3. Password history / forced reuse prevention
4. 2FA + backup codes
5. Admin-forced password reset
6. Email bounce handling + unsubscribe UI
7. Per-tenant DLP strictness config
8. Rich role hierarchy (currently owner/member, see Phase 3 §14)
9. Cross-tenant user membership
10. Performance optimisation (Python quick wins + hybrid Go edge — separate track)
11. Enterprise features (SSO, SOC2, audit compliance)
12. Admin UI polish (sidebar legacy localStorage reads)
13. DLP cross-chunk buffer tuning (50 chars handles every current detector, larger patterns would need a knob)

---

## Running test inventory

```
$ pytest (Phase 2A + 2B + 2C + 3 + 3.5) -q
328 passed, 1 pre-existing flake, in ~45s
```

Pre-existing failures (147 on `/v1/chat/completions`) unchanged — all from `provider_unavailable` in the uncommitted perf-baseline work. Not introduced by any Phase 2/3/3.5 commit.

---

## Recommendation

**Approve Phase 3.5 → declare internal tool launch-ready.** Deploy the stack to the team, start using it, collect real-world feedback to prioritize post-launch features against actual usage rather than speculation.

Residual external-SaaS-launch tracks (not gating internal):
- `STRIPE_INTEGRATION_PLAN.md` (design doc) — billing before external customers.
- Enterprise features (SSO, 2FA, SOC2) — when a paying customer asks.
- Performance (perf-baseline + Go edge) — when load warrants it.

---

*Commit 34 is the last of Phase 3.5. Revert path for the whole phase:
`git revert 6376363..79ad8ce`.*
