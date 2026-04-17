"""P1-10: API documentation validation tests.

Verifies:
- Public API doc exists
- Documents all public endpoints
- Documents error codes
- Documents auth mechanism
- Documents streaming behavior
"""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestPublicApiDocExists:
    def test_doc_exists(self):
        assert (DOCS_DIR / "PUBLIC_API.md").exists()


class TestDocContent:
    def _doc(self):
        return (DOCS_DIR / "PUBLIC_API.md").read_text(encoding="utf-8")

    def test_documents_chat_endpoint(self):
        assert "/v1/chat/completions" in self._doc()

    def test_documents_models_endpoint(self):
        assert "/v1/models" in self._doc()

    def test_documents_health_endpoint(self):
        assert "/health" in self._doc()

    def test_documents_auth(self):
        doc = self._doc()
        assert "Bearer" in doc
        assert "Authorization" in doc

    def test_documents_error_codes(self):
        doc = self._doc()
        for code in [
            "authentication_error",
            "validation_error",
            "dlp_violation",
            "provider_error",
            "provider_timeout",
        ]:
            assert code in doc, f"Missing error code: {code}"

    def test_documents_streaming(self):
        doc = self._doc()
        assert "stream" in doc.lower()
        assert "text/event-stream" in doc

    def test_documents_policy_action(self):
        doc = self._doc()
        assert "policy_action" in doc

    def test_documents_request_tracing(self):
        doc = self._doc()
        assert "X-Request-Id" in doc

    def test_documents_model_selection(self):
        doc = self._doc()
        assert "auto" in doc
        assert "gpt-4o" in doc
        assert "claude" in doc
