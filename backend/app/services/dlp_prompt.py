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

_MASK_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")

DLP_PLACEHOLDER_PROMPT = (
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

    if result and result[0].role == "system":
        result[0] = Msg(
            role="system",
            content=DLP_PLACEHOLDER_PROMPT + "\n\n" + result[0].content,
        )
    else:
        result.insert(0, Msg(role="system", content=DLP_PLACEHOLDER_PROMPT))

    return result
