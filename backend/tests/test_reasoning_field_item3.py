"""P1 Item 3 — reasoning field-name compatibility.

DeepSeek-native / Groq R1-distill stream thinking as ``reasoning_content``;
OpenRouter normalises the same models' thinking into ``reasoning``. The
gateway parser must surface reasoning from BOTH spellings, otherwise
``openrouter/deepseek/deepseek-r1`` thinking never reaches the UI (the
reported bug). Covers the streaming SSE parser and the non-streaming
response parser.
"""

from __future__ import annotations

from app.services.providers.openai_adapter import _parse_sse_line
from app.services.providers.parsers import parse_openai_response


def _sse(delta: dict) -> str:
    import json

    return "data: " + json.dumps(
        {"choices": [{"delta": delta, "finish_reason": None}], "model": "m"}
    )


class TestStreamingReasoningField:
    def test_openrouter_reasoning_field(self):
        chunk = _parse_sse_line(_sse({"reasoning": "let me think..."}))
        assert chunk is not None
        assert chunk.reasoning_delta == "let me think..."

    def test_deepseek_reasoning_content_field(self):
        chunk = _parse_sse_line(_sse({"reasoning_content": "step 1..."}))
        assert chunk is not None
        assert chunk.reasoning_delta == "step 1..."

    def test_reasoning_content_wins_when_both_present(self):
        chunk = _parse_sse_line(
            _sse({"reasoning_content": "canonical", "reasoning": "alt"})
        )
        assert chunk.reasoning_delta == "canonical"

    def test_plain_content_has_no_reasoning(self):
        chunk = _parse_sse_line(_sse({"content": "hello"}))
        assert chunk.reasoning_delta is None
        assert chunk.delta == "hello"


class TestNonStreamingReasoningField:
    def test_openrouter_message_reasoning(self):
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "answer",
                                "reasoning": "thought process"},
                    "finish_reason": "stop",
                }
            ],
            "model": "deepseek/deepseek-r1",
        }
        resp = parse_openai_response(data)
        assert resp.reasoning_content == "thought process"

    def test_deepseek_message_reasoning_content(self):
        data = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "answer",
                                "reasoning_content": "native thinking"},
                    "finish_reason": "stop",
                }
            ],
            "model": "deepseek-reasoner",
        }
        resp = parse_openai_response(data)
        assert resp.reasoning_content == "native thinking"

    def test_plain_response_no_reasoning(self):
        data = {
            "choices": [
                {"message": {"role": "assistant", "content": "hi"},
                 "finish_reason": "stop"}
            ],
            "model": "gpt-4o",
        }
        resp = parse_openai_response(data)
        assert resp.reasoning_content is None
