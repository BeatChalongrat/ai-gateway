"""P2-05: Shared response parser tests.

Verifies:
- parse_openai_response: content, model, finish_reason, usage, raw
- parse_anthropic_response: content blocks, stop_reason mapping, usage mapping
- Cross-provider consistency: both produce ProviderResponse with same field types
"""

import pytest

from app.domain.providers import ProviderResponse, ProviderUsage
from app.services.providers.parsers import (
    parse_anthropic_response,
    parse_openai_response,
)


# ---------------------------------------------------------------------------
# OpenAI parser
# ---------------------------------------------------------------------------


class TestParseOpenAI:
    def test_basic_response(self):
        data = {
            "id": "chatcmpl-123",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        r = parse_openai_response(data)
        assert r.content == "Hello"
        assert r.model == "gpt-4o"
        assert r.finish_reason == "stop"
        assert r.usage.total_tokens == 15
        assert r.raw is data

    def test_missing_usage(self):
        data = {
            "model": "m",
            "choices": [
                {"message": {"content": "hi"}, "finish_reason": "stop"}
            ],
        }
        r = parse_openai_response(data)
        assert r.usage is None

    def test_content_filter_finish_reason(self):
        data = {
            "model": "m",
            "choices": [
                {"message": {"content": ""}, "finish_reason": "content_filter"}
            ],
        }
        r = parse_openai_response(data)
        assert r.finish_reason == "content_filter"

    def test_empty_content(self):
        data = {
            "model": "m",
            "choices": [
                {"message": {"content": ""}, "finish_reason": "stop"}
            ],
        }
        r = parse_openai_response(data)
        assert r.content == ""

    def test_missing_content_key(self):
        data = {
            "model": "m",
            "choices": [{"message": {}, "finish_reason": "stop"}],
        }
        r = parse_openai_response(data)
        assert r.content == ""


# ---------------------------------------------------------------------------
# Anthropic parser
# ---------------------------------------------------------------------------


class TestParseAnthropic:
    def test_basic_response(self):
        data = {
            "id": "msg-123",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        r = parse_anthropic_response(data)
        assert r.content == "Hello"
        assert r.model == "claude-3-5-sonnet-20241022"
        assert r.finish_reason == "stop"
        assert r.usage.prompt_tokens == 10
        assert r.usage.completion_tokens == 5
        assert r.usage.total_tokens == 15

    def test_max_tokens_maps_to_length(self):
        data = {
            "model": "m",
            "content": [{"type": "text", "text": "..."}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        r = parse_anthropic_response(data)
        assert r.finish_reason == "length"

    def test_multiple_text_blocks(self):
        data = {
            "model": "m",
            "content": [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
            ],
            "stop_reason": "end_turn",
        }
        r = parse_anthropic_response(data)
        assert r.content == "AB"

    def test_non_text_blocks_ignored(self):
        data = {
            "model": "m",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "id": "tool_1"},
            ],
            "stop_reason": "end_turn",
        }
        r = parse_anthropic_response(data)
        assert r.content == "Hello"

    def test_empty_content_blocks(self):
        data = {
            "model": "m",
            "content": [],
            "stop_reason": "end_turn",
        }
        r = parse_anthropic_response(data)
        assert r.content == ""

    def test_raw_preserved(self):
        data = {
            "id": "msg-xyz",
            "model": "m",
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
        }
        r = parse_anthropic_response(data)
        assert r.raw["id"] == "msg-xyz"


# ---------------------------------------------------------------------------
# Cross-provider consistency
# ---------------------------------------------------------------------------


class TestCrossProviderConsistency:
    def test_both_return_provider_response(self):
        openai_data = {
            "model": "gpt-4o",
            "choices": [
                {"message": {"content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        anthropic_data = {
            "model": "claude-3-5-sonnet",
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        r1 = parse_openai_response(openai_data)
        r2 = parse_anthropic_response(anthropic_data)
        assert isinstance(r1, ProviderResponse)
        assert isinstance(r2, ProviderResponse)

    def test_both_normalize_finish_stop(self):
        r1 = parse_openai_response({
            "model": "m",
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        })
        r2 = parse_anthropic_response({
            "model": "m",
            "content": [{"type": "text", "text": ""}],
            "stop_reason": "end_turn",
        })
        assert r1.finish_reason == "stop"
        assert r2.finish_reason == "stop"

    def test_both_normalize_finish_length(self):
        r1 = parse_openai_response({
            "model": "m",
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        })
        r2 = parse_anthropic_response({
            "model": "m",
            "content": [{"type": "text", "text": ""}],
            "stop_reason": "max_tokens",
        })
        assert r1.finish_reason == "length"
        assert r2.finish_reason == "length"

    def test_usage_field_names_consistent(self):
        r1 = parse_openai_response({
            "model": "m",
            "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        })
        r2 = parse_anthropic_response({
            "model": "m",
            "content": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        })
        assert r1.usage.prompt_tokens == r2.usage.prompt_tokens
        assert r1.usage.completion_tokens == r2.usage.completion_tokens
        assert r1.usage.total_tokens == r2.usage.total_tokens
