# DLP Mask/Unmask Accuracy Report

**Date**: 2026-05-12
**Scope**: 8 detector categories + 6 round-trip scenarios
**Test harness**: `tests/test_dlp_accuracy.py` + `tests/integration/test_dlp_roundtrip_live.py`
**Reproduce**: `pytest -s tests/test_dlp_accuracy.py tests/integration/test_dlp_roundtrip_live.py`

---

## 1. Accuracy table (curated corpus, NER on)

| Detector       | Precision | Recall |  TP | FN |  FP | Notes |
|----------------|----------:|-------:|----:|---:|----:|-------|
| email          |     1.000 |  1.000 |   4 |  0 |   0 | All 4 caught (incl. Thai context, `+tag@subdomain`) |
| phone_number   |     1.000 |  0.750 |   3 |  1 |   0 | Missed Bangkok `02-555-1234` format |
| credit_card    |     0.750 |  1.000 |   3 |  0 |   1 | False positive on Luhn-valid 16-digit order number |
| thai_id        |     1.000 |  0.333 |   1 |  2 |   0 | Misses *unformatted* 13-digit IDs (no dashes) |
| passport       |     1.000 |  0.500 |   1 |  1 |   0 | Misses Thai-prefixed `หนังสือเดินทาง AB123456` |
| credential     |     1.000 |  0.500 |   2 |  2 |   0 | Misses real `sk-ant-*` and `sk-proj-*` formats |
| thai_name      |     1.000 |  0.500 |   1 |  1 |   0 | NER missed prefixed `ลูกค้าชื่อ X` form |
| ip_address     |     1.000 |  1.000 |   3 |  0 |   0 | All 4 forms caught |
| **OVERALL**    | **0.947** | **0.720** | **18** | **7** | **1** |  |

**Overall precision 94.7%** — when the gateway flags something, it's almost always real PII.
**Overall recall 72.0%** — about 28% of curated PII samples slipped past detection.

---

## 2. Round-trip results

| #     | Scenario                                          | Result | Notes |
|-------|---------------------------------------------------|:------:|-------|
| RT-01 | Email mask → echo → unmask (non-stream)           |   ✓    | Provider saw `[EMAIL_0]`; user received `john@acme.com` |
| RT-02 | Multiple PII types in one message                 |   ✓    | THAI_NAME / EMAIL / PHONE_NUMBER all restored |
| RT-03 | Credential blocked (`Authorization: Bearer …`)    |   ✓    | HTTP 403, `dispatch_count=0` — provider never called |
| RT-04 | AI hallucinates new PII (outbound scan)           |   ✓ *  | `hacker@evil.com` masked to `[EMAIL_N]` — only with `policy_profile_id="strict"`; default profile lets PII pass outbound |
| RT-05 | Streaming round-trip                              |   ✓    | 3-chunk stream assembled to restored body via 120-char rolling tail |
| RT-06 | Multi-turn orphan resolution                      |   ✓    | Turn-2 echo of `[EMAIL_0]` resolved from Redis vault → `john@acme.com` |

\* RT-04 needs a non-default profile because the default profile's `category_actions_output` only covers `credential`/`secret` — see Known Issue #6.

---

## 3. Known issues (FP / FN / design)

Severity scale used here:
- **CRITICAL** — PII leaks to provider (FN at inbound mask)
- **NEEDS_FIX** — User receives garbage or wrong restoration (FP that corrupts text, orphan)
- **ACCEPTABLE** — Detection noise but masked output is still safe

### #1 — `api_key_secret` misses Anthropic / OpenAI key formats — **CRITICAL**

The detector caught `Authorization: Bearer ...` and the GitHub PAT pattern (`ghp_xxxx…`), but **missed** both:
- `sk-ant-api03-abc123xyz` (Anthropic API key prefix)
- `OPENAI_API_KEY=sk-proj-abcdef123456` (OpenAI project-key prefix)

These are real, currently-in-use credential formats. If an end-user pastes one into a prompt, it will be sent verbatim to the upstream provider.

**Fix approach**: add prefix regexes for `sk-ant-(api03|admin01)-[A-Za-z0-9_-]{20,}` and `sk-proj-[A-Za-z0-9_-]{20,}` to `app/services/dlp/detectors/api_key_secret.py`. Verify against the [Anthropic key docs](https://docs.anthropic.com) and [OpenAI key docs](https://platform.openai.com).

### #2 — `thai_national_id` misses unformatted 13-digit IDs — **CRITICAL**

Caught: `1-1001-12345-67-8` (dashed canonical form).
Missed: `3100600871635`, `1234567890123` (raw 13-digit runs).

Real Thai government systems print IDs in both forms; the unformatted variant is also what appears in CSV exports, copy-pastes from spreadsheets, and most database dumps. Missing this is a PDPA exposure surface.

**Fix approach**: extend the detector regex with an alternative that matches a 13-digit run when:
- preceded by an identifying Thai keyword (`เลขบัตร`, `บัตรประชาชน`, `ID:`, `เลขประจำตัว`), OR
- the trailing digit passes the Thai-ID check-digit (mod-11 weighted sum).

The check-digit gate would also keep FP low because random 13-digit runs (`รหัสสินค้า 1234567890123`) usually fail it.

### #3 — `passport` misses Thai-prefixed form — **NEEDS_FIX**

Caught: `passport AA1234567`.
Missed: `หนังสือเดินทาง AB123456`.

Thai is the gateway's primary user-content language, so missing the Thai keyword variant is a notable gap.

**Fix approach**: add `หนังสือเดินทาง` (and the dotless variant `หนังสือเดินทาง`) to the prefix-keyword set in the passport detector.

### #4 — `phone` misses Bangkok area code format `02-555-1234` — **NEEDS_FIX**

The detector catches mobile (`081-…`, `089-…`) and international (`+66…`) forms but appears to miss the 02-prefixed landline pattern. Bangkok lines are ~10% of Thai numbers in business contexts (customer-support tickets, address books).

**Fix approach**: verify the phone regex allows a two-digit area-code prefix before the dash; if not, broaden it.

### #5 — `thai_name` (NER) misses prefixed name with `ลูกค้าชื่อ` — **ACCEPTABLE**

Presidio NER caught `คุณ สมหญิง รักไทย ต้องการ` but missed `ลูกค้าชื่อ สมชาย ใจดี โทรมา`. The Thai-name regex detector (`thai_name`) requires explicit honorifics (`คุณ`, `นาย`, `นาง`) which `ลูกค้าชื่อ` (lit. "customer-name") isn't.

Rated ACCEPTABLE because the surrounding business context (`ลูกค้าชื่อ`) is itself a strong signal that an operator could add to a tenant-scoped keyword rule rather than baking into a global detector. NER coverage is well-known to be language-context-dependent.

### #6 — Credit-card detector flags Luhn-valid 16-digit order number — **ACCEPTABLE**

`order #4242424242424242` was masked as a credit card. The number IS Luhn-valid (it's the canonical Visa test card), so the detector is technically correct. The masking is "wrong" only in the application sense.

Rated ACCEPTABLE because the result is over-masking, not under-masking — no PII leaks. Workarounds:
- Tenants whose product genuinely uses 16-digit order IDs can add a project-scoped allowlist rule (or whitelist the specific test-card numbers).
- A future refinement could look at the surrounding token (`order #`, `invoice`, `order_id=`) and downgrade card matches in those contexts.

### #7 — Default profile lets PII through outbound — **ACCEPTABLE (design choice)**

`DEFAULT_PROFILE.category_actions_output` covers only `credential` + `secret`. PII categories (`pii`, `financial`, `network`, `identity`) fall through to ALLOW on the outbound side. This is intentional per the profile's inline comment — the gateway is meant to answer questions about *example* PII (test card numbers, RFC-1918 IPs, fictional emails) without masking them on the way back.

Tenants who need outbound PII masking should use `policy_profile_id="strict"` (where `category_actions_output=None` makes it fall back to the inbound action map). RT-04 above demonstrates this.

No code fix needed; this is a documentation / onboarding signal — the dashboard should expose the per-direction action map so admins understand the trade-off.

---

## 4. Suggested triage order

1. **Issue #1** (`api_key_secret` Anthropic/OpenAI key formats) — high probability of real production exposure; small detector patch.
2. **Issue #2** (Thai ID unformatted variant) — PDPA-relevant; needs a check-digit helper.
3. **Issue #3** (passport Thai prefix) — one-line keyword set addition.
4. **Issue #4** (Bangkok phone landline) — regex tweak.
5. **Issues #5 / #6 / #7** — acceptable as-is; revisit if customer-reported.

---

## 4b. Post-fix update (2026-05-12)

All four detector gaps from §3 were patched in the same session as
the audit. The accuracy corpus in `test_dlp_accuracy.py` was extended
with the realistic new samples (Thai landlines, raw 13-digit IDs with
keyword context, Thai-prefixed passport, current-format API keys) and
re-run.

### Updated accuracy table

| Detector       | Precision | Recall (was) | Recall (now) | TP | FN | FP | Status |
|----------------|----------:|-------------:|-------------:|---:|---:|---:|:-------|
| email          |     1.000 |        1.000 |        1.000 |  4 |  0 |  0 | — |
| phone_number   |     1.000 |        0.750 |    **1.000** |  8 |  0 |  0 | **Fix 4** ✓ |
| credit_card    |     0.750 |        1.000 |        1.000 |  3 |  0 |  1 | unchanged (over-mask noise) |
| thai_id        |     1.000 |        0.333 |    **1.000** |  6 |  0 |  0 | **Fix 2** ✓ |
| passport       |     1.000 |        0.500 |    **1.000** |  4 |  0 |  0 | **Fix 3** ✓ |
| credential     |     1.000 |        0.500 |    **0.875** |  7 |  1 |  0 | **Fix 1** ✓ |
| thai_name      |     1.000 |        0.500 |        0.500 |  1 |  1 |  0 | unchanged (out of scope) |
| ip_address     |     1.000 |        1.000 |        1.000 |  3 |  0 |  0 | — |
| **OVERALL**    | **0.973** |    **0.720** |    **0.947** | **36** | **2** | **1** | |

**Overall recall: 72.0 % → 94.7 %.**

### Per-issue resolution

- **#1 `api_key_secret` — FIXED.** Added standalone patterns for the
  newer Anthropic role-prefix forms (`sk-ant-api03-…`, `sk-ant-admin01-…`)
  and OpenAI project / live keys (`sk-proj-…`, `sk-live-…`), all with
  a 12+ char body minimum. The literal prefixes are the strong
  discriminator; 12 char body keeps the FP surface negligible while
  catching realistic truncated paste samples. Credential recall
  0.500 → 0.875.
- **#2 `thai_national_id` — FIXED.** Added a keyword-context gate
  (`เลขประจำตัวประชาชน` / `บัตรประชาชน` / `เลขประจำตัว` /
  `บัตรประจำตัว` / `เลขบัตร` / `national id` / `citizen id` / `ID`)
  that bypasses the check-digit when the operator has labelled the
  field. The check-digit path remains and dedupes against the keyword
  path. thai_id recall 0.333 → 1.000.
- **#3 `passport` — FIXED.** Added Thai keyword variants
  (`พาสปอร์ต`, `เลขหนังสือเดินทาง`, `passport no`, `passport number`,
  `travel document`) and broadened the value pattern from `\d{7,8}`
  to `\d{6,9}` to catch diplomatic / 6-digit national variants the
  audit sampled. Keyword gating keeps FP at zero. passport recall
  0.500 → 1.000.
- **#4 `phone` (Bangkok landline) — FIXED.** Added a `_PHONE_THAI_LANDLINE_RE`
  branch matching `0[2-9]` + 3-digit exchange + 4-digit subscriber
  with optional separators. Negative lookahead for digits and
  alphabetic prevents fusing with longer numeric strings or
  swallowing version markers like `02-release`. phone_number recall
  0.750 → 1.000.

### Remaining (acceptable) gaps after fixes

- **`credential` — `sk-ant-api03-abc123xyz` (9-char body) still FN.**
  Real Anthropic keys are 100+ chars; the 9-char audit sample is
  shorter than any issued key has ever been. Lowering the minimum
  below 12 would invite cross-contamination with random short
  identifiers that share the prefix.
- **`credit_card` — Luhn-valid 16-digit order number still FP.**
  No code change made — the over-mask is a safer failure mode than
  under-mask and the spec marked it ACCEPTABLE. Tenants whose product
  uses 16-digit order IDs can add a project-scoped allowlist rule.
- **`thai_name` — NER misses `ลูกค้าชื่อ X` prefix.** Not in this
  session's scope. Tracked as ACCEPTABLE in the original audit and
  open to a tenant-scoped keyword rule rather than a global detector
  change.

### Related test updates

- `tests/test_new_detectors.py::TestThaiNationalIdDetector` had two
  `_invalid_checksum_rejected` cases asserting the *previous* policy
  (checksum rejection wins even with a keyword). Renamed and
  re-asserted to match the new policy:
  - `test_thirteen_digits_invalid_checksum_with_keyword_is_matched`
    — keyword-context bypass now matches.
  - `test_thirteen_digits_invalid_checksum_with_dashes_still_rejected`
    — dashed form bypasses the keyword path (regex requires bare
    13-digit run), so checksum rejection still wins.

### Suite status

- 2,180 tests passing (no regressions vs. pre-fix baseline).
- Same 6 pre-existing failures deselected as in prior sessions
  (`test_api_key_rotation`, `test_lifecycle_logging`, two
  `test_migrations`, `test_ner_detector`, `test_privacy_rbac`).

---

## 5. Test infrastructure notes

- The accuracy harness uses the real `DetectorRegistry.scan_text()`, not a mock — same code path the orchestrator runs.
- The round-trip suite reuses the conftest's existing `ProviderDispatcher.dispatch` stub + the `OpenAIAdapter.stream_chat` stub; each test overrides per-case with its own mock that records the masked-inbound text.
- RT-06 swaps the module-level `conversation_vault._redis` for `fakeredis.FakeStrictRedis` so the multi-turn flow is exercised without a live Redis dependency.
- No production code was modified for this audit.
