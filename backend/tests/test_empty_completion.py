"""FIX49 Part A — success must mean the user got an answer.

Pins the two swallow points measured live in the 2026-09-03 audit
(25/25 blank ``gpt-5.6-terra`` turns recorded as success):

  * OpenAI's Responses API delivers failures INSIDE a 200 stream
    (``type: "error"`` + ``response.failed``) — the parser must raise,
    not skip the line as an unknown lifecycle event.
  * OpenRouter (and OpenAI-compatible aggregators) forward upstream
    failures as ``{"error": {...}}`` data frames on the chat-completions
    shape — same rule.

And the truth-telling backstop: ``empty_completion`` exists in the
error catalogue and is retryable — the route/orchestrator guards use it
for any stream or response that ends with no text and no tool call.
"""

import json

import pytest

from app.core import error_catalog
from app.core.exceptions import (
    ProviderError,
    ProviderUnavailableError,
    RateLimitExceededError,
)
from app.services.providers.error_handler import map_stream_error_payload
from app.services.providers.openai_adapter import (
    _parse_responses_sse_line,
    _parse_sse_line,
)


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload)


# ── in-stream error payload classification ─────────────────────────────

def test_insufficient_quota_maps_to_rate_limit():
    exc = map_stream_error_payload(
        {"type": "insufficient_quota", "code": "credit_balance_exhausted",
         "message": "You have no credits remaining."},
        "openai",
    )
    assert isinstance(exc, RateLimitExceededError)
    assert exc.details["body"]["error"]["code"] == "credit_balance_exhausted"


def test_overloaded_maps_to_unavailable():
    exc = map_stream_error_payload({"message": "The engine is overloaded"}, "openrouter")
    assert isinstance(exc, ProviderUnavailableError)


def test_unknown_error_maps_to_provider_error():
    exc = map_stream_error_payload({"message": "something odd"}, "deepseek")
    assert isinstance(exc, ProviderError)
    assert "deepseek" in str(exc)


# ── chat-completions stream shape (OpenRouter et al.) ──────────────────

def test_error_frame_raises():
    line = _sse({"error": {"message": "Rate limit exceeded", "code": 429}})
    with pytest.raises(RateLimitExceededError):
        _parse_sse_line(line, provider_name="openrouter")


def test_error_next_to_choices_does_not_raise():
    # Some aggregators attach a vestigial error object beside real
    # choices — content must still flow.
    line = _sse({
        "error": {"message": "ignored"},
        "choices": [{"delta": {"content": "hi"}, "finish_reason": None}],
    })
    chunk = _parse_sse_line(line, provider_name="openrouter")
    assert chunk is not None and chunk.delta == "hi"


def test_plain_chunk_unaffected():
    line = _sse({"choices": [{"delta": {"content": "4"}, "finish_reason": "stop"}]})
    chunk = _parse_sse_line(line)
    assert chunk is not None
    assert chunk.delta == "4" and chunk.finish_reason == "stop"


# ── Responses API stream shape ─────────────────────────────────────────

def test_responses_error_event_raises():
    line = _sse({"type": "error", "error": {
        "type": "insufficient_quota", "code": "credit_balance_exhausted",
        "message": "You have no credits remaining."}})
    with pytest.raises(RateLimitExceededError):
        _parse_responses_sse_line(line)


def test_responses_failed_event_raises():
    line = _sse({"type": "response.failed", "response": {
        "error": {"code": "server_error", "message": "boom"}}})
    with pytest.raises(ProviderError):
        _parse_responses_sse_line(line)


def test_responses_incomplete_surfaces_length_finish():
    line = _sse({"type": "response.incomplete", "response": {
        "model": "gpt-5.6-terra",
        "usage": {"input_tokens": 10, "output_tokens": 64,
                  "output_tokens_details": {"reasoning_tokens": 60}}}})
    chunk = _parse_responses_sse_line(line)
    assert chunk is not None
    assert chunk.finish_reason == "length"
    assert chunk.usage is not None and chunk.usage.reasoning_tokens == 60


def test_responses_lifecycle_events_still_ignored():
    assert _parse_responses_sse_line(_sse({"type": "response.created"})) is None
    assert _parse_responses_sse_line(_sse({"type": "response.in_progress"})) is None


def test_responses_text_delta_unaffected():
    chunk = _parse_responses_sse_line(
        _sse({"type": "response.output_text.delta", "delta": "4"})
    )
    assert chunk is not None and chunk.delta == "4"


# ── catalogue contract ─────────────────────────────────────────────────

def test_empty_completion_is_catalogued_and_retryable():
    info = error_catalog.describe("empty_completion")
    assert info.retryable is True
    assert "empty" in info.message.lower()


# ── the opposite twin (FIX50 0.3): short answers stay success ──────────
#
# Both guards (public_chat streaming, orchestrator non-streaming) decide
# through error_catalog.is_empty_completion. These pin the criterion as
# EMPTY, never SHORT — the A.3 trap that until now was only verified
# live and would not have survived a refactor to a length threshold.

@pytest.mark.parametrize("answer", ["ใช่", "4", "No.", " ครับ "])
def test_one_word_answer_is_not_empty(answer):
    assert error_catalog.is_empty_completion(answer, has_tool_calls=False) is False


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", None])
def test_blank_answer_is_empty(blank):
    assert error_catalog.is_empty_completion(blank, has_tool_calls=False) is True


def test_tool_call_only_turn_is_not_empty():
    assert error_catalog.is_empty_completion("", has_tool_calls=True) is False


def test_both_guards_route_through_the_shared_predicate():
    # The predicate is only a real pin if both call sites use it.
    import inspect

    from app.api.routes import public_chat
    from app.services import orchestrator

    assert "is_empty_completion" in inspect.getsource(public_chat)
    assert "is_empty_completion" in inspect.getsource(orchestrator)
