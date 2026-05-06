# DLP detector coverage gaps

Surfaced by the May 2026 PII pipeline audit (`sran-pii-test.sh` scenarios 2 + 5). The orphan-token symptoms (`[ADDRESS_0]`, `[NAME_1]` reaching the user) were addressed by hardening the DLP system prompt and excluding system messages from DLP scans. The underlying *detector* gaps below remain — they are the reason the model had to guess in the first place.

Each entry: detector, evidence, scope of fix, rough sizing.

---

## 1. `thai_address` misses real-world addresses

**Detector:** `backend/app/services/dlp/detectors/thai_address.py`

**Evidence (audit scenario 2):**

User input contained:
```
ที่อยู่ 99/123 ซอยสุขุมวิท 31 แขวงคลองตันเหนือ เขตวัฒนา กรุงเทพ 10110
```
Inbound DLP: `findings=4` — `email`, `phone`×2, `presidio_ner` (matched "Thai" from system prompt). **No `thai_address` finding.** The address pattern `<house-number>/<sub> <ซอย/ถนน> <X> <แขวง/เขต> <X> <กรุงเทพ> <postal>` was not matched.

The existing detector test (`test_dlp_all_detectors.py:140-145`) covers a different shape (`อยู่บ้านเลขที่ 88/9 ถนนสีลม ...`) and does pass — so the regex handles `บ้านเลขที่` + `ถนน` but not the `ที่อยู่` + `ซอย ... แขวง ... เขต ...` form.

**Fix scope:** widen the address regex to match the `ที่อยู่ <house>/<sub> ซอย<X> <num> แขวง<X> เขต<X> <จังหวัด> <postal>` form. Regression tests should be added to `test_dlp_all_detectors.py` to lock both shapes in.

---

## 2. `thai_name` misses titled Thai personal names in real prose

**Detector:** `backend/app/services/dlp/detectors/thai_name.py`

**Evidence (audit scenarios 2 + 5):**

| Input phrase | Detector fired? |
|---|---|
| `นายสมชาย ใจดี` (scenario 2) | ❌ no |
| `คุณสมหญิง รักดี` (scenario 5) | ❌ no |
| `คุณวิชัย เก่งมาก` (scenario 5) | ❌ no |

The detector's existing test (`test_dlp_all_detectors.py` TC1) DOES catch the same `นายสมชาย ใจดี` substring when it's part of the `ผมชื่อ นายสมชาย ใจดี ...` phrase under TestTC1ThaiMega — so the regex matches in isolation but fails in scenario 2's full prose. Likely a regex-anchor or surrounding-context interaction.

**Fix scope:** investigate why the same input matches in unit-test isolation but not in the full scenario-2 message. Check whether NFKC normalisation, preprocessor offset map, or detector ordering is suppressing the match. Until fixed, the model can hallucinate `[NAME_X]` placeholders for names it sees verbatim (the [REDACTED] system-prompt rule mitigates the user-facing harm but the underlying detection failure remains).

---

## 3. `thai_bank_account` misses dashed bank account format

**Detector:** `backend/app/services/dlp/detectors/thai_bank_account.py`

**Evidence (audit scenario 5):**

User input contained `บัญชี 123-4-56789-0` and `บัญชี 987-6-54321-0`. Inbound DLP findings did NOT include `thai_bank_account` for either. Both account numbers reached the provider in the masked payload as raw text. (The model echoed them verbatim, which actually worked out correctly because they were never tokenised — but that was lucky.)

**Fix scope:** confirm the regex covers the `<3>-<1>-<5>-<1>` Thai bank account form (`123-4-56789-0`). Add regression test to `test_dlp_all_detectors.py`.

---

## 4. Presidio NER running on `en_core_web_sm` — no Thai NER coverage

**Component:** `backend/app/services/dlp/ner_status.py:82-159`

**Evidence (gateway boot log):**

```
{"available": ["en_core_web_sm"],
 "note": "en_core_web_lg is missing; PresidioDetector will use a smaller fallback model. Accuracy may be lower than the large model.",
 "event": "dlp_presidio_using_fallback_spacy_model"}
```

The current container image only has `en_core_web_sm`. No Thai NER model is installed. This means:
- Thai PERSON / LOCATION / ORGANIZATION entities are detected by the English model only when they're written in Roman script.
- Thai-script names like `นายสมชาย ใจดี` get zero NER coverage from Presidio.
- The system was relying on `thai_name` (regex) for these — and that detector has its own gap (issue #2).

**Fix scope (infra):**
1. Update Dockerfile to also install `th_core_news_lg` (or `th_core_news_sm` as fallback). The `ner_status.load_analyzer_sync` already prefers Thai if available (`ner_status.py:113-114`).
2. Add a CI smoke check that asserts the analyzer is initialised with both English and Thai supported languages.
3. Document the disk-space cost (Thai LG model is ~580MB on top of the existing English model).

---

---

## 5. Streaming endpoint bypasses regex guardrail

**Severity:** HIGH (security — jailbreak / prompt-injection bypass)

**Component:** `backend/app/api/routes/public_chat.py` (streaming entrypoint)

The non-streaming orchestrator runs the regex guardrail check at
`orchestrator.py:282-323` (`jailbreak_dan` / `prompt_injection` /
`harmful_synthesis` rules from `guardrail_service.py`). The streaming
entrypoint in `public_chat.py` does **not** run this check at all —
the request flow goes:

```
auth → DLP scan → DLP block-or-mask → moderation → router → stream
                                                 ^^^^
                                       no guardrail step
```

**Impact:** clients setting `stream: true` can submit prompt-injection
or jailbreak content that the non-streaming path would block with
`GuardrailViolationError`. The streaming path returns whatever the
provider responds.

**Discovered:** during the May 2026 block-path persistence audit. While
mapping all six 403-paths, the audit found only five — the streaming
guardrail was simply absent (so its persistence question was moot).

**Why not fixed in the persistence PR:** scope larger than persistence.
Needs a design decision among:

1. **Pre-stream gate** — run guardrail once before opening the SSE
   response, fail fast with 403 (matches non-streaming semantics).
   Simple but loses any chunk-level context.
2. **Per-chunk on rolling-tail** — match the DLP pattern at
   `public_chat.py:278-420` (rolling 50-char window). More complex
   but symmetric with how DLP already works on streams.
3. **Split rules by enforcement timing** — pre-stream rules
   (`jailbreak_dan`, `prompt_injection`, …) gate before the stream
   opens; per-chunk rules (TBD) run on the buffer.

**Mitigation today:** none in code. Operational mitigation: tenants
who require guardrail coverage on streaming should disable the
streaming path at the API-key or project level until this is fixed.

---

## 6. `public_chat.py:508` opens `SessionLocal()` directly — bypasses DI

**Severity:** MEDIUM (production-multitenancy concern + test-isolation footgun)

**Component:** `backend/app/api/routes/public_chat.py:508` (streaming `event_generator`)

The streaming endpoint runs the dep-injected session through the
synchronous part of the request handler, but the SSE generator runs
*after* FastAPI has unwound the dependency tree (the route function
returns the `StreamingResponse` before the generator starts producing
chunks). By the time `event_generator` needs to persist the request
log, the dep session is closed — so the code opens a fresh
`SessionLocal()` directly:

```python
log_session = SessionLocal()
try:
    orchestrator.request_log_service.persist(log_session, RequestRecord(...))
    log_session.commit()
```

**Why this is a problem in production:**

- Any future read-replica routing, sharded-DB setup, or per-tenant DB
  selection that's wired through `Depends(get_db_session)` would be
  silently bypassed by this code path.
- `SessionLocal()` is a module-level singleton bound to the global
  engine. The streaming persist always lands on the global DB
  regardless of any dep-time tenant routing.

**Why this is a problem in tests:**

- The integration test conftest overrides `Depends(get_db_session)` to
  point at an in-memory SQLite engine. That override does NOT cover
  direct `SessionLocal()` calls, so streaming tests' persist calls
  used to write to the live Postgres dev DB. Surfaced by 314
  `tenant_id='test-tenant'` rows polluting the operator's dashboard
  in May 2026.
- Mitigated for tests by an autouse fixture in `tests/conftest.py`
  that monkeypatches `app.core.db.SessionLocal` to the integration
  engine. Production code path is unchanged.

**Why not fixed now:** the fix is non-trivial — needs a way to access
a sessionmaker that respects whatever dep-time routing the caller
intended. Options include:

1. Pass a sessionmaker through `event_generator` from the request
   handler (closure capture) instead of importing the global one.
2. Inject a `sessionmaker_factory` via a separate FastAPI dep that
   returns a maker rather than a session, so streaming code can mint
   sessions whenever it needs one.
3. Refactor the streaming logger to write through a queue + background
   worker that owns its own session.

All three require touching every direct `SessionLocal()` call in
`public_chat.py` and `admin_ai_review.py`. Out of scope for the test
isolation work that surfaced this.

**Other direct `SessionLocal()` callers** (lower test-leak risk because
they don't run inside a TestClient request, but same DI-bypass concern
in production): `app/main.py:108,147,241` (startup hooks),
`app/api/admin_auth.py:151`, `app/api/routes/admin_ai_review.py:713,817,856`.

---

## 7. Future-proof guard for new `SessionLocal()` callers

**Severity:** LOW (process / safety net)

**Status:** Considered but not adopted in May 2026.

The autouse `SessionLocal` monkeypatch in `tests/conftest.py` only
catches `SessionLocal()` callers that exist *today*. A future PR that
introduces a new direct caller and forgets to use the dep override
would silently regress: tests would keep passing while writing to the
dev DB.

**Considered fix (not adopted):** add a runtime guard inside
`app/core/db.py`'s engine factory that errors if `PYTEST_CURRENT_TEST`
is set and the engine URL doesn't include `test` or `:memory:`:

```python
def _make_engine() -> Engine:
    url = settings.database_url
    if "PYTEST_CURRENT_TEST" in os.environ and \
       "test" not in url.lower() and ":memory:" not in url.lower():
        raise RuntimeError(
            f"Refusing to create DB engine to {url!r} during pytest. "
            "Tests must override DATABASE_URL or use dependency_overrides."
        )
    return create_engine(url, ...)
```

**Why not adopted:** the rule "don't change production DB connection
logic" took priority over the safety net. The autouse monkeypatch is
sufficient as long as future contributors notice when their new
streaming/background test starts polluting the dev DB.

**Reconsider if:** a regression caused by a missed monkeypatch
surfaces (i.e. someone adds a new `SessionLocal()` call site that the
autouse fixture doesn't cover, or replaces `SessionLocal` import with
a different binding). At that point the runtime guard becomes
obviously cheaper than catching the pollution after it lands.

---

## 8. Tooling: heavy-scenario harness corrupts Thai input on Windows MSYS

**Severity:** LOW (test tooling only; zero impact on production traffic, zero on unit tests)

**Status:** Diagnosed May 2026 during the heavy multi-detector live test. Filed for separate fix; not blocking the detector-coverage campaign.

**Symptom:** When the heavy scenario is sent via `bash` + `curl` on Windows MSYS, every Thai character in `input_raw` arrives at the backend as `0x3F` (`'?'`). The Portal's `/requests` page then renders `???` in the User → Service and Service → AI sections — the Portal is faithfully showing what's in the DB.

**Evidence:** Row `req_8b3d0f17117b47d78d3e22b2de86df1f` from the heavy test:

| Column         | char_len | byte_len | bytes/char | Verdict       |
|----------------|---------:|---------:|-----------:|---------------|
| `input_raw`    |      792 |      792 |       1.00 | All ASCII (`?`s) |
| `output_final` |      628 |     1454 |       2.32 | Real Thai UTF-8  |

Same row, same write path, two columns: input was already `?`s **before** insert; the AI's response came back as proper UTF-8 from OpenAI and was inserted with no loss. That isolates the corruption to ingress, not storage.

Cross-check: rows from earlier the same day (`req_e834c0fe...`, `req_b67356e8...`, `req_39a9a04b...`) show `bytes/char` ratios of 1.82–2.50, confirming the backend write path stores Thai correctly when given UTF-8 bytes. The corruption is client-side and only on this dev workstation's shell pipeline.

**Root cause:** On Windows MSYS, command substitution (`PAYLOAD=$(jq ...)`) buffers the child process's stdout through a code-page conversion (CP874/CP1252) before assigning to the shell variable. Non-ASCII bytes that don't map to the active code page collapse to `?`. By the time `curl` sends the body, the Thai bytes are already lost.

**Workaround for Phase Final rerun:** avoid `$()` capture for any payload containing non-ASCII bytes. Two viable forms:

1. Build the payload to a file with `jq`, then send with `--data-binary`:
   ```bash
   jq -n --rawfile usr "$INPUT_FILE" '...' > /tmp/payload.json
   curl --data-binary @/tmp/payload.json ...
   ```
2. Send via Python (`httpx` / `requests`) — bypasses MSYS shell entirely.

**Impact summary:**
- Production traffic: zero (real clients send UTF-8 over HTTP and the backend stores it correctly).
- Unit tests: zero (Python string literals don't transit the shell).
- Bash-driven heavy-scenario reruns: blocked until the harness is updated.

**Reconsider if:** the same `bytes/char = 1.00` pattern appears on rows that did NOT come from this MSYS shell pipeline. That would indicate a real backend bug rather than a tooling artifact.

---

## How these were surfaced

`sran-pii-test.sh` scenarios 2 and 5 produced `[ADDRESS_0]` and `[NAME_1]` orphan tokens in user-facing output. Diagnosis (`traces/diag-gateway-logs.txt`, May 2026) showed:

- Tokens were **not** in `span_map` — so `restore()` correctly left them as literals.
- They were generated by the model itself, prompted by the DLP system prompt's instruction to "refer to placeholders by their token".
- The reason the model had to guess: the detectors above never fired on the corresponding input PII.

The system-prompt fix (use `[REDACTED]` for un-masked sensitive content) and the system-message scan exclusion together close the user-visible orphan path. Detector improvements above would push detection coverage closer to 100% so the model rarely has anything to guess about.
