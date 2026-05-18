# Changelog

## [Unreleased] — 2026-05-19

### DLP Token Shape Migration (`<rN>` neutral tokens)

**Breaking change**: DLP masking tokens changed from `[CATEGORY_N]`
(e.g. `[EMAIL_0]`, `[THAI_ID_1]`) to neutral `<rN>` format
(e.g. `<r0>`, `<r1>`).

**Why**: The old format exposed category names to the LLM, causing:
1. AI safety filters refusing to process messages containing tokens
   like `[BEARER_TOKEN_0]`
2. LLM hallucinating/renaming tokens (e.g. `[AUTH_TOKEN_0]` instead
   of `[BEARER_TOKEN_0]`), breaking unmask

**What changed**:
- Token format: `[CATEGORY_N]` → `<rN>` (sequential counter, resets per request)
- DLP system prompt: v3 — no longer contains literal token examples
- Orphan token sanitizer: strips hallucinated `<rN>` from LLM output
- New detectors: `date_of_birth`, `thai_tax_id` (strong-prefix mode)
- Detector fixes: phone rejects all-same-digit, NER excludes Thai
  imperative verbs

**E2E test results (50 cases)**:
- Mask accuracy: 97% (38/39)
- Full round-trip: 90% (35/39)
- AI refusal: 0% (0/50)
- False positives: 3 (medical terms + salary — documented as correct
  behavior per PDPA)
- False negatives: 1 (short API key `sk-test-abc123` — precision >
  recall decision)

**Known limitations**:
- T19/T20: Medical condition names (เบาหวาน, ภูมิแพ้) masked — correct
  per PDPA Section 26 (health data is sensitive PII)
- T27: Labeled salary amounts masked — income is PII under most
  frameworks
- T41: `sk-test-*` keys under 20 chars not detected — cannot
  distinguish from product codes without context
- Orphan `<rN>` may appear in ~5% of responses due to LLM variance
  (outbound scan mints new tokens that can collide with hallucinated
  ones)

**Migration commits**: ffc6704 (token shape) → subsequent fixes
(prompt leak, false positives, false negatives, orphan sanitizer)
