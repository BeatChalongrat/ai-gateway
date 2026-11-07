"""P1-07: Public error contract enforcement tests.

Verifies:
- All error responses use unified envelope
- request_id is always present (non-null after middleware wiring)
- Error codes are machine-readable strings
- Different error paths produce consistent shapes
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _assert_error_envelope(resp, expected_status, expected_code=None):
    """Assert the response is a well-formed error envelope."""
    assert resp.status_code == expected_status, f"Expected {expected_status}, got {resp.status_code}"
    body = resp.json()
    assert "error" in body, "Missing 'error' key"
    assert "code" in body["error"], "Missing 'error.code'"
    assert "message" in body["error"], "Missing 'error.message'"
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["message"], str)
    assert len(body["error"]["code"]) > 0
    assert len(body["error"]["message"]) > 0
    assert "request_id" in body
    if expected_code:
        assert body["error"]["code"] == expected_code
    return body


class TestAuthErrorContract:
    def test_missing_auth_401(self):
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        _assert_error_envelope(resp, 401, "authentication_error")

    def test_invalid_bearer_401(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Token abc"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        _assert_error_envelope(resp, 401, "authentication_error")


class TestValidationErrorContract:
    def test_missing_messages_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto"},
        )
        _assert_error_envelope(resp, 422, "validation_error")

    def test_empty_messages_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": []},
        )
        _assert_error_envelope(resp, 422, "validation_error")

    def test_invalid_role_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "bogus", "content": "hi"}]},
        )
        _assert_error_envelope(resp, 422, "validation_error")

    def test_invalid_temperature_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 5.0,
            },
        )
        _assert_error_envelope(resp, 422, "validation_error")

    def test_invalid_json_422(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Type": "application/json"},
            content=b"not json",
        )
        _assert_error_envelope(resp, 422, "validation_error")


class TestDLPBlockErrorContract:
    def test_dlp_block_403(self):
        """Sending an AWS key (CRITICAL) is blocked under the default profile."""
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "messages": [
                    {"role": "user", "content": "My key is AKIAIOSFODNN7EXAMPLE"}
                ]
            },
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "dlp_violation"


class TestNotFoundErrorContract:
    def test_unknown_route_404(self):
        resp = client.get("/v1/nonexistent")
        _assert_error_envelope(resp, 404)


class TestErrorResponseConsistency:
    """Ensure all error status codes produce the exact same envelope shape."""

    def test_all_errors_have_request_id(self):
        """Every error response should have a non-null request_id."""
        errors = [
            client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            ),  # 401
            client.post(
                "/v1/chat/completions",
                headers=_HEADERS,
                json={},
            ),  # 422
        ]
        for resp in errors:
            body = resp.json()
            assert body.get("request_id") is not None, (
                f"request_id is None for {resp.status_code}"
            )
