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
from typing import TYPE_CHECKING

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
# Used by ``restore`` to detect surviving tokens (orphans) and by
# ``orchestrator._scan_text_as_result`` to filter mask-token literals
# out of outbound scan results.
_MASK_TOKEN_RE = re.compile(r"<r[A-Za-z0-9_\-]+>")

# Doubled-bracket variants the LLM occasionally invents — same failure
# mode as the legacy ``[[NAME_0]]`` shape but for the new ``<rN>``
# tokens. ``<<r0>>`` is the strict double; ``<<r0>`` / ``<r0>>`` are
# the asymmetric forms observed in the May 2026 audit on the legacy
# shape and likely to recur in the same proportions here.
_DOUBLE_BRACKET_TOKEN_RE = re.compile(r"<<(r[A-Za-z0-9_\-]+)>>")
_DOUBLE_OPEN_TOKEN_RE = re.compile(r"<<(r[A-Za-z0-9_\-]+)>(?!>)")
_DOUBLE_CLOSE_TOKEN_RE = re.compile(r"(?<!<)<(r[A-Za-z0-9_\-]+)>>")


def redact_orphan_tokens(text: str, span_map: dict[str, str]) -> str:
    """Strip ``<rN>`` tokens that survived ``restore`` and have no
    entry in ``span_map``.

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

    No-op when the text is empty or contains no ``<rN>`` tokens.
    """
    if not text or "<r" not in text:
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
        result = re.sub(r" {2,}", " ", result).strip()
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


def _normalize_tokens(text: str, span_map: dict[str, str]) -> str:
    """Rewrite near-miss token variants to their canonical ``<rN>`` form.

    Restore relies on exact ``str.replace`` against the keys in
    ``span_map``. LLMs occasionally echo tokens back with cosmetic
    drift — uppercase (``<R0>``), padded angles (``< r0 >``), a single
    trailing space (``<r0 >``) — and every such variant misses the
    replace and leaks the literal token to the user. This pre-pass
    canonicalises any token shape matching ``_FUZZY_TOKEN_RE`` to the
    lowercase, whitespace-free form *iff* that canonical form is
    present in ``span_map``; otherwise the original substring is
    preserved untouched so we don't accidentally rewrite unrelated
    angle-bracketed text the model produced.
    """
    if not span_map:
        return text

    def replacer(m: re.Match) -> str:
        canonical = f"<r{m.group(1)}>"
        return canonical if canonical in span_map else m.group(0)

    return _FUZZY_TOKEN_RE.sub(replacer, text)


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
            existing_value_prefix=existing[:20],
            new_value_prefix=original[:20],
            disambiguated_token=token,
        )

    span_map[token] = original
    reverse_map[original] = token
    return token


def _allocate_token(
    detector_name: str,
    counters: dict[str, int],
    span_map: dict[str, str],
    original: str,
) -> str:
    """Pick the next ``[BASE_N]`` token for this detector and bind it.

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
    """
    # Single shared counter under the neutral ``<rN>`` shape. The
    # legacy per-base counter dict can't be used because the emitted
    # token no longer carries the base — two detectors starting at 0
    # would both mint ``<r0>`` for different values. ``__global__`` is
    # the canonical key; legacy per-base entries written by
    # ``start_counters`` callers are absorbed by taking the max across
    # all values so an outbound pass still continues past the inbound
    # high-water mark.
    if "__global__" in counters:
        idx = counters["__global__"]
    elif counters:
        idx = max(counters.values())
    else:
        idx = 0
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
            existing_value_prefix=existing[:20],
            new_value_prefix=original[:20],
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


class DLPTransformer:
    """Applies enforcement actions to text by replacing sensitive spans."""

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
            # Find match_actions whose spans fall within this message
            msg_actions = [
                ma for ma in match_actions
                if 0 <= ma.match.start < len(msg.content)
                and ma.match.end <= len(msg.content)
                and msg.content[ma.match.start:ma.match.end] == ma.match.matched_text
            ]

            result = self.transform_text(msg.content, msg_actions)
            results.append(result)
            new_messages.append(
                MessageModel(role=msg.role, content=result.transformed_text)
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
    ) -> tuple[list[Message], dict[str, str], dict[str, int]]:
        """Mask messages using indexed tokens and return a span_map.

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

        for msg in messages:
            # Find match_actions whose spans fall within this message.
            msg_actions = [
                ma for ma in match_actions
                if 0 <= ma.match.start < len(msg.content)
                and ma.match.end <= len(msg.content)
                and msg.content[ma.match.start:ma.match.end] == ma.match.matched_text
            ]

            # Filter to actionable items (MASK or REDACT).
            actionable = [
                ma for ma in msg_actions
                if ma.action in (EnforcementAction.MASK, EnforcementAction.REDACT)
            ]

            if not actionable:
                new_messages.append(
                    MessageModel(role=msg.role, content=msg.content)
                )
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
            # ``<rN>`` and only fresh values consume a new counter.
            replacements: list[tuple[int, int, str, str]] = []
            # (start, end, replacement, original)
            for ma in resolved:
                if ma.action == EnforcementAction.MASK:
                    original = msg.content[ma.match.start:ma.match.end]
                    existing_token = reverse_map.get(original)
                    if existing_token is not None:
                        span_map.setdefault(existing_token, original)
                        token = existing_token
                    else:
                        token = _allocate_token(
                            ma.match.detector_name, counters, span_map, original
                        )
                        reverse_map[original] = token
                    replacements.append(
                        (ma.match.start, ma.match.end, token, original)
                    )
                else:  # REDACT
                    replacements.append(
                        (ma.match.start, ma.match.end, "[REDACTED]", "")
                    )

            # Second pass: apply in reverse offset order.
            result_text = msg.content
            for start, end, replacement, _orig in sorted(
                replacements, key=lambda r: r[0], reverse=True
            ):
                result_text = result_text[:start] + replacement + result_text[end:]

            new_messages.append(
                MessageModel(role=msg.role, content=result_text)
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
    ) -> tuple[str, dict[str, str], dict[str, int]]:
        """Mask a single text string using indexed tokens + span_map.

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
                existing_token = reverse_map.get(original)
                if existing_token is not None:
                    span_map.setdefault(existing_token, original)
                    token = existing_token
                else:
                    token = _allocate_token(
                        ma.match.detector_name, counters, span_map, original
                    )
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

        Processes longer tokens first so that ``[EMAIL_10]`` is replaced
        before ``[EMAIL_1]`` — otherwise the shorter token would match
        the prefix of the longer one and corrupt the output.

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
            # model may have echoed back before the exact-match replace
            # loop runs — otherwise ``[ EMAIL_0 ]`` would never match the
            # ``[EMAIL_0]`` key and would leak as a literal token.
            result = _normalize_tokens(result, active_span_map)
            for token in sorted(active_span_map, key=len, reverse=True):
                result = result.replace(token, active_span_map[token])
        # Orphan detection runs whether or not span_map was empty: a
        # bracketed [DETECTOR_N] surviving to this point is suspicious
        # either way.
        orphans = set(_MASK_TOKEN_RE.findall(result))
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
            for token, value in resolved.items():
                result = result.replace(token, value)
                logger.info(
                    "dlp_orphan_resolved_from_vault",
                    token=token,
                )
            orphans = set(_MASK_TOKEN_RE.findall(result))
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
