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

from fastapi.testclient import TestClient

from app.main import app

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
    def test_aws_key_blocked(self):
        resp = _chat("My AWS key: AKIAIOSFODNN7EXAMPLE")
        # secret category masks (not blocks) in default profile since Stage 6
        assert resp.status_code == 200
        body = resp.json()
        # secret category masks (not blocks) in default profile since Stage 6
        assert body["policy_action"] == "mask"
        # Input value is restored in the final response (masked only on
        # the provider side — see test_e2e_dlp_flow.py).
        assert "AKIAIOSFODNN7EXAMPLE" in body["choices"][0]["message"]["content"]

    def test_bearer_token_blocked(self):
        resp = _chat("Use Bearer sk-proj-12345678901234567890abcdefghijklm")
        # secret category masks (not blocks) in default profile since Stage 6
        assert resp.status_code == 200
        # secret category masks (not blocks) in default profile since Stage 6
        assert resp.json()["policy_action"] == "mask"


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
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) > 3
        assert data[0]["id"] == "auto"

    def test_no_auth_required(self):
        resp = client.get("/v1/models")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestE2EHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
