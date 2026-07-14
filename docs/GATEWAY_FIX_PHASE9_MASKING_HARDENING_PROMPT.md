# GATEWAY FIX — PHASE 9: Masking-invariant hardening (intentional egress made controllable)

You are working on the **core AI-gateway backend** (FastAPI, `backend/`, :8080). **Backend-only** — do not touch chat-edge, mcp-gateway, the web apps, or the desktop app. This phase does NOT regress the DLP pipeline; it tightens the two intentional raw-egress paths and adds a guard so no NEW path can leak by accident.

## The principle (read this first — it shapes every task)

The gateway's core invariant is **"no user content reaches a third party without DLP masking."** Two paths intentionally send RAW content because masking would break the feature:

- **Web search** — the real query is needed for useful results; masking `"prognosis for คุณสมชาย"` → `"prognosis for <PERSON_1>"` returns garbage.
- **Media generation** — the prompt IS the creative intent; masking a name yields the wrong image/voice.

This design is CORRECT and stays. But the goal is **"no ACCIDENTAL leak,"** not "mask everything." So the intentional egress must be: (1) still blocking the credential/secret class (you never need a leaked API key to run a search), (2) not leaking raw content into your OWN datastores where it isn't needed, (3) audit-flagged so a tenant's compliance team can see it, (4) per-tenant controllable, and (5) protected by a CI gate so a future dev can't quietly add a THIRD raw-egress path.

## Verified anchors (build on these; re-confirm exact line numbers in R0 — code drifts)

- **Media path already does it right:** `backend/app/services/media_gen/submit_helpers.py:60-77` `scan_media_prompt_with_profile()` runs the DLP scan with `ner_enabled=False`, raises 403 on `enforcement.is_blocked` (secret/credential) or `content_policy_blocked`, and lets identity PII pass. This is the template to mirror for search.
- **Search path has NO DLP at all:** `backend/app/api/routes/public_search.py:47-99` — `payload.query` goes straight to `search_service.search(...)` (`app/services/search/service.py:83`) → external providers (`app/services/search/providers.py`, Tavily/Brave/SerpAPI/Google/Bing). Nothing scans it.
- **Raw query stored in your own ledger:** `public_search.py:82` (`f"search:{payload.query[:40]}"` as the credit reference) and `:86` (`description=f"web_search: {payload.query[:50]}"`). This is raw PII at rest in the credit ledger — not needed by any provider.
- **DLP scan primitives:** `GatewayOrchestrator._scan_text_as_result_async(text, direction, ner_enabled)` and `._scan_and_evaluate(detection, profile)`; profiles via `app/services/policy/profile_service.get_runtime_profile_async(db, "<name>")`. The `EnforcementAction`/`is_blocked` shape is in `app/domain/policy`.

## Ground rules

- Read every file you touch IN FULL; do R0 first and answer it in the summary.
- Do NOT change chat-completion DLP behavior. The main pipeline (mask → guardrail → moderation → routing → dispatch → outbound restore) is correct and out of scope.
- Follow existing patterns: FastAPI routes, `GatewayError` subclasses for typed errors, structlog, Alembic migrations under `backend/migrations/versions/`, pytest under `backend/tests`.
- Finish state: `python -m pytest` green on touched suites; state exactly what you ran.

## R0 — Mandatory reconnaissance (answer all in the summary)

1. DLP profiles: is there a `"search"` runtime profile, or only `"media_gen"` + default? How `get_runtime_profile_async` resolves an unknown name (fallback behavior). What category names distinguish credential/secret from identity PII in the enforcement result (so A1 can block ONLY the credential class).
2. `_scan_text_as_result_async` / `_scan_and_evaluate` signatures and how `enforcement.is_blocked` is computed vs per-category actions — you need "block if any credential/secret-category detection, else allow" precisely.
3. The credit-deduction call (`deduct_portal_credit_async`): what the `reference`/`description` args are used for downstream (are they shown in the portal UI / admin? searched? just stored?) — so A2 replaces them safely.
4. Tenant model (`app/models/tenant.py`): existing `search_enabled`/`search_credit_cost` columns and the migration pattern — A4 adds per-tenant egress-policy columns here.
5. Every OTHER outbound-to-third-party call site that carries user content (for the A3 CI gate whitelist): confirm the audit's finding that ONLY chat completions (masked), search (raw, intentional), and media (raw, intentional) egress user content — grep the provider/adapter/httpx call sites and list them.

---

## TASK A1 — Web search: block the credential/secret class (identity PII still passes)

Mirror the media pattern so a query never leaks a secret, while keeping search useful.

1. Add `scan_search_query(query: str, db, tenant_policy)` (place it beside the search service or as a shared helper reused by media — DRY if clean). Behavior: no-op when `settings.dlp_enabled` is false; else run `_scan_text_as_result_async(query, direction="input", ner_enabled=False)` + `_scan_and_evaluate` against the appropriate profile (R0.1). Raise a typed 4xx (`GatewayError` subclass, code e.g. `search_query_blocked`) ONLY when a credential/secret-category detection is enforced — identity PII passes through unchanged.
2. Call it in `public_search.py::search` BEFORE `search_service.search(...)`. On block, return the typed error (do not run the search, do not deduct credit).
3. Do NOT mask the query — masking is intentionally NOT done here (document why in a comment: search needs the real query; only the credential class is withheld).
4. Tests: query with an API-key/password pattern → 403 `search_query_blocked`, provider never called; query with a name/phone/email → passes, provider called with the RAW query (assert the outbound query equals the input — this pins the intentional-passthrough behavior); DLP disabled → passes.

## TASK A2 — Stop storing raw query text in the credit ledger

`public_search.py:82,86` persist `query[:40]`/`query[:50]` into the credit reference + description. That's raw PII at rest with no functional need.

1. Replace both with a non-PII reference: a stable `"web_search"` description and, if a correlation id is useful, the request id or a hash of the query (`sha256(query)[:12]`) — NOT the query text. Confirm via R0.3 that nothing downstream depends on the literal query text in these fields (if the portal shows it as "what you searched", that's a product decision — flag it, don't silently change UX; default to removing the raw text).
2. Test: after a search, the persisted ledger row's reference/description contains no substring of the query.

## TASK A3 — DLP egress CI gate (the drift guard)

The whole point: make "search + media are the ONLY raw-egress paths, and even they block credentials" a MACHINE-CHECKED invariant, so a future provider/integration can't quietly add a third leak.

1. Add a test module `backend/tests/test_dlp_egress_invariant.py` that enumerates outbound-to-third-party call sites carrying user content (from the R0.5 list) and asserts, per site, one of two contracts:
   - **MASKED** (chat completions, moderation, semantic-router-if-ever-external, cache, AI-review): the content passed to the outbound call is post-`mask_messages` (contains `<rN>`/no raw PII for a seeded-PII input).
   - **INTENTIONAL-RAW-WHITELIST** (search, media only): allowed to carry raw identity PII BUT must have passed the credential/secret block first.
   - Any call site not in either list → test fails with a message telling the dev to classify it.
   Implement this with the strongest mechanism practical: prefer an end-to-end test that seeds a known PII string through each public entrypoint and asserts on the captured outbound payload (mock the provider/search/media HTTP layer to capture what would be sent). If full E2E is impractical for some sites, a structural test (assert each user-content egress helper routes through the masking transformer OR is on the explicit whitelist constant) is acceptable — but the whitelist must be a single named constant in the codebase, referenced by both the gate and the call sites, so adding a raw path requires consciously editing the whitelist.
2. The whitelist constant lives in app code (e.g. `app/services/dlp/egress_policy.py::RAW_EGRESS_ALLOWED = {"web_search", "media_gen"}`) with a docstring stating the rule. Search/media reference it (or a helper `assert_egress_allowed(channel)`); everything else is masked by construction.
3. Seed-PII fixtures: a name, a phone, an email, a Thai national ID, an API-key-shaped secret. The gate asserts: masked paths emit none raw; whitelisted paths emit identity PII raw but the secret is blocked upstream (A1 / existing media scan).

## TASK A4 — Per-tenant egress policy + audit flag

1. **Migration:** add tenant columns (Alembic, follow R0.4): `search_pii_egress` and `media_pii_egress` — enum/text `allow` | `block` (default `allow`, preserving today's behavior). `block` makes the respective scan fail-closed on ANY identity PII (not just credentials) — for compliance-strict tenants (e.g. healthcare).
2. Wire into A1's search scan and the existing media scan: when the tenant's policy is `block`, treat identity-PII detections as blocking too. Default `allow` keeps the intentional-passthrough behavior byte-for-byte.
3. **Audit flag:** when a request egresses raw identity PII on a whitelisted path (i.e., detections were present but allowed through under `allow` policy), record an audit signal — reuse the existing request-log/audit mechanism (R0: find how DLP block events are audited today, e.g. `safe_*_snapshot`/audit rows) and add a `raw_egress: true` + channel + detection-category-counts (NOT the values) marker, so a tenant's compliance view can answer "which searches/prompts left unmasked." No raw PII in the audit record — categories/counts only.
4. Tests: tenant `block` → identity PII in a search query/media prompt → 403; tenant `allow` (default) → passes but an audit row with `raw_egress:true` + categories (no values) is written; no-PII query → passes, no raw_egress flag.

## TASK A5 — Documentation

Update (or create) the security/privacy doc (find the existing one — `docs/DLP_ARCHITECTURE.md` or similar) with a short, explicit section: **"Intentional raw egress."** State plainly that web-search queries and media-generation prompts are sent to third-party providers WITHOUT identity-PII masking (by design, for feature correctness), that the credential/secret class is always withheld, that this is per-tenant controllable (`allow`/`block`), and that raw egress is audit-flagged. This is the disclosure enterprise/PDPA customers need to consent to. Keep it factual and short.

---

## Final verification checklist

1. `python -m pytest` green on: the new `test_dlp_egress_invariant.py`, search tests, media tests (unchanged behavior under default `allow`), tenant-policy tests. State what you ran.
2. **Invariant statement (written):** confirm, path by path from the R0.5 list, that every user-content egress is either masked or on the named whitelist — and that the CI gate would fail if a new unclassified path were added (describe how you verified this, e.g. by temporarily adding a fake raw egress in a scratch test and seeing the gate catch it — then removing it).
3. Confirm the default-`allow` tenant behavior is byte-for-byte unchanged (search still sends the real query, media still generates from the real prompt) — the ONLY new blocks under default policy are the credential/secret class on search (which media already blocked).
4. Summarize changes file by file + note that chat-completion DLP was not touched. List follow-up for Phase 10 (correctness P0/P1: media file-type, chat-edge SSE busy-spin, mcp-gateway leaks, desktop stale-image + read_file guard) WITHOUT implementing them.
