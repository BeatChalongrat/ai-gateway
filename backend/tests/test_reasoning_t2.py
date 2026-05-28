"""T2 (Thinking Mode) — adapter + parser coverage.

Scope:
* Anthropic adapter forwards ``thinking`` config + parses thinking
  blocks (non-stream + streaming SSE state machine).
* OpenAI adapter forwards ``reasoning_effort`` + swaps
  ``max_tokens`` → ``max_completion_tokens`` + strips temperature/top_p
  for reasoning-capable models, and parses ``reasoning_content``
  (non-stream + streaming).
* Gating: both adapters silently ignore the public ``reasoning``
  config when the resolved model is not flagged
  ``supports_reasoning=True``.
* Backward compatibility: a request without ``payload.reasoning`` (or
  with ``mode='off'``) produces a body byte-identical to the pre-T2
  shape.

These tests cover the unit-level transforms the orchestrator and SSE
emitter depend on. End-to-end SSE behaviour (analysis_status frames,
DLP routing) is exercised separately by the streaming-route tests.
"""

from __future__ import annotations

import json

import pytest

from app.domain.providers import ProviderStreamChunk
from app.domain.routing import ModelCandidate
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest, ReasoningConfig
from app.services.providers import (
    anthropic_adapter as anthropic_mod,
    openai_adapter as openai_mod,
)
from app.services.providers.anthropic_adapter import AnthropicAdapter
from app.services.providers.openai_adapter import OpenAIAdapter, _parse_sse_line
from app.services.providers.parsers import (
    parse_anthropic_response,
    parse_openai_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(model: str, *, reasoning: ReasoningConfig | None = None) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        reasoning=reasoning,
    )


def _capable_candidate(provider: str, model: str) -> ModelCandidate:
    return ModelCandidate(
        provider=provider,
        model=model,
        supports_reasoning=True,
        reasoning_strategy=(
            "anthropic_adaptive_thinking" if provider == "anthropic"
            else "openai_responses_reasoning"
        ),
        default_reasoning_mode="medium",
    )


def _non_capable_candidate(provider: str, model: str) -> ModelCandidate:
    return ModelCandidate(
        provider=provider, model=model, supports_reasoning=False
    )


# ===========================================================================
# Anthropic — _build_body
# ===========================================================================


class TestAnthropicBuildBody:
    def test_thinking_block_added_when_capable_and_requested(self, monkeypatch):
        monkeypatch.setattr(
            anthropic_mod,
            "get_candidate",
            lambda m: _capable_candidate("anthropic", m),
            raising=False,
        )
        # The adapter does ``from app.services.providers.model_registry
        # import get_candidate`` inside _build_body, so patch the
        # registry symbol directly.
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("anthropic", m),
        )
        adapter = AnthropicAdapter()
        payload = _payload(
            "claude-sonnet-4-20250514",
            reasoning=ReasoningConfig(mode="medium", show_summary=True),
        )
        body = adapter._build_body(
            payload, payload.messages, resolved_model="claude-sonnet-4-20250514"
        )
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        # Anthropic requires temperature=1 with thinking on.
        assert body["temperature"] == 1
        # max_tokens must exceed the budget.
        assert body["max_tokens"] > 4096

    def test_high_mode_uses_larger_budget(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("anthropic", m),
        )
        adapter = AnthropicAdapter()
        payload = _payload(
            "claude-sonnet-4-20250514",
            reasoning=ReasoningConfig(mode="high"),
        )
        body = adapter._build_body(payload, payload.messages)
        assert body["thinking"]["budget_tokens"] == 8192
        assert body["max_tokens"] > 8192

    def test_low_mode_uses_smaller_budget(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("anthropic", m),
        )
        adapter = AnthropicAdapter()
        payload = _payload(
            "claude-sonnet-4-20250514",
            reasoning=ReasoningConfig(mode="low"),
        )
        body = adapter._build_body(payload, payload.messages)
        assert body["thinking"]["budget_tokens"] == 2048

    def test_thinking_skipped_when_model_not_capable(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _non_capable_candidate("anthropic", m),
        )
        adapter = AnthropicAdapter()
        payload = _payload(
            "claude-3-5-sonnet-20241022",
            reasoning=ReasoningConfig(mode="medium"),
        )
        body = adapter._build_body(payload, payload.messages)
        assert "thinking" not in body

    def test_thinking_skipped_when_mode_off(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("anthropic", m),
        )
        adapter = AnthropicAdapter()
        payload = _payload(
            "claude-sonnet-4-20250514",
            reasoning=ReasoningConfig(mode="off", show_summary=True),
        )
        body = adapter._build_body(payload, payload.messages)
        assert "thinking" not in body

    def test_body_unchanged_when_no_reasoning_field(self):
        # No registry patch — when reasoning is None we must never
        # even hit the registry. If we do, the live registry returns
        # whatever it has — but the result is the same: no thinking.
        adapter = AnthropicAdapter()
        payload = _payload("claude-3-5-sonnet-20241022")  # reasoning=None
        body = adapter._build_body(payload, payload.messages)
        assert "thinking" not in body


# ===========================================================================
# Anthropic — non-stream parser
# ===========================================================================


class TestAnthropicParser:
    def test_reasoning_content_extracted_from_thinking_block(self):
        data = {
            "content": [
                {"type": "thinking", "thinking": "Let me reason: "},
                {"type": "thinking", "thinking": "step by step."},
                {"type": "text", "text": "Final answer."},
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        resp = parse_anthropic_response(data)
        assert resp.reasoning_content == "Let me reason: step by step."
        assert resp.content == "Final answer."

    def test_reasoning_content_none_when_no_thinking_blocks(self):
        data = {
            "content": [{"type": "text", "text": "Hi"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        resp = parse_anthropic_response(data)
        assert resp.reasoning_content is None

    def test_redacted_thinking_is_dropped(self):
        data = {
            "content": [
                {"type": "redacted_thinking", "data": "opaque-blob"},
                {"type": "text", "text": "Answer."},
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        resp = parse_anthropic_response(data)
        assert resp.reasoning_content is None
        assert resp.content == "Answer."


# ===========================================================================
# Anthropic — SSE state machine
# ===========================================================================


class TestAnthropicSseStream:
    @pytest.mark.asyncio
    async def test_thinking_delta_emits_reasoning_delta_chunk(self):
        adapter = AnthropicAdapter()
        sse_lines = [
            'event: message_start',
            'data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514","usage":{"input_tokens":5,"output_tokens":0}}}',
            '',
            'event: content_block_start',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
            '',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me think..."}}',
            '',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig-xyz"}}',
            '',
            'event: content_block_stop',
            'data: {"type":"content_block_stop","index":0}',
            '',
            'event: content_block_start',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
            '',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Answer."}}',
            '',
            'event: message_delta',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}',
            '',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]

        async def aiter():
            for line in sse_lines:
                yield line

        chunks: list[ProviderStreamChunk] = []
        async for c in adapter._process_sse_stream(
            aiter(), initial_model="claude-sonnet-4-20250514"
        ):
            chunks.append(c)

        reasoning_chunks = [c for c in chunks if c.reasoning_delta]
        content_chunks = [c for c in chunks if c.delta]
        assert [c.reasoning_delta for c in reasoning_chunks] == ["Let me think..."]
        assert [c.delta for c in content_chunks] == ["Answer."]
        # The signature_delta must NOT surface as reasoning text.
        assert all(
            "sig-xyz" not in (c.reasoning_delta or "") for c in chunks
        )
        # Final chunk on message_stop carries finish_reason.
        assert chunks[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_thinking_delta_without_matching_block_start_is_dropped(self):
        adapter = AnthropicAdapter()
        # No content_block_start for thinking → the delta should be
        # dropped defensively (matches the tool_use stray-delta rule).
        sse_lines = [
            'event: message_start',
            'data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514","usage":{"input_tokens":1,"output_tokens":0}}}',
            '',
            'event: content_block_delta',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"orphan"}}',
            '',
            'event: message_stop',
            'data: {"type":"message_stop"}',
        ]

        async def aiter():
            for line in sse_lines:
                yield line

        chunks = []
        async for c in adapter._process_sse_stream(
            aiter(), initial_model="claude-sonnet-4-20250514"
        ):
            chunks.append(c)
        assert not any(c.reasoning_delta for c in chunks)


# ===========================================================================
# OpenAI — _build_body
# ===========================================================================


class TestOpenAIBuildBody:
    def test_reasoning_effort_added_for_capable_model(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("openai", m),
        )
        adapter = OpenAIAdapter()
        payload = _payload(
            "o3-mini",
            reasoning=ReasoningConfig(mode="medium", show_summary=True),
        )
        # Set max_tokens via metadata-respecting top-level field.
        payload.max_tokens = 1024
        payload.temperature = 0.5
        payload.top_p = 0.9
        body = adapter._build_body(payload, payload.messages, resolved_model="o3-mini")
        assert body["reasoning_effort"] == "medium"
        # max_tokens swap.
        assert "max_tokens" not in body
        assert body["max_completion_tokens"] == 1024
        # Sampling knobs stripped.
        assert "temperature" not in body
        assert "top_p" not in body

    def test_mode_maps_to_effort(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("openai", m),
        )
        adapter = OpenAIAdapter()
        for mode, expected in [("auto", "medium"), ("low", "low"), ("high", "high")]:
            payload = _payload("o3-mini", reasoning=ReasoningConfig(mode=mode))
            body = adapter._build_body(payload, payload.messages, resolved_model="o3-mini")
            assert body["reasoning_effort"] == expected, mode

    def test_reasoning_skipped_when_model_not_capable(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _non_capable_candidate("openai", m),
        )
        adapter = OpenAIAdapter()
        payload = _payload("gpt-4o-mini", reasoning=ReasoningConfig(mode="medium"))
        payload.temperature = 0.7
        body = adapter._build_body(payload, payload.messages, resolved_model="gpt-4o-mini")
        assert "reasoning_effort" not in body
        # temperature must still be there for non-reasoning models.
        assert body["temperature"] == 0.7

    def test_reasoning_skipped_when_mode_off(self, monkeypatch):
        from app.services.providers import model_registry
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable_candidate("openai", m),
        )
        adapter = OpenAIAdapter()
        payload = _payload(
            "o3-mini",
            reasoning=ReasoningConfig(mode="off", show_summary=True),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="o3-mini")
        assert "reasoning_effort" not in body

    def test_body_unchanged_when_no_reasoning_field(self):
        adapter = OpenAIAdapter()
        payload = _payload("gpt-4o-mini")
        payload.temperature = 0.7
        payload.max_tokens = 256
        body = adapter._build_body(payload, payload.messages, resolved_model="gpt-4o-mini")
        assert "reasoning_effort" not in body
        assert "max_completion_tokens" not in body
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 256


# ===========================================================================
# OpenAI — stream + non-stream parsers
# ===========================================================================


class TestOpenAIParsers:
    def test_stream_parses_reasoning_content_delta(self):
        line = "data: " + json.dumps(
            {
                "choices": [
                    {"delta": {"reasoning_content": "thinking..."}, "finish_reason": None}
                ],
                "model": "deepseek-reasoner",
            }
        )
        chunk = _parse_sse_line(line)
        assert chunk is not None
        assert chunk.reasoning_delta == "thinking..."
        # Plain content stays empty.
        assert chunk.delta == ""

    def test_stream_text_delta_keeps_reasoning_none(self):
        line = "data: " + json.dumps(
            {
                "choices": [
                    {"delta": {"content": "hello"}, "finish_reason": None}
                ],
                "model": "gpt-4o",
            }
        )
        chunk = _parse_sse_line(line)
        assert chunk.reasoning_delta is None
        assert chunk.delta == "hello"

    def test_stream_usage_chunk_extracts_reasoning_tokens(self):
        line = "data: " + json.dumps(
            {
                "choices": [],
                "model": "o3-mini",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 50,
                    "total_tokens": 60,
                    "completion_tokens_details": {"reasoning_tokens": 35},
                },
            }
        )
        chunk = _parse_sse_line(line)
        assert chunk is not None
        assert chunk.usage is not None
        assert chunk.usage.reasoning_tokens == 35

    def test_non_stream_extracts_reasoning_content(self):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Final answer.",
                        "reasoning_content": "Internal reasoning.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "model": "deepseek-reasoner",
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "total_tokens": 15,
                "completion_tokens_details": {"reasoning_tokens": 4},
            },
        }
        resp = parse_openai_response(data)
        assert resp.reasoning_content == "Internal reasoning."
        assert resp.content == "Final answer."
        assert resp.usage.reasoning_tokens == 4

    def test_non_stream_reasoning_none_when_absent(self):
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Hi"},
                    "finish_reason": "stop",
                }
            ],
            "model": "gpt-4o",
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
        resp = parse_openai_response(data)
        assert resp.reasoning_content is None
        assert resp.usage.reasoning_tokens == 0
