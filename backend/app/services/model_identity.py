"""FIX27 Part 2 — tell the model which model it is.

A model does not know its own name: asked "คุณคือโมเดลอะไร" a request routed
to gpt-5-mini answered "ChatGPT (รุ่น GPT-4o)". The gateway is the ONLY
component that knows the model it is about to call — after auto-routing and
after any fallback swap — so it states the identity itself, once, on the
system prompt, immediately before each provider attempt.

Appended to the END of the first system message (or a new one when there
is none) so the provider-side prompt-cache prefix stays stable; the line
is a constant per model, so the cache key changes only when the model does.
Never mutates the caller's list.
"""

from __future__ import annotations

from app.schemas.common import Message

IDENTITY_MARKER = "[model identity]"


def identity_line(model: str, provider: str) -> str:
    return (
        f"{IDENTITY_MARKER} You are the model `{model}` (provider: {provider}), served "
        "through the SRAN AI Gateway. If the user asks which model or AI you are, answer "
        f"with exactly `{model}`; never present yourself as a different model or product "
        "name, and never guess a version you were not told."
    )


def inject_model_identity(messages: list[Message], model: str, provider: str) -> list[Message]:
    """Return a new message list carrying the identity line for *model*.

    An earlier identity line (a previous fallback attempt) is replaced, so a
    swapped provider never inherits the failed model's name.
    """
    if not model:
        return messages
    line = identity_line(model, provider or "unknown")
    result = list(messages)
    if result and result[0].role == "system" and isinstance(result[0].content, str):
        base = _strip_identity(result[0].content)
        result[0] = result[0].model_copy(update={"content": (base + "\n\n" + line) if base else line})
    else:
        result.insert(0, Message(role="system", content=line))
    return result


def _strip_identity(text: str) -> str:
    idx = text.find("\n\n" + IDENTITY_MARKER)
    if idx >= 0:
        return text[:idx]
    if text.startswith(IDENTITY_MARKER):
        return ""
    return text
