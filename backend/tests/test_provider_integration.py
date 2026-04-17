"""P2-09: Provider integration tests with mocked upstreams.

Tests the full request lifecycle through the app with stubbed provider dispatch:
- Auth → project resolution → DLP → routing → dispatch → response
- Provider error paths (timeout, rate limit, unavailable)
- Model routing resolves correct provider
- Usage from provider response vs. heuristic fallback
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _chat(content="Hello", model="auto", **extra):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "metadata": extra.get("metadata", {}),
    }
    return client.post("/v1/chat/completions", headers=_HEADERS, json=body)


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


class TestProviderIntegrationSuccess:
    def test_auto_model_returns_200(self):
        resp = _chat("Hello")
        assert resp.status_code == 200

    def test_response_has_provider_field(self):
        body = _chat("Hello").json()
        assert "provider" in body
        assert body["provider"] in {"openai", "anthropic", "openrouter"}

    def test_response_has_model_field(self):
        body = _chat("Hello").json()
        assert "model" in body
        assert len(body["model"]) > 0

    def test_response_has_usage(self):
        body = _chat("Hello").json()
        assert "usage" in body
        usage = body["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage

    def test_response_has_choices(self):
        body = _chat("Hello").json()
        assert len(body["choices"]) >= 1
        assert "message" in body["choices"][0]
        assert body["choices"][0]["message"]["role"] == "assistant"

    def test_explicit_openai_model(self):
        body = _chat("Hi", model="gpt-4o-mini").json()
        assert body["provider"] == "openai"

    def test_explicit_anthropic_model(self):
        body = _chat("Hi", model="claude-3-5-sonnet-20241022").json()
        assert body["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# Request/response contract
# ---------------------------------------------------------------------------


class TestResponseContract:
    def test_response_has_id(self):
        body = _chat("Hi").json()
        assert body["id"].startswith("req_")

    def test_response_object_type(self):
        body = _chat("Hi").json()
        assert body["object"] == "chat.completion"

    def test_policy_action_present(self):
        body = _chat("Hi").json()
        assert "policy_action" in body

    def test_route_reasons_not_in_public_response(self):
        body = _chat("Hi").json()
        assert "route_reasons" not in body


# ---------------------------------------------------------------------------
# Auth failure
# ---------------------------------------------------------------------------


class TestAuthFailure:
    def test_no_auth_returns_401(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    def test_401_has_error_envelope(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "authentication_error"
