"""T3 (Thinking Mode) — Gemini + OpenRouter + OpenAI-compatible + cost.

Scope:
* Gemini adapter forwards ``thinkingConfig`` + parses ``thought=true``
  parts as ``reasoning_content`` / ``reasoning_delta``; surfaces
  ``thoughtsTokenCount`` as ``reasoning_tokens``.
* OpenRouter adapter forwards both ``include_reasoning`` and
  ``reasoning.effort`` shapes (covers legacy + newer OR API styles).
* OpenAI-compatible base forwards ``reasoning_effort`` for capable
  models (DeepSeek-R1 / Groq R1-distill) without stripping the
  legacy sampling knobs.
* Per-model capability gating (T2 contract) holds across all three
  new adapter paths.
* Cost: ``_estimate_reasoning_cost`` returns USD attributable to
  reasoning at the model's output rate; ``request_logs`` row
  carries the two new columns.

These tests use monkeypatch on ``model_registry.get_candidate`` to
isolate adapter behaviour from the live DB-backed registry. Tests
that need to exercise the actual ORM use the in-memory SQLite engine
the existing suite already configures.
"""

from __future__ import annotations

import json

import pytest

from app.domain.observability import RequestRecord, RequestStatus
from app.domain.routing import ModelCandidate
from app.models.request_log import RequestLogORM
from app.schemas.common import Message, UsageInfo
from app.schemas.public import ChatCompletionsRequest, ReasoningConfig
from app.services.providers import model_registry
from app.services.providers.google_adapter import (
    GoogleAIAdapter,
    _GEMINI_THINKING_LEVEL_MAP,
    _parse_response as parse_gemini_response,
    _parse_stream_chunk as parse_gemini_stream_chunk,
)
from app.services.providers.openrouter_adapter import OpenRouterAdapter
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.request_log_service import RequestLogService
from app.services.usage_service import _estimate_reasoning_cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(model: str, *, reasoning: ReasoningConfig | None = None) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        reasoning=reasoning,
    )


def _capable(provider: str, model: str, *, strategy: str) -> ModelCandidate:
    return ModelCandidate(
        provider=provider,
        model=model,
        supports_reasoning=True,
        reasoning_strategy=strategy,
        default_reasoning_mode="medium",
    )


def _non_capable(provider: str, model: str) -> ModelCandidate:
    return ModelCandidate(
        provider=provider, model=model, supports_reasoning=False
    )


# ===========================================================================
# Gemini — _build_body
# ===========================================================================


class TestGeminiBuildBody:
    def test_thinking_config_added_when_capable_and_requested(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("google", m, strategy="gemini_thinking_level"),
        )
        adapter = GoogleAIAdapter()
        payload = _payload(
            "gemini-2.5-pro",
            reasoning=ReasoningConfig(mode="medium", show_summary=True),
        )
        body = adapter._build_body(
            payload, payload.messages, resolved_model="gemini-2.5-pro"
        )
        cfg = body["generationConfig"]["thinkingConfig"]
        assert cfg == {"thinkingLevel": "MEDIUM", "includeThoughts": True}

    def test_mode_maps_to_thinking_level(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("google", m, strategy="gemini_thinking_level"),
        )
        adapter = GoogleAIAdapter()
        for mode, expected in [
            ("auto", "MEDIUM"),
            ("low", "LOW"),
            ("medium", "MEDIUM"),
            ("high", "HIGH"),
        ]:
            payload = _payload(
                "gemini-2.5-pro", reasoning=ReasoningConfig(mode=mode),
            )
            body = adapter._build_body(payload, payload.messages, resolved_model="gemini-2.5-pro")
            level = body["generationConfig"]["thinkingConfig"]["thinkingLevel"]
            assert level == expected, mode

    def test_include_thoughts_follows_show_summary(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("google", m, strategy="gemini_thinking_level"),
        )
        adapter = GoogleAIAdapter()
        payload_show = _payload(
            "gemini-2.5-pro",
            reasoning=ReasoningConfig(mode="medium", show_summary=False),
        )
        body = adapter._build_body(
            payload_show, payload_show.messages,
            resolved_model="gemini-2.5-pro",
        )
        assert body["generationConfig"]["thinkingConfig"]["includeThoughts"] is False

    def test_thinking_skipped_for_non_capable_gemini_15(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _non_capable("google", m),
        )
        adapter = GoogleAIAdapter()
        payload = _payload(
            "gemini-1.5-pro",
            reasoning=ReasoningConfig(mode="medium", show_summary=True),
        )
        body = adapter._build_body(
            payload, payload.messages, resolved_model="gemini-1.5-pro"
        )
        gen = body.get("generationConfig", {})
        assert "thinkingConfig" not in gen

    def test_body_unchanged_when_no_reasoning_field(self):
        adapter = GoogleAIAdapter()
        payload = _payload("gemini-1.5-pro")
        body = adapter._build_body(
            payload, payload.messages, resolved_model="gemini-1.5-pro"
        )
        assert "thinkingConfig" not in body.get("generationConfig", {})


# ===========================================================================
# Gemini — response + stream parsers
# ===========================================================================


class TestGeminiParsers:
    def test_response_thought_parts_become_reasoning_content(self):
        data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "First, let me reason. ", "thought": True},
                            {"text": "Continuing the reasoning.", "thought": True},
                            {"text": "Final answer."},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 20,
                "totalTokenCount": 50,
                "thoughtsTokenCount": 15,
            },
            "modelVersion": "gemini-2.5-pro",
        }
        resp = parse_gemini_response(data)
        assert resp.reasoning_content == "First, let me reason. Continuing the reasoning."
        assert resp.content == "Final answer."
        assert resp.usage.reasoning_tokens == 15

    def test_response_no_thought_parts_keeps_reasoning_none(self):
        data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Plain answer."}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
            "modelVersion": "gemini-1.5-flash",
        }
        resp = parse_gemini_response(data)
        assert resp.reasoning_content is None
        assert resp.usage.reasoning_tokens == 0

    def test_stream_chunk_with_thought_part(self):
        chunk = parse_gemini_stream_chunk(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "thinking...", "thought": True}]
                        }
                    }
                ],
                "modelVersion": "gemini-2.5-pro",
            }
        )
        assert chunk is not None
        assert chunk.reasoning_delta == "thinking..."
        assert chunk.delta == ""

    def test_stream_chunk_mixed_thought_and_text(self):
        chunk = parse_gemini_stream_chunk(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "thought ", "thought": True},
                                {"text": "visible"},
                            ]
                        }
                    }
                ],
                "modelVersion": "gemini-2.5-pro",
            }
        )
        assert chunk.reasoning_delta == "thought "
        assert chunk.delta == "visible"


# ===========================================================================
# OpenRouter — _build_body
# ===========================================================================


class TestOpenRouterBuildBody:
    def test_reasoning_flags_added_for_capable_model(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("openrouter", m, strategy="openai_compatible_reasoning"),
        )
        adapter = OpenRouterAdapter()
        payload = _payload(
            "anthropic/claude-sonnet-4",
            reasoning=ReasoningConfig(mode="high", show_summary=True),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="anthropic/claude-sonnet-4")
        assert body["include_reasoning"] is True
        assert body["reasoning"] == {"effort": "high"}

    def test_mode_off_skips_flags(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("openrouter", m, strategy="openai_compatible_reasoning"),
        )
        adapter = OpenRouterAdapter()
        payload = _payload(
            "anthropic/claude-sonnet-4",
            reasoning=ReasoningConfig(mode="off"),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="anthropic/claude-sonnet-4")
        assert "include_reasoning" not in body
        assert "reasoning" not in body

    def test_non_capable_model_skips_flags(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _non_capable("openrouter", m),
        )
        adapter = OpenRouterAdapter()
        payload = _payload(
            "openai/gpt-4o",
            reasoning=ReasoningConfig(mode="medium"),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="openai/gpt-4o")
        assert "include_reasoning" not in body

    def test_body_unchanged_when_no_reasoning_field(self):
        adapter = OpenRouterAdapter()
        payload = _payload("openai/gpt-4o")
        body = adapter._build_body(payload, payload.messages, resolved_model="openai/gpt-4o")
        assert "include_reasoning" not in body
        assert "reasoning" not in body


# ===========================================================================
# OpenAI-compatible base — _build_body
# ===========================================================================


class TestOpenAICompatibleBuildBody:
    def test_reasoning_effort_added_for_capable_model(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("deepseek", m, strategy="openai_compatible_reasoning"),
        )
        adapter = OpenAICompatibleAdapter()
        adapter.provider_name = "deepseek"
        adapter.base_url = "https://api.deepseek.com/v1"
        payload = _payload(
            "deepseek-reasoner",
            reasoning=ReasoningConfig(mode="high"),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="deepseek-reasoner")
        assert body["reasoning_effort"] == "high"

    def test_temperature_and_max_tokens_preserved(self, monkeypatch):
        # OpenAI-compatible providers (DeepSeek/Groq) accept the legacy
        # sampling knobs alongside reasoning — unlike native OpenAI
        # o-series which rejects temperature. Verify we don't strip.
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _capable("deepseek", m, strategy="openai_compatible_reasoning"),
        )
        adapter = OpenAICompatibleAdapter()
        adapter.provider_name = "deepseek"
        adapter.base_url = "https://api.deepseek.com/v1"
        payload = _payload(
            "deepseek-reasoner",
            reasoning=ReasoningConfig(mode="medium"),
        )
        payload.temperature = 0.5
        payload.max_tokens = 512
        body = adapter._build_body(payload, payload.messages, resolved_model="deepseek-reasoner")
        assert body["temperature"] == 0.5
        assert body["max_tokens"] == 512
        assert body["reasoning_effort"] == "medium"

    def test_non_capable_model_skips_reasoning(self, monkeypatch):
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: _non_capable("deepseek", m),
        )
        adapter = OpenAICompatibleAdapter()
        adapter.provider_name = "deepseek"
        adapter.base_url = "https://api.deepseek.com/v1"
        payload = _payload(
            "deepseek-chat",
            reasoning=ReasoningConfig(mode="medium"),
        )
        body = adapter._build_body(payload, payload.messages, resolved_model="deepseek-chat")
        assert "reasoning_effort" not in body


# ===========================================================================
# Cost: _estimate_reasoning_cost
# ===========================================================================


class TestReasoningCost:
    def test_zero_tokens_returns_zero(self):
        assert _estimate_reasoning_cost("o3-mini", 0) == 0.0

    def test_uses_candidate_output_rate(self, monkeypatch):
        # Output rate $0.000010/token × 1000 tokens = $0.01.
        from app.services import usage_service as usage_mod

        monkeypatch.setattr(
            usage_mod,
            "get_candidate",
            lambda m: ModelCandidate(
                provider="openai",
                model=m,
                cost_per_input_token=0.0000025,
                cost_per_output_token=0.000010,
            ),
            raising=False,
        )
        # The function imports get_candidate inside, so patch the
        # registry source too.
        monkeypatch.setattr(
            model_registry,
            "get_candidate",
            lambda m: ModelCandidate(
                provider="openai",
                model=m,
                cost_per_input_token=0.0000025,
                cost_per_output_token=0.000010,
            ),
        )
        assert _estimate_reasoning_cost("gpt-4o", 1000) == 0.01

    def test_unknown_model_falls_back_to_legacy_rate(self, monkeypatch):
        # No candidate → legacy default rate $0.000001/token × 100 tokens.
        monkeypatch.setattr(model_registry, "get_candidate", lambda m: None)
        # 100 * 1e-6 = 1e-4, rounded to 8 places.
        assert _estimate_reasoning_cost("unknown-model", 100) == 0.0001


# ===========================================================================
# Request log persistence — reasoning columns
# ===========================================================================


class TestRequestLogReasoningPersistence:
    def test_persist_round_trips_reasoning_columns(self, db_session):
        svc = RequestLogService()
        rec = RequestRecord(
            request_id="test-req-reasoning-1",
            tenant_id="t1",
            project_id="p1",
            provider="openai",
            model="o3-mini",
            status=RequestStatus.SUCCESS,
            policy_action="allow",
            latency_ms=42,
            prompt_tokens=10,
            completion_tokens=50,
            total_tokens=60,
            estimated_cost_usd=0.00012345,
            reasoning_tokens=35,
            reasoning_cost=0.000175,
        )
        svc.persist(db_session, rec)
        db_session.commit()
        row = db_session.get(RequestLogORM, "test-req-reasoning-1")
        assert row is not None
        assert row.reasoning_tokens == 35
        assert row.reasoning_cost == pytest.approx(0.000175)

    def test_persist_defaults_reasoning_to_zero_when_unset(self, db_session):
        # Non-reasoning request — RequestRecord defaults must round-trip.
        svc = RequestLogService()
        rec = RequestRecord(
            request_id="test-req-reasoning-2",
            tenant_id="t1",
            project_id="p1",
            provider="openai",
            model="gpt-4o-mini",
            status=RequestStatus.SUCCESS,
            policy_action="allow",
            latency_ms=10,
            prompt_tokens=5,
            completion_tokens=5,
            total_tokens=10,
            estimated_cost_usd=0.00001,
        )
        svc.persist(db_session, rec)
        db_session.commit()
        row = db_session.get(RequestLogORM, "test-req-reasoning-2")
        assert row is not None
        assert row.reasoning_tokens == 0
        assert row.reasoning_cost == 0.0


# ===========================================================================
# UsageInfo schema field
# ===========================================================================


def test_usage_info_accepts_reasoning_tokens():
    info = UsageInfo(
        prompt_tokens=10,
        completion_tokens=50,
        total_tokens=60,
        reasoning_tokens=35,
    )
    assert info.reasoning_tokens == 35


def test_usage_info_reasoning_tokens_optional():
    info = UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    assert info.reasoning_tokens is None
