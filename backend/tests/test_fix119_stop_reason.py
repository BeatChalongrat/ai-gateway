"""FIX119 Part B — why the model stopped is one closed set, one table.

The table lives in app.services.providers.stop_reason and is pinned to
docs/contracts/terminal_cause.json (the Go and TS trees pin themselves to
the same file). Every adapter family carries the closed-set value beside
the OpenAI-shaped finish_reason; a value nobody named is `unknown`, and a
provider that says nothing is `unknown` too — never a guessed end_turn.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.services.providers import stop_reason as sr
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.google_adapter import _parse_response as google_parse_response
from app.services.providers.google_adapter import _parse_stream_chunk as google_parse_stream
from app.services.providers.openai_adapter import _parse_responses_sse_line, _parse_sse_line
from app.services.providers.parsers import parse_anthropic_response, parse_openai_response
from app.services.providers.request_ids import provider_request_id_from

def _contract_path() -> Path:
    # The repo layout on a checkout, the /docs mount inside the backend container.
    for cand in (Path(__file__).resolve().parents[2] / "docs" / "contracts" / "terminal_cause.json", Path("/docs/contracts/terminal_cause.json")):
        if cand.exists():
            return cand
    pytest.skip("docs/contracts/terminal_cause.json is not reachable from this test run")


CONTRACT = _contract_path()


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_closed_set_and_table_are_pinned_to_the_contract(contract):
    assert list(sr.STOP_REASONS) == contract["stop_reasons"]
    assert sr.STOP_REASON_MAP == contract["stop_reason_map"]
    for family, table in sr.STOP_REASON_MAP.items():
        for raw, mapped in table.items():
            assert mapped in sr.STOP_REASONS, f"{family}:{raw} → {mapped} is outside the set"


@pytest.mark.parametrize(
    "family,raw,want",
    [
        ("openai", "stop", "end_turn"),
        ("openai", "length", "max_tokens"),
        ("openai", "tool_calls", "tool_use"),
        ("openai", "function_call", "tool_use"),
        ("openai", "content_filter", "content_filter"),
        ("anthropic", "end_turn", "end_turn"),
        ("anthropic", "max_tokens", "max_tokens"),
        ("anthropic", "stop_sequence", "stop_sequence"),
        ("anthropic", "tool_use", "tool_use"),
        ("anthropic", "refusal", "refusal"),
        ("anthropic", "pause_turn", "end_turn"),
        ("anthropic", "model_context_window_exceeded", "context_window_exceeded"),
        ("google", "STOP", "end_turn"),
        ("google", "MAX_TOKENS", "max_tokens"),
        ("google", "SAFETY", "content_filter"),
        ("google", "RECITATION", "content_filter"),
        ("google", "OTHER", "unknown"),
        ("google", "FINISH_REASON_UNSPECIFIED", "unknown"),
    ],
)
def test_every_native_value_of_the_three_live_families(family, raw, want):
    assert sr.normalize_stop_reason(family, raw) == want


def test_missing_is_unknown_never_end_turn():
    assert sr.normalize_stop_reason("openai", None) == "unknown"
    assert sr.normalize_stop_reason("anthropic", "") == "unknown"
    assert sr.normalize_stop_reason("google", None) == "unknown"


def test_unmapped_value_is_unknown_and_logged_with_provider_and_value(caplog):
    with caplog.at_level(logging.WARNING):
        assert sr.normalize_stop_reason("openai", "brand_new_reason", provider="sranai") == "unknown"
        assert sr.normalize_stop_reason("martian", "stop", provider="martian") == "unknown"
    text = caplog.text
    assert "stop_reason_unmapped" in text
    assert "brand_new_reason" in text and "sranai" in text
    assert "unknown_family" in text


# ── adapters carry the closed-set value beside finish_reason ────────────────

def test_openai_stream_chunk_carries_stop_reason():
    line = 'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"length"}]}'
    chunk = _parse_sse_line(line, provider_name="sranai")
    assert chunk is not None
    assert chunk.finish_reason == "length"
    assert chunk.stop_reason == "max_tokens"
    mid = _parse_sse_line('data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}')
    assert mid is not None and mid.stop_reason is None


def test_openai_nonstream_carries_stop_reason_and_missing_is_unknown():
    r = parse_openai_response({"model": "m", "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})
    assert (r.finish_reason, r.stop_reason) == ("stop", "end_turn")
    r2 = parse_openai_response({"model": "m", "choices": [{"message": {"content": "x"}}]})
    assert r2.finish_reason == "stop"  # the OpenAI-shaped default, unchanged
    assert r2.stop_reason == "unknown"  # the honest one


def test_responses_api_synthesised_reasons_carry_stop_reason():
    state: dict = {}
    cut = _parse_responses_sse_line(
        'data: {"type":"response.incomplete","response":{"model":"m","usage":null}}', state
    )
    assert cut is not None and (cut.finish_reason, cut.stop_reason) == ("length", "max_tokens")
    done = _parse_responses_sse_line(
        'data: {"type":"response.completed","response":{"model":"m","usage":null}}', state
    )
    assert done is not None and (done.finish_reason, done.stop_reason) == ("stop", "end_turn")


@pytest.mark.asyncio
async def test_anthropic_stream_reads_stop_reason_from_message_delta_and_keeps_refusal():
    async def lines():
        for ln in [
            'event: message_start',
            'data: {"type":"message_start","message":{"model":"claude-x","usage":{"input_tokens":3}}}',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"no"}}',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"refusal"},"usage":{"output_tokens":1}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]:
            yield ln

    adapter = AnthropicAdapter()
    chunks = [c async for c in adapter._process_sse_stream(lines(), initial_model="claude-x")]
    final = chunks[-1]
    assert final.finish_reason == "refusal"  # untranslated by the OpenAI map, as before
    assert final.stop_reason == "refusal"  # named in the closed set


@pytest.mark.asyncio
async def test_anthropic_stream_without_message_delta_is_unknown():
    async def lines():
        for ln in [
            'event: message_start',
            'data: {"type":"message_start","message":{"model":"claude-x","usage":{"input_tokens":3}}}',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]:
            yield ln

    chunks = [c async for c in AnthropicAdapter()._process_sse_stream(lines(), initial_model="claude-x")]
    assert chunks[-1].finish_reason == "stop"  # the OpenAI-shaped default, unchanged
    assert chunks[-1].stop_reason == "unknown"  # message_start is always null; nothing was said


def test_anthropic_nonstream_stop_sequence_survives():
    r = parse_anthropic_response({"model": "c", "content": [{"type": "text", "text": "x"}], "stop_reason": "stop_sequence", "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert (r.finish_reason, r.stop_reason) == ("stop", "stop_sequence")
    r2 = parse_anthropic_response({"model": "c", "content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert r2.stop_reason == "unknown"


def test_google_max_tokens_and_other():
    stream = google_parse_stream({"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "MAX_TOKENS"}]})
    assert stream is not None and (stream.finish_reason, stream.stop_reason) == ("length", "max_tokens")
    resp = google_parse_response({"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "OTHER"}]})
    assert (resp.finish_reason, resp.stop_reason) == ("stop", "unknown")
    none = google_parse_response({"candidates": [{"content": {"parts": [{"text": "x"}]}}]})
    assert none.stop_reason == "unknown"


def test_dataclasses_default_to_unknown_and_no_provider_id():
    assert ProviderResponse(content="", model="m").stop_reason == "unknown"
    assert ProviderStreamChunk().stop_reason is None
    assert ProviderStreamChunk().provider_request_id is None


# ── the provider's own request id ──────────────────────────────────────────

def test_provider_request_id_from_headers():
    assert provider_request_id_from({"x-request-id": "req_openai_1"}) == "req_openai_1"
    assert provider_request_id_from({"request-id": "req_anthropic_1"}) == "req_anthropic_1"
    assert provider_request_id_from({"content-type": "application/json"}) is None
    assert provider_request_id_from(None) is None
    assert len(provider_request_id_from({"x-request-id": "x" * 500}) or "") == 128
