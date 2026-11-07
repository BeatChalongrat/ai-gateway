"""P6-03: Structured request lifecycle logging tests.

Verifies lifecycle events are logged with correct fields by capturing
structlog output during a chat request.
"""

import io
import json
import logging

import pytest
import structlog

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


def _capture_structlog():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    return buf


class TestLifecycleLogging:
    def test_request_start_logged(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "request_start" in output

    def test_dlp_scan_logged(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "dlp_scan_complete" in output

    def test_route_selected_logged(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "route_selected" in output

    def test_request_complete_logged(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "request_complete" in output

    def test_complete_has_latency(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        output = buf.getvalue()
        assert "total_ms" in output

    def test_no_raw_content_in_logs(self):
        buf = _capture_structlog()
        client.post(
            "/v1/chat/completions",
            headers=_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "My SSN is 123-45-6789"}],
            },
        )
        output = buf.getvalue()
        assert "123-45-6789" not in output
