"""PR-05: Request body size limit tests."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}

# FIX126 O8 — /v1/chat/completions carries a 20 MB override (vision turns
# inline base64 images, see app/main.py); the 1 MB default applies to every
# other path. The tests used to send ~1-2 MB to the chat route, which is
# now legitimately accepted (422 from validation), so they probe past the
# chat override and also pin the 1 MB default on a non-chat path.
_CHAT_LIMIT = 20 * 1024 * 1024


class TestBodySizeLimit:
    def test_normal_request_passes(self):
        resp = client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200

    def test_oversized_request_rejected(self):
        # A body larger than the default 1 MB, on a path without an override.
        big_content = "x" * (1024 * 1024 + 100)
        resp = client.post(
            "/v1/search",
            headers={**_HEADERS, "Content-Length": str(len(big_content))},
            content=big_content,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"]["code"] == "request_too_large"

    def test_oversized_chat_request_rejected(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Length": str(_CHAT_LIMIT + 1)},
            content="x" * 100,
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "request_too_large"

    def test_error_has_standard_envelope(self):
        resp = client.post(
            "/v1/chat/completions",
            headers={**_HEADERS, "Content-Length": str(_CHAT_LIMIT + 1)},
            content="x" * 100,
        )
        assert resp.status_code == 413
        body = resp.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
