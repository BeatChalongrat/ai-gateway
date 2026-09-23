"""FIX130 — hidden thinking keeps the stream alive.

When the client does not see the reasoning (``show_summary`` off, or mode
"off" on a row whose box cannot stop thinking), the route used to emit no
frame at all while the model thought. The stream idle guard (60 s) then
closed a healthy stream mid-thought: live, qwen3.5-9b spent 8,723 reasoning
tokens and the turn ended ``chat_stream_timeout kind=idle`` with no answer.

The route now writes an SSE comment (``: thinking``) at most every
``HIDDEN_REASONING_KEEPALIVE_S`` of hidden thinking. It carries no text, so
the reasoning stays hidden; chat-edge counts it as model activity.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import public_chat
from app.domain.routing import ModelCandidate
from app.main import app
from app.services.providers.model_registry import _registry
from app.services.providers.sranai_adapter import SranAIAdapter

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}
MODEL = "fix130-qwen-keepalive"
THINKING = ["Thinking Process:\n", "1. The user asks 2+3.\n", "2. Answer: 5.\n", "Secret-ish plan text\n"]
PIECES = THINKING + ["</think>", "\n\n", "5 ครับ"]


def _vllm_stream(pieces: list[str]) -> bytes:
    frames = [{"model": MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
    frames += [{"model": MODEL, "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]} for p in pieces]
    frames.append({"model": MODEL, "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "stop"}]})
    frames.append({"model": MODEL, "choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 40, "total_tokens": 90}})
    return ("".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n").encode()


@pytest.fixture
def qwen_box(monkeypatch):
    monkeypatch.setitem(
        _registry._model_map,
        MODEL,
        ModelCandidate(
            provider="sranai", model=MODEL, supports_reasoning=True,
            reasoning_strategy="openai_compatible_reasoning_content",
            default_reasoning_mode="auto",
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_vllm_stream(PIECES))

    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://box.test/v1")

    async def _client(self, api_key=None):
        return fake

    monkeypatch.setattr(SranAIAdapter, "_get_client", _client)
    # Every hidden fragment is "due" — the test does not wait 10 s.
    monkeypatch.setattr(public_chat, "HIDDEN_REASONING_KEEPALIVE_S", 0.0, raising=False)


def _post(show_summary: bool) -> str:
    resp = client.post(
        "/v1/chat/completions",
        headers=_HEADERS,
        json={
            "model": MODEL,
            "stream": True,
            "messages": [{"role": "user", "content": "2+3 เท่ากับเท่าไร"}],
            "reasoning": {"mode": "auto", "show_summary": show_summary},
        },
    )
    assert resp.status_code == 200, resp.text[:300]
    return resp.text


def _answer(body: str) -> str:
    out = ""
    for line in body.splitlines():
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            ev = json.loads(line[6:])
            out += ((ev.get("choices") or [{}])[0].get("delta", {}) or {}).get("content") or ""
    return out


@pytest.mark.real_adapter
def test_hidden_thinking_writes_keepalive_comments_and_no_text(qwen_box):
    body = _post(show_summary=False)
    comments = [line for line in body.splitlines() if line.startswith(":")]
    assert comments, "hidden thinking produced no frame at all — the idle guard would close the stream"
    assert all(line == ": thinking" for line in comments), comments
    # The reasoning itself stays hidden and out of the answer.
    assert "reasoning_summary_delta" not in body
    assert "Secret-ish plan text" not in body
    assert _answer(body).strip() == "5 ครับ"


@pytest.mark.real_adapter
def test_shown_thinking_needs_no_keepalive(qwen_box):
    body = _post(show_summary=True)
    assert not [line for line in body.splitlines() if line.startswith(":")]
    assert "reasoning_summary_delta" in body
    assert _answer(body).strip() == "5 ครับ"
