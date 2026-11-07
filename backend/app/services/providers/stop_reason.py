"""Why the model stopped — one closed set, one readable table (FIX119).

``finish_reason`` on the wire stays OpenAI-shaped (``stop | length |
tool_calls | content_filter``) because the gateway is an OpenAI-compatible
API and its callers parse that vocabulary. But that vocabulary loses what
the clients need to say: Anthropic's ``refusal`` and ``stop_sequence``
both became ``stop``, Gemini's ``OTHER`` became ``stop``, and a provider
that sent nothing at all was reported as a clean finish — the same guess
FIX118 took out of the run's ending.

``stop_reason`` is the gateway's own closed set, carried beside
``finish_reason`` on the final streaming chunk and on the non-streaming
choice. The table below is the ONLY place a provider family's native
value is turned into it. ``docs/contracts/terminal_cause.json`` is the
source of truth; ``tests/test_stop_reason.py`` fails when this file and
the contract disagree, and the Go/TS trees pin themselves to the same
file.

Rules:
* a value the family's table does not name → ``unknown``, and the
  provider and the raw value are logged (``stop_reason_unmapped``) so the
  missing row is visible rather than swallowed;
* ``None`` / empty → ``unknown``, never ``end_turn``.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

STOP_REASONS: tuple[str, ...] = (
    "end_turn",
    "max_tokens",
    "tool_use",
    "stop_sequence",
    "content_filter",
    "refusal",
    "context_window_exceeded",
    "unknown",
)

UNKNOWN = "unknown"

# family → native value → closed-set value. Keep in step with
# docs/contracts/terminal_cause.json → stop_reason_map (pinned by test).
STOP_REASON_MAP: dict[str, dict[str, str]] = {
    # OpenAI and every OpenAI-compatible provider (openrouter, sranai,
    # groq, deepseek, xai, qwen, kimi, zai, minimax).
    "openai": {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "content_filter",
    },
    "anthropic": {
        "end_turn": "end_turn",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence",
        "tool_use": "tool_use",
        "refusal": "refusal",
        # A server-tool pause: the turn is resumable, the text so far is a
        # complete answer segment — closest to a finished turn.
        "pause_turn": "end_turn",
        "model_context_window_exceeded": "context_window_exceeded",
    },
    "google": {
        "STOP": "end_turn",
        "MAX_TOKENS": "max_tokens",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "BLOCKLIST": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "SPII": "content_filter",
        "MALFORMED_FUNCTION_CALL": "unknown",
        "OTHER": "unknown",
        "FINISH_REASON_UNSPECIFIED": "unknown",
    },
}


def normalize_stop_reason(family: str, raw: str | None, *, provider: str | None = None) -> str:
    """Map a provider family's native stop value into the closed set.

    ``family`` is the wire dialect the adapter parsed (``openai`` /
    ``anthropic`` / ``google``); ``provider`` is the adapter's own name for
    the log line (``sranai``, ``openrouter``, …).
    """
    if raw is None or raw == "":
        return UNKNOWN
    table = STOP_REASON_MAP.get(family)
    if table is None:
        log.warning("stop_reason_unmapped", provider=provider or family, family=family, value=raw, reason="unknown_family")
        return UNKNOWN
    mapped = table.get(raw)
    if mapped is None:
        log.warning("stop_reason_unmapped", provider=provider or family, family=family, value=raw)
        return UNKNOWN
    return mapped


def is_stop_reason(value: str | None) -> bool:
    return value in STOP_REASONS
