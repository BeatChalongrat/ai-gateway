"""Span-based text transformation engine for DLP enforcement.

Applies mask/redact replacements at exact character offsets.  Processes
spans in reverse offset order so that replacing a later span does not
shift the positions of earlier spans.

Pipeline position:
    Detectors → Classifier → Evaluator → **Transformer** → Audit

Design constraints:
- No policy logic.  The transformer receives MatchActions (already decided)
  and applies them mechanically.
- Overlapping spans are resolved: the more restrictive action wins, the
  other is skipped.
- ALLOW and BLOCK actions produce no text modification.
- Input text and messages are never mutated.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

import structlog

from app.domain.dlp import (
    EnforcementAction,
    MatchAction,
    TransformationResult,
    TransformedSpan,
)

if TYPE_CHECKING:
    from app.schemas.common import Message

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AllocatedTokenRecord:
    """One emitted mask token bound to the EXACT finding/span it masked.

    Phase 4A.1.1 C2 fix — produced by the V2 masking path so Vault V2
    entries derive metadata from the precise applied finding, NOT from a
    raw-value reverse lookup. Immutable; carries no cross-request hash of
    the raw value.
    """

    token: str
    raw_value: str
    category: str
    detector: str | None
    entity_type: str | None
    action: str
    policy_v2: Mapping[str, object] = field(default_factory=dict)
    message_index: int | None = None
    start: int | None = None
    end: int | None = None


def _build_allocated_record(token, original, ma, message_index):
    """Build an :class:`AllocatedTokenRecord` from a token + its MatchAction."""
    m = ma.match
    pv = _pv2(ma)
    return AllocatedTokenRecord(
        token=token,
        raw_value=original,
        category=getattr(m, "category", "") or "",
        detector=getattr(m, "detector_name", None),
        entity_type=getattr(m, "entity_type", None),
        action=ma.action.name,
        policy_v2=dict(pv) if pv else {},
        message_index=message_index,
        start=getattr(m, "start", None),
        end=getattr(m, "end", None),
    )

# Neutral ``<rN>`` token shape — May 2026 switch from ``[CATEGORY_N]``.
# Why: OpenAI's safety filter refuses prompts containing tokens like
# ``[BEARER_TOKEN_0]`` / ``[NATIONAL_ID_0]`` because the category name
# itself reads as a sensitive-data marker. The ``<rN>`` shape carries
# no semantic hint and avoids the refusal.
#
# Both emission paths produce this shape:
#   - counter form        ``<r0>`` / ``<r1>``        (digits-only suffix)
#   - deterministic       ``<r{hmac_suffix}>``       (8-char base64url)
#   - deterministic       ``<r{hmac_suffix}_{disambig}>`` (collision)
#
# Pseudonym mode (``DLP_PSEUDONYM_MODE=true``) adds a parallel shape
# ``<<CATEGORY_N>>`` for identity-class detectors only — secret-class
# detectors keep the ``<rN>`` form regardless of mode. The combined
# regex below matches both so restore / orphan-detection / regex-sync
# sites work in mixed-shape situations (e.g. a vault that has legacy
# ``<r0>`` entries plus new ``<<EMAIL_1>>`` entries).
#
# Used by ``restore`` to detect surviving tokens (orphans) and by
# ``orchestrator._scan_text_as_result`` to filter mask-token literals
# out of outbound scan results.
_MASK_TOKEN_RE = re.compile(
    r"<r[A-Za-z0-9_\-]+>|<<[A-Z][A-Z0-9_]*>>"
)

# Re-exported alias — call sites in ``dlp_prompt`` / ``public_chat`` /
# ``orchestrator`` import this name instead of defining their own copy
# so the regex stays in lockstep with the token emitter. Adding a new
# token shape only needs an edit here.
ANY_MASK_TOKEN_RE = _MASK_TOKEN_RE

# Pseudonym-only matcher (without the legacy ``<rN>`` alternative).
# Used by the per-category counter-recovery helper to identify which
# bucket a vault token belongs to.
_PSEUDONYM_TOKEN_RE = re.compile(r"<<([A-Z][A-Z0-9_]*?)_([A-Z]|\d+)>>")

# Doubled-bracket variants the LLM occasionally invents — same failure
# mode as the legacy ``[[NAME_0]]`` shape but for the new ``<rN>``
# tokens. ``<<r0>>`` is the strict double; ``<<r0>`` / ``<r0>>`` are
# the asymmetric forms observed in the May 2026 audit on the legacy
# shape and likely to recur in the same proportions here.
_DOUBLE_BRACKET_TOKEN_RE = re.compile(r"<<(r[A-Za-z0-9_\-]+)>>")
_DOUBLE_OPEN_TOKEN_RE = re.compile(r"<<(r[A-Za-z0-9_\-]+)>(?!>)")
_DOUBLE_CLOSE_TOKEN_RE = re.compile(r"(?<!<)<(r[A-Za-z0-9_\-]+)>>")


def contains_mask_token(text: str) -> bool:
    """True if ``text`` contains any DLP mask placeholder.

    Recognises both shapes (``<rN>`` and ``<<CATEGORY_N>>``) via the
    same ``ANY_MASK_TOKEN_RE`` used by restore/orphan-detection, so
    callers (e.g. the tool-argument placeholder-safety gate) stay in
    lockstep with the emitter and never hard-code the format. Read-only;
    does NOT change the placeholder format.
    """
    if not text:
        return False
    return ANY_MASK_TOKEN_RE.search(text) is not None


def redact_orphan_tokens(
    text: str, span_map: dict[str, str], *, trim_edges: bool = True
) -> str:
    """Strip mask tokens that survived ``restore`` and have no
    entry in ``span_map``.

    Recognises both shapes:
      - legacy counter-form ``<rN>`` (always emitted by secret-class
        detectors and by every detector when pseudonym mode is off)
      - pseudonym ``<<CATEGORY_N>>`` (emitted by identity-class
        detectors when ``DLP_PSEUDONYM_MODE=true``)

    Runs in the orchestrator's success path AFTER ``restore`` so the
    user-facing reply never carries an AI-hallucinated mask token or
    a leftover outbound-mask placeholder. Orphans are deleted (not
    replaced with a literal like ``[REDACTED]``) because the value
    is already gone from the text — surfacing a sentinel just
    confuses the reader, while removing the token leaves natural
    prose. The audit log (``dlp_orphan_redacted`` at the caller)
    still records which tokens were dropped.

    Whitespace cleanup runs only when at least one substitution
    actually happened, so the common no-orphan path is a no-op and
    pre-existing whitespace patterns in clean responses are
    preserved verbatim.

    No-op when the text is empty or contains no mask-token shape.

    ``trim_edges=False`` is for a SLICE of a streamed answer (FIX130): its
    edges are not the answer's edges, so the whitespace there — often the
    space between the previous slice's last word and this slice's first —
    must stay. Trimming it glued words ("the staging box" → "the
    stagingbox") whenever the 120-char DLP tail happened to cut at a space.
    """
    if not text or ("<r" not in text and "<<" not in text):
        return text

    def _replace(m: "re.Match[str]") -> str:
        token = m.group()
        if span_map and token in span_map:
            return token  # real binding — restore handled it
        return ""  # strip orphan silently

    result = _MASK_TOKEN_RE.sub(_replace, text)
    if result != text:
        # Collapse the double-spaces an interior strip leaves behind
        # ("hello <r5> world" → "hello  world" → "hello world") and
        # trim any leading/trailing whitespace from edge cases where
        # the orphan was at the start or end of the text.
        result = re.sub(r" {2,}", " ", result)
        if trim_edges:
            result = result.strip()
    return result


def sanitize_hallucinated_brackets(text: str, span_map: dict[str, str]) -> str:
    """Rewrite ``[[CAT_N]]`` tokens the model invented in its reply.

    Runs against the **raw provider response** before any output DLP
    scan or restore step. Two outcomes per double-bracket match:

    * Inner ``[CAT_N]`` is a real binding in ``span_map``: unwrap to
      the single-bracket form so ``restore`` can resolve it as
      intended. (The model just over-bracketed — clumsy but not a
      privacy event.)
    * Inner token is not in ``span_map``: replace the whole
      ``[[…]]`` with the literal text ``[REDACTED]``. This is the
      shape the orphan path used to surface verbatim in the user's
      reply; the sanitizer turns it into a deterministic safe value
      so the panel never reads ``Phone: [[NAME_1]]``.

    Returns the rewritten text; no-op when no double-bracket tokens
    are present.
    """
    if not text or ("<<" not in text and ">>" not in text):
        return text

    def _unwrap_or_redact(m: "re.Match[str]") -> str:
        inner = f"<{m.group(1)}>"
        if span_map and inner in span_map:
            return inner
        return "[REDACTED]"

    # Pass 1 — ``[[NAME_0]]`` (full double). Existing behaviour: unwrap
    # to the single-bracket form when the inner is a real binding,
    # otherwise [REDACTED].
    result = _DOUBLE_BRACKET_TOKEN_RE.sub(_unwrap_or_redact, text)

    # Pass 2 — ``[[NAME_0]`` (double open, single close). Same
    # treatment as the full double form: it's a model artefact and the
    # inner token is the meaningful part.
    result = _DOUBLE_OPEN_TOKEN_RE.sub(_unwrap_or_redact, result)

    # Pass 3 — ``[NAME_0]]`` (single open, trailing close). Strip the
    # extra ``]`` regardless of whether the inner is a real binding:
    # downstream restore handles unbinding the legitimate inner token,
    # and ``redact_orphan_tokens`` handles unmapped inners. All we do
    # here is rescue the stray bracket so the user-visible text isn't
    # corrupted by ``[ID_0]]"`` and friends.
    def _strip_trailing_close(m: "re.Match[str]") -> str:
        return f"<{m.group(1)}>"

    result = _DOUBLE_CLOSE_TOKEN_RE.sub(_strip_trailing_close, result)

    return result

# Tolerant variant of ``_MASK_TOKEN_RE`` used by ``_normalize_tokens`` to
# rewrite near-miss token shapes the model may emit back to us. We accept
# any letter case (``<R0>`` / ``<r0>``) and incidental whitespace inside
# the angles. The capture group is the body without the surrounding
# brackets / spaces so the replacer can rebuild the canonical ``<rN>``
# form.
_FUZZY_TOKEN_RE = re.compile(
    r'<\s*[Rr]\s*(\d+|[A-Za-z0-9_\-]+)\s*>'
)

# Pseudonym fuzzy variant — accepts uppercase / lowercase content and
# whitespace inside the doubled angles. The capture groups are
# ``prefix`` and ``suffix`` so the replacer can rebuild the canonical
# ``<<PREFIX_SUFFIX>>`` form.
_FUZZY_PSEUDONYM_RE = re.compile(
    r'<<\s*([A-Za-z][A-Za-z0-9_]*?)\s*_\s*([A-Za-z]|\d+)\s*>>'
)


def _normalize_tokens(text: str, span_map: dict[str, str]) -> str:
    """Rewrite near-miss token variants to their canonical form.

    Restore relies on exact ``str.replace`` against the keys in
    ``span_map``. LLMs occasionally echo tokens back with cosmetic
    drift — uppercase (``<R0>``), padded angles (``< r0 >``), a single
    trailing space (``<r0 >``) — and every such variant misses the
    replace and leaks the literal token to the user. This pre-pass
    canonicalises both shapes:

      - ``<rN>``        — fuzzy on case + whitespace
      - ``<<CAT_N>>``   — fuzzy on case + whitespace inside the angles

    A rewrite happens *iff* the canonical form is present in
    ``span_map``; otherwise the original substring is preserved
    untouched so unrelated angle-bracketed text the model produced
    is not accidentally rewritten.
    """
    if not span_map:
        return text

    def replacer_legacy(m: re.Match) -> str:
        canonical = f"<r{m.group(1)}>"
        return canonical if canonical in span_map else m.group(0)

    result = _FUZZY_TOKEN_RE.sub(replacer_legacy, text)

    def replacer_pseudo(m: re.Match) -> str:
        prefix = m.group(1).upper()
        suffix = m.group(2)
        # Letter suffix → uppercase; numeric suffix kept as-is.
        if suffix.isalpha():
            suffix = suffix.upper()
        canonical = f"<<{prefix}_{suffix}>>"
        return canonical if canonical in span_map else m.group(0)

    return _FUZZY_PSEUDONYM_RE.sub(replacer_pseudo, result)


def _substitute_tokens_single_pass(text: str, token_map: dict[str, str]) -> str:
    """Replace every ``token_map`` key in *text* with its value — ONE scan.

    Phase 12 A3. The naive per-token ``str.replace`` loop re-scans the
    accumulating result after every substitution, so a restored VALUE
    that itself contains a still-pending token literal (e.g. the value
    bound to ``<<PERSON_A>>`` is ``"call <<EMAIL_1>> later"`` while
    ``<<EMAIL_1>>`` is also in the map) gets double-substituted and the
    user's original data is corrupted. A single compiled alternation
    with a callback consumes each source occurrence exactly once and
    never looks inside replacement values.

    Ordering: alternatives are sorted longest-first because Python's
    ``re`` picks the FIRST alternative that matches at a position —
    without the sort, ``<r1>`` would shadow ``<r10>`` (prefix tokens),
    which is the same guarantee the old loop got from its
    ``sorted(key=len, reverse=True)``.
    """
    if not text or not token_map:
        return text
    tokens = [t for t in sorted(token_map, key=len, reverse=True) if t]
    if not tokens:
        return text
    pattern = re.compile("|".join(re.escape(t) for t in tokens))
    return pattern.sub(lambda m: token_map[m.group(0)], text)


# Descriptive placeholder tokens. Keyed on DetectionMatch.detector_name so
# the model (and the caller) sees "[EMAIL]" instead of "*****ple.com", which
# preserves enough context for the model to respond sensibly about the
# masked content without ever seeing the raw value.
DEFAULT_MASK_TOKEN = "[REDACTED_PII]"

MASK_TOKENS: dict[str, str] = {
    "email": "[EMAIL]",
    "ssn": "[SSN]",
    "phone": "[PHONE_NUMBER]",
    "credit_card": "[CREDIT_CARD]",
    "bearer_token": "[BEARER_TOKEN]",
    "private_key": "[PRIVATE_KEY]",
    "password_field": "[PASSWORD]",
    "ip_address": "[IP_ADDRESS]",
    "passport": "[PASSPORT]",
    "iban": "[IBAN]",
    "date_of_birth": "[DATE_OF_BIRTH]",
    "vehicle_plate": "[VEHICLE_PLATE]",
    "api_key_secret": "[CREDENTIAL]",
    # NER entities (PERSON, LOCATION, ORG, GPE, NRP) all share a single
    # "[NAME]" token in v1. Lossy vs entity type but keeps the token
    # readable and avoids per-entity config at the mask site.
    "presidio_ner": "[NAME]",
    # Regex-based fallback for bare Thai names — shares the [NAME]
    # token (and base-keyed counter) with presidio_ner so an output
    # restore step never has to distinguish the two sources.
    "thai_name_no_prefix": "[NAME]",
    # FIX37 — the Thai NER layer emits person spans under ``thai_ner`` and
    # address spans under ``thai_ner_location``. Both share a base token
    # with the engine that already covers that shape, so one person keeps
    # one placeholder however many detectors found them.
    "thai_ner": "[NAME]",
    "thai_ner_location": "[ADDRESS]",
    # Additional detectors from the DLP coverage expansion.
    "thai_national_id": "[THAI_ID]",
    "thai_tax_id": "[TAX_ID]",
    "thai_bank_account": "[BANK_ACCOUNT]",
    "thai_promptpay": "[PROMPTPAY]",
    "thai_address": "[ADDRESS]",
    "address": "[ADDRESS]",
    "thai_name": "[THAI_NAME]",
    "income": "[INCOME]",
    "db_connection_string": "[CREDENTIAL]",
    "medical_record": "[MEDICAL_RECORD]",
    "mac_address": "[MAC_ADDRESS]",
    # PDPA Section 26 sensitive data detectors.
    "religion_belief": "[RELIGION]",
    "health_condition": "[HEALTH_CONDITION]",
    "criminal_record": "[CRIMINAL_RECORD]",
    "biometric_data": "[BIOMETRIC]",
    "line_id": "[LINE_ID]",
}


def mask_token_for(detector_name: str) -> str:
    return MASK_TOKENS.get(detector_name, DEFAULT_MASK_TOKEN)


def _mask_base_name(detector_name: str) -> str:
    """Return the bracket-free base name for a detector's mask token.

    Multiple detectors can map to the same base (e.g. ``api_key_secret``
    and ``db_connection_string`` both use ``[CREDENTIAL]``). Callers that
    allocate indexed tokens MUST key their counters on this base name
    rather than on ``detector_name`` — otherwise two detectors that
    share a base will both emit ``[CREDENTIAL_0]`` and clobber each
    other in ``span_map``.
    """
    base = MASK_TOKENS.get(detector_name, DEFAULT_MASK_TOKEN)
    return base.strip("[]")


def _indexed_token(detector_name: str, index: int) -> str:
    """Return a neutral indexed mask token like ``<r0>`` so multiple
    occurrences of the same detector stay distinguishable for restore.

    The ``detector_name`` parameter is retained for callsite symmetry
    with the legacy ``[CATEGORY_N]`` shape but no longer affects the
    emitted token — the new shape is intentionally category-free so
    upstream safety filters (notably OpenAI's) cannot pattern-match a
    sensitive category name like ``BEARER_TOKEN`` / ``NATIONAL_ID``
    embedded in the prompt and refuse the request.
    """
    return f"<r{index}>"


def mask_token_base(token: str) -> str:
    """Return the body of a ``<rN>`` mask token without the brackets.

    The legacy shape carried a category prefix (``[EMAIL_0]`` -> ``EMAIL``);
    the new neutral shape does not, so this returns the ``r``-prefixed
    identifier instead. Kept for any external caller that wants a key
    derivable from the token alone.

    Examples:
        ``<r0>`` -> ``r0``
        ``<rAbCd1234>`` -> ``rAbCd1234``
    """
    if not token.startswith("<") or not token.endswith(">"):
        return ""
    return token[1:-1]


# ---------------------------------------------------------------------------
# Deterministic-tokenization helpers (May 2026)
# ---------------------------------------------------------------------------
#
# Counter-based tokens (``[EMAIL_0]``, ``[EMAIL_1]``, …) have two
# problems in multi-turn scenarios:
#
# 1. Counter resets per request. Turn 1 mints ``[EMAIL_0]`` for
#    ``alice@x.com``; turn 2 (fresh counter) mints ``[EMAIL_0]`` for
#    ``bob@y.com``. The vault binds the same key twice and one
#    original is lost.
# 2. The token reveals ordering. ``[EMAIL_0]`` always means "first
#    email in this message", which is mild metadata leakage.
#
# The deterministic scheme below uses an HMAC of the original value
# under a per-conversation key. Properties:
#
#   - Same value + same conversation_key → same token, every time.
#   - Different values (or different conversations) → different
#     tokens (collision probability ≈ 2⁻⁴⁸ per pair at 8-char
#     suffix).
#   - The token leaks no information about the original value or
#     position — only the category prefix is human-readable.
#
# The counter mode is preserved as the default (``conversation_key
# = None``) so existing tests and callers continue to work.

_TOKEN_SUFFIX_LEN = 8


def _make_conversation_key(
    hmac_secret: str,
    conversation_id: str,
) -> bytes:
    """Derive a per-conversation HMAC key from the global DLP secret.

    Returning the *derived* bytes (not the raw secret) gives an
    extra layer of isolation: leaking one conversation's key does
    not reveal the global secret. The downstream
    ``_make_deterministic_token`` uses these derived bytes directly.
    """
    return _hmac.new(
        hmac_secret.encode("utf-8"),
        conversation_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _make_deterministic_token(
    category: str,
    value: str,
    conversation_key: bytes,
) -> str:
    """Mint an opaque ``[CATEGORY_xxxxxxxx]`` token for ``value``.

    ``category`` is the bracket-free mask-token base name
    (``"EMAIL"``, ``"PHONE_NUMBER"``, …). ``value`` is the original
    PII string. ``conversation_key`` is the per-conversation HMAC
    key from ``_make_conversation_key``.

    The 8-char base64url suffix gives ~2⁴⁸ slots per category —
    collision probability negligible even at 10⁴ PII items per
    conversation. The caller is responsible for the disambiguator
    fallback when a same-suffix collision *does* hit a different
    value (see ``_allocate_deterministic_token``).
    """
    digest = _hmac.new(
        conversation_key,
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    suffix = base64.urlsafe_b64encode(digest).decode("ascii")[:_TOKEN_SUFFIX_LEN]
    # ``category`` is intentionally unused in the emitted token under
    # the neutral ``<rN>`` shape — preserved as a parameter so callers
    # remain unchanged across the format flip.
    del category
    return f"<r{suffix}>"


def _allocate_deterministic_token(
    detector_name: str,
    conversation_key: bytes,
    span_map: dict[str, str],
    reverse_map: dict[str, str],
    original: str,
) -> str:
    """Pick the deterministic token for ``original`` and bind it.

    Three properties enforced here:

    1. **Dedup** — if ``original`` has already been masked in this
       call, return the previously-minted token (one token per
       value, even when the value appears N times).
    2. **Collision guard** — if the HMAC suffix already maps to a
       *different* value in ``span_map`` (birthday paradox), append
       a short disambiguator derived from the value itself. After
       the disambiguator the token remains deterministic for the
       same value.
    3. **Stable binding** — record the binding in both ``span_map``
       (token → original) and ``reverse_map`` (original → token) so
       subsequent lookups in this call hit the cache.
    """
    existing_token = reverse_map.get(original)
    if existing_token is not None:
        return existing_token

    base = _mask_base_name(detector_name)
    token = _make_deterministic_token(base, original, conversation_key)

    existing = span_map.get(token)
    if existing is not None and existing != original:
        # Suffix collision with a different value — extremely
        # unlikely but the bind must not overwrite. Append a short
        # base64 fragment of the value to disambiguate; this keeps
        # the new token deterministic for the same input.
        disambig = base64.urlsafe_b64encode(
            original.encode("utf-8")
        ).decode("ascii")[:4]
        # ``token`` is ``<rXXXXXXXX>``; insert disambig before the
        # closing ``>`` to keep the shape neutral.
        token = f"<{token.strip('<>')}_{disambig}>"
        logger.warning(
            "dlp_deterministic_token_collision",
            base=base,
            existing_length=len(existing),
            new_length=len(original),
            disambiguated_token=token,
        )

    span_map[token] = original
    reverse_map[original] = token
    return token


# ---------------------------------------------------------------------------
# Pseudonymization layer (opt-in via DLP_PSEUDONYM_MODE)
# ---------------------------------------------------------------------------
#
# Goal: give the LLM readable per-category placeholders so it can
# reason about entity relationships ("the same person", "their email",
# "their phone") without ever seeing raw values. The provider sees
# tokens like ``<<PERSON_A>>`` / ``<<EMAIL_1>>``; the user sees the
# original value restored 100%.
#
# Layering:
#   - identity-class detectors (PSEUDONYM_MAP)  → ``<<CAT_N>>``
#   - secret-class detectors  (SECRET_DETECTORS)→ ``<rN>`` (legacy,
#                                                  never restored)
#   - bypass    (BYPASS_DETECTORS)              → None (don't mask)
#   - everything else                           → ``<rN>`` (legacy fallback)
#
# Per-category counter ``counters[prefix]`` is used for pseudonym
# tokens. The legacy ``__global__`` counter remains for ``<rN>``
# tokens so outbound mask passes stay collision-free with inbound.

# Detector name → readable category prefix used inside ``<<PREFIX_N>>``.
# Only identity-class detectors appear here. Anything not in this map
# (and not in SECRET_DETECTORS / BYPASS_DETECTORS) falls back to the
# legacy ``<rN>`` shape — keeps the migration safe.
PSEUDONYM_MAP: dict[str, str] = {
    # People
    "thai_name": "PERSON",
    "thai_name_no_prefix": "PERSON",
    "presidio_ner": "PERSON",
    # FIX37 — the Thai NER layer. Same PERSON / ADDRESS categories as the
    # regex detectors it backs up, so a name found by both engines keeps
    # ONE <<PERSON_A>> across the conversation instead of minting a
    # second placeholder.
    "thai_ner": "PERSON",
    # Contact
    "email": "EMAIL",
    "phone": "PHONE",
    "line_id": "LINE_ID",
    # Government IDs
    "thai_national_id": "NID",
    "passport": "PASSPORT",
    "thai_tax_id": "TAX_ID",
    "ssn": "SSN",
    # Financial
    "thai_bank_account": "ACCOUNT",
    "credit_card": "CARD",
    "iban": "IBAN",
    "thai_promptpay": "PROMPTPAY",
    # Network
    "ip_address": "IP",
    "mac_address": "MAC",
    # Location / demographic
    "address": "ADDRESS",
    "thai_address": "ADDRESS",
    "thai_ner_location": "ADDRESS",
    "date_of_birth": "DOB",
    "vehicle_plate": "PLATE",
    # PDPA Section 26 sensitive
    "medical_record": "MEDICAL",
    "health_condition": "HEALTH",
    "religion_belief": "RELIGION",
    "criminal_record": "CRIMINAL",
    "biometric_data": "BIOMETRIC",
}

# Categories whose suffix is a letter (A, B, …, Z, then 27, 28, …
# numeric for overflow). Only PERSON in v1 — keeps "Alice and Bob"
# readable as ``<<PERSON_A>>`` / ``<<PERSON_B>>`` in the prompt.
LETTER_SUFFIX_CATEGORIES: set[str] = {"PERSON"}

# Detectors whose findings stay on the ``<rN>`` shape regardless of
# pseudonym mode. The token is never added to the restore allow-list
# (policy never RESTOREs ``secret`` / ``credential``) so the
# placeholder reaches the user as an orphan-stripped silence.
SECRET_DETECTORS: set[str] = {
    "bearer_token",
    "api_key_secret",
    "private_key",
    "password_field",
    "db_connection_string",
}

# Detectors whose findings are NOT masked at all — the original value
# survives untouched.
#
# ``income`` was previously bypassed so the LLM could do arithmetic on
# salary figures. That coupled two decisions unsafely: it relied on name
# masking to unlink identity from the raw salary, but Thai-name coverage
# is far below the level that assumption needs, so a bypassed salary and
# a leaked name reached the provider in the same span (re-linkable). The
# ``income`` detector only fires on an explicit salary/income label (not
# on product prices like "ราคา 500 บาท"), so masking it is safe. Secure-
# calc (which computes on the raw value server-side) remains the path for
# legitimate salary arithmetic when enabled.
BYPASS_DETECTORS: set[str] = set()

# Phase 2A.1 — V2 numeric metadata accessors + message-index binding.
_POLICY_V2_KEY = "policy_v2"
# Supplemental/confidential/secret categories whose mask binding must
# never silently fail (fail closed instead of leaking the value).
_BIND_FAIL_CLOSED_CATEGORIES = frozenset({"secret", "credential", "financial"})


class MaskBindingError(Exception):
    """Raised when a message-index-bound sensitive finding cannot be
    validated against its target message — fail closed, never skip."""


def _pv2(match_action) -> dict:
    return (match_action.match.metadata or {}).get(_POLICY_V2_KEY) or {}


def _match_message_index(match_action):
    return _pv2(match_action).get("message_index")


def _match_force_mask(match_action) -> bool:
    return bool(_pv2(match_action).get("force_mask"))


def _is_pseudonym_mode_on() -> bool:
    """Centralised flag check.

    Lazy-imports settings so transformer.py stays usable in isolation
    (test_transformer.py exercises it without spinning the full app
    settings stack).
    """
    try:
        from app.core.config import settings
    except Exception:
        return False
    return bool(getattr(settings, "dlp_pseudonym_mode", False))


def _letter_suffix(idx: int) -> str:
    """Return ``A`` for idx=0, ``B`` for idx=1, … ``Z`` for idx=25.

    Beyond 25 we wrap to a numeric suffix starting at ``27`` so the
    token stays regex-clean (no ``[`` / ``\\``) and so a reader can
    tell at a glance that the conversation has > 26 distinct people.
    Two-letter suffixes would collide with multi-word abbreviations
    and complicate vault recovery — numeric overflow is simpler.
    """
    if 0 <= idx < 26:
        return chr(65 + idx)
    return str(idx + 1)


def _decode_letter_suffix(suffix: str) -> int:
    """Inverse of ``_letter_suffix``. Returns -1 on parse failure."""
    if len(suffix) == 1 and "A" <= suffix <= "Z":
        return ord(suffix) - 65
    try:
        return int(suffix) - 1
    except ValueError:
        return -1


def _pseudonym_token_for(prefix: str, counters: dict[str, int]) -> str:
    """Mint the next pseudonym token for ``prefix`` and advance the counter.

    PERSON-like categories use a letter suffix (A, B, …); others use
    a 1-indexed numeric suffix so the prompt reads ``<<EMAIL_1>>``,
    not ``<<EMAIL_0>>`` (1-indexed is easier for humans to follow).
    """
    if prefix in LETTER_SUFFIX_CATEGORIES:
        idx = counters.get(prefix, 0)
        suffix = _letter_suffix(idx)
        counters[prefix] = idx + 1
    else:
        idx = counters.get(prefix, 0) + 1
        suffix = str(idx)
        counters[prefix] = idx
    return f"<<{prefix}_{suffix}>>"


def recover_counters_from_vault(
    vault_tokens: list[str] | dict[str, str],
) -> dict[str, int]:
    """Build a fresh ``counters`` dict primed past the vault's
    highest-used index in every bucket.

    Recognises BOTH shapes:
      - ``<rN>``  (legacy)        → ``counters['__global__']``
      - ``<<CAT_N>>`` (pseudonym) → ``counters[CAT]``

    The dict returned is suitable for passing to ``mask_messages`` /
    ``mask_text`` as ``start_counters`` so newly minted tokens skip
    over slots the vault already owns. Returned dict is empty when
    the vault is empty — caller treats empty as "fresh start".
    """
    tokens = list(vault_tokens.keys() if isinstance(vault_tokens, dict) else vault_tokens)
    if not tokens:
        return {}
    counters: dict[str, int] = {}
    counter_re = re.compile(r"^<r(\d+)>$")
    for token in tokens:
        m_global = counter_re.match(token)
        if m_global:
            idx = int(m_global.group(1)) + 1
            counters["__global__"] = max(counters.get("__global__", 0), idx)
            continue
        m_pseudo = _PSEUDONYM_TOKEN_RE.match(token)
        if not m_pseudo:
            continue
        prefix, suffix = m_pseudo.group(1), m_pseudo.group(2)
        if prefix in LETTER_SUFFIX_CATEGORIES:
            recovered = _decode_letter_suffix(suffix)
            if recovered < 0:
                continue
            counters[prefix] = max(counters.get(prefix, 0), recovered + 1)
        else:
            try:
                counters[prefix] = max(counters.get(prefix, 0), int(suffix))
            except ValueError:
                continue
    return counters


def _allocate_token(
    detector_name: str,
    counters: dict[str, int],
    span_map: dict[str, str],
    original: str,
    *,
    force_mask: bool = False,
    allocator=None,
) -> str | None:
    """Pick the next ``[BASE_N]`` token for this detector and bind it.

    Phase 4A.1.1: when ``allocator`` is provided (Vault V2 path), the
    token comes EXCLUSIVELY from the scoped atomic reservation — the
    in-request ``__global__`` counter and pseudonym shapes are bypassed
    so the Redis ``__counter__`` is the single allocation authority. The
    income/``force_mask`` bypass is still honoured. Exhausting the
    reserved range raises (fail closed — never an in-request ``<rN>``).

    Counter keying: by *base* name. This is the fix for the collision
    bug where two detectors sharing a base (``api_key_secret`` and
    ``db_connection_string`` → both ``[CREDENTIAL]``) each reset their
    own index to 0 and produced the same ``[CREDENTIAL_0]`` token,
    losing one value on ``span_map`` dict write.

    Defensive guard: if the token somehow still collides with an
    existing span_map entry whose original is *different*, log a clear
    error and allocate a fresh-offset token so both values survive.
    After the base-keyed counter fix this branch should never fire in
    practice — keeping it means a future MASK_TOKENS edit that
    introduces a new collision pattern fails loud instead of
    corrupting data silently.

    Routing under ``DLP_PSEUDONYM_MODE=true``:
      - ``BYPASS_DETECTORS`` (income)        → returns ``None`` so the
                                               caller leaves the value
                                               untouched in the text.
      - ``SECRET_DETECTORS``                 → ``<rN>`` + ``__global__``
                                               counter (legacy shape;
                                               the orphan-redact pass
                                               strips it from the
                                               user-facing reply).
      - detector in ``PSEUDONYM_MAP``         → ``<<CAT_N>>`` + per-
                                               category counter.
      - unknown detector                     → ``<rN>`` (legacy fallback).

    Under ``DLP_PSEUDONYM_MODE=false`` (default), all routing collapses
    to the legacy ``<rN>`` + ``__global__`` path so existing callers
    and tests keep identical behaviour.
    """
    pseudonym_mode = _is_pseudonym_mode_on()

    # Bypass — only honoured in pseudonym mode. In legacy mode every
    # finding still gets a placeholder; flipping the mode is the
    # opt-in for letting income / similar fields through to the LLM.
    # ``force_mask`` (Phase 2A.1) overrides the bypass: when the V2
    # numeric policy classified this finding CONFIDENTIAL_NUMERIC, the
    # value must be masked even though ``income`` is in BYPASS_DETECTORS.
    if pseudonym_mode and detector_name in BYPASS_DETECTORS and not force_mask:
        return None

    # Vault V2 reserved-token path — single allocation authority. No
    # pseudonym shape, no internal counter, no collision fallback (each
    # reserved token is unique by construction).
    if allocator is not None:
        token = allocator.next()
        span_map[token] = original
        return token

    # Identity-class pseudonym path (only in pseudonym mode + detector
    # listed in PSEUDONYM_MAP + not in SECRET_DETECTORS).
    if (
        pseudonym_mode
        and detector_name in PSEUDONYM_MAP
        and detector_name not in SECRET_DETECTORS
    ):
        prefix = PSEUDONYM_MAP[detector_name]
        token = _pseudonym_token_for(prefix, counters)
        # Collision guard — same value mapped to the same token, even
        # via the per-category counter, would be rare but possible if
        # ``counters`` was pre-seeded past the actual binding. Fail
        # loud rather than overwrite.
        existing = span_map.get(token)
        if existing is not None and existing != original:
            logger.error(
                "dlp_pseudonym_collision",
                token=token,
                existing_length=len(existing),
                new_length=len(original),
                detector=detector_name,
            )
            # Advance past this slot and try again — the per-category
            # counter has already incremented, so the next call will
            # land on a fresh suffix.
            return _allocate_token(
                detector_name, counters, span_map, original,
                force_mask=force_mask,
            )
        span_map[token] = original
        return token

    # Legacy path: ``<rN>`` + ``__global__`` counter. Reached for:
    #   - secret-class detectors  (regardless of mode)
    #   - any detector            in legacy mode
    #   - unknown detectors       (defensive fallback)
    #
    # Only the ``__global__`` key feeds the legacy index. Per-category
    # pseudonym counters (``counters["PERSON"]=2``) MUST NOT bleed
    # into the secret-class counter — a secret following an identity
    # would otherwise mint ``<r2>`` on an empty global. Pre-pseudonym
    # callers always wrote ``__global__`` (or left counters empty);
    # vault recovery is the only producer of per-category keys and
    # they're kept strictly separate from the legacy bucket.
    idx = counters.get("__global__", 0)
    token = f"<r{idx}>"
    counters["__global__"] = idx + 1

    existing = span_map.get(token)
    if existing is not None and existing != original:
        # Counter collision across two distinct values that landed on
        # the same ``<rN>`` slot — log and bail to a high-offset slot
        # rather than overwrite the existing binding.
        logger.error(
            "dlp_span_map_collision",
            token=token,
            existing_length=len(existing),
            new_length=len(original),
            detector=detector_name,
        )
        fallback = idx
        while True:
            fallback += 1
            alt = f"<r{fallback}>"
            if alt not in span_map:
                token = alt
                counters["__global__"] = fallback + 1
                break

    span_map[token] = original
    return token


# ---------------------------------------------------------------------------
# Bare-name substring masking (pseudonym mode only)
# ---------------------------------------------------------------------------
#
# After the main detector-driven mask pass, multi-word PERSON entries
# in ``span_map`` are split into individual words and the messages are
# scanned for standalone occurrences. This catches multi-turn cases
# where the user wrote the full name ("ลูกค้า สมชาย ใจดี") in turn 1
# and only the first name ("สมชาย เคยสั่งซื้อ?") in turn 3 — the
# ``thai_name_no_prefix`` regex demands two whitespace-separated Thai
# words, so it misses the bare-name occurrence; this second pass picks
# it up and binds it to the same pseudonym so the LLM sees one entity.
#
# Boundary policy:
#   - Word boundary on both sides by default (no Thai consonant or
#     ASCII alnum directly adjacent).
#   - LEFT-side soft boundary: a known Thai honorific (``คุณ`` / ``นาย``
#     / ``นาง`` / ``นางสาว`` / ``น.ส.``) directly preceding the word
#     also counts as a valid boundary, so ``คุณใจดี`` matches ``ใจดี``
#     as the bound surname. Right side is always hard-boundary.
#
# FP guards:
#   - Only PERSON tokens are split (other categories don't have natural
#     word decompositions that map to identity).
#   - Words with < 3 non-combining Thai/ASCII letters are skipped
#     (matches short tokens are high-FP).
#   - A word that appears in two different PERSON entries is marked
#     ambiguous and dropped from the substitution table.
_BARE_NAME_HONORIFICS = (
    "คุณ",
    "นาย",
    "นาง",
    "นางสาว",
    "น.ส.",
)


def _is_thai_codepoint(ch: str) -> bool:
    return 0x0E00 <= ord(ch) <= 0x0E7F


def _bare_name_letter_count(word: str) -> int:
    """Count base letters in *word*, ignoring Thai combining marks.

    A 'base letter' is any non-combining codepoint — Thai consonants
    and standalone vowels count; Thai vowel-above / vowel-below / tone
    marks do not. ``"กุล"`` therefore counts as 2 (ก, ล) and ``"สมชาย"``
    counts as 5 (ส, ม, ช, า, ย). This matches the user-visible
    "letter" count and is what the < 3 FP guard keys on.
    """
    import unicodedata as _ud
    return sum(1 for c in word if not _ud.combining(c))


def _replace_bare_word(
    text: str, word: str, token: str
) -> str:
    """Replace standalone occurrences of *word* with *token*.

    See module docstring above for the boundary policy. Manual
    scanner instead of regex because the left-side honorific
    soft-boundary requires variable-width lookbehind which Python's
    stdlib ``re`` doesn't support.
    """
    if not word or word not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    word_len = len(word)
    while i < n:
        idx = text.find(word, i)
        if idx == -1:
            out.append(text[i:])
            break
        # Left boundary.
        left_ok = (idx == 0)
        if not left_ok:
            prev = text[idx - 1]
            if not _is_thai_codepoint(prev) and not prev.isalnum():
                left_ok = True
            else:
                for honorific in _BARE_NAME_HONORIFICS:
                    h_len = len(honorific)
                    if idx >= h_len and text[idx - h_len:idx] == honorific:
                        left_ok = True
                        break
        # Right boundary (always hard).
        end = idx + word_len
        right_ok = (end == n)
        if not right_ok:
            nxt = text[end]
            if not _is_thai_codepoint(nxt) and not nxt.isalnum():
                right_ok = True
        if left_ok and right_ok:
            out.append(text[i:idx])
            out.append(token)
            i = end
        else:
            # Advance past this position by 1 to allow re-anchor on
            # the next candidate (consistent with the same step-1-on-
            # reject behaviour used in ``thai_name_no_prefix.detect``).
            out.append(text[i:idx + 1])
            i = idx + 1
    return "".join(out)


class DLPTransformer:
    """Applies enforcement actions to text by replacing sensitive spans."""

    def _mask_bare_substrings(
        self,
        messages: "list[Message]",
        span_map: dict[str, str],
        reverse_map: dict[str, str],
    ) -> "list[Message]":
        """Second-pass bare-name expansion. See module-level docstring.

        Only fires under ``DLP_PSEUDONYM_MODE=true`` — legacy ``<rN>``
        mode keeps its existing behaviour so callers / tests pinned to
        the old contract are unaffected.
        """
        if not _is_pseudonym_mode_on() or not span_map:
            return messages

        # Build word → token table from PERSON entries only.
        word_to_token: dict[str, str] = {}
        ambiguous: set[str] = set()
        for token, value in span_map.items():
            if not token.startswith("<<PERSON_"):
                continue
            parts = value.split()
            if len(parts) < 2:
                continue  # single-word value has no decomposition
            for word in parts:
                if _bare_name_letter_count(word) < 3:
                    continue
                existing = word_to_token.get(word)
                if existing is None:
                    word_to_token[word] = token
                elif existing != token:
                    ambiguous.add(word)
        for w in ambiguous:
            word_to_token.pop(w, None)

        if not word_to_token:
            return messages

        out: list = []
        for msg in messages:
            # Vision / list-content messages have no scannable spans and
            # _replace_bare_word expects str — pass them through intact.
            if not isinstance(msg.content, str):
                out.append(msg)
                continue
            text = msg.content
            for word, token in word_to_token.items():
                text = _replace_bare_word(text, word, token)
            # model_copy preserves tool_calls / tool_call_id / name /
            # reasoning_* — rebuilding as Message(role, content) would drop
            # them and orphan a following role="tool" message (OpenAI 400).
            out.append(msg.model_copy(update={"content": text}))

        # Bookkeeping: tell the caller's reverse_map about the
        # bare-word bindings so any subsequent within-call dedup
        # (a value seen later identical to a bare word) reuses the
        # same token. The vault carry-over (orchestrator.py writes
        # ``span_map`` to Redis, not ``reverse_map``) is unaffected.
        for word, token in word_to_token.items():
            reverse_map.setdefault(word, token)
        return out

    def transform_text(
        self,
        text: str,
        match_actions: list[MatchAction],
    ) -> TransformationResult:
        """Apply mask/redact to *text* at the spans specified by *match_actions*.

        Returns a TransformationResult with the modified text and a record
        of every span that was replaced.
        """
        # Filter to actionable items only (MASK or REDACT)
        actionable = [
            ma for ma in match_actions
            if ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
        ]

        if not actionable:
            return TransformationResult(transformed_text=text)

        # Resolve overlaps then sort by start descending for reverse processing
        resolved = _resolve_overlaps(actionable)
        resolved.sort(key=lambda ma: ma.match.start, reverse=True)

        result_text = text
        spans: list[TransformedSpan] = []

        for ma in resolved:
            start = ma.match.start
            end = ma.match.end

            if ma.action == EnforcementAction.MASK:
                replacement = mask_token_for(ma.match.detector_name)
            else:  # REDACT
                replacement = "[REDACTED]"

            result_text = result_text[:start] + replacement + result_text[end:]

            spans.append(
                TransformedSpan(
                    start=start,
                    end=end,
                    original_length=end - start,
                    replacement_length=len(replacement),
                    action=ma.action,
                )
            )

        # Return spans in forward order (ascending start) for readability
        spans.sort(key=lambda s: s.start)

        return TransformationResult(
            transformed_text=result_text,
            transformed_spans=spans,
        )

    def transform_messages(
        self,
        messages: list[Message],
        match_actions: list[MatchAction],
    ) -> tuple[list[Message], list[TransformationResult]]:
        """Apply transformations to a list of messages.

        Match actions are routed to the correct message by matching the
        span offsets against each message's content.  Since scan_text
        runs per-message (offsets are relative to each message), all
        match_actions for a given message share that message's offset space.

        For simplicity in MVP, match_actions are applied to all messages
        that contain the matched text at the specified offsets.  The caller
        (orchestrator) is responsible for grouping match_actions by message
        index if needed.

        Parameters
        ----------
        messages : list of Message
            Original messages (not mutated).
        match_actions : list of MatchAction
            All enforcement actions across all messages.

        Returns
        -------
        tuple of (new_messages, results)
            new_messages: list of Message with transformed content.
            results: one TransformationResult per message.
        """
        from app.schemas.common import Message as MessageModel

        new_messages: list[MessageModel] = []
        results: list[TransformationResult] = []

        for msg in messages:
            # Vision-format messages (content is a list of content
            # blocks for image+text turns) carry no scannable
            # spans — the rust_dlp_client scanner flattens to text
            # blocks only, and image bytes have no PII surface here.
            # Skip transformation and pass the message through
            # unchanged so the downstream provider still sees the
            # image_url + text array intact.
            if not isinstance(msg.content, str):
                new_messages.append(msg)
                results.append(self.transform_text("", []))
                continue

            # Find match_actions whose spans fall within this message
            msg_actions = [
                ma for ma in match_actions
                if 0 <= ma.match.start < len(msg.content)
                and ma.match.end <= len(msg.content)
                and msg.content[ma.match.start:ma.match.end] == ma.match.matched_text
            ]

            result = self.transform_text(msg.content, msg_actions)
            results.append(result)
            # model_copy preserves tool_calls / tool_call_id / name /
            # reasoning_* (rebuilding as Message(role, content) drops them).
            new_messages.append(
                msg.model_copy(update={"content": result.transformed_text})
            )

        return new_messages, results

    def mask_messages(
        self,
        messages: list[Message],
        match_actions: list[MatchAction],
        *,
        conversation_key: bytes | None = None,
        start_counters: dict[str, int] | None = None,
        vault_reverse_map: dict[str, str] | None = None,
        token_allocator=None,
        record_sink: list | None = None,
    ) -> tuple[list[Message], dict[str, str], dict[str, int]]:
        """Mask messages using indexed tokens and return a span_map.

        Phase 4A.1.1 (Vault V2): when ``token_allocator`` is provided,
        tokens are consumed from a scoped atomic reservation instead of the
        in-request counter, within-call value-dedup is disabled (each
        finding/span gets its OWN token), and one
        :class:`AllocatedTokenRecord` per emitted token is appended to
        ``record_sink``. When ``token_allocator`` is None (legacy/default),
        behaviour is byte-identical to before.

        Unlike ``transform_messages``, each MASK replacement uses a
        per-detector indexed token (``[EMAIL_0]``, ``[EMAIL_1]`` …) so
        that two masked emails remain distinguishable.  The returned
        ``span_map`` maps each indexed token back to its original value
        and is used by ``restore()`` to reverse the masking on the
        provider's response.

        The final element of the tuple is the per-**base-token** index
        ``counters`` as they stand after this call. Counters are keyed
        on the stripped mask token name (e.g. ``"CREDENTIAL"``), not
        the detector name, so multiple detectors that share a base
        (``api_key_secret`` + ``db_connection_string`` both map to
        ``[CREDENTIAL]``) get distinct indices and do not collide in
        ``span_map``.

        Callers that also mask content in the *output* direction
        (provider response) within the same request must thread these
        counters into ``mask_text(..., start_counters=...)`` — otherwise
        the outbound pass starts its own counter at 0 and can collide
        with inbound tokens, which would make ``restore()`` splice the
        user's own input PII into positions where the provider wrote
        something different.

        Cross-turn determinism: when ``vault_reverse_map`` is supplied
        (value → token from a prior turn's vault read), any value
        already present in the map reuses its prior token, so
        ``alice@x.com`` keeps the same ``<r0>`` across turns of one
        conversation. ``start_counters`` primes the counter so freshly
        minted tokens skip over indices the vault already owns.
        Together these replace the legacy ``conversation_key`` HMAC
        scheme — the parameter is accepted for backwards compatibility
        but no longer affects the emitted token shape, which is always
        the counter form ``<rN>``.

        REDACT actions keep the static ``[REDACTED]`` replacement and
        are intentionally NOT added to the span_map — redaction is
        meant to be permanent.

        BLOCK and ALLOW actions are pass-through (no text change).
        """
        from app.schemas.common import Message as MessageModel
        # ``conversation_key`` is accepted for backwards-compat but
        # unused under the counter-only emission scheme.
        del conversation_key

        new_messages: list[MessageModel] = []
        span_map: dict[str, str] = {}
        # Counters are seeded from ``start_counters`` so a caller can
        # pre-prime ``__global__`` past whatever indices the vault
        # already owns. Otherwise counters are per-call: a fresh
        # request starts at 0.
        counters: dict[str, int] = (
            dict(start_counters) if start_counters else {}
        )
        # Reverse map for within-call dedup — value → token so the
        # same PII string masked twice in this call reuses its first
        # token. Pre-seeded with cross-turn bindings when the caller
        # passes ``vault_reverse_map``: a value already minted in a
        # prior turn keeps the same ``<rN>``. The caller is expected
        # to also pass ``start_counters`` primed past the highest
        # vault index so newly minted tokens don't collide with vault
        # ones — keeping span_map free of unused vault bindings means
        # ``restore`` and ``redact_orphan_tokens`` only see the
        # tokens actually emitted in this turn's text.
        reverse_map: dict[str, str] = (
            dict(vault_reverse_map) if vault_reverse_map else {}
        )

        for idx, msg in enumerate(messages):
            # FIX27: vision list-content messages are no longer passed
            # through unmasked. Both scanners flatten a list to its text
            # blocks ("\n"-joined — rust_dlp_client._flatten_content and
            # DetectorRegistry.scan_messages) and bind spans to THAT view,
            # so the text blocks of an image+text turn are scannable; the
            # old pass-through let an attachment turn carry PII around the
            # masking layer on every surface that sends images. Image
            # blocks are never touched.
            blocks: list | None = None
            if isinstance(msg.content, str):
                text_view = msg.content
            elif isinstance(msg.content, list):
                blocks = msg.content
                text_view = flatten_list_content(blocks)
            else:
                new_messages.append(msg)
                continue

            # Find match_actions whose spans fall within this message.
            # Phase 2A.1: a finding carrying ``message_index`` is bound to
            # exactly that message — it is applied ONLY there, never to a
            # different message even if the offset+value coincide. A
            # message-index-bound sensitive finding that fails span
            # validation FAILS CLOSED (no silent skip). Findings WITHOUT a
            # message_index keep the legacy offset+value match (single-text
            # callers, and the unchanged legacy decision path in shadow).
            def _slice_ok(ma):
                s, e = ma.match.start, ma.match.end
                return (
                    0 <= s < len(text_view)
                    and e <= len(text_view)
                    and text_view[s:e] == ma.match.matched_text
                )

            msg_actions = []
            for ma in match_actions:
                mi = _match_message_index(ma)
                if mi is not None:
                    if mi != idx:
                        continue
                    if _slice_ok(ma):
                        msg_actions.append(ma)
                    elif (
                        ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
                        and ma.match.category in _BIND_FAIL_CLOSED_CATEGORIES
                    ):
                        raise MaskBindingError(
                            f"message-index binding failed for "
                            f"{ma.match.detector_name} on message {idx}"
                        )
                    # else: non-sensitive bound finding that doesn't
                    # validate → drop (safe).
                elif _slice_ok(ma):
                    msg_actions.append(ma)

            # Filter to actionable items (MASK or REDACT).
            actionable = [
                ma for ma in msg_actions
                if ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
            ]

            if not actionable:
                # Nothing to mask — copy to preserve tool_calls /
                # tool_call_id / name / reasoning_* (a bare
                # Message(role, content) would drop them and orphan a
                # following role="tool" message → OpenAI 400).
                new_messages.append(msg.model_copy())
                continue

            resolved = _resolve_overlaps(actionable)
            # Sort by start ASCENDING so we allocate indices in reading order,
            # then apply replacements in REVERSE so offsets stay valid.
            resolved.sort(key=lambda ma: ma.match.start)

            # First pass: assign tokens in reading order using the
            # counter scheme. Per-value dedup (within-call AND
            # cross-turn via vault_reverse_map) is handled by
            # ``reverse_map`` — the lookup happens before
            # ``_allocate_token`` so a vault hit reuses its prior
            # token (``<rN>`` or ``<<CAT_N>>``) and only fresh values
            # consume a new counter.
            #
            # When ``_allocate_token`` returns ``None`` the detector
            # is in ``BYPASS_DETECTORS`` (currently ``income``) — the
            # span is dropped from ``replacements`` so the original
            # value stays in the text unmodified.
            replacements: list[tuple[int, int, str, str]] = []
            # (start, end, replacement, original)
            for ma in resolved:
                if ma.action == EnforcementAction.MASK:
                    original = text_view[ma.match.start:ma.match.end]
                    if token_allocator is not None:
                        # V2: reserved token per span; no value-dedup so
                        # duplicate values bind to distinct tokens/records.
                        token = _allocate_token(
                            ma.match.detector_name, counters, span_map, original,
                            force_mask=_match_force_mask(ma),
                            allocator=token_allocator,
                        )
                        if token is None:
                            continue  # bypass — value untouched
                        if record_sink is not None:
                            record_sink.append(
                                _build_allocated_record(token, original, ma, idx)
                            )
                    else:
                        existing_token = reverse_map.get(original)
                        if existing_token is not None:
                            span_map.setdefault(existing_token, original)
                            token = existing_token
                        else:
                            token = _allocate_token(
                                ma.match.detector_name, counters, span_map, original,
                                force_mask=_match_force_mask(ma),
                            )
                            if token is None:
                                # Bypass — leave the value untouched.
                                continue
                            reverse_map[original] = token
                    replacements.append(
                        (ma.match.start, ma.match.end, token, original)
                    )
                else:  # REDACT
                    replacements.append(
                        (ma.match.start, ma.match.end, "[REDACTED]", "")
                    )

            # Second pass: apply in reverse offset order.
            if blocks is not None:
                new_messages.append(
                    msg.model_copy(
                        update={
                            "content": apply_replacements_to_blocks(blocks, replacements)
                        }
                    )
                )
                continue
            result_text = text_view
            for start, end, replacement, _orig in sorted(
                replacements, key=lambda r: r[0], reverse=True
            ):
                result_text = result_text[:start] + replacement + result_text[end:]

            # model_copy preserves tool_calls / tool_call_id / name /
            # reasoning_* (rebuilding as Message(role, content) drops them).
            new_messages.append(msg.model_copy(update={"content": result_text}))

        # Second pass — bare-name substring expansion (pseudonym mode).
        # No-op when the flag is off or when no PERSON multi-word
        # entries landed in this turn's span_map.
        new_messages = self._mask_bare_substrings(
            new_messages, span_map, reverse_map
        )

        return new_messages, span_map, counters

    def mask_text(
        self,
        text: str,
        match_actions: list[MatchAction],
        *,
        start_counters: dict[str, int] | None = None,
        conversation_key: bytes | None = None,
        vault_reverse_map: dict[str, str] | None = None,
        token_allocator=None,
        record_sink: list | None = None,
        record_message_index: int | None = None,
    ) -> tuple[str, dict[str, str], dict[str, int]]:
        """Mask a single text string using indexed tokens + span_map.

        Phase 4A.1.1 (Vault V2): ``token_allocator`` / ``record_sink``
        behave as in :meth:`mask_messages` (reserved tokens, per-span
        records). Legacy behaviour is byte-identical when both are None.

        Shares the same indexing scheme as ``mask_messages`` but operates
        on a raw string (used for the provider response path where there
        is no Message envelope).

        Pass ``start_counters`` to continue the indexing sequence from a
        prior ``mask_messages`` call within the same request. Without
        this, an outbound pass would start at index 0 and collide with
        inbound tokens already in the caller's span_map — ``restore``
        would then replace the AI's newly-invented value with the
        user's original PII. See ``mask_messages`` docstring for the
        rationale.

        ``vault_reverse_map`` carries cross-turn bindings (value →
        prior token). Any value already in the map reuses its prior
        ``<rN>`` so the same PII keeps the same token across turns of
        one conversation. ``conversation_key`` is accepted for
        backwards compatibility but unused — tokens are always the
        counter form.
        """
        del conversation_key  # backwards-compat shim, see docstring

        actionable = [
            ma for ma in match_actions
            if ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
        ]
        counters: dict[str, int] = (
            dict(start_counters) if start_counters else {}
        )
        if not actionable:
            return text, {}, counters

        resolved = _resolve_overlaps(actionable)
        resolved.sort(key=lambda ma: ma.match.start)

        replacements: list[tuple[int, int, str]] = []
        span_map: dict[str, str] = {}
        reverse_map: dict[str, str] = (
            dict(vault_reverse_map) if vault_reverse_map else {}
        )

        for ma in resolved:
            if ma.action == EnforcementAction.MASK:
                original = text[ma.match.start:ma.match.end]
                if token_allocator is not None:
                    token = _allocate_token(
                        ma.match.detector_name, counters, span_map, original,
                        force_mask=_match_force_mask(ma),
                        allocator=token_allocator,
                    )
                    if token is None:
                        continue  # bypass
                    if record_sink is not None:
                        record_sink.append(
                            _build_allocated_record(
                                token, original, ma, record_message_index
                            )
                        )
                else:
                    existing_token = reverse_map.get(original)
                    if existing_token is not None:
                        span_map.setdefault(existing_token, original)
                        token = existing_token
                    else:
                        token = _allocate_token(
                            ma.match.detector_name, counters, span_map, original,
                            force_mask=_match_force_mask(ma),
                        )
                        if token is None:
                            # Bypass — leave the value untouched.
                            continue
                        reverse_map[original] = token
                replacements.append((ma.match.start, ma.match.end, token))
            else:  # REDACT
                replacements.append((ma.match.start, ma.match.end, "[REDACTED]"))

        result_text = text
        for start, end, replacement in sorted(
            replacements, key=lambda r: r[0], reverse=True
        ):
            result_text = result_text[:start] + replacement + result_text[end:]

        return result_text, span_map, counters

    def restore(
        self,
        text: str,
        span_map: dict[str, str],
        vault_resolver=None,
        *,
        allowed_tokens: set[str] | None = None,
    ) -> str:
        """Reverse indexed masking by replacing each token with its original.

        Single-pass substitution (Phase 12 A3): one compiled alternation
        over the escaped token set, ordered longest-first so ``<r10>``
        wins over its ``<r1>`` prefix at the same position. One scan
        means a restored VALUE is never re-visited — the old per-token
        ``str.replace`` loop re-scanned the whole string after every
        replacement, so a value that happened to contain another
        still-pending token literal was corrupted by a later pass.

        ``vault_resolver`` is an optional callable
        ``(list[str]) -> dict[str, str]`` that the caller wires to a
        cross-turn store (``ConversationVault``). When the local
        ``span_map`` doesn't cover a token surviving in the text — a
        multi-turn echo of a placeholder minted in an earlier turn —
        the resolver gets a chance to fill the gap. Anything the
        resolver still can't map is logged as
        ``dlp_orphan_unresolvable`` and passes through to the user
        as the literal token (unchanged failure mode).
        """
        if not text:
            return text
        result = text
        active_span_map = span_map
        if allowed_tokens is not None:
            active_span_map = {
                token: value
                for token, value in span_map.items()
                if token in allowed_tokens
            }
        # Diagnostic snapshot BEFORE restore — kept at debug level so
        # operators can flip ``LOG_LEVEL=DEBUG`` to reproduce the
        # span_map / orphan analysis used during the May 2026 audit
        # without re-editing this file.
        logger.debug(
            "dlp_restore_debug_start",
            span_map_size=len(span_map),
            span_map_tokens=list(span_map.keys()),
            span_map_value_lengths={k: len(v) for k, v in active_span_map.items()},
            text_len=len(text),
            tokens_in_text=sorted(set(_MASK_TOKEN_RE.findall(text))),
        )
        if active_span_map:
            # Canonicalise lowercase / whitespace-padded variants the
            # model may have echoed back before the exact-match
            # substitution runs — otherwise ``[ EMAIL_0 ]`` would never
            # match the ``[EMAIL_0]`` key and would leak as a literal
            # token.
            result = _normalize_tokens(result, active_span_map)
            result = _substitute_tokens_single_pass(result, active_span_map)
        # Orphan detection runs whether or not span_map was empty: a
        # bracketed [DETECTOR_N] surviving to this point is suspicious
        # either way.
        orphans = set(_MASK_TOKEN_RE.findall(result))
        # A token-shaped literal that is a KEY of ``active_span_map`` can
        # only survive the single pass above as part of a restored VALUE
        # (every occurrence in the source text was consumed). It is the
        # user's original data, not an orphan — never re-substitute it
        # via the vault path or warn about it.
        orphans -= set(active_span_map)
        # Diagnostic snapshot AFTER restore — same purpose as the
        # _start event above.
        logger.debug(
            "dlp_restore_debug_end",
            matched_tokens_in_input=[t for t in active_span_map if t in text],
            unmatched_span_map_tokens=[t for t in active_span_map if t not in text],
            orphans_in_output=sorted(orphans),
            output_len=len(result),
        )
        if orphans and vault_resolver is not None:
            # Cross-turn fallback. ``vault_resolver`` may raise (caller
            # contract: it must not) — wrap defensively so a vault bug
            # never breaks a response.
            try:
                resolved = vault_resolver(sorted(orphans)) or {}
            except Exception as exc:
                logger.warning(
                    "dlp_vault_resolver_failed",
                    error=str(exc)[:200],
                )
                resolved = {}
            # Same single-pass discipline as the main substitution above:
            # a vault-restored value containing another pending orphan
            # token literal must not be corrupted by a later replace.
            result = _substitute_tokens_single_pass(result, resolved)
            for token in resolved:
                logger.info(
                    "dlp_orphan_resolved_from_vault",
                    token=token,
                )
            orphans = set(_MASK_TOKEN_RE.findall(result))
            orphans -= set(active_span_map) | set(resolved)
            if orphans:
                logger.warning(
                    "dlp_orphan_unresolvable",
                    orphan_tokens=sorted(orphans),
                    output_len=len(result),
                )
        elif orphans:
            logger.warning(
                "dlp_orphan_mask_tokens",
                orphan_tokens=sorted(orphans),
                span_map_size=len(span_map),
                output_len=len(result),
            )
        return result


def flatten_list_content(blocks: list) -> str:
    """The text view of a vision list-content message: its ``text`` blocks
    joined with ``"\n"`` — byte-identical to what both scanners hand the
    detectors (``rust_dlp_client._flatten_content`` and
    ``DetectorRegistry.scan_messages``), so a finding's offsets index this
    string. Non-text blocks contribute nothing."""
    return "\n".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )


def apply_replacements_to_blocks(
    blocks: list, replacements: list[tuple[int, int, str, str]]
) -> list:
    """Apply ``(start, end, replacement, original)`` spans — offsets in the
    :func:`flatten_list_content` view — to the text blocks of a vision
    message. Returns a NEW block list (dict copies); image and other
    non-text blocks are carried over untouched.

    A span is applied to every text block it overlaps, on that block's
    own slice. A span that straddles the ``"\n"`` joint between two blocks
    therefore lands as a token in each — a duplicated placeholder is the
    safe failure; leaving either half in clear would be a leak.
    """
    if not replacements:
        return [dict(b) if isinstance(b, dict) else b for b in blocks]
    out: list = []
    offset = 0
    for b in blocks:
        if not (isinstance(b, dict) and b.get("type") == "text"):
            out.append(dict(b) if isinstance(b, dict) else b)
            continue
        text = b.get("text", "")
        lo, hi = offset, offset + len(text)
        local: list[tuple[int, int, str]] = []
        for start, end, replacement, _orig in replacements:
            if end <= lo or start >= hi:
                continue
            local.append((max(start, lo) - lo, min(end, hi) - lo, replacement))
        for s, e, replacement in sorted(local, key=lambda r: r[0], reverse=True):
            text = text[:s] + replacement + text[e:]
        nb = dict(b)
        nb["text"] = text
        out.append(nb)
        offset = hi + 1  # the "\n" joint
    return out


def _resolve_overlaps(actions: list[MatchAction]) -> list[MatchAction]:
    """Resolve overlapping spans.

    Resolution rules, checked in order:

    1. **Safety first** — a higher ``EnforcementAction`` always wins.
       ``BLOCK`` beats ``MASK`` beats ``REDACT`` beats ``ALLOW``
       regardless of detector specificity. A strict profile's BLOCK
       on a credential must never be downgraded by a more-specific
       detector that happens to MASK on the same span.
    2. **Specificity** — when actions are equal, the match with the
       higher ``DetectionMatch.priority`` wins. This lets a specific
       detector (e.g. ``thai_national_id`` with ``priority=10``) beat
       a generic over-matcher (``phone`` at default priority 0) on
       the same 13-digit run.
    3. **Position** — when both action and priority are equal, the
       match that starts earlier wins (stable ordering).

    The losing span is dropped entirely — partial overlap replacement
    is too fragile.
    """
    if len(actions) <= 1:
        return list(actions)

    # Sort by (start asc, action desc, priority desc) so that within any
    # overlapping group the highest-priority + highest-action match is
    # encountered first. The loop below still performs an explicit
    # priority comparison for the case where two overlapping spans
    # start at different offsets.
    sorted_actions = sorted(
        actions,
        key=lambda ma: (
            ma.match.start,
            -ma.action,
            -getattr(ma.match, "priority", 0),
        ),
    )

    kept: list[MatchAction] = [sorted_actions[0]]

    for current in sorted_actions[1:]:
        prev = kept[-1]
        if current.match.start < prev.match.end:
            # Overlap — apply rules 1, 2, 3 in order.
            if current.action > prev.action:
                kept[-1] = current  # rule 1: safety first
            elif current.action == prev.action:
                prev_priority = getattr(prev.match, "priority", 0)
                curr_priority = getattr(current.match, "priority", 0)
                if curr_priority > prev_priority:
                    kept[-1] = current  # rule 2: specificity wins
                # else: rule 3 — keep prev (it started earlier by sort)
            # else: lower action — keep prev
        else:
            kept.append(current)

    return kept
