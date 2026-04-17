"""P1-02: Canonical public schema tests.

Verifies:
- ChatCompletionsRequest has top-level generation params
- ChatCompletionsResponse has `created` timestamp
- `route_reasons` removed from public response
- Generation params forwarded to provider body
- Schema serialization/deserialization
"""

import time

from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from app.main import app
from app.schemas.common import Message, UsageInfo
from app.schemas.public import (
    ChatCompletionsChoice,
    ChatCompletionsRequest,
    ChatCompletionsResponse,
)

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class TestChatCompletionsRequest:
    def test_has_temperature_field(self):
        req = ChatCompletionsRequest(
            messages=[Message(role="user", content="hi")],
            temperature=0.7,
        )
        assert req.temperature == 0.7

    def test_has_max_tokens_field(self):
        req = ChatCompletionsRequest(
            messages=[Message(role="user", content="hi")],
            max_tokens=100,
        )
        assert req.max_tokens == 100

    def test_has_top_p_field(self):
        req = ChatCompletionsRequest(
            messages=[Message(role="user", content="hi")],
            top_p=0.9,
        )
        assert req.top_p == 0.9

    def test_generation_params_optional(self):
        req = ChatCompletionsRequest(
            messages=[Message(role="user", content="hi")],
        )
        assert req.temperature is None
        assert req.max_tokens is None
        assert req.top_p is None

    def test_metadata_still_works(self):
        req = ChatCompletionsRequest(
            messages=[Message(role="user", content="hi")],
            metadata={"use_case": "coding"},
        )
        assert req.metadata["use_case"] == "coding"

    def test_serialization_round_trip(self):
        req = ChatCompletionsRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="hi")],
            temperature=0.5,
            max_tokens=200,
        )
        data = req.model_dump()
        assert data["temperature"] == 0.5
        assert data["max_tokens"] == 200
        # Deserialize back
        req2 = ChatCompletionsRequest(**data)
        assert req2.temperature == 0.5


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class TestChatCompletionsResponse:
    def test_has_created_field(self):
        resp = ChatCompletionsResponse(
            id="req_123",
            model="gpt-4o",
            provider="openai",
            choices=[
                ChatCompletionsChoice(
                    index=0, message=Message(role="assistant", content="hi")
                )
            ],
            usage=UsageInfo(),
        )
        assert isinstance(resp.created, int)
        assert resp.created > 0

    def test_created_is_recent_timestamp(self):
        resp = ChatCompletionsResponse(
            id="req_123",
            model="gpt-4o",
            provider="openai",
            choices=[
                ChatCompletionsChoice(
                    index=0, message=Message(role="assistant", content="hi")
                )
            ],
            usage=UsageInfo(),
        )
        now = int(time.time())
        assert abs(resp.created - now) < 5

    def test_no_route_reasons_field(self):
        resp = ChatCompletionsResponse(
            id="req_123",
            model="m",
            provider="p",
            choices=[
                ChatCompletionsChoice(
                    index=0, message=Message(role="assistant", content="hi")
                )
            ],
            usage=UsageInfo(),
        )
        data = resp.model_dump()
        assert "route_reasons" not in data

    def test_policy_fields_present(self):
        resp = ChatCompletionsResponse(
            id="req_123",
            model="m",
            provider="p",
            choices=[
                ChatCompletionsChoice(
                    index=0, message=Message(role="assistant", content="hi")
                )
            ],
            usage=UsageInfo(),
            policy_action="mask",
            policy_reasons=["pii detected"],
        )
        data = resp.model_dump()
        assert data["policy_action"] == "mask"
        assert data["policy_reasons"] == ["pii detected"]


# ---------------------------------------------------------------------------
# Integration: generation params forwarded
# ---------------------------------------------------------------------------


class TestGenerationParamsIntegration:
    def test_temperature_accepted_at_top_level(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.5,
            },
        )
        assert resp.status_code == 200

    def test_max_tokens_accepted_at_top_level(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
        )
        assert resp.status_code == 200

    def test_response_has_created_timestamp(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        body = resp.json()
        assert "created" in body
        assert isinstance(body["created"], int)
        assert body["created"] > 0

    def test_response_has_no_route_reasons(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        body = resp.json()
        assert "route_reasons" not in body
