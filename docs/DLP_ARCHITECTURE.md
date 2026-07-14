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

## Intentional raw egress (Phase 9)

Two channels — and only two — send user content to third-party providers **without identity-PII masking**, because masking would break the feature itself:

- **Web search** (`POST /v1/search` → Tavily/Brave/SerpAPI/Google/Bing): the real query is required for useful results. A masked query ("prognosis for `<PERSON_1>`") returns garbage.
- **Media generation** (`/v1/images|videos|audio/generations` + the chat-edge media tool path → OpenAI/Replicate/ElevenLabs): the prompt is the creative intent; masking a name yields the wrong image/voice/video.

This pass-through is deliberate and bounded:

1. **The credential/secret class is always withheld.** Both channels run the `media_gen` DLP profile before egress; API keys, bearer tokens, private keys, DB connection strings, and financial instruments block the request (403 `search_query_blocked` / DLP 403). A search never needs a leaked API key to be useful.
2. **Per-tenant control.** `tenants.search_pii_egress` and `tenants.media_pii_egress` (`allow` | `block`, default `allow`). Under `block`, ANY identity PII in a search query or media prompt fails the request closed — for compliance-strict tenants (e.g. healthcare).
3. **Audited.** Every allowed raw egress that actually carried identity PII writes a `request_logs` row with `policy_action = raw_egress_allow`, the channel, and detection **category counts only** — never the query/prompt text or matched values. A tenant's compliance view can answer "which searches/prompts left unmasked."
4. **No raw text in our own stores.** The search credit-ledger row carries a query hash, not the query.
5. **Machine-checked.** The whitelist is a single constant (`app/services/dlp/egress_policy.py::RAW_EGRESS_ALLOWED = {"web_search", "media_gen"}`) and `tests/test_dlp_egress_invariant.py` fails CI if any new outbound-HTTP module is added without being classified as masked, whitelisted, or user-content-free.

Enterprise/PDPA customers should be shown this section as the disclosure they consent to.

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
