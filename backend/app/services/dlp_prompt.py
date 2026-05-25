"""DLP system-prompt injection for masked messages.

When the DLP pipeline replaces tokens in user messages (e.g. <r0>,
<r1>), some LLMs may still refuse to respond because the placeholder
tokens look suspicious. This module injects a short system-level
explanation so the model treats them as normal placeholders.

The prompt copy is deliberately neutral — it does *not* mention "PII",
"sensitive data", "masked", or "personal information". The earlier
copy used those terms and OpenAI's safety filter would interpret the
whole request as something it should refuse, ending in canned
"I can't assist with that" responses even when the user just sent a
phone number that should round-trip through the gateway transparently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Import the shared regex from the transformer so there is exactly ONE
# definition of the mask-token shape in the codebase. Adding a new
# shape (or extending the pseudonym form) only needs an edit to
# ``transformer.ANY_MASK_TOKEN_RE``. Previously this module carried a
# duplicate definition that silently drifted apart from the
# transformer's during the May 2026 token-shape change.
from app.services.dlp.transformer import ANY_MASK_TOKEN_RE as _MASK_TOKEN_RE

if TYPE_CHECKING:
    from app.schemas.common import Message

DLP_PLACEHOLDER_PROMPT_V3 = (
    "Two rules for handling placeholders in this conversation:\n"
    "1. Preserve every existing angle-bracket placeholder (in the "
    "shapes <<...>> or <r...>) exactly as written in the input. "
    "Do not modify, abbreviate, translate, or rename them.\n"
    "2. Do not invent new placeholder tokens of either shape. "
    "If you need to refer to information that was not provided, use "
    "plain descriptions (e.g. \"the customer's address\") instead of "
    "creating new placeholders."
)

# ``V1`` was the long-form variant carrying a ``[CATEGORY_NUMBER]``
# example. Under the neutral ``<rN>`` shape the long form is replaced
# entirely by the v3 wording — both names point at the same string so
# call sites that imported v1 keep working without an env override.
DLP_PLACEHOLDER_PROMPT_V1 = DLP_PLACEHOLDER_PROMPT_V3


def _select_prompt() -> str:
    """Return the active prompt based on ``DLP_PROMPT_VARIANT``.

    Both v1 and v3 now point at the same neutral wording — the env
    knob is preserved for forward-compat in case a future variant
    needs A/B comparison.
    """
    from app.core.config import settings

    variant = (getattr(settings, "dlp_prompt_variant", "v3") or "v3").lower()
    if variant == "v1":
        return DLP_PLACEHOLDER_PROMPT_V1
    return DLP_PLACEHOLDER_PROMPT_V3


# Back-compat alias — call sites (and the regression tests) import this
# symbol directly.
DLP_PLACEHOLDER_PROMPT = DLP_PLACEHOLDER_PROMPT_V3


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
