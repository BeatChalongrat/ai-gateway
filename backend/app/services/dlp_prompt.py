"""DLP system-prompt injection for masked messages.

When the DLP pipeline replaces tokens in user messages (e.g. [NAME_0],
[EMAIL_0]), some LLMs refuse to respond because the bracketed tokens
look suspicious. This module injects a short system-level explanation
so the model treats them as normal placeholders.

The prompt copy is deliberately neutral — it does *not* mention "PII",
"sensitive data", "masked", or "personal information". The earlier
copy used those terms and OpenAI's safety filter would interpret the
whole request as something it should refuse, ending in canned
"I can't assist with that" responses even when the user just sent a
phone number that should round-trip through the gateway transparently.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.common import Message

# Matches both the indexed (``[NAME_0]``) and the HMAC-suffixed
# (``[NAME_aB3kM9xQ]``) token forms emitted by ``transformer.py``.
# The previous numeric-only pattern silently missed every
# conversation-mode request (``DLP_HMAC_KEY`` + ``conversation_id``)
# so the placeholder system prompt was never injected and the AI
# fell back to refusing the request — same failure mode that
# motivated the prompt rewrite in the first place. Pattern is kept
# in lockstep with ``transformer._MASK_TOKEN_RE``.
_MASK_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_[A-Za-z0-9_\-]+\]")

DLP_PLACEHOLDER_PROMPT_V1 = (
    "Some values in this message have been replaced with placeholder "
    "tokens like [CATEGORY_NUMBER] for privacy. Treat each token as "
    "a regular text value. Echo tokens exactly as written if needed. "
    "Critical rule: do not invent new tokens for content the gateway "
    "did not already replace — use [REDACTED] for such content. "
    "Never create new tokens. If you want to refer to a replaced "
    "value, use the exact token already provided. Do not number new "
    "tokens. "
    "Continue helping normally."
)


# Variant v3 — drops the literal ``[CATEGORY_NUMBER]`` example entirely.
# The May 2026 50-request audit showed ``gpt-4o-mini`` invents tokens in
# ~48% of replies; the leading hallucination driver was the model
# pattern-matching the ``[CATEGORY_NUMBER]`` example and emitting
# similar shapes (``[NAME_1]``, ``[BEARER_TOKEN_0]``,
# ``[YOUR_COMPANY]``) verbatim. Removing the example removes the
# pattern seed. Positive-frame instruction ("echo each one back
# unchanged") replaces the prior negative list. ``[REDACTED]`` stays
# as the orphan-substitute (keeps the regression tests around the
# critical-rule contract happy).
DLP_PLACEHOLDER_PROMPT_V3 = (
    "If the message contains bracketed tokens, echo each one back "
    "unchanged when you reference its value. When you need to refer "
    "to anything else that looks like personal data, write [REDACTED]. "
    "Do not generate any other bracketed text. Continue helping the "
    "user."
)


def _select_prompt() -> str:
    """Return the active prompt based on ``DLP_PROMPT_VARIANT``.

    Defaults to v1 so the existing regression suite
    (``TestRedactedRuleInPrompt``: requires ``Critical rule`` +
    ``[CATEGORY_NUMBER]``) keeps passing without an env override.
    Operators wanting to A/B v3 set ``DLP_PROMPT_VARIANT=v3`` on the
    backend container. Unknown values silently fall back to v1.
    """
    from app.core.config import settings

    variant = (getattr(settings, "dlp_prompt_variant", "v1") or "v1").lower()
    if variant == "v3":
        return DLP_PLACEHOLDER_PROMPT_V3
    return DLP_PLACEHOLDER_PROMPT_V1


# Back-compat alias — call sites (and the regression tests) import this
# symbol directly. Default = v1 string so the regression assertions
# (``[REDACTED]``, ``Critical rule``, ``[CATEGORY_NUMBER]``) still hold
# without an env override.
DLP_PLACEHOLDER_PROMPT = DLP_PLACEHOLDER_PROMPT_V1


def has_mask_tokens(messages: list[Message]) -> bool:
    """Return True if any message content contains DLP mask tokens."""
    for msg in messages:
        if isinstance(msg.content, str) and _MASK_TOKEN_RE.search(msg.content):
            return True
    return False


def inject_dlp_system_prompt(messages: list[Message]) -> list[Message]:
    """Inject a system prompt explaining DLP placeholders if needed.

    Returns a new list (never mutates the input).  If no mask tokens are
    found the original list is returned unchanged.
    """
    if not has_mask_tokens(messages):
        return messages

    from app.schemas.common import Message as Msg

    result = list(messages)

    active_prompt = _select_prompt()
    if result and result[0].role == "system":
        result[0] = Msg(
            role="system",
            content=active_prompt + "\n\n" + result[0].content,
        )
    else:
        result.insert(0, Msg(role="system", content=active_prompt))

    return result
