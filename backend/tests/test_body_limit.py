"""PR-05: Request body size limit tests."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


class TestBodySizeLimit:
    def test_normal_request_passes(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200

    def test_oversized_request_rejected(self):
        # Create a body larger than 1MB
        big_content = "x" * (1024 * 1024 + 100)
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Length": str(len(big_content) + 200)},
            content=big_content,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"]["code"] == "request_too_large"

    def test_error_has_standard_envelope(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Length": "2000000"},
            content="x" * 100,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
