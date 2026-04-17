"""P8-06: Alert/webhook foundation tests."""

import io
import logging

import structlog
import pytest

from app.services.alert_service import (
    Alert, AlertService, AlertSeverity, AlertType,
)


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


class TestAlertEmission:
    def test_emit_logs_alert(self):
        buf = _capture()
        svc = AlertService()
        svc.emit(Alert(
            alert_type=AlertType.BUDGET_THRESHOLD,
            severity=AlertSeverity.WARNING,
            tenant_id="t1",
            message="test",
        ))
        assert "alert_emitted" in buf.getvalue()
        assert "budget_threshold" in buf.getvalue()

    def test_budget_warning(self):
        buf = _capture()
        svc = AlertService()
        svc.budget_warning("t1", "p1", 0.8, 1.0)
        output = buf.getvalue()
        assert "budget_threshold" in output
        assert "80.0%" in output

    def test_budget_critical_at_100(self):
        buf = _capture()
        svc = AlertService()
        svc.budget_warning("t1", "p1", 1.0, 1.0)
        output = buf.getvalue()
        assert '"severity": "critical"' in output

    def test_sensitive_action(self):
        buf = _capture()
        svc = AlertService()
        svc.sensitive_action("t1", "key_revoke", "k1", {"key_id": "k1"})
        output = buf.getvalue()
        assert "sensitive_action" in output
        assert "key_revoke" in output


class TestAlertModel:
    def test_frozen(self):
        a = Alert(alert_type=AlertType.QUOTA_EXHAUSTED, severity=AlertSeverity.CRITICAL, tenant_id="t1")
        with pytest.raises(AttributeError):
            a.tenant_id = "t2"  # type: ignore[misc]
