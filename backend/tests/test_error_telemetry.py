"""P6-06: Request outcome and error telemetry tests."""

import io
import logging

import structlog
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _capture():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    structlog.configure(
        processors=[structlog.stdlib.add_log_level, structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    return buf


class TestSuccessOutcome:
    def test_success_logged_with_status(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert '"status": "success"' in output


class TestErrorTelemetry:
    def test_auth_error_has_path(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "gateway_error" in output
        assert "chat/completions" in output

    def test_validation_error_logged(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={},
        )
        output = buf.getvalue()
        assert "validation_error" in output

    def test_dlp_block_logged_as_error(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "user", "content": "AKIAIOSFODNN7EXAMPLE"}]},
        )
        output = buf.getvalue()
        # secret category masks (not blocks) in default profile since Stage 6 —
        # a mask action should still produce an audit/finding log entry.
        assert '"action": "mask"' in output or "dlp_finding" in output

    def test_error_includes_request_id(self):
        buf = _capture()
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "request_id" in output
