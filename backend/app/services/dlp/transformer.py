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

# Any bracketed ALL-CAPS token with a trailing index — same shape produced
# by ``_indexed_token``. Used by ``restore`` to detect tokens that survive
# restore because they are not in the span_map (orphans).
_MASK_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")


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
    # Additional detectors from the DLP coverage expansion.
    "thai_national_id": "[THAI_ID]",
    "thai_tax_id": "[TAX_ID]",
    "thai_bank_account": "[BANK_ACCOUNT]",
    "thai_promptpay": "[PROMPTPAY]",
    "thai_address": "[ADDRESS]",
    "db_connection_string": "[CREDENTIAL]",
    "medical_record": "[MEDICAL_RECORD]",
    "mac_address": "[MAC_ADDRESS]",
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
    """Return an indexed mask token like '[EMAIL_0]' so multiple
    occurrences of the same detector stay distinguishable for restore.
    """
    return f"[{_mask_base_name(detector_name)}_{index}]"


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
    base = _mask_base_name(detector_name)
    idx = counters.get(base, 0)
    token = f"[{base}_{idx}]"
    counters[base] = idx + 1

    existing = span_map.get(token)
    if existing is not None and existing != original:
        # Base-keyed counter failed to avoid collision — bail to a
        # high-offset slot rather than overwrite the existing binding.
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
            alt = f"[{base}_{fallback}]"
            if alt not in span_map:
                token = alt
                counters[base] = fallback + 1
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

        REDACT actions keep the static ``[REDACTED]`` replacement and
        are intentionally NOT added to the span_map — redaction is
        meant to be permanent.

        BLOCK and ALLOW actions are pass-through (no text change).
        """
        from app.schemas.common import Message as MessageModel

        new_messages: list[MessageModel] = []
        span_map: dict[str, str] = {}
        # Counters are keyed on the *base* mask token name (see
        # ``_mask_base_name``) so detectors that share a base — e.g.
        # api_key_secret and db_connection_string both emit
        # ``[CREDENTIAL]`` — still allocate disjoint indices within a
        # single request. Per-message keying would let the base counter
        # reset between messages and reintroduce the collision.
        counters: dict[str, int] = {}

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

            # First pass: assign indexed tokens in reading order.
            replacements: list[tuple[int, int, str, str]] = []
            # (start, end, replacement, original)
            for ma in resolved:
                if ma.action == EnforcementAction.MASK:
                    original = msg.content[ma.match.start:ma.match.end]
                    token = _allocate_token(
                        ma.match.detector_name, counters, span_map, original
                    )
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
        """
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

        for ma in resolved:
            if ma.action == EnforcementAction.MASK:
                original = text[ma.match.start:ma.match.end]
                token = _allocate_token(
                    ma.match.detector_name, counters, span_map, original
                )
                replacements.append((ma.match.start, ma.match.end, token))
            else:  # REDACT
                replacements.append((ma.match.start, ma.match.end, "[REDACTED]"))

        result_text = text
        for start, end, replacement in sorted(
            replacements, key=lambda r: r[0], reverse=True
        ):
            result_text = result_text[:start] + replacement + result_text[end:]

        return result_text, span_map, counters

    def restore(self, text: str, span_map: dict[str, str]) -> str:
        """Reverse indexed masking by replacing each token with its original.

        Processes longer tokens first so that ``[EMAIL_10]`` is replaced
        before ``[EMAIL_1]`` — otherwise the shorter token would match
        the prefix of the longer one and corrupt the output.

        If the text still contains indexed mask tokens *after* restore
        (so: tokens the caller never registered in ``span_map``), emit
        an ``dlp_orphan_mask_tokens`` warning. Behavior is intentionally
        unchanged — the literal token passes through to the user — but
        operators get visibility on the orphan so they can diagnose
        stale history replays, split-vault bugs, or provider echoes
        of tokens the gateway never issued.
        """
        if not text:
            return text
        result = text
        if span_map:
            for token in sorted(span_map, key=len, reverse=True):
                result = result.replace(token, span_map[token])
        # Orphan detection runs whether or not span_map was empty: a
        # bracketed [DETECTOR_N] surviving to this point is suspicious
        # either way.
        orphans = set(_MASK_TOKEN_RE.findall(result))
        if orphans:
            logger.warning(
                "dlp_orphan_mask_tokens",
                orphan_tokens=sorted(orphans),
                span_map_size=len(span_map),
                text_snippet=result[:120],
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
