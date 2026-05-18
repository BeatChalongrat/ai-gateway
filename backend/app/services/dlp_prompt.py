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
    "This system masks PII with tokens like [CATEGORY_N] before sending "
    "to you. Treat each token as a normal value. Echo tokens exactly as "
    "written — never modify, invent, or double-bracket them. Do not warn "
    "about sensitive data or ask for original values."
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
