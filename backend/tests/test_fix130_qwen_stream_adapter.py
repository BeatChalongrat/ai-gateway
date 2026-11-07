"""FIX130 B1 — the REAL OpenAI-compatible ``stream_chat`` / ``chat`` over a
fake vLLM transport (``real_adapter``) returns the sranai box's in-band
thinking as reasoning, not as the answer.

The wire bytes are the shape measured straight from the box in FIX130
step 1 (the raw-shape capture of the FIX130 probes): content
deltas only, the thinking first, ``</think>`` as its own delta, then the
answer; ``reasoning_content`` never populated. Before FIX130 the whole
thing reached the caller as ``delta``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.routing import ModelCandidate
from app.schemas.public import ChatCompletionsRequest, Message, ReasoningConfig
from app.services.providers import model_registry
from app.services.providers.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.providers.reasoning_strategies import (
    STRATEGY_ENABLE_THINKING,
    STRATEGY_NONE,
    STRATEGY_REASONING_CONTENT,
)

QWEN_PIECES = [
    "Thinking", " Process", ":", "\n\n", "1.  **Analyze the Request:** 2+3", "\n", "</think>", "\n\n", "5",
]


def _cand(provider, model, *, reasoning=True, strategy=STRATEGY_REASONING_CONTENT):
    return ModelCandidate(
        provider=provider, model=model, supports_reasoning=reasoning,
        reasoning_strategy=strategy, default_reasoning_mode="auto",
    )


def _vllm_stream(pieces: list[str], finish: str = "stop") -> bytes:
    lines = [
        'data: ' + json.dumps({"model": "qwen3.5-9b", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
    ]
    for p in pieces:
        lines.append('data: ' + json.dumps({"model": "qwen3.5-9b", "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}))
    lines.append('data: ' + json.dumps({"model": "qwen3.5-9b", "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": finish}]}))
    lines.append('data: ' + json.dumps({"model": "qwen3.5-9b", "choices": [], "usage": {"prompt_tokens": 13, "completion_tokens": 91, "total_tokens": 104}}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


class _Box(OpenAICompatibleAdapter):
    provider_name = "sranai"

    def __init__(self, handler):
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://box.test/v1")

    async def _get_client(self, api_key=None):
        return self._client

    def _request_headers(self, api_key=None):
        return {}


def _req(model="qwen3.5-9b", **kw):
    return ChatCompletionsRequest(model=model, messages=[Message(role="user", content="2+3")], **kw)


@pytest.mark.asyncio
@pytest.mark.real_adapter
async def test_stream_chat_routes_qwen_thinking_to_reasoning_delta(monkeypatch):
    monkeypatch.setattr(model_registry, "get_candidate", lambda m: _cand("sranai", m))
    body = _vllm_stream(QWEN_PIECES)
    adapter = _Box(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))
    chunks = [c async for c in adapter.stream_chat(_req(stream=True), _req().messages, resolved_model="qwen3.5-9b")]
    answer = "".join(c.delta for c in chunks)
    reasoning = "".join(c.reasoning_delta or "" for c in chunks)
    assert answer == "5"
    assert reasoning.startswith("Thinking Process:") and "</think>" not in reasoning
    assert chunks[-1].usage is not None and chunks[-1].usage.completion_tokens == 91


@pytest.mark.asyncio
@pytest.mark.real_adapter
async def test_stream_chat_leaves_a_normal_row_byte_identical(monkeypatch):
    monkeypatch.setattr(model_registry, "get_candidate", lambda m: _cand("sranai", m, reasoning=False, strategy=STRATEGY_NONE))
    pieces = ["Use ", "`<think>`", " tags ", "like </think> this."]
    body = _vllm_stream(pieces)
    adapter = _Box(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))
    chunks = [c async for c in adapter.stream_chat(_req("Typhoon", stream=True), _req().messages, resolved_model="Typhoon")]
    assert [c.delta for c in chunks if c.delta] == pieces
    assert all(c.reasoning_delta is None for c in chunks)


@pytest.mark.asyncio
@pytest.mark.real_adapter
async def test_stream_chat_thinking_off_switch_turns_the_implicit_rule_off(monkeypatch):
    """enable_thinking=false was sent (FIX72 row, mode off): the box answers
    directly — measured, 22 chars, no </think> — so nothing may be held."""
    monkeypatch.setattr(model_registry, "get_candidate", lambda m: _cand("sranai", m, strategy=STRATEGY_ENABLE_THINKING))
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_vllm_stream(["2 บวก 3 ", "เท่ากับ 5 ครับ"]))

    adapter = _Box(handler)
    payload = _req("qwen3.8-27b-fp8", stream=True, reasoning=ReasoningConfig(mode="off"))
    chunks = [c async for c in adapter.stream_chat(payload, payload.messages, resolved_model="qwen3.8-27b-fp8")]
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert [c.delta for c in chunks if c.delta] == ["2 บวก 3 ", "เท่ากับ 5 ครับ"]


@pytest.mark.asyncio
@pytest.mark.real_adapter
async def test_chat_non_stream_splits_the_implicit_block(monkeypatch):
    monkeypatch.setattr(model_registry, "get_candidate", lambda m: _cand("sranai", m))
    data = {
        "model": "qwen3.6-35b-a3b",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Here's a thinking process: …✅\n</think>\n\n5 ครับ"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 274, "completion_tokens": 399, "total_tokens": 673},
    }
    adapter = _Box(lambda r: httpx.Response(200, json=data))
    out = await adapter.chat(_req("qwen3.6-35b-a3b"), _req().messages, resolved_model="qwen3.6-35b-a3b")
    assert out.content == "5 ครับ"
    assert out.reasoning_content.startswith("Here's a thinking process")
