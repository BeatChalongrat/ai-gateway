# DLP Architecture

Data Loss Prevention (DLP) in the Secure AI Gateway scans request and response content for sensitive data, evaluates policy rules, and transforms content before it leaves or enters the gateway.

## Pipeline

```
Request messages
    |
    v
[1. Detection]     DetectorRegistry runs all enabled detectors
    |               Produces: list[DetectionMatch] with char-offset spans
    v
[2. Classification] SensitivityClassifier adjusts severity
    |               Escalates when multiple categories co-occur
    v
[3. Evaluation]    PolicyEvaluator maps findings to actions
    |               Consumes: DetectionResult + PolicyProfile
    |               Produces: EnforcementDecision (per-match actions)
    v
[4. Enforcement]   If BLOCK -> raise DLPViolationError (403)
    |               If MASK/REDACT -> DLPTransformer rewrites content
    v
[5. Audit]         AuditService emits DLPAuditEntry records
                    Privacy-safe: no raw matched text in logs
```

The same pipeline runs on provider responses when `scan_responses` is enabled.

## Module Map

```
app/
  domain/
    dlp.py              Data contracts (DetectionMatch, DetectionResult,
                         EnforcementDecision, TransformationResult, DLPAuditEntry)
    policy.py           PolicyProfile dataclass

  services/
    dlp/
      detectors/
        base.py         BaseDetector abstract class
        registry.py     DetectorRegistry + get_default_registry()
        email.py        EmailDetector
        phone.py        PhoneDetector
        ssn.py          SSNDetector
        bearer_token.py BearerTokenDetector (AWS, OpenAI, GitHub, Slack)
        private_key.py  PrivateKeyDetector (PEM headers)
        password_field.py PasswordFieldDetector (config-style passwords)
      classifier.py     SensitivityClassifier + DEFAULT_SEVERITY_MAP
      transformer.py    DLPTransformer (span-based mask/redact)

    policy/
      evaluator.py      PolicyEvaluator
      profiles.py       Built-in profiles + get_profile()

    audit_service.py    DLP audit emission via structlog
    orchestrator.py     Wires the full pipeline into the request lifecycle
```

## Detectors

| Detector | Category | Default Severity | What It Finds |
|----------|----------|-----------------|---------------|
| `email` | pii | MEDIUM | Email addresses (RFC-ish regex) |
| `phone` | pii | MEDIUM | Phone numbers (10+ digits with separators or word boundary) |
| `ssn` | pii | HIGH | US SSN in XXX-XX-XXXX format (validates area/group/serial) |
| `bearer_token` | secret | CRITICAL | Bearer tokens, AWS keys (AKIA), OpenAI keys (sk-), GitHub (ghp_/gho_/ghs_), Slack (xoxb-/xoxp-) |
| `private_key` | secret | CRITICAL | PEM private key headers (RSA, EC, DSA, OPENSSH) |
| `password_field` | secret | HIGH | Password/secret assignments in config-like text |

All detectors are deterministic (regex-based, confidence = 1.0).

## Sensitivity Levels

Ordered from least to most severe:

| Level | Value | Meaning |
|-------|-------|---------|
| NONE | 0 | No findings |
| LOW | 1 | Minimal risk |
| MEDIUM | 2 | Standard PII (email, phone) |
| HIGH | 3 | Sensitive PII (SSN), passwords |
| CRITICAL | 4 | Secrets, API keys, private keys |

**Escalation rules:** When findings span 2+ categories or 3+ entity types in a single scan, all findings below HIGH are escalated to HIGH.

## Enforcement Actions

| Action | Meaning | Pipeline Behavior |
|--------|---------|-------------------|
| ALLOW | No sensitive data, or below policy threshold | Content passes through unchanged |
| MASK | Sensitive data found, policy says to mask | `mask_value()` replaces span with `****...last4` |
| REDACT | Policy says to redact | Span replaced with `[REDACTED]` |
| BLOCK | Finding is too sensitive to proceed | `DLPViolationError` raised (HTTP 403) |

The overall action for a request is the highest (most restrictive) across all findings: ALLOW < MASK < REDACT < BLOCK.

## Policy Profiles

A `PolicyProfile` controls enforcement:

```json
{
  "profile_id": "default",
  "name": "Default",
  "category_actions": {
    "pii": "MASK",
    "secret": "BLOCK"
  },
  "severity_threshold_block": null,
  "scan_responses": true
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | string | Unique identifier |
| `name` | string | Display name |
| `category_actions` | dict | Maps finding category to action (ALLOW, MASK, REDACT, BLOCK) |
| `severity_threshold_block` | string or null | Minimum severity that triggers BLOCK regardless of category. Null disables. |
| `scan_responses` | bool | Whether provider responses are scanned |

**Built-in profiles:** `default` (masks PII, blocks secrets) and `strict` (also blocks financial, severity threshold at CRITICAL).

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DLP_ENABLED` | `true` | Master switch. Set to `false` to skip all DLP scanning. |
| `DLP_DEFAULT_PROFILE` | `default` | Profile used when the project doesn't specify one. |
| `DLP_DISABLED_DETECTORS` | (empty) | Comma-separated detector names to exclude. |
| `DLP_SCAN_RESPONSES` | `true` | Global override for response scanning. Both this AND the profile's `scan_responses` must be true. |

## Privacy Guarantee

`DLPAuditEntry` is the only DLP type that reaches logs or persistence. It contains:
- `detector_name`, `entity_type`, `category`, `severity`, `action`, `field_path`, `direction`

It does **not** contain `matched_text`. The raw sensitive value exists only in transient memory during the request lifecycle and is never written to any log, database, or external system.

## Intentional raw egress (Phase 9; extended by FIX 2)

Four channels — and only four — send user content to third-party providers **without identity-PII masking**, because masking would break the feature itself:

- **Web search** (`POST /v1/search` → Tavily/Brave/SerpAPI/Google/Bing): the real query is required for useful results. A masked query ("prognosis for `<PERSON_1>`") returns garbage.
- **Media generation** (`/v1/images|videos|audio/generations` + the chat-edge media tool path → OpenAI/Replicate/ElevenLabs): the prompt is the creative intent; masking a name yields the wrong image/voice/video.
- **Embeddings** (`POST /v1/embeddings` → OpenAI; used by knowledge-base indexing and RAG retrieval): the embedding vector of a masked chunk ("notes about `<PERSON_1>`") no longer matches the real query, so retrieval breaks. Covers **both** the KB *document content* on index and the *search query* on retrieval. *(FIX 2 — the gateway now fronts this so chat-edge no longer calls OpenAI directly.)*
- **Voice** (`POST /v1/voice/transcribe` + `/v1/voice/synthesize` → OpenAI Whisper/TTS): STT needs the real audio; TTS needs the real text to speak. *(FIX 2 — likewise gateway-fronted.)*

This pass-through is deliberate and bounded:

1. **The credential/secret class is always withheld.** The text channels (search, media, embeddings, TTS text) run the `media_gen` DLP profile before egress; API keys, bearer tokens, private keys, DB connection strings, and financial instruments block the request (403 `search_query_blocked` / `raw_egress_blocked` / DLP 403). A KB document or spoken command containing a secret can never leak it. *(One documented limitation: STT **audio** cannot be scanned before transcription — there is no text yet. A `block`-policy tenant is refused STT outright; otherwise the raw-audio egress is audit-flagged, and the resulting transcript re-enters the **masked** chat pipeline, where the credential class IS enforced.)*
2. **Per-tenant control.** `tenants.search_pii_egress`, `media_pii_egress`, `kb_pii_egress` (embeddings), and `voice_pii_egress` (`allow` | `block`, default `allow`). Under `block`, ANY identity PII in that channel's content fails the request closed — for compliance-strict tenants (e.g. healthcare). **Deploy note:** the default is `allow` (preserves today's behaviour); a compliance-strict tenant must be switched to `block` per channel.
3. **Audited.** Every allowed raw egress that actually carried identity PII (or, for STT, any allowed raw-audio egress) writes a `request_logs` row with `policy_action = raw_egress_allow`, the channel, and detection **category counts only** — never the query/prompt/transcript text or matched values. A tenant's compliance view can answer "which searches/prompts/KB-indexing/voice left unmasked."
4. **No raw text in our own stores.** The search credit-ledger row carries a query hash, not the query.
5. **Machine-checked.** The whitelist is a single constant (`app/services/dlp/egress_policy.py::RAW_EGRESS_ALLOWED = {"web_search", "media_gen", "embeddings", "voice", "agent_documents"}` — the fifth is described two sections below) and `tests/test_dlp_egress_invariant.py` fails CI if any new outbound-HTTP module is added without being classified as masked, whitelisted, or user-content-free — and additionally pins that chat-edge's embeddings/voice Go paths route through the gateway (not OpenAI directly), so the bypass cannot silently return.

Enterprise/PDPA customers should be shown this section as the disclosure they consent to.

## Masking follows where the data goes — an in-house model is not egress

Masking exists to stop data leaving the organisation. A request that cannot leave is not masked; every other request is masked as described above. The rule lives in one module, `app/services/dlp/in_house.py`, and it is **not** a fifth raw-egress channel: nothing reaches a third party.

A chat-completion request reaches its model **as typed** only when all of these hold:

| condition | where it comes from |
| --- | --- |
| the caller says the data may not leave | request header `X-Sran-Egress: closed` |
| the operator has named the caller | the caller's project id is in `DLP_IN_HOUSE_PROJECTS` (empty by default: the header then means nothing) |
| the provider that answers is in-house | `SELF_HOSTED_PROVIDERS` (`app/services/providers/base_urls.py`) |
| nothing in the request can carry it elsewhere | no tools, not a stream, no secure calculator, responses scanned |

What does not change for such a request:

1. **A block still blocks.** Only the mask is not applied.
2. **Our own stores never hold the unmasked text.** The scan and the mask still run; the request log (`input_masked`, `output_raw`, `output_final`), the guardrail and moderation are fed the masked copy. The response cache is neither read nor written.
3. **No other provider gets it.** The request switches fallback off for itself, and the dispatcher holds the rule on its own (`dispatch_with_fallback(in_house_only=True)`): a primary that is not in-house is refused before anything is sent, a hop outside is never a candidate. If a turn is rescued by another provider after all, that provider is handed the masked copy and the answer is restored the usual way.
4. **The record says what happened.** The caller is told by `X-Gateway-Masking: off | on` — what was done, not what it asked for — and the log row carries `dlp_input_action = 'in_house_unmasked'` (`'mask'` keeps meaning "the provider saw placeholders").

Today the one caller that sends the header is Agent Market's runner, which holds each agent's egress switch. Since 12 Oct 2026 masking is that caller's own, separate setting (next section); a caller that sends only this header is read as described here. Ordinary chat never sends it: a chat has no egress switch — its tools, connectors and web search can carry what the model read out of the organisation — so chat is masked whichever model answers. Tests: `tests/test_dlp_in_house.py`, `tests/test_dlp_in_house_dispatcher.py`.

## A named caller's own word on masking — the fifth raw channel (`agent_documents`)

On 12 Oct 2026 the owner separated masking from where the data may go. Whether an agent's documents may leave the organisation is one choice of the admin's; whether personal data in them is masked before a model reads it is another, off by default. So a request whose data **may** leave, and whose caller says it is not to be masked, is sent to a provider **outside** the organisation as it was typed. That is a raw channel like the four above, and it is on the same list (`agent_documents`).

The caller says it per request: `X-Sran-Masking: off` or `on`. The gate is `app/services/dlp/in_house.py::decide`:

| the request says | the provider is | what is sent |
| --- | --- | --- |
| `X-Sran-Masking: on` (with or without `X-Sran-Egress: closed`) | any | masked |
| `X-Sran-Masking: off` and `X-Sran-Egress: closed` | in-house | as typed, held to in-house providers |
| `X-Sran-Masking: off` and `X-Sran-Egress: closed` | outside | masked — the data may not leave as typed |
| `X-Sran-Masking: off`, no `closed` | in-house | as typed |
| `X-Sran-Masking: off`, no `closed` | outside | **as typed** — `agent_documents` |
| neither header | any | masked, as always |

What bounds it:

1. **Only a caller the operator named.** The same list as above (`DLP_IN_HOUSE_PROJECTS`, empty by default). For anyone else the header means nothing.
2. **The credential/secret class is still withheld.** A request in which the scan found a key, a password or a secret keeps its mask on the way out, whatever the caller asked (`credential_found`). Identity, network and financial findings pass as typed — an accounting document is made of them.
3. **A block still blocks**, the request log, the guardrail and moderation keep the masked copy, the response cache is not used, and no other model is tried with the typed text.
4. **Audited by its own row.** The request's log row says `dlp_input_action = 'sent_as_typed'` with the findings counted by category; the caller is told `X-Gateway-Masking: off`.
5. **Each change of the setting is in the admin audit log** (Agent Market: `agent_market.agent.masked`, `agent_market.agent.documents_may_leave`), and each run whose documents left as typed says so on the face of its card.

**What the gateway masks is text.** The image of a page — a scanned invoice read by a model — is sent to the model named as it is, on every path, and always was: no detector reads an image. `X-Gateway-Masking: on` says what was done with the text of a request and nothing about an image in it. Agent Market therefore keeps a page inside the organisation while an agent's masking switch is on, and counts a page read by an outside model as "sent outside, not masked" whatever the header said.

Enterprise/PDPA customers should be shown this section with the one on intentional raw egress. Tests: `tests/test_dlp_in_house.py` (the refusals: a caller nobody named, data that may not leave, the word `on`, a key in the text, the channel taken off the list).

## Code interpreter sandbox (Phase 13) — an internal trust boundary

The gateway runs a code interpreter: the model can author Python (the `run_python` tool) and the plan-exec workspaces (Excel/Doc/Pptx) run generated code against the user's own files. Unlike the two raw-egress channels above, **this is not an egress channel at all** — it is an *internal trust boundary*, the same class as the NER sidecar. The interpreter processes RAW user data, but nothing it touches can reach a third party from inside the boundary.

The boundary is enforced by the sandbox, not by convention. Every run is a per-run ephemeral `docker run --rm` of the dedicated `ai-gateway-sandbox` image, launched by trusted Go in chat-edge (`services/chat-edge-api/internal/sandbox/sandbox.go`) with a fixed lockdown flag set — no model input ever reaches the `docker run` argv:

1. **No network egress — proven at two layers.** The container is launched with `--network none` (no route to postgres/redis/gateway/internet), and the seccomp profile (`services/chat-edge-api/sandbox/seccomp.json`) is default-deny (`SCMP_ACT_ERRNO`) with **every networking syscall removed** — `socket`/`connect`/`bind`/`listen`/`accept`/`sendto`/`recvfrom`/… are absent, so model-authored code cannot even create a socket. The raw user data inside the interpreter has no path out.
2. **No secrets in the boundary.** The container inherits none of chat-edge's env — no API keys, DB strings, or provider credentials cross into the code that runs on user data (`--cap-drop ALL`, `--user 65534:65534`, `--security-opt no-new-privileges`, read-only rootfs, resource caps).
3. **Results reach a provider ONLY through the masked completion pipeline.** The interpreter's stdout and produced artifacts come back to chat-edge, are surfaced to the user, and on the next turn are fed to the model as ordinary conversation content — which means they pass through the same DLP mask → guardrail → dispatch pipeline (`orchestrator.py`) as any other message before any provider adapter sees them. The model's authored code and its stdout are DLP-masked like all other content; identity PII the code printed is masked before egress, and the credential/secret class blocks.
4. **Machine-checked, both languages.** The no-third-party-network guarantee is pinned by a Go unit test (`internal/sandbox/sandbox_test.go::TestSandbox_DockerArgsAlwaysNoNetwork`) that fails if the arg builder ever emits a `--network` value other than `none`. The Python egress gate (`tests/test_dlp_egress_invariant.py`) cross-pins that proof and the seccomp no-socket property, and asserts the interpreter is correctly **absent** from `RAW_EGRESS_ALLOWED` — it is a no-egress path, not a raw one.

**Why this is stronger than a typical hosted code interpreter (e.g. ChatGPT Advanced Data Analysis).** In ADA-style designs the sandbox runs the user's data and the *same* provider that hosts the sandbox also hosts the model — there is no masking layer between the analyzed data and the model provider, so raw data (names, IDs, secrets in a spreadsheet) is visible to the provider by construction. Here the sandbox is ours, network-isolated, and its output re-enters the model only after DLP masking. **Raw user data processed by the interpreter never reaches a third party through code execution**; the only provider-bound representation of that data is the masked completion.

## Adding a New Detector

1. Create `app/services/dlp/detectors/your_detector.py`
2. Subclass `BaseDetector`, set `name`, `category`, `default_sensitivity`
3. Implement `detect(text: str) -> list[DetectionMatch]` with accurate `start`/`end` spans
4. Add the entity type to `DEFAULT_SEVERITY_MAP` in `classifier.py`
5. Register in `get_default_registry()` in `registry.py`
6. Write tests with positive and negative cases

The detector must not contain policy logic — it only detects and returns findings.

## Adding a New Policy Profile

1. Create a `PolicyProfile` instance in `profiles.py` (or future DB)
2. Add to the `_PROFILES` dict for `get_profile()` lookup
3. Set `category_actions` for each finding category
4. Optionally set `severity_threshold_block` for blanket blocking above a severity

## Future Extensions

The architecture is designed to accommodate:

- **ML/NER detectors** — implement `BaseDetector`, set `confidence < 1.0`, policy can threshold on confidence
- **Tokenization vault** — add a `TOKENIZE` action to `EnforcementAction`, transformer replaces spans with vault tokens
- **Provider-specific restrictions** — evaluator can check `route.provider_name` against profile rules
- **Regional data controls** — add `region_constraint` to `PolicyProfile`, evaluator checks against project region
- **Structured JSON scanning** — detectors already receive plain text; a pre-processor can extract JSON field values
- **File/attachment scanning** — add a file-aware scanner alongside the text scanner
- **DB-backed profiles** — replace `_PROFILES` dict in `profiles.py` with a repository lookup
