"""OpenAI prompt-cache (Level 1) — parse cached_tokens + discounted cost.

Covers reading ``usage.prompt_tokens_details.cached_tokens`` (which OpenAI
returns for free) into ``ProviderUsage.cached_prompt_tokens`` and billing
the cached subset at 50% of the input rate. Every other provider / cache
miss leaves the field at 0, so the cost stays byte-identical.
"""

from __future__ import annotations

import pytest

from app.services.providers.openai_adapter import _parse_sse_line
from app.services.providers.parsers import parse_openai_response
from app.services.usage_service import (
    _CACHED_INPUT_RATE_MULTIPLIER,
    _estimate_cost,
)


def _openai_body(usage: dict | None) -> dict:
    body = {
        "model": "gpt-4o",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"},
             "finish_reason": "stop"}
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


# ─────────────────── parser: non-streaming ───────────────────


def test_parse_reads_cached_tokens():
    resp = parse_openai_response(
        _openai_body(
            {
                "prompt_tokens": 2000,
                "completion_tokens": 100,
                "total_tokens": 2100,
                "prompt_tokens_details": {"cached_tokens": 1500},
            }
        )
    )
    assert resp.usage is not None
    assert resp.usage.cached_prompt_tokens == 1500
    # Existing fields untouched.
    assert resp.usage.prompt_tokens == 2000
    assert resp.usage.completion_tokens == 100


def test_parse_no_details_defaults_zero():
    resp = parse_openai_response(
        _openai_body(
            {"prompt_tokens": 2000, "completion_tokens": 100, "total_tokens": 2100}
        )
    )
    assert resp.usage is not None
    assert resp.usage.cached_prompt_tokens == 0


def test_parse_empty_details_defaults_zero():
    # prompt_tokens_details present but without cached_tokens → 0.
    resp = parse_openai_response(
        _openai_body(
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {},
            }
        )
    )
    assert resp.usage is not None
    assert resp.usage.cached_prompt_tokens == 0


# ─────────────────── parser: streaming usage chunk ───────────────────


def test_stream_usage_chunk_reads_cached_tokens():
    line = (
        'data: {"choices":[],"model":"gpt-4o","usage":'
        '{"prompt_tokens":2000,"completion_tokens":100,"total_tokens":2100,'
        '"prompt_tokens_details":{"cached_tokens":1500}}}'
    )
    chunk = _parse_sse_line(line)
    assert chunk is not None
    assert chunk.usage is not None
    assert chunk.usage.cached_prompt_tokens == 1500


def test_stream_usage_chunk_no_details_defaults_zero():
    line = (
        'data: {"choices":[],"model":"gpt-4o","usage":'
        '{"prompt_tokens":50,"completion_tokens":10,"total_tokens":60}}'
    )
    chunk = _parse_sse_line(line)
    assert chunk is not None
    assert chunk.usage is not None
    assert chunk.usage.cached_prompt_tokens == 0


# ─────────────────── cost calculation ───────────────────

# claude-3-5-sonnet-20241022 rates (mirrors test_usage_cost_split.py):
# input = 0.000003, output = 0.000015 $/token.
_MODEL = "claude-3-5-sonnet-20241022"
_R_IN = 0.000003
_R_OUT = 0.000015


def test_cost_with_cache_applies_half_rate():
    # prompt=2000 (1500 cached), completion=0 → isolate the input side.
    # uncached 500 × full + cached 1500 × half.
    cost = _estimate_cost(_MODEL, 2000, 0, 1500)
    expected = 500 * _R_IN + 1500 * _R_IN * _CACHED_INPUT_RATE_MULTIPLIER
    assert cost == pytest.approx(expected)


def test_cost_with_cache_includes_completion():
    cost = _estimate_cost(_MODEL, 2000, 100, 1500)
    expected = (
        500 * _R_IN
        + 1500 * _R_IN * _CACHED_INPUT_RATE_MULTIPLIER
        + 100 * _R_OUT
    )
    assert cost == pytest.approx(expected)


def test_cost_zero_cache_identical_to_legacy_formula():
    # Default param AND explicit 0 must both equal the pre-cache result.
    legacy = _estimate_cost(_MODEL, 2000, 100)
    explicit_zero = _estimate_cost(_MODEL, 2000, 100, 0)
    expected = 2000 * _R_IN + 100 * _R_OUT
    assert legacy == pytest.approx(expected)
    assert explicit_zero == pytest.approx(expected)


def test_cost_cache_is_cheaper_by_exact_discount():
    # Registry-independent: the saving must be exactly cached × rate × 0.5,
    # derived from the function's own input-rate delta.
    full = _estimate_cost(_MODEL, 2000, 100, 0)
    cached = _estimate_cost(_MODEL, 2000, 100, 1500)
    rate_from_delta = (
        _estimate_cost(_MODEL, 2000, 0) - _estimate_cost(_MODEL, 1000, 0)
    ) / 1000
    expected_saving = 1500 * rate_from_delta * (1 - _CACHED_INPUT_RATE_MULTIPLIER)
    assert (full - cached) == pytest.approx(expected_saving)


def test_cost_cached_clamped_to_prompt_tokens():
    # cached > prompt must not produce a negative uncached count; clamp to
    # prompt so the whole prompt bills at the cached rate, never below 0.
    cost = _estimate_cost(_MODEL, 1000, 0, 5000)
    expected = 1000 * _R_IN * _CACHED_INPUT_RATE_MULTIPLIER
    assert cost == pytest.approx(expected)
