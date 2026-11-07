"""Structured-Outputs passthrough — OpenAIAdapter._build_body forwards
``response_format`` verbatim when the request carries it, and omits it
entirely when None.

Motivation (FIX11 §5): conversation-title generation asks gpt-4o-mini
for a strict json_schema so a small model has nowhere to preamble in
the wrong language. The gateway (chat-edge) threads ``response_format``
through, but ``ChatCompletionsRequest`` only actually forwards it to
OpenAI if the adapter copies it into the wire body — these tests pin
that behaviour and prove the no-``response_format`` path stays
byte-identical for existing callers.
"""

from __future__ import annotations

from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.providers.openai_adapter import OpenAIAdapter


TITLE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "conversation_title",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
            },
        },
    },
}


def _payload(model: str, *, response_format=None) -> ChatCompletionsRequest:
    return ChatCompletionsRequest(
        model=model,
        messages=[Message(role="user", content="hi")],
        response_format=response_format,
    )


def test_response_format_forwarded_when_set():
    adapter = OpenAIAdapter()
    payload = _payload("gpt-4o-mini", response_format=TITLE_RESPONSE_FORMAT)
    body = adapter._build_body(payload, payload.messages, resolved_model="gpt-4o-mini")
    assert body["response_format"] == TITLE_RESPONSE_FORMAT


def test_response_format_omitted_when_none():
    adapter = OpenAIAdapter()
    payload = _payload("gpt-4o-mini")
    # Existing callers never set the field — the wire body must stay
    # byte-identical (no empty response_format key).
    body = adapter._build_body(payload, payload.messages, resolved_model="gpt-4o-mini")
    assert "response_format" not in body


def test_response_format_default_is_none():
    # Schema default keeps every existing caller unchanged at the model
    # boundary — the field is simply absent unless explicitly supplied.
    payload = ChatCompletionsRequest(
        model="gpt-4o-mini",
        messages=[Message(role="user", content="hi")],
    )
    assert payload.response_format is None
