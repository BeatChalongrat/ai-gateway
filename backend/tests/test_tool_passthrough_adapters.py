"""Tool/function-calling passthrough — OpenAI-compatible base + OpenRouter.

Scope (Tasks 1/2 of the tool-parity fix):
* ``_build_body`` forwards ``payload.tools`` / ``tool_choice`` when the
  resolved model is flagged ``supports_tools=True`` in the registry.
* A tools request against a non-tool model raises ``ValidationError``
  instead of silently stripping the tools (the pre-fix behaviour).
* Assistant ``tool_calls`` and tool-result ``tool_call_id`` / ``name``
  survive message serialization (the old flatten dropped them).
* A plain chat payload (no tools) stays byte-identical — no empty
  ``tools`` / ``tool_choice`` keys appear.

These tests monkeypatch ``model_registry.get_candidate`` to isolate
adapter behaviour from the live DB-backed registry, mirroring
``test_reasoning_t3.py``.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.domain.routing import ModelCandidate
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers import model_registry
from app.services.providers.openai_compatible_adapter import (
    OpenAICompatibleAdapter,
    serialize_openai_wire_message,
)
from app.services.providers.openrouter_adapter import OpenRouterAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CompatAdapter(OpenAICompatibleAdapter):
    """Concrete stand-in — the base leaves base_url/provider_name empty."""

    base_url = "https://compat.example/v1"
    provider_name = "compat-test"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

TOOL_CALLS = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": "{}"},
    }
]


def _tool_conversation() -> list[Message]:
    return [
        Message(role="user", content="weather in bkk?"),
        Message(role="assistant", content=None, tool_calls=TOOL_CALLS),
        Message(role="tool", content="32C", tool_call_id="call_1", name="get_weather"),
    ]


def _payload(model: str, *, tools=None, tool_choice=None) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        tools=tools,
        tool_choice=tool_choice,
    )


def _tool_capable(model: str) -> ModelCandidate:
    return ModelCandidate(provider="test", model=model, supports_tools=True)


def _non_tool(model: str) -> ModelCandidate:
    return ModelCandidate(provider="test", model=model, supports_tools=False)


# Both adapters share the same _build_body contract; parametrize over
# (adapter instance, model name) — a non-anthropic/* OpenRouter model
# keeps the cache_control branch out of these assertions.
ADAPTERS = [
    pytest.param(_CompatAdapter(), "deepseek-chat", id="openai_compatible"),
    pytest.param(OpenRouterAdapter(), "openai/gpt-4o", id="openrouter"),
]


# ===========================================================================
# Tool passthrough + capability gate
# ===========================================================================


class TestToolPassthrough:
    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_tools_forwarded_when_model_capable(self, monkeypatch, adapter, model):
        monkeypatch.setattr(model_registry, "get_candidate", _tool_capable)
        payload = _payload(model, tools=TOOLS, tool_choice="auto")
        body = adapter._build_body(payload, payload.messages, resolved_model=model)
        assert body["tools"] == TOOLS
        assert body["tool_choice"] == "auto"

    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_tool_choice_omitted_when_not_set(self, monkeypatch, adapter, model):
        monkeypatch.setattr(model_registry, "get_candidate", _tool_capable)
        payload = _payload(model, tools=TOOLS)
        body = adapter._build_body(payload, payload.messages, resolved_model=model)
        assert body["tools"] == TOOLS
        assert "tool_choice" not in body

    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_non_tool_model_raises_validation_error(self, monkeypatch, adapter, model):
        monkeypatch.setattr(model_registry, "get_candidate", _non_tool)
        payload = _payload(model, tools=TOOLS)
        with pytest.raises(ValidationError):
            adapter._build_body(payload, payload.messages, resolved_model=model)

    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_unknown_model_raises_validation_error(self, monkeypatch, adapter, model):
        monkeypatch.setattr(model_registry, "get_candidate", lambda m: None)
        payload = _payload(model, tools=TOOLS)
        with pytest.raises(ValidationError):
            adapter._build_body(payload, payload.messages, resolved_model=model)


# ===========================================================================
# Message serialization — tool history survives
# ===========================================================================


class TestToolHistorySerialization:
    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_tool_calls_and_results_preserved(self, monkeypatch, adapter, model):
        monkeypatch.setattr(model_registry, "get_candidate", _tool_capable)
        payload = _payload(model, tools=TOOLS)
        body = adapter._build_body(
            payload, _tool_conversation(), resolved_model=model
        )
        user, assistant, tool = body["messages"]
        assert user == {"role": "user", "content": "weather in bkk?"}
        assert assistant == {"role": "assistant", "tool_calls": TOOL_CALLS}
        assert tool == {
            "role": "tool",
            "content": "32C",
            "tool_call_id": "call_1",
            "name": "get_weather",
        }

    def test_helper_scopes_fields_by_role(self):
        # tool_calls must not leak onto non-assistant turns even if set.
        msg = Message(role="user", content="hi", tool_calls=TOOL_CALLS)
        assert serialize_openai_wire_message(msg) == {"role": "user", "content": "hi"}


# ===========================================================================
# No-tools regression — plain chat body unchanged
# ===========================================================================


class TestPlainChatUnchanged:
    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_no_tools_keys_without_tools(self, adapter, model):
        payload = _payload(model)
        body = adapter._build_body(payload, payload.messages, resolved_model=model)
        assert "tools" not in body
        assert "tool_choice" not in body
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.parametrize("adapter,model", ADAPTERS)
    def test_empty_tools_list_treated_as_no_tools(self, adapter, model):
        # An explicit empty list must not trip the capability gate.
        payload = _payload(model, tools=[])
        body = adapter._build_body(payload, payload.messages, resolved_model=model)
        assert "tools" not in body
