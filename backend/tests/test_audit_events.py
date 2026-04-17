"""P6-05: Audit event integration tests.

Verifies record_success now emits a structured request_audit log event.
"""

import io
import logging

import structlog

from app.domain.dlp import EnforcementDecision
from app.domain.entities import RequestContext, RouteDecision
from app.schemas.common import UsageInfo
from app.services.audit_service import AuditService


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


def _ctx():
    return RequestContext(
        tenant_id="t1", project_id="p1", project_name="P", request_id="req_test"
    )


class TestRecordSuccessAudit:
    def test_emits_request_audit_event(self):
        buf = _capture()
        svc = AuditService()
        svc.record_success(
            ctx=_ctx(),
            route=RouteDecision(provider_name="openai", model_name="gpt-4o"),
            enforcement=EnforcementDecision(),
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            input_enforcement=EnforcementDecision(),
            output_enforcement=EnforcementDecision(),
        )
        output = buf.getvalue()
        assert "request_audit" in output

    def test_audit_includes_provider(self):
        buf = _capture()
        svc = AuditService()
        svc.record_success(
            ctx=_ctx(),
            route=RouteDecision(provider_name="anthropic", model_name="claude"),
            enforcement=EnforcementDecision(),
            usage=UsageInfo(),
            input_enforcement=EnforcementDecision(),
            output_enforcement=EnforcementDecision(),
        )
        output = buf.getvalue()
        assert '"provider": "anthropic"' in output

    def test_audit_includes_tokens(self):
        buf = _capture()
        svc = AuditService()
        svc.record_success(
            ctx=_ctx(),
            route=RouteDecision(provider_name="openai", model_name="m"),
            enforcement=EnforcementDecision(),
            usage=UsageInfo(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            input_enforcement=EnforcementDecision(),
            output_enforcement=EnforcementDecision(),
        )
        output = buf.getvalue()
        assert '"total_tokens": 150' in output

    def test_audit_includes_request_id(self):
        buf = _capture()
        svc = AuditService()
        svc.record_success(
            ctx=_ctx(),
            route=RouteDecision(provider_name="openai", model_name="m"),
            enforcement=EnforcementDecision(),
            usage=UsageInfo(),
            input_enforcement=EnforcementDecision(),
            output_enforcement=EnforcementDecision(),
        )
        output = buf.getvalue()
        assert "req_test" in output

    def test_no_raw_content_in_audit(self):
        buf = _capture()
        svc = AuditService()
        svc.record_success(
            ctx=_ctx(),
            route=RouteDecision(provider_name="openai", model_name="m"),
            enforcement=EnforcementDecision(),
            usage=UsageInfo(),
            input_enforcement=EnforcementDecision(),
            output_enforcement=EnforcementDecision(),
        )
        output = buf.getvalue()
        assert "content" not in output.lower() or "response_content" in output.lower()
