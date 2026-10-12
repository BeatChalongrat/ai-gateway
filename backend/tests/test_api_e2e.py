"""P1-09: End-to-end API integration tests.

Full lifecycle coverage through the public API boundary:
- Auth → project resolution → DLP → routing → provider → response
- Success path with different models
- DLP transformation (masking) path
- DLP block path (secrets)
- Validation failure path
- Auth failure path
- Response contract validation
- Request-id propagation
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.provider_key import ProviderKeyORM
from tests.conftest import _IntegrationSession

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content="Hello", model="auto", **kwargs):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "metadata": kwargs.pop("metadata", {}),
        **kwargs,
    }
    return client.post("/v1/chat/completions", headers=_HEADERS, json=body)


# ---------------------------------------------------------------------------
# Full success lifecycle
# ---------------------------------------------------------------------------


class TestE2ESuccessPath:
    def test_simple_request(self):
        resp = _chat("What is 2+2?")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert body["id"].startswith("req_")
        assert isinstance(body["created"], int)
        assert body["policy_action"] == "allow"

    def test_with_explicit_model(self):
        resp = _chat("Hi", model="gpt-4o-mini")
        assert resp.status_code == 200
        assert resp.json()["provider"] == "openai"

    def test_with_anthropic_model(self):
        resp = _chat("Hi", model="claude-3-5-sonnet-20241022")
        assert resp.status_code == 200
        assert resp.json()["provider"] == "anthropic"

    def test_with_generation_params(self):
        resp = _chat("Hi", temperature=0.5, max_tokens=100, top_p=0.9)
        assert resp.status_code == 200

    def test_with_system_message(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hi"},
                ],
            },
        )
        assert resp.status_code == 200

    def test_multi_turn_conversation(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [
                    {"role": "user", "content": "My name is Alice."},
                    {"role": "assistant", "content": "Hello Alice!"},
                    {"role": "user", "content": "What is my name?"},
                ],
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DLP transformation path
# ---------------------------------------------------------------------------


class TestE2EDLPMasking:
    def test_email_masked_returns_200(self):
        resp = _chat("Send to user@example.com please")
        assert resp.status_code == 200
        body = resp.json()
        assert body["policy_action"] == "mask"
        assert len(body["policy_reasons"]) > 0

    def test_email_restored_in_response(self):
        """Masking is applied before the provider call; the input PII is
        restored in the final response so the user sees the original.
        The masked-to-provider guarantee is verified directly in
        test_e2e_dlp_flow.test_pii_masked_before_provider."""
        resp = _chat("Send to user@example.com please")
        content = resp.json()["choices"][0]["message"]["content"]
        assert "user@example.com" in content


class TestE2EDLPBlock:
    """The block path end to end. A credential is BLOCKED under the strict
    profile (selected here by the per-request ``policy_profile_id``); the
    default profile masks it and the request proceeds — that half is
    test_dlp_config.TestDLPEnabledToggle and test_dlp_policy_runtime_privacy."""

    def test_aws_key_blocked(self):
        resp = _chat("My AWS key: AKIAIOSFODNN7EXAMPLE", policy_profile_id="strict")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"
        assert "SECRET" in body["error"]["categories"]

    def test_bearer_token_blocked(self):
        resp = _chat(
            "Use Bearer sk-proj-12345678901234567890abcdefghijklm",
            policy_profile_id="strict",
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "dlp_violation"
        assert "SECRET" in resp.json()["error"]["categories"]


# ---------------------------------------------------------------------------
# Auth failure
# ---------------------------------------------------------------------------


class TestE2EAuthFailure:
    def test_no_header(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    def test_wrong_scheme(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Token abc123"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation failure
# ---------------------------------------------------------------------------


class TestE2EValidation:
    def test_empty_messages(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": []},
        )
        assert resp.status_code == 422

    def test_invalid_role(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "invalid", "content": "hi"}]},
        )
        assert resp.status_code == 422

    def test_temperature_out_of_range(self):
        resp = _chat("hi", temperature=3.0)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Response contract
# ---------------------------------------------------------------------------


class TestE2EResponseContract:
    def test_response_has_all_fields(self):
        body = _chat("Hi").json()
        required = {"id", "object", "created", "model", "provider", "choices", "usage"}
        assert required.issubset(body.keys())

    def test_usage_has_all_fields(self):
        usage = _chat("Hi").json()["usage"]
        required = {"prompt_tokens", "completion_tokens", "total_tokens"}
        assert required.issubset(usage.keys())

    def test_choice_has_message_and_finish_reason(self):
        choice = _chat("Hi").json()["choices"][0]
        assert "message" in choice
        assert "finish_reason" in choice
        assert choice["message"]["role"] == "assistant"

    def test_request_id_in_header(self):
        resp = _chat("Hi")
        assert "x-request-id" in resp.headers

    def test_request_id_in_error_body(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.json()["request_id"] is not None


# ---------------------------------------------------------------------------
# Models endpoint
# ---------------------------------------------------------------------------


class TestE2EModelsEndpoint:
    def test_returns_models(self):
        # The listing is ``auto`` plus the models of providers that hold an
        # active, validated key (a keyless provider's models cannot be
        # called, so they are not offered) — seed one such key for openai.
        session = _IntegrationSession()
        key = ProviderKeyORM(
            id=str(uuid.uuid4()),
            provider_name="openai",
            label="e2e-models-listing",
            api_key_encrypted="not-a-real-key",
            is_active=True,
            last_test_status="valid",
        )
        session.add(key)
        session.commit()
        try:
            resp = client.get("/v1/models")
        finally:
            session.delete(key)
            session.commit()
            session.close()
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) > 3
        assert data[0]["id"] == "auto"
        openai_ids = {m["id"] for m in data if m["provider"] == "openai"}
        assert {"gpt-4o", "gpt-4o-mini"} <= openai_ids

    def test_no_auth_required(self):
        resp = client.get("/v1/models")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


# AM15: removed — TestE2EHealth::test_health_returns_ok asserted 200 "ok" from /health with nothing behind the process; /health is a component check that answers 503 "unhealthy" when the database is unreachable (by design, FIX126-O8-5), each component state is tested in tests/test_health.py::test_health_is_200_ok_when_every_component_answers / ::test_health_is_503_unhealthy_when_the_database_is_down / ::test_health_is_200_degraded_when_only_redis_is_down, and the live-stack 200 "ok" is tests/test_health.py::test_health_is_ok_on_the_live_stack.
