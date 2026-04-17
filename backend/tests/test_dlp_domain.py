"""Tests for P3-01: DLP domain model and contracts.

Verifies:
- Enum ordering (SensitivityLevel, EnforcementAction)
- Dataclass instantiation and field types
- Computed properties (has_sensitive_content, highest_severity, etc.)
- Privacy safety (DLPAuditEntry has no matched_text)
- Serialization friendliness (dataclasses.asdict works)
- Frozen dataclass immutability where expected
"""

import dataclasses

import pytest

from app.domain.dlp import (
    DLPAuditEntry,
    DetectionMatch,
    DetectionResult,
    EnforcementAction,
    EnforcementDecision,
    MatchAction,
    SensitivityLevel,
    TransformationResult,
    TransformedSpan,
)


# ---------------------------------------------------------------------------
# Fixtures — reusable test data
# ---------------------------------------------------------------------------


def _email_match(start: int = 10, end: int = 30) -> DetectionMatch:
    return DetectionMatch(
        detector_name="email",
        entity_type="email_address",
        category="pii",
        start=start,
        end=end,
        matched_text="user@example.com" + "x" * (end - start - 16),
        severity=SensitivityLevel.MEDIUM,
    )


def _cc_match(start: int = 0, end: int = 16) -> DetectionMatch:
    return DetectionMatch(
        detector_name="credit_card",
        entity_type="credit_card_number",
        category="financial",
        start=start,
        end=end,
        matched_text="4111111111111111",
        severity=SensitivityLevel.CRITICAL,
    )


def _aws_match(start: int = 0, end: int = 20) -> DetectionMatch:
    return DetectionMatch(
        detector_name="aws_key",
        entity_type="aws_access_key",
        category="secret",
        start=start,
        end=end,
        matched_text="AKIAIOSFODNN7EXAMPLE",
        severity=SensitivityLevel.CRITICAL,
    )


# ---------------------------------------------------------------------------
# SensitivityLevel enum
# ---------------------------------------------------------------------------


class TestSensitivityLevel:
    def test_ordering(self):
        assert SensitivityLevel.NONE < SensitivityLevel.LOW
        assert SensitivityLevel.LOW < SensitivityLevel.MEDIUM
        assert SensitivityLevel.MEDIUM < SensitivityLevel.HIGH
        assert SensitivityLevel.HIGH < SensitivityLevel.CRITICAL

    def test_max_returns_highest(self):
        levels = [SensitivityLevel.LOW, SensitivityLevel.CRITICAL, SensitivityLevel.MEDIUM]
        assert max(levels) == SensitivityLevel.CRITICAL

    def test_comparison_with_int(self):
        assert SensitivityLevel.LOW == 1
        assert SensitivityLevel.CRITICAL > 2

    def test_all_members_present(self):
        names = {m.name for m in SensitivityLevel}
        assert names == {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


# ---------------------------------------------------------------------------
# EnforcementAction enum
# ---------------------------------------------------------------------------


class TestEnforcementAction:
    def test_ordering(self):
        assert EnforcementAction.ALLOW < EnforcementAction.MASK
        assert EnforcementAction.MASK < EnforcementAction.REDACT
        assert EnforcementAction.REDACT < EnforcementAction.BLOCK

    def test_max_gives_most_restrictive(self):
        actions = [EnforcementAction.ALLOW, EnforcementAction.MASK, EnforcementAction.BLOCK]
        assert max(actions) == EnforcementAction.BLOCK

    def test_all_members_present(self):
        names = {m.name for m in EnforcementAction}
        assert names == {"ALLOW", "MASK", "REDACT", "BLOCK"}


# ---------------------------------------------------------------------------
# DetectionMatch
# ---------------------------------------------------------------------------


class TestDetectionMatch:
    def test_basic_instantiation(self):
        m = _email_match(start=5, end=21)
        assert m.detector_name == "email"
        assert m.entity_type == "email_address"
        assert m.category == "pii"
        assert m.start == 5
        assert m.end == 21
        assert m.severity == SensitivityLevel.MEDIUM
        assert m.confidence == 1.0
        assert m.metadata is None

    def test_custom_confidence_and_metadata(self):
        m = DetectionMatch(
            detector_name="ml_ner",
            entity_type="person_name",
            category="pii",
            start=0,
            end=10,
            matched_text="John Smith",
            severity=SensitivityLevel.HIGH,
            confidence=0.87,
            metadata={"model": "spacy-en-lg", "entity_label": "PERSON"},
        )
        assert m.confidence == 0.87
        assert m.metadata["model"] == "spacy-en-lg"

    def test_frozen(self):
        m = _email_match()
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.severity = SensitivityLevel.CRITICAL  # type: ignore[misc]

    def test_serializable_via_asdict(self):
        m = _email_match(start=0, end=16)
        d = dataclasses.asdict(m)
        assert isinstance(d, dict)
        assert d["detector_name"] == "email"
        assert d["start"] == 0
        assert d["severity"] == SensitivityLevel.MEDIUM


# ---------------------------------------------------------------------------
# DetectionResult
# ---------------------------------------------------------------------------


class TestDetectionResult:
    def test_empty_result(self):
        r = DetectionResult()
        assert r.has_sensitive_content is False
        assert r.match_count == 0
        assert r.highest_severity == SensitivityLevel.NONE
        assert r.categories_found == set()
        assert r.summary == {}

    def test_with_matches(self):
        r = DetectionResult(
            matches=[_email_match(), _cc_match()],
            scanned_direction="input",
            scan_target="messages",
        )
        assert r.has_sensitive_content is True
        assert r.match_count == 2
        assert r.highest_severity == SensitivityLevel.CRITICAL
        assert r.categories_found == {"pii", "financial"}

    def test_summary_counts(self):
        r = DetectionResult(
            matches=[
                _email_match(start=0, end=16),
                _email_match(start=20, end=36),
                _cc_match(start=40, end=56),
            ]
        )
        assert r.summary == {"email_address": 2, "credit_card_number": 1}

    def test_highest_severity_single_match(self):
        r = DetectionResult(matches=[_email_match()])
        assert r.highest_severity == SensitivityLevel.MEDIUM

    def test_direction_and_target(self):
        r = DetectionResult(
            scanned_direction="output",
            scan_target="response_content",
        )
        assert r.scanned_direction == "output"
        assert r.scan_target == "response_content"


# ---------------------------------------------------------------------------
# MatchAction
# ---------------------------------------------------------------------------


class TestMatchAction:
    def test_basic(self):
        m = _email_match()
        ma = MatchAction(
            match=m,
            action=EnforcementAction.MASK,
            reason_code="pii_mask_policy",
            reason_message="PII detected; masking per default policy.",
        )
        assert ma.match is m
        assert ma.action == EnforcementAction.MASK
        assert ma.reason_code == "pii_mask_policy"

    def test_frozen(self):
        ma = MatchAction(
            match=_email_match(),
            action=EnforcementAction.MASK,
            reason_code="test",
            reason_message="test",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            ma.action = EnforcementAction.BLOCK  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EnforcementDecision
# ---------------------------------------------------------------------------


class TestEnforcementDecision:
    def test_no_matches_allows(self):
        d = EnforcementDecision()
        assert d.overall_action == EnforcementAction.ALLOW
        assert d.is_blocked is False
        assert d.requires_transformation is False

    def test_mask_decision(self):
        ma = MatchAction(
            match=_email_match(),
            action=EnforcementAction.MASK,
            reason_code="pii_mask",
            reason_message="Mask PII",
        )
        d = EnforcementDecision(match_actions=[ma])
        assert d.overall_action == EnforcementAction.MASK
        assert d.is_blocked is False
        assert d.requires_transformation is True

    def test_block_escalation(self):
        ma_mask = MatchAction(
            match=_email_match(),
            action=EnforcementAction.MASK,
            reason_code="pii_mask",
            reason_message="Mask",
        )
        ma_block = MatchAction(
            match=_cc_match(),
            action=EnforcementAction.BLOCK,
            reason_code="financial_block",
            reason_message="Block",
        )
        d = EnforcementDecision(
            match_actions=[ma_mask, ma_block],
            reasons=["PII masked", "Financial data blocked"],
        )
        assert d.overall_action == EnforcementAction.BLOCK
        assert d.is_blocked is True
        assert d.requires_transformation is False  # block > mask, no transform needed

    def test_redact_requires_transformation(self):
        ma = MatchAction(
            match=_email_match(),
            action=EnforcementAction.REDACT,
            reason_code="pii_redact",
            reason_message="Redact",
        )
        d = EnforcementDecision(match_actions=[ma])
        assert d.overall_action == EnforcementAction.REDACT
        assert d.requires_transformation is True

    def test_policy_profile_id(self):
        d = EnforcementDecision(policy_profile_id="strict-finance")
        assert d.policy_profile_id == "strict-finance"


# ---------------------------------------------------------------------------
# TransformationResult
# ---------------------------------------------------------------------------


class TestTransformationResult:
    def test_no_transforms(self):
        r = TransformationResult(transformed_text="clean text")
        assert r.transformed_text == "clean text"
        assert r.transformations_applied == 0
        assert r.transformed_spans == []

    def test_with_spans(self):
        spans = [
            TransformedSpan(start=5, end=21, original_length=16, replacement_length=12, action=EnforcementAction.MASK),
            TransformedSpan(start=30, end=46, original_length=16, replacement_length=10, action=EnforcementAction.REDACT),
        ]
        r = TransformationResult(
            transformed_text="some ************ and [REDACTED] end",
            transformed_spans=spans,
        )
        assert r.transformations_applied == 2

    def test_transformed_span_frozen(self):
        s = TransformedSpan(start=0, end=5, original_length=5, replacement_length=3, action=EnforcementAction.MASK)
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.start = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DLPAuditEntry — privacy safety
# ---------------------------------------------------------------------------


class TestDLPAuditEntry:
    def test_basic_instantiation(self):
        e = DLPAuditEntry(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            severity="medium",
            action="mask",
            field_path="messages[0].content",
            direction="input",
        )
        assert e.detector_name == "email"
        assert e.action == "mask"
        assert e.direction == "input"

    def test_no_matched_text_field(self):
        """Core privacy guarantee: DLPAuditEntry must not have matched_text."""
        field_names = {f.name for f in dataclasses.fields(DLPAuditEntry)}
        assert "matched_text" not in field_names
        assert "matched_text_preview" not in field_names
        assert "raw_text" not in field_names

    def test_frozen(self):
        e = DLPAuditEntry(
            detector_name="email",
            entity_type="email_address",
            category="pii",
            severity="medium",
            action="mask",
            field_path="messages[0].content",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.action = "block"  # type: ignore[misc]

    def test_serializable_via_asdict(self):
        e = DLPAuditEntry(
            detector_name="credit_card",
            entity_type="credit_card_number",
            category="financial",
            severity="critical",
            action="block",
            field_path="messages[1].content",
            direction="input",
        )
        d = dataclasses.asdict(e)
        assert isinstance(d, dict)
        assert "matched_text" not in d
        assert d["severity"] == "critical"
        assert d["field_path"] == "messages[1].content"

    def test_default_direction(self):
        e = DLPAuditEntry(
            detector_name="ssn",
            entity_type="ssn",
            category="pii",
            severity="high",
            action="redact",
            field_path="messages[0].content",
        )
        assert e.direction == "input"


# ---------------------------------------------------------------------------
# Framework-agnostic verification
# ---------------------------------------------------------------------------


class TestFrameworkAgnostic:
    def test_no_pydantic_import(self):
        """domain/dlp.py must not import pydantic, fastapi, or sqlalchemy."""
        import inspect
        import app.domain.dlp as mod

        source = inspect.getsource(mod)
        assert "pydantic" not in source
        assert "fastapi" not in source
        assert "sqlalchemy" not in source

    def test_no_external_framework_imports(self):
        """Verify no pydantic/fastapi/sqlalchemy types in the module source."""
        import inspect
        import app.domain.dlp as mod

        source = inspect.getsource(mod)
        for framework in ("pydantic", "fastapi", "sqlalchemy", "starlette", "httpx"):
            assert f"import {framework}" not in source, f"Found import of {framework}"
            assert f"from {framework}" not in source, f"Found from-import of {framework}"
