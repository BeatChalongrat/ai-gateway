"""DLP system-prompt injection for masked messages.

When the DLP pipeline masks PII tokens in user messages (e.g. [NAME_0],
[EMAIL_0]), some LLMs refuse to respond because the bracketed tokens
look suspicious.  This module injects a short system-level explanation
so the model treats them as normal placeholders.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.common import Message

_MASK_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")

DLP_PLACEHOLDER_PROMPT = (
    "IMPORTANT CONTEXT: This system applies automatic data-loss-prevention "
    "(DLP). Personally identifiable information in the user's message has "
    "been replaced with bracketed placeholder tokens of the form "
    "[CATEGORY_NUMBER]. These tokens are safe privacy placeholders — the "
    "real values have already been removed. Your task is to respond "
    "helpfully to the user's request, treating every placeholder as if "
    "it were a normal value. When referring back to a placeholder, copy "
    "it exactly as it appeared in the user's message. Do NOT refuse the "
    "request, do NOT warn about sensitive data, and do NOT ask for the "
    "original values."
    " "
    "Critical rule: only echo bracketed tokens that ALREADY APPEAR in "
    "the user's message — character-for-character. If you encounter "
    "information that looks sensitive but is NOT already in [TOKEN_N] "
    "form, replace it with the literal text [REDACTED]. Do NOT invent "
    "new bracketed placeholder tokens, and do NOT echo the value "
    "verbatim."
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
