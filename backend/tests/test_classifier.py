"""Tests for P3-04: Sensitivity classification and severity model.

Verifies:
- DEFAULT_SEVERITY_MAP covers all detector entity types
- baseline_severity() lookups
- classify() escalation rules (multi-category, multi-entity-type)
- No downgrade guarantee
- Pure function behavior (input not mutated)
- Custom severity map override
"""

import pytest

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.classifier import (
    DEFAULT_SEVERITY_MAP,
    SensitivityClassifier,
)


# ---------------------------------------------------------------------------
# Match factory helpers
# ---------------------------------------------------------------------------


def _match(
    detector_name: str = "email",
    entity_type: str = "email_address",
    category: str = "pii",
    severity: SensitivityLevel = SensitivityLevel.MEDIUM,
) -> DetectionMatch:
    return DetectionMatch(
        detector_name=detector_name,
        entity_type=entity_type,
        category=category,
        start=0,
        end=10,
        matched_text="x" * 10,
        severity=severity,
    )


def _email() -> DetectionMatch:
    return _match("email", "email_address", "pii", SensitivityLevel.MEDIUM)


def _phone() -> DetectionMatch:
    return _match("phone", "phone_number", "pii", SensitivityLevel.HIGH)


def _ssn() -> DetectionMatch:
    return _match("ssn", "us_ssn", "pii", SensitivityLevel.HIGH)


def _aws_key() -> DetectionMatch:
    return _match("bearer_token", "aws_access_key", "secret", SensitivityLevel.CRITICAL)


def _private_key() -> DetectionMatch:
    return _match("private_key", "private_key_header", "secret", SensitivityLevel.CRITICAL)


def _password() -> DetectionMatch:
    return _match("password_field", "password_in_config", "secret", SensitivityLevel.HIGH)


# ---------------------------------------------------------------------------
# DEFAULT_SEVERITY_MAP coverage
# ---------------------------------------------------------------------------


class TestDefaultSeverityMap:
    def test_covers_email(self):
        assert DEFAULT_SEVERITY_MAP["email_address"] == SensitivityLevel.MEDIUM

    def test_covers_phone(self):
        assert DEFAULT_SEVERITY_MAP["phone_number"] == SensitivityLevel.HIGH

    def test_covers_ssn(self):
        assert DEFAULT_SEVERITY_MAP["us_ssn"] == SensitivityLevel.HIGH

    def test_covers_bearer_token(self):
        assert DEFAULT_SEVERITY_MAP["bearer_token"] == SensitivityLevel.CRITICAL

    def test_covers_aws_key(self):
        assert DEFAULT_SEVERITY_MAP["aws_access_key"] == SensitivityLevel.CRITICAL

    def test_covers_openai_key(self):
        assert DEFAULT_SEVERITY_MAP["openai_api_key"] == SensitivityLevel.CRITICAL

    def test_covers_github_token(self):
        assert DEFAULT_SEVERITY_MAP["github_token"] == SensitivityLevel.CRITICAL

    def test_covers_slack_token(self):
        assert DEFAULT_SEVERITY_MAP["slack_token"] == SensitivityLevel.CRITICAL

    def test_covers_private_key(self):
        assert DEFAULT_SEVERITY_MAP["private_key_header"] == SensitivityLevel.CRITICAL

    def test_covers_password(self):
        assert DEFAULT_SEVERITY_MAP["password_in_config"] == SensitivityLevel.HIGH

    def test_all_detector_entity_types_present(self):
        """Cross-check: every entity type produced by the default registry
        must have an entry in the severity map."""
        from app.services.dlp.detectors.registry import get_default_registry

        registry = get_default_registry()
        # Collect all entity types by running detectors on a crafted text
        sample = (
            "user@example.com 555-123-4567 123-45-6789 "
            "AKIAIOSFODNN7EXAMPLE sk-abcdefghij1234567890abcd "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn "
            "xoxb-123456789-abcdefghijk "
            "-----BEGIN PRIVATE KEY----- "
            "password=hunter2"
        )
        matches = registry.scan_text(sample)
        entity_types_found = {m.entity_type for m in matches}

        for et in entity_types_found:
            assert et in DEFAULT_SEVERITY_MAP, (
                f"Entity type {et!r} produced by detectors but missing from DEFAULT_SEVERITY_MAP"
            )


# ---------------------------------------------------------------------------
# baseline_severity() lookups
# ---------------------------------------------------------------------------


class TestBaselineSeverity:
    def test_known_entity_type(self):
        c = SensitivityClassifier()
        assert c.baseline_severity("email_address") == SensitivityLevel.MEDIUM

    def test_unknown_entity_type_returns_medium(self):
        c = SensitivityClassifier()
        assert c.baseline_severity("unknown_type") == SensitivityLevel.MEDIUM

    def test_custom_map(self):
        custom = {"email_address": SensitivityLevel.CRITICAL}
        c = SensitivityClassifier(severity_map=custom)
        assert c.baseline_severity("email_address") == SensitivityLevel.CRITICAL


# ---------------------------------------------------------------------------
# classify() — no escalation cases
# ---------------------------------------------------------------------------


class TestClassifyNoEscalation:
    classifier = SensitivityClassifier()

    def test_empty_matches(self):
        assert self.classifier.classify([]) == []

    def test_single_email_stays_medium(self):
        result = self.classifier.classify([_email()])
        assert len(result) == 1
        assert result[0].severity == SensitivityLevel.MEDIUM

    def test_single_aws_key_stays_critical(self):
        result = self.classifier.classify([_aws_key()])
        assert len(result) == 1
        assert result[0].severity == SensitivityLevel.CRITICAL

    def test_two_pii_same_type_no_escalation(self):
        """Two emails = same category (pii), same entity type → no escalation."""
        result = self.classifier.classify([_email(), _email()])
        assert all(m.severity == SensitivityLevel.MEDIUM for m in result)

    def test_email_plus_phone_same_category(self):
        """Email + phone = same category (pii), 2 entity types → no escalation (need 3).
        Email stays MEDIUM, phone stays HIGH (its baseline)."""
        result = self.classifier.classify([_email(), _phone()])
        email_match = [m for m in result if m.entity_type == "email_address"][0]
        phone_match = [m for m in result if m.entity_type == "phone_number"][0]
        assert email_match.severity == SensitivityLevel.MEDIUM
        assert phone_match.severity == SensitivityLevel.HIGH


# ---------------------------------------------------------------------------
# classify() — escalation cases
# ---------------------------------------------------------------------------


class TestClassifyEscalation:
    classifier = SensitivityClassifier()

    def test_multi_category_escalates(self):
        """Email (pii) + AWS key (secret) = 2 categories → escalate email to HIGH."""
        result = self.classifier.classify([_email(), _aws_key()])
        email_match = [m for m in result if m.entity_type == "email_address"][0]
        aws_match = [m for m in result if m.entity_type == "aws_access_key"][0]
        assert email_match.severity == SensitivityLevel.HIGH  # escalated from MEDIUM
        assert aws_match.severity == SensitivityLevel.CRITICAL  # unchanged (already above HIGH)

    def test_three_entity_types_escalates(self):
        """Email + phone + SSN = 3 entity types → escalate MEDIUM to HIGH."""
        result = self.classifier.classify([_email(), _phone(), _ssn()])
        email_match = [m for m in result if m.entity_type == "email_address"][0]
        phone_match = [m for m in result if m.entity_type == "phone_number"][0]
        ssn_match = [m for m in result if m.entity_type == "us_ssn"][0]
        assert email_match.severity == SensitivityLevel.HIGH  # escalated
        assert phone_match.severity == SensitivityLevel.HIGH  # escalated
        assert ssn_match.severity == SensitivityLevel.HIGH  # already HIGH, unchanged

    def test_pii_plus_secret_escalates_pii(self):
        """Email (pii) + password (secret) = 2 categories."""
        result = self.classifier.classify([_email(), _password()])
        email_match = [m for m in result if m.entity_type == "email_address"][0]
        pwd_match = [m for m in result if m.entity_type == "password_in_config"][0]
        assert email_match.severity == SensitivityLevel.HIGH  # escalated
        assert pwd_match.severity == SensitivityLevel.HIGH  # already HIGH


# ---------------------------------------------------------------------------
# No-downgrade guarantee
# ---------------------------------------------------------------------------


class TestNoDowngrade:
    classifier = SensitivityClassifier()

    def test_critical_never_downgraded(self):
        matches = [_aws_key(), _email()]
        result = self.classifier.classify(matches)
        aws_match = [m for m in result if m.entity_type == "aws_access_key"][0]
        assert aws_match.severity == SensitivityLevel.CRITICAL

    def test_high_never_downgraded(self):
        matches = [_ssn()]
        result = self.classifier.classify(matches)
        assert result[0].severity == SensitivityLevel.HIGH


# ---------------------------------------------------------------------------
# Pure function / immutability
# ---------------------------------------------------------------------------


class TestPurity:
    classifier = SensitivityClassifier()

    def test_original_matches_not_mutated(self):
        original = _email()
        original_severity = original.severity
        self.classifier.classify([original, _aws_key()])
        # DetectionMatch is frozen, so mutation would raise. But let's be explicit:
        assert original.severity == original_severity

    def test_returns_new_list(self):
        matches = [_email()]
        result = self.classifier.classify(matches)
        assert result is not matches

    def test_escalated_match_is_new_object(self):
        """When severity changes, the returned match should be a new instance."""
        original = _email()
        result = self.classifier.classify([original, _aws_key()])
        escalated = [m for m in result if m.entity_type == "email_address"][0]
        assert escalated is not original
        assert escalated.severity == SensitivityLevel.HIGH

    def test_non_escalated_match_is_same_object(self):
        """When severity doesn't change, return the original (no unnecessary copy)."""
        original = _email()
        result = self.classifier.classify([original])
        assert result[0] is original
