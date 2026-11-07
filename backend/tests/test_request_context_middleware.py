"""P1-03: Request context middleware and endpoint stabilization tests.

Verifies:
- RequestContextMiddleware is wired and generates X-Request-Id
- Custom X-Request-Id is echoed back
- request_id appears in error responses
- Chat endpoint uses stabilized response schema
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


class TestRequestContextMiddleware:
    def test_response_has_x_request_id(self):
        resp = client.get("/health")
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 0

    def test_custom_request_id_echoed(self):
        resp = client.get(
            "/health", headers={"X-Request-Id": "custom-123"}
        )
        assert resp.headers["x-request-id"] == "custom-123"

    def test_chat_response_has_request_id_header(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert "x-request-id" in resp.headers

    def test_error_response_has_request_id(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "request_id" in body
        # Now that middleware is wired, request_id should be populated
        assert body["request_id"] is not None
        assert len(body["request_id"]) > 0

    def test_validation_error_has_request_id(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={},  # missing messages
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["request_id"] is not None


class TestChatEndpointStability:
    def test_response_has_required_fields(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        body = resp.json()
        # OpenAI-compatible core fields
        assert "id" in body
        assert "object" in body
        assert "created" in body
        assert "model" in body
        assert "choices" in body
        assert "usage" in body
        # Gateway extensions
        assert "provider" in body
        assert "policy_action" in body
        # Removed internal field
        assert "route_reasons" not in body

    def test_response_object_is_chat_completion(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.json()["object"] == "chat.completion"
