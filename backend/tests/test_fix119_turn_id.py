"""FIX119 Part A — the caller's turn id rides the request context and the wire.

The backend keeps minting its own ``req_<hex>`` per gateway request (the
request_logs primary key); the caller's ``X-Request-Id`` — chat-edge's run
id for a web turn, the desktop's run id — is carried beside it as
``turn_id`` and bound into every log line, so a grep for the id the person
sees finds this layer too.
"""

from __future__ import annotations

import dataclasses

from starlette.requests import Request

from app.api.routes.public_chat import _turn_id_from
from app.domain.entities import RequestContext
from app.schemas.public import ChatCompletionsChoice, Message


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": raw, "query_string": b""})


def test_turn_id_is_read_from_x_request_id_when_safe():
    assert _turn_id_from(_request({"X-Request-Id": "5f3c1a2b-0000-4000-8000-0123456789ab"})) == "5f3c1a2b-0000-4000-8000-0123456789ab"
    assert _turn_id_from(_request({"X-Request-Id": "  run_2026.09.15-a "})) == "run_2026.09.15-a"


def test_turn_id_is_absent_when_missing_or_unsafe():
    assert _turn_id_from(_request({})) is None
    assert _turn_id_from(_request({"X-Request-Id": "short"})) is None
    assert _turn_id_from(_request({"X-Request-Id": "has spaces in it"})) is None
    assert _turn_id_from(_request({"X-Request-Id": "<script>" + "a" * 20})) is None


def test_request_context_carries_turn_id_beside_request_id():
    ctx = RequestContext(tenant_id="t", project_id="p", project_name="n", request_id="req_abc")
    assert ctx.turn_id is None
    bound = dataclasses.replace(ctx, turn_id="5f3c1a2b-0000-4000-8000-0123456789ab")
    assert bound.request_id == "req_abc"
    assert bound.turn_id == "5f3c1a2b-0000-4000-8000-0123456789ab"


def test_choice_carries_stop_reason_and_defaults_to_unknown():
    c = ChatCompletionsChoice(index=0, message=Message(role="assistant", content="x"))
    assert c.finish_reason == "stop"
    assert c.stop_reason == "unknown"
    c2 = ChatCompletionsChoice(index=0, message=Message(role="assistant", content="x"), finish_reason="length", stop_reason="max_tokens")
    assert c2.model_dump()["stop_reason"] == "max_tokens"
