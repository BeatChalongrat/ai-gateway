"""P1 Item 4 — Anthropic prompt caching: system block, OpenRouter
breakpoint, usage parsing, and provider-aware cost.
"""

from __future__ import annotations

import pytest

from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.openrouter_adapter import OpenRouterAdapter
from app.services.providers.parsers import parse_anthropic_response
from app.services.usage_service import _estimate_cost


def _payload(model="claude-sonnet-4"):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
    )


# --------------------------------------------------------------------------
# 4A — Anthropic adapter sends system as a cache_control content block.
# --------------------------------------------------------------------------


class TestAnthropicSystemBlock:
    def test_system_is_content_block_with_cache_control(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(
            settings, "anthropic_prompt_caching_enabled", True, raising=False
        )
        adapter = AnthropicAdapter()
        msgs = [
            Message(role="system", content="You are SRAN Chat."),
            Message(role="user", content="hi"),
        ]
        body = adapter._build_body(_payload(), msgs)
        assert isinstance(body["system"], list)
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["system"][0]["text"] == "You are SRAN Chat."

    def test_caching_disabled_keeps_plain_string(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(
            settings, "anthropic_prompt_caching_enabled", False, raising=False
        )
        adapter = AnthropicAdapter()
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
        ]
        body = adapter._build_body(_payload(), msgs)
        assert body["system"] == "sys"


# --------------------------------------------------------------------------
# 4B — OpenRouter adds cache_control only for anthropic/* models.
# --------------------------------------------------------------------------


class TestOpenRouterCacheControl:
    def test_anthropic_model_gets_cache_control(self):
        adapter = OpenRouterAdapter()
        msgs = [
            Message(role="system", content="sys prompt"),
            Message(role="user", content="hi"),
        ]
        body = adapter._build_body(
            _payload(), msgs, resolved_model="anthropic/claude-sonnet-4"
        )
        sys_msg = body["messages"][0]
        assert isinstance(sys_msg["content"], list)
        assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_non_anthropic_model_keeps_plain_string(self):
        adapter = OpenRouterAdapter()
        msgs = [
            Message(role="system", content="sys prompt"),
            Message(role="user", content="hi"),
        ]
        body = adapter._build_body(
            _payload(), msgs, resolved_model="openai/gpt-4o"
        )
        assert body["messages"][0]["content"] == "sys prompt"


# --------------------------------------------------------------------------
# 4C — usage parsing reads cache_read + cache_creation.
# --------------------------------------------------------------------------


class TestAnthropicUsageParsing:
    def test_cache_tokens_parsed(self):
        data = {
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-sonnet-4",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 40,
            },
        }
        resp = parse_anthropic_response(data)
        assert resp.usage.cached_prompt_tokens == 100
        assert resp.usage.cache_creation_prompt_tokens == 40
        # full prompt = fresh + read + creation
        assert resp.usage.prompt_tokens == 150
        assert resp.usage.completion_tokens == 5

    def test_no_cache_fields_unchanged(self):
        data = {
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-sonnet-4",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        resp = parse_anthropic_response(data)
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.cached_prompt_tokens == 0
        assert resp.usage.cache_creation_prompt_tokens == 0


# --------------------------------------------------------------------------
# 4D — provider-aware cost.
# --------------------------------------------------------------------------


class TestCachedCost:
    # Use a legacy-flat-rate model id (no registry candidate) so the math
    # is deterministic: cost = tokens * rate, rate cancels in the ratios.
    def test_anthropic_cached_read_is_10pct(self):
        full = _estimate_cost("claude-sonnet-4", 1000, 0, 0, 0)
        cached = _estimate_cost("claude-sonnet-4", 1000, 0, 1000, 0)
        # 1000 cached read @ 10% vs 1000 uncached @ 100% → ratio 0.10
        assert cached == pytest.approx(full * 0.10, rel=1e-6)

    def test_openai_cached_read_is_50pct(self):
        full = _estimate_cost("gpt-4o-mini", 1000, 0, 0, 0)
        cached = _estimate_cost("gpt-4o-mini", 1000, 0, 1000, 0)
        assert cached == pytest.approx(full * 0.50, rel=1e-6)

    def test_anthropic_cache_creation_is_125pct(self):
        base = _estimate_cost("claude-sonnet-4", 1000, 0, 0, 0)
        creation = _estimate_cost("claude-sonnet-4", 1000, 0, 0, 1000)
        assert creation == pytest.approx(base * 1.25, rel=1e-6)

    def test_zero_cache_is_unchanged(self):
        # cached=0 + creation=0 → identical to the no-cache formula.
        a = _estimate_cost("gpt-4o", 1234, 567, 0, 0)
        b = _estimate_cost("gpt-4o", 1234, 567)
        assert a == b
