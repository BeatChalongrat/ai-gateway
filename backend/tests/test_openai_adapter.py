"""P2-02: OpenAI adapter tests with mocked HTTP.

Verifies:
- Successful chat completion → ProviderResponse
- Request body mapping (model, messages, optional params)
- Response parsing (content, model, finish_reason, usage)
- Authorization header sent
- Raw response preserved
"""

import pytest
import respx
import httpx

from app.domain.providers import ProviderResponse, ProviderUsage
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.openai_adapter import OpenAIAdapter, OPENAI_BASE_URL


def _openai_response(
    content="Hello!",
    model="gpt-4o-mini",
    finish_reason="stop",
    prompt_tokens=10,
    completion_tokens=5,
):
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _make_payload(model="gpt-4o-mini", metadata=None):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestOpenAIAdapterSuccess:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_provider_response(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert isinstance(result, ProviderResponse)

    @respx.mock
    @pytest.mark.asyncio
    async def test_content_extracted(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(content="World")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hello")]
        )
        assert result.content == "World"

    @respx.mock
    @pytest.mark.asyncio
    async def test_model_from_response(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(model="gpt-4o-2024-08-06")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.model == "gpt-4o-2024-08-06"

    @respx.mock
    @pytest.mark.asyncio
    async def test_finish_reason(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_response(finish_reason="length")
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.finish_reason == "length"

    @respx.mock
    @pytest.mark.asyncio
    async def test_usage_extracted(self):
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_response(prompt_tokens=20, completion_tokens=30),
            )
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage is not None
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 30
        assert result.usage.total_tokens == 50

    @respx.mock
    @pytest.mark.asyncio
    async def test_raw_response_preserved(self):
        raw = _openai_response()
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=raw)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.raw["id"] == "chatcmpl-abc123"


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


class TestOpenAIRequestMapping:
    @respx.mock
    @pytest.mark.asyncio
    async def test_model_sent_in_body(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(model="gpt-4o"),
            [Message(role="user", content="test")],
        )
        body = route.calls[0].request.content
        import json

        parsed = json.loads(body)
        assert parsed["model"] == "gpt-4o"

    @respx.mock
    @pytest.mark.asyncio
    async def test_resolved_model_used_over_payload(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(model="auto"),
            [Message(role="user", content="test")],
            resolved_model="gpt-4o-mini",
        )
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["model"] == "gpt-4o-mini"

    @respx.mock
    @pytest.mark.asyncio
    async def test_messages_mapped(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        msgs = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello"),
        ]
        await adapter.chat(_make_payload(), msgs)
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert len(parsed["messages"]) == 2
        assert parsed["messages"][0]["role"] == "system"
        assert parsed["messages"][1]["content"] == "Hello"

    @respx.mock
    @pytest.mark.asyncio
    async def test_internal_user_message_name_not_forwarded(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(),
            [Message(role="user", content="Hello", name="__screenshot__")],
        )
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["messages"] == [{"role": "user", "content": "Hello"}]

    @respx.mock
    @pytest.mark.asyncio
    async def test_tool_message_fields_preserved(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(),
            [
                Message(
                    role="tool",
                    content="42",
                    tool_call_id="call_123",
                    name="calculator",
                )
            ],
        )
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["messages"] == [
            {
                "role": "tool",
                "content": "42",
                "tool_call_id": "call_123",
                "name": "calculator",
            }
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_optional_params_forwarded(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        payload = _make_payload(metadata={"temperature": 0.7, "max_tokens": 100})
        await adapter.chat(payload, [Message(role="user", content="Hi")])
        import json

        parsed = json.loads(route.calls[0].request.content)
        assert parsed["temperature"] == 0.7
        assert parsed["max_tokens"] == 100

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_header_sent(self):
        route = respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )
        adapter = OpenAIAdapter()
        await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        auth = route.calls[0].request.headers.get("authorization")
        assert auth is not None
        assert auth.startswith("Bearer ")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestOpenAIEdgeCases:
    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_usage_in_response(self):
        data = _openai_response()
        del data["usage"]
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.usage is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_content(self):
        data = _openai_response(content="")
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=data)
        )
        adapter = OpenAIAdapter()
        result = await adapter.chat(
            _make_payload(), [Message(role="user", content="Hi")]
        )
        assert result.content == ""


# --- P2: Responses API parsers (o-series visible reasoning) -----------------
# The live OpenAI org here is not verified for reasoning summaries, so these
# exercise the parsers directly with synthetic Responses-API payloads to
# prove the mapping is correct (output array -> content + reasoning_content,
# input_tokens/output_tokens -> usage, SSE type dispatch).

from app.services.providers.openai_adapter import (  # noqa: E402
    _parse_responses_response,
    _parse_responses_sse_line,
)


def test_parse_responses_response_splits_reasoning_and_text():
    data = {
        "model": "o4-mini",
        "output": [
            {"type": "reasoning", "summary": [
                {"type": "summary_text", "text": "First I "},
                {"type": "summary_text", "text": "considered the cases."},
            ]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "The answer is 9."},
            ]},
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 150},
        },
    }
    r = _parse_responses_response(data)
    assert r.content == "The answer is 9."
    assert r.reasoning_content == "First I considered the cases."
    assert r.model == "o4-mini"
    assert r.usage.prompt_tokens == 20
    assert r.usage.completion_tokens == 200
    assert r.usage.reasoning_tokens == 150
    assert r.usage.total_tokens == 220  # derived when not provided


def test_parse_responses_response_no_summary_when_org_unverified():
    # Unverified org: reasoning item carries no summary parts. Reasoning
    # text is None; the token count still surfaces via usage.
    data = {
        "model": "o3-mini",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "Hi"}]},
        ],
        "usage": {"input_tokens": 5, "output_tokens": 40,
                   "output_tokens_details": {"reasoning_tokens": 30}},
    }
    r = _parse_responses_response(data)
    assert r.content == "Hi"
    assert r.reasoning_content is None
    assert r.usage.reasoning_tokens == 30


def test_parse_responses_sse_line_dispatch():
    text = _parse_responses_sse_line(
        'data: {"type":"response.output_text.delta","delta":"Hel"}'
    )
    assert text is not None and text.delta == "Hel" and text.reasoning_delta is None

    think = _parse_responses_sse_line(
        'data: {"type":"response.reasoning_summary_text.delta","delta":"because"}'
    )
    assert think is not None and think.reasoning_delta == "because" and think.delta == ""

    done = _parse_responses_sse_line(
        'data: {"type":"response.completed","response":{"model":"o4-mini",'
        '"usage":{"input_tokens":3,"output_tokens":7,'
        '"output_tokens_details":{"reasoning_tokens":4}}}}'
    )
    assert done is not None and done.finish_reason == "stop"
    assert done.usage.reasoning_tokens == 4

    # Lifecycle / unknown events are ignored.
    assert _parse_responses_sse_line('data: {"type":"response.created"}') is None
    assert _parse_responses_sse_line("event: response.completed") is None
    assert _parse_responses_sse_line("data: [DONE]") is None


# ---------------------------------------------------------------------------
# GPT-5 / o-series parameter contract (max_completion_tokens family shaping)
# ---------------------------------------------------------------------------
# These families reject legacy ``max_tokens`` (400 unsupported_parameter —
# the live "Model unavailable, please try again" failure) and non-default
# ``temperature`` / ``top_p``. The shaping is decided by MODEL FAMILY, not
# by reasoning mode, so a plain desktop request (no reasoning config,
# catalog flags irrelevant) gets the right wire shape.

from app.services.providers.openai_adapter import _uses_completion_token_contract


def _payload_with_knobs(model: str) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        max_tokens=64,
        temperature=0.3,
        top_p=0.9,
        metadata={},
    )


class TestCompletionTokenContract:
    def _body(self, model: str) -> dict:
        return OpenAIAdapter()._build_body(
            _payload_with_knobs(model), [Message(role="user", content="Hi")]
        )

    def test_gpt5_renames_max_tokens_and_drops_sampling_knobs(self):
        body = self._body("gpt-5")
        assert body["max_completion_tokens"] == 64
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert "top_p" not in body

    def test_oddly_named_family_member_gets_the_same_shape(self):
        # gpt-5.6-luna: a manually-seeded row whose name doesn't look like a
        # dated OpenAI id — must still ride the gpt-5 family contract.
        body = self._body("gpt-5.6-luna")
        assert body["max_completion_tokens"] == 64
        assert "max_tokens" not in body
        assert "temperature" not in body
        assert "top_p" not in body

    def test_o_series_is_covered_without_a_reasoning_config(self):
        body = self._body("o3-mini")
        assert body["max_completion_tokens"] == 64
        assert "max_tokens" not in body

    def test_gpt4_era_body_is_byte_for_byte_unchanged(self):
        # Full-body equality: any regression on the legacy path fails loudly.
        assert self._body("gpt-4o") == {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.3,
            "max_tokens": 64,
            "top_p": 0.9,
        }

    def test_family_rule_boundaries(self):
        assert _uses_completion_token_contract("gpt-5")
        assert _uses_completion_token_contract("gpt-5-mini")
        assert _uses_completion_token_contract("gpt-5.6-luna")
        assert _uses_completion_token_contract("o1")
        assert _uses_completion_token_contract("o1-mini")
        assert _uses_completion_token_contract("o4-mini")
        assert not _uses_completion_token_contract("gpt-4o")
        assert not _uses_completion_token_contract("gpt-4.1-mini")
        assert not _uses_completion_token_contract("gpt-50")  # boundary, not prefix-greed
        assert not _uses_completion_token_contract("o1000x")


# ---------------------------------------------------------------------------
# gpt-5.6 generation: function tools require an explicit reasoning_effort
# of "none" on /chat/completions (400 invalid_request_error,
# param=reasoning_effort otherwise — the model reasons by DEFAULT server-
# side, so the conflict fires even when the request carries no reasoning
# fields at all). Verified live against gpt-5.6-luna / -sol / -terra; the
# toolless requests that already passed carry no pin and stay unchanged.
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current time",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _payload_with_tools(model: str) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        max_tokens=64,
        tools=_TOOLS,
        metadata={},
    )


class TestGpt56ToolsEffortPin:
    def _body(self, model: str) -> dict:
        return OpenAIAdapter()._build_body(
            _payload_with_tools(model), [Message(role="user", content="Hi")]
        )

    def test_gpt56_generation_with_tools_pins_effort_none(self):
        for model in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"):
            body = self._body(model)
            assert body["reasoning_effort"] == "none", model
            assert body["tools"] == _TOOLS

    def test_gpt56_without_tools_gets_no_pin(self):
        body = OpenAIAdapter()._build_body(
            _payload_with_knobs("gpt-5.6-luna"), [Message(role="user", content="Hi")]
        )
        assert "reasoning_effort" not in body

    def test_older_gpt5_family_with_tools_is_not_pinned(self):
        # gpt-5.1 – gpt-5.4 accept tools at their default effort (verified
        # live) — pinning "none" would silently disable reasoning there.
        for model in ("gpt-5", "gpt-5.1", "gpt-5.4-mini", "o3-mini", "gpt-4o"):
            body = self._body(model)
            assert "reasoning_effort" not in body, model
            assert body["tools"] == _TOOLS
        assert not _uses_completion_token_contract("chat-latest")


# ---------------------------------------------------------------------------
# Responses-API fallback logging (visibility fix)
# ---------------------------------------------------------------------------
# The fallback used to swallow the exception, which hid a 100% failure rate
# (org not verified for reasoning summaries) for months. These tests pin the
# contract: the fallback STILL answers the user, and the warning record now
# carries the exception type, upstream status, and provider body. The module
# logger is patched directly — structlog.testing.capture_logs proved
# order-dependent under the full suite.

from unittest.mock import Mock

from app.services.providers import openai_adapter as openai_adapter_module
from app.services.providers import model_registry as model_registry_module


class _ReasoningCandidate:
    supports_reasoning = True
    reasoning_strategy = "openai_responses_reasoning"


# Captured at import time — BEFORE the conftest autouse fixture
# ``_mock_adapter_stream_chat`` replaces the class attribute — so the
# streaming test below can restore and exercise the real generator.
_REAL_STREAM_CHAT = OpenAIAdapter.stream_chat


_ORG_UNVERIFIED_BODY = {
    "error": {
        "message": "Your organization must be verified to generate reasoning summaries.",
        "type": "invalid_request_error",
        "param": "reasoning.summary",
        "code": "unsupported_value",
    }
}


def _reasoning_payload(model="o4-mini"):
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="Hi")],
        reasoning={"mode": "medium", "show_summary": True},
        metadata={},
    )


class TestResponsesFallbackLogging:
    def _arm(self, monkeypatch):
        monkeypatch.setattr(
            model_registry_module, "get_candidate", lambda m: _ReasoningCandidate()
        )
        mock_log = Mock()
        monkeypatch.setattr(openai_adapter_module, "log", mock_log)
        return mock_log

    @respx.mock
    @pytest.mark.asyncio
    async def test_nonstream_fallback_logs_status_and_body(self, monkeypatch):
        mock_log = self._arm(monkeypatch)
        respx.post(f"{OPENAI_BASE_URL}/responses").mock(
            return_value=httpx.Response(400, json=_ORG_UNVERIFIED_BODY)
        )
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_response())
        )

        result = await OpenAIAdapter().chat(
            _reasoning_payload(), [Message(role="user", content="Hi")]
        )

        # Fallback behaviour unchanged: the user still gets an answer.
        assert result.content == "Hello!"

        assert mock_log.warning.call_count == 1
        event = mock_log.warning.call_args[0][0]
        kwargs = mock_log.warning.call_args[1]
        assert event == "openai_responses_fallback_to_chat"
        assert kwargs["model"] == "o4-mini"
        assert kwargs["error_type"] == "UpstreamBadResponseError"
        assert kwargs["status"] == 400
        assert "must be verified" in str(kwargs["body"])

    @respx.mock
    @pytest.mark.asyncio
    async def test_stream_fallback_logs_status_and_body(self, monkeypatch):
        # Undo the conftest autouse stream stub for this test only.
        monkeypatch.setattr(OpenAIAdapter, "stream_chat", _REAL_STREAM_CHAT)
        mock_log = self._arm(monkeypatch)
        respx.post(f"{OPENAI_BASE_URL}/responses").mock(
            return_value=httpx.Response(400, json=_ORG_UNVERIFIED_BODY)
        )
        sse = (
            'data: {"id":"c","object":"chat.completion.chunk","model":"o4-mini",'
            '"choices":[{"index":0,"delta":{"content":"Yes"},"finish_reason":null}]}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{OPENAI_BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, text=sse)
        )

        chunks = [
            c
            async for c in OpenAIAdapter().stream_chat(
                _reasoning_payload(), [Message(role="user", content="Hi")]
            )
        ]

        # Fallback behaviour unchanged: chunks still flow from /chat/completions.
        assert any(c.delta == "Yes" for c in chunks)

        assert mock_log.warning.call_count == 1
        event = mock_log.warning.call_args[0][0]
        kwargs = mock_log.warning.call_args[1]
        assert event == "openai_responses_stream_fallback_to_chat"
        assert kwargs["error_type"] == "UpstreamBadResponseError"
        assert kwargs["status"] == 400
        assert "must be verified" in str(kwargs["body"])


# ---------------------------------------------------------------------------
# Responses API with tools (gpt-5 family) — request translation, stream
# translation, and the fallback pin precedence.
# ---------------------------------------------------------------------------

from app.services.providers.openai_adapter import (
    _parse_responses_response,
    _parse_responses_sse_line as _sse,
)


def _tool_history():
    return [
        Message(role="user", content="What time is it?"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[{"id": "call_1", "type": "function",
                         "function": {"name": "get_time", "arguments": "{}"}}],
        ),
        Message(role="tool", content="14:30", tool_call_id="call_1", name="get_time"),
    ]


class TestResponsesToolTranslation:
    def test_history_translates_to_function_call_items(self):
        items = OpenAIAdapter._messages_to_responses_input(_tool_history())
        assert items == [
            {"role": "user", "content": "What time is it?"},
            {"type": "function_call", "call_id": "call_1",
             "name": "get_time", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "14:30"},
        ]

    def test_tool_defs_flatten_and_summary_only_when_wanted(self, monkeypatch):
        payload = ChatCompletionsRequest(
            model="gpt-5.4-mini",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            reasoning={"mode": "medium", "show_summary": False},
            metadata={},
        )
        body = OpenAIAdapter()._build_responses_body(
            payload, [Message(role="user", content="Hi")]
        )
        assert body["tools"] == [{
            "type": "function", "name": "get_time",
            "description": "Get current time",
            "parameters": {"type": "object", "properties": {}},
        }]
        assert body["reasoning"] == {"effort": "medium"}  # no summary key

    def test_named_tool_choice_flattens(self):
        payload = ChatCompletionsRequest(
            model="gpt-5.4-mini",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            tool_choice={"type": "function", "function": {"name": "get_time"}},
            reasoning={"mode": "auto", "show_summary": True},
            metadata={},
        )
        body = OpenAIAdapter()._build_responses_body(
            payload, [Message(role="user", content="Hi")]
        )
        assert body["tool_choice"] == {"type": "function", "name": "get_time"}


class TestResponsesToolStreaming:
    def test_function_call_events_become_chat_style_deltas(self):
        state: dict = {}
        header = _sse(
            'data: {"type":"response.output_item.added","output_index":1,'
            '"item":{"id":"fc_abc","type":"function_call","call_id":"call_9",'
            '"name":"get_time","arguments":""}}',
            state,
        )
        assert header is not None
        assert header.tool_calls == [{
            "index": 0, "id": "call_9", "type": "function",
            "function": {"name": "get_time", "arguments": ""},
        }]

        frag = _sse(
            'data: {"type":"response.function_call_arguments.delta",'
            '"item_id":"fc_abc","delta":"{}"}',
            state,
        )
        assert frag is not None
        assert frag.tool_calls == [{"index": 0, "function": {"arguments": "{}"}}]

        done = _sse(
            'data: {"type":"response.completed","response":{"model":"gpt-5.4-mini",'
            '"usage":{"input_tokens":3,"output_tokens":7}}}',
            state,
        )
        assert done is not None and done.finish_reason == "tool_calls"

    def test_toolless_stream_still_finishes_stop(self):
        state: dict = {}
        done = _sse(
            'data: {"type":"response.completed","response":{"model":"gpt-5.4-mini"}}',
            state,
        )
        assert done is not None and done.finish_reason == "stop"

    def test_nonstream_function_call_items_become_tool_calls(self):
        r = _parse_responses_response({
            "model": "gpt-5.4-mini",
            "output": [
                {"type": "reasoning", "summary": [
                    {"type": "summary_text", "text": "check the clock"}]},
                {"type": "function_call", "call_id": "call_2",
                 "name": "get_time", "arguments": "{}"},
            ],
        })
        assert r.finish_reason == "tool_calls"
        assert r.tool_calls == [{
            "id": "call_2", "type": "function",
            "function": {"name": "get_time", "arguments": "{}"},
        }]
        assert r.reasoning_content == "check the clock"


class TestFallbackPinPrecedence:
    def test_effort_is_overridden_to_none_on_tools_fallback(self, monkeypatch):
        # Reasoning wanted + flag true (the post-075 world) but the body
        # is built for /chat/completions (Responses fell back): the pin
        # must WIN over the reasoning block for the whole gpt-5 family.
        monkeypatch.setattr(
            model_registry_module, "get_candidate", lambda m: _ReasoningCandidate()
        )
        payload = ChatCompletionsRequest(
            model="gpt-5.4-mini",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            reasoning={"mode": "medium", "show_summary": True},
            metadata={},
        )
        body = OpenAIAdapter()._build_body(
            payload, [Message(role="user", content="Hi")]
        )
        assert body["reasoning_effort"] == "none"

    def test_gpt56_effortless_tools_pin_survives(self):
        body = OpenAIAdapter()._build_body(
            _payload_with_tools("gpt-5.6-luna"), [Message(role="user", content="Hi")]
        )
        assert body["reasoning_effort"] == "none"


class TestResponsesEligibilityTiers:
    def test_gpt5_family_eligible_with_tools(self, monkeypatch):
        monkeypatch.setattr(
            model_registry_module, "get_candidate", lambda m: _ReasoningCandidate()
        )
        payload = ChatCompletionsRequest(
            model="gpt-5.4-mini",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            reasoning={"mode": "auto", "show_summary": True},
            metadata={},
        )
        assert OpenAIAdapter._responses_eligible(payload, None)

    def test_o_series_with_tools_stays_on_chat_completions(self, monkeypatch):
        monkeypatch.setattr(
            model_registry_module, "get_candidate", lambda m: _ReasoningCandidate()
        )
        payload = ChatCompletionsRequest(
            model="o4-mini",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            reasoning={"mode": "medium", "show_summary": True},
            metadata={},
        )
        assert not OpenAIAdapter._responses_eligible(payload, None)


class TestResponsesTierIsProtocolNotData:
    """Step 5 — the eligibility GATE is data (reasoning_strategy on the
    catalog row); the tools TIER is protocol (_RESPONSES_WITH_TOOLS_FAMILIES).
    A future model carrying the strategy but outside the family tuple gets
    the conservative summary-only tier, not Responses-with-tools."""

    def test_strategy_without_family_gets_conservative_tier(self, monkeypatch):
        monkeypatch.setattr(
            model_registry_module, "get_candidate", lambda m: _ReasoningCandidate()
        )
        # With tools: NOT eligible (conservative tier).
        payload = ChatCompletionsRequest(
            model="future-thinker-1",
            messages=[Message(role="user", content="Hi")],
            tools=_TOOLS,
            reasoning={"mode": "auto", "show_summary": True},
            metadata={},
        )
        assert not OpenAIAdapter._responses_eligible(payload, None)
        # Summary-only: eligible — the strategy row is what admits it.
        payload_no_tools = ChatCompletionsRequest(
            model="future-thinker-1",
            messages=[Message(role="user", content="Hi")],
            reasoning={"mode": "auto", "show_summary": True},
            metadata={},
        )
        assert OpenAIAdapter._responses_eligible(payload_no_tools, None)

    def test_no_catalog_row_means_never_eligible(self, monkeypatch):
        monkeypatch.setattr(model_registry_module, "get_candidate", lambda m: None)
        payload = ChatCompletionsRequest(
            model="gpt-5.4-mini",
            messages=[Message(role="user", content="Hi")],
            reasoning={"mode": "auto", "show_summary": True},
            metadata={},
        )
        assert not OpenAIAdapter._responses_eligible(payload, None)
