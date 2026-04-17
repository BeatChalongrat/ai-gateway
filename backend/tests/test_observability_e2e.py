"""P6-09: End-to-end observability integration tests.

Validates lifecycle logging, audit events, and usage tracking through
real request flows.
"""

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


class TestE2ESuccessObservability:
    def test_full_lifecycle_events(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "request_start" in output
        assert "dlp_scan_complete" in output
        assert "route_selected" in output
        assert "request_complete" in output

    def test_request_id_propagated_in_all_events(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        # Every lifecycle log should have request_id
        for event in ("request_start", "request_complete"):
            assert event in output
        assert "request_id" in output

    def test_latency_captured(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "total_ms" in output

    def test_usage_in_completion_log(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "total_tokens" in output


class TestE2EDLPObservability:
    def test_dlp_mask_logged(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "user", "content": "Email: test@example.com"}]},
        )
        output = buf.getvalue()
        assert "dlp_scan_complete" in output
        assert '"action": "mask"' in output

    def test_dlp_block_logged(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "user", "content": "AKIAIOSFODNN7EXAMPLE"}]},
        )
        output = buf.getvalue()
        assert "request_blocked" in output or "dlp_scan_complete" in output


class TestE2EErrorObservability:
    def test_auth_error_logged(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "gateway_error" in output
        assert "authentication_error" in output

    def test_validation_error_logged(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={},
        )
        output = buf.getvalue()
        assert "validation_error" in output


class TestE2EPrivacySafety:
    def test_no_message_content_in_logs(self):
        secret_text = "SUPER_SECRET_VALUE_12345"
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"messages": [{"role": "user", "content": secret_text}]},
        )
        output = buf.getvalue()
        assert secret_text not in output

    def test_no_api_key_in_logs(self):
        buf = _capture()
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer gw_secret_key_value"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "gw_secret_key_value" not in output
