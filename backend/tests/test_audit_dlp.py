"""Tests for P3-09: Privacy-safe DLP audit event emission.

Verifies:
- MatchAction → DLPAuditEntry conversion strips matched_text
- Structlog output contains expected fields
- Structlog output does NOT contain matched_text
- Both input and output directions emit correctly
- record_blocked_request emits entries and logs warning
- record_success emits findings for both directions
- Empty enforcement produces no entries
"""

import dataclasses
import io
import json
import logging

import pytest
import structlog

from app.domain.dlp import (
    DLPAuditEntry,
    DetectionMatch,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
    SensitivityLevel,
)
from app.domain.entities import RequestContext
from app.services.audit_service import AuditService


def _ctx(**kwargs):
    defaults = {
        "tenant_id": "t1",
        "project_id": "p1",
        "project_name": "Test",
    }
    defaults.update(kwargs)
    return RequestContext(**defaults)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_action(
    detector_name: str = "email",
    entity_type: str = "email_address",
    category: str = "pii",
    severity: SensitivityLevel = SensitivityLevel.MEDIUM,
    action: EnforcementAction = EnforcementAction.MASK,
    matched_text: str = "user@example.com",
    start: int = 10,
    end: int = 26,
) -> MatchAction:
    return MatchAction(
        match=DetectionMatch(
            detector_name=detector_name,
            entity_type=entity_type,
            category=category,
            start=start,
            end=end,
            matched_text=matched_text,
            severity=severity,
        ),
        action=action,
        reason_code="test_policy",
        reason_message="Test reason",
    )


def _enforcement(*match_actions: MatchAction) -> EnforcementDecision:
    return EnforcementDecision(
        match_actions=list(match_actions),
        policy_profile_id="default",
        reasons=["Test reason"],
    )


def _capture_structlog() -> io.StringIO:
    """Configure structlog to write JSON to a StringIO buffer for capture."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    return buf


# ---------------------------------------------------------------------------
# emit_dlp_findings — entry conversion
# ---------------------------------------------------------------------------


class TestEmitDLPFindings:
    service = AuditService()

    def test_returns_dlp_audit_entries(self):
        ma = _match_action()
        entries = self.service.emit_dlp_findings([ma], direction="input")
        assert len(entries) == 1
        assert isinstance(entries[0], DLPAuditEntry)

    def test_entry_fields_correct(self):
        ma = _match_action(
            detector_name="ssn",
            entity_type="us_ssn",
            category="pii",
            severity=SensitivityLevel.HIGH,
            action=EnforcementAction.REDACT,
            start=5,
            end=16,
        )
        entries = self.service.emit_dlp_findings([ma], direction="output")
        e = entries[0]
        assert e.detector_name == "ssn"
        assert e.entity_type == "us_ssn"
        assert e.category == "pii"
        assert e.severity == "high"
        assert e.action == "redact"
        assert e.direction == "output"
        assert "5:16" in e.field_path

    def test_no_matched_text_in_entry(self):
        """Core privacy guarantee: DLPAuditEntry has no matched_text."""
        ma = _match_action(matched_text="sensitive@secret.com")
        entries = self.service.emit_dlp_findings([ma], direction="input")
        entry = entries[0]

        # Structural check
        field_names = {f.name for f in dataclasses.fields(entry)}
        assert "matched_text" not in field_names

        # Value check — serialize and verify
        entry_dict = dataclasses.asdict(entry)
        serialized = json.dumps(entry_dict)
        assert "sensitive@secret.com" not in serialized

    def test_multiple_entries(self):
        actions = [
            _match_action(detector_name="email", entity_type="email_address"),
            _match_action(
                detector_name="ssn",
                entity_type="us_ssn",
                matched_text="123-45-6789",
                start=30,
                end=41,
            ),
        ]
        entries = self.service.emit_dlp_findings(actions, direction="input")
        assert len(entries) == 2
        assert entries[0].detector_name == "email"
        assert entries[1].detector_name == "ssn"

    def test_empty_actions_returns_empty(self):
        entries = self.service.emit_dlp_findings([], direction="input")
        assert entries == []

    def test_field_path_prefix_customizable(self):
        ma = _match_action(start=0, end=10)
        entries = self.service.emit_dlp_findings(
            [ma], direction="output", field_path_prefix="response_content"
        )
        assert entries[0].field_path.startswith("response_content")


# ---------------------------------------------------------------------------
# Structlog output verification
# ---------------------------------------------------------------------------


class TestStructlogOutput:
    def test_log_contains_expected_fields(self):
        buf = _capture_structlog()
        service = AuditService()
        ma = _match_action()
        service.emit_dlp_findings([ma], direction="input")

        output = buf.getvalue()
        assert "dlp_finding" in output
        assert "email" in output
        assert "email_address" in output
        assert "pii" in output
        assert "medium" in output
        assert "mask" in output
        assert "input" in output

    def test_log_does_not_contain_matched_text(self):
        """The raw sensitive value must NEVER appear in log output."""
        buf = _capture_structlog()
        service = AuditService()
        secret = "super_secret_password_12345"
        ma = _match_action(matched_text=secret)
        service.emit_dlp_findings([ma], direction="input")

        output = buf.getvalue()
        assert secret not in output


# ---------------------------------------------------------------------------
# record_blocked_request
# ---------------------------------------------------------------------------


class TestRecordBlockedRequest:
    def test_emits_entries_and_returns_them(self):
        service = AuditService()
        ma = _match_action(action=EnforcementAction.BLOCK)
        enforcement = _enforcement(ma)
        entries = service.record_blocked_request(
            ctx=_ctx(tenant_id="t1", project_id="p1"),
            enforcement=enforcement,
            direction="input",
        )
        assert len(entries) == 1
        assert entries[0].action == "block"

    def test_direction_passed_through(self):
        service = AuditService()
        ma = _match_action(action=EnforcementAction.BLOCK)
        enforcement = _enforcement(ma)
        entries = service.record_blocked_request(
            ctx=_ctx(),
            enforcement=enforcement,
            direction="output",
        )
        assert entries[0].direction == "output"

    def test_empty_enforcement_no_entries(self):
        service = AuditService()
        enforcement = EnforcementDecision()
        entries = service.record_blocked_request(
            ctx=_ctx(),
            enforcement=enforcement,
        )
        assert entries == []


# ---------------------------------------------------------------------------
# record_success
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    def test_emits_input_findings(self):
        buf = _capture_structlog()
        service = AuditService()
        ma = _match_action()
        enforcement = _enforcement(ma)
        service.record_success(
            ctx=_ctx(),
            route=None,
            enforcement=enforcement,
            usage=None,
            input_enforcement=enforcement,
            output_enforcement=None,
        )
        output = buf.getvalue()
        assert "dlp_finding" in output
        assert '"direction": "input"' in output

    def test_emits_output_findings(self):
        buf = _capture_structlog()
        service = AuditService()
        ma = _match_action()
        enforcement = _enforcement(ma)
        service.record_success(
            ctx=_ctx(),
            route=None,
            enforcement=enforcement,
            usage=None,
            input_enforcement=None,
            output_enforcement=enforcement,
        )
        output = buf.getvalue()
        assert "dlp_finding" in output
        assert '"direction": "output"' in output

    def test_no_findings_no_log(self):
        buf = _capture_structlog()
        service = AuditService()
        empty = EnforcementDecision()
        service.record_success(
            ctx=_ctx(),
            route=None,
            enforcement=empty,
            usage=None,
            input_enforcement=empty,
            output_enforcement=empty,
        )
        output = buf.getvalue()
        assert "dlp_finding" not in output


# ---------------------------------------------------------------------------
# Privacy stress test
# ---------------------------------------------------------------------------


class TestPrivacyStress:
    """Verify that various sensitive values never leak through audit."""

    SENSITIVE_VALUES = [
        "user@secret-corp.com",
        "123-45-6789",
        "AKIAIOSFODNN7EXAMPLE",
        "sk-proj-abcdefghij1234567890abcd",
        "-----BEGIN PRIVATE KEY-----",
        "DB_PASSWORD=hunter2",
    ]

    def test_none_leak_through_emit(self):
        service = AuditService()
        for val in self.SENSITIVE_VALUES:
            ma = _match_action(matched_text=val)
            entries = service.emit_dlp_findings([ma], direction="input")
            entry_dict = dataclasses.asdict(entries[0])
            serialized = json.dumps(entry_dict)
            assert val not in serialized, f"Sensitive value leaked: {val!r}"
