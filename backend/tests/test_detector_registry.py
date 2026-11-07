"""Tests for P3-02: Detector interface and registry.

Verifies:
- BaseDetector is abstract and cannot be instantiated directly
- Stub detectors conform to the interface and produce valid matches
- DetectorRegistry registration, retrieval, and duplicate rejection
- scan_text merges matches from multiple detectors, sorted by offset
- scan_messages produces a DetectionResult with correct properties
- get_default_registry returns a usable registry instance
"""

import pytest

from app.domain.dlp import DetectionMatch, DetectionResult, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.detectors.registry import DetectorRegistry, get_default_registry
from app.schemas.common import Message


# ---------------------------------------------------------------------------
# Stub detectors for testing
# ---------------------------------------------------------------------------


class StubEmailDetector(BaseDetector):
    name = "email"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        """Finds the literal string 'email@test.com' in text."""
        matches = []
        target = "email@test.com"
        start = 0
        while True:
            idx = text.find(target, start)
            if idx == -1:
                break
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="email_address",
                    category=self.category,
                    start=idx,
                    end=idx + len(target),
                    matched_text=target,
                    severity=self.default_sensitivity,
                )
            )
            start = idx + len(target)
        return matches


class StubPhoneDetector(BaseDetector):
    name = "phone"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        """Finds the literal string '555-1234' in text."""
        matches = []
        target = "555-1234"
        start = 0
        while True:
            idx = text.find(target, start)
            if idx == -1:
                break
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="phone_number",
                    category=self.category,
                    start=idx,
                    end=idx + len(target),
                    matched_text=target,
                    severity=self.default_sensitivity,
                )
            )
            start = idx + len(target)
        return matches


class StubSecretDetector(BaseDetector):
    name = "aws_key"
    category = "secret"
    default_sensitivity = SensitivityLevel.CRITICAL

    def detect(self, text: str) -> list[DetectionMatch]:
        target = "AKIAIOSFODNN7EXAMPLE"
        matches = []
        idx = text.find(target)
        if idx != -1:
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="aws_access_key",
                    category=self.category,
                    start=idx,
                    end=idx + len(target),
                    matched_text=target,
                    severity=self.default_sensitivity,
                )
            )
        return matches


# ---------------------------------------------------------------------------
# BaseDetector abstraction
# ---------------------------------------------------------------------------


class TestBaseDetector:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseDetector()  # type: ignore[abstract]

    def test_stub_implements_interface(self):
        d = StubEmailDetector()
        assert d.name == "email"
        assert d.category == "pii"
        assert d.default_sensitivity == SensitivityLevel.MEDIUM
        assert hasattr(d, "detect")

    def test_detect_returns_list_of_matches(self):
        d = StubEmailDetector()
        result = d.detect("contact email@test.com for info")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], DetectionMatch)

    def test_detect_empty_on_clean_text(self):
        d = StubEmailDetector()
        assert d.detect("no sensitive data here") == []

    def test_repr(self):
        d = StubEmailDetector()
        r = repr(d)
        assert "StubEmailDetector" in r
        assert "email" in r
        assert "pii" in r


# ---------------------------------------------------------------------------
# DetectorRegistry — registration
# ---------------------------------------------------------------------------


class TestRegistryRegistration:
    def test_register_and_all(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        assert len(reg) == 2
        names = [d.name for d in reg.all()]
        assert names == ["email", "phone"]

    def test_duplicate_name_raises(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(StubEmailDetector())

    def test_contains(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        assert "email" in reg
        assert "phone" not in reg

    def test_by_category(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        reg.register(StubSecretDetector())

        pii = reg.by_category("pii")
        assert len(pii) == 2
        assert all(d.category == "pii" for d in pii)

        secrets = reg.by_category("secret")
        assert len(secrets) == 1
        assert secrets[0].name == "aws_key"

        assert reg.by_category("financial") == []

    def test_registration_order_preserved(self):
        reg = DetectorRegistry()
        reg.register(StubPhoneDetector())
        reg.register(StubEmailDetector())
        names = [d.name for d in reg.all()]
        assert names == ["phone", "email"]

    def test_empty_registry(self):
        reg = DetectorRegistry()
        assert len(reg) == 0
        assert reg.all() == []


# ---------------------------------------------------------------------------
# DetectorRegistry — scan_text
# ---------------------------------------------------------------------------


class TestRegistryScanText:
    def test_single_detector(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        matches = reg.scan_text("reach us at email@test.com today")
        assert len(matches) == 1
        assert matches[0].detector_name == "email"
        assert matches[0].matched_text == "email@test.com"

    def test_multiple_detectors_merged(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        text = "call 555-1234 or email email@test.com"
        matches = reg.scan_text(text)
        assert len(matches) == 2
        # Both found
        types = {m.detector_name for m in matches}
        assert types == {"email", "phone"}

    def test_sorted_by_start_offset(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        # Phone appears before email in the text
        text = "call 555-1234 or write email@test.com"
        matches = reg.scan_text(text)
        assert matches[0].start < matches[1].start
        assert matches[0].detector_name == "phone"
        assert matches[1].detector_name == "email"

    def test_multiple_matches_same_detector(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        text = "a email@test.com b email@test.com c"
        matches = reg.scan_text(text)
        assert len(matches) == 2
        assert matches[0].start < matches[1].start

    def test_clean_text_returns_empty(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        assert reg.scan_text("nothing sensitive here") == []

    def test_empty_text(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        assert reg.scan_text("") == []

    def test_empty_registry_returns_empty(self):
        reg = DetectorRegistry()
        assert reg.scan_text("email@test.com") == []

    def test_span_integrity(self):
        """Verify matched_text == text[start:end] for every match."""
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        text = "info: email@test.com phone: 555-1234"
        for m in reg.scan_text(text):
            assert text[m.start:m.end] == m.matched_text, (
                f"Span mismatch for {m.detector_name}: "
                f"text[{m.start}:{m.end}]={text[m.start:m.end]!r} != {m.matched_text!r}"
            )


# ---------------------------------------------------------------------------
# DetectorRegistry — scan_messages
# ---------------------------------------------------------------------------


class TestRegistryScanMessages:
    def test_single_message(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        messages = [Message(role="user", content="email email@test.com")]
        result = reg.scan_messages(messages)

        assert isinstance(result, DetectionResult)
        assert result.has_sensitive_content is True
        assert result.match_count == 1
        assert result.highest_severity == SensitivityLevel.MEDIUM
        assert result.scanned_direction == "input"
        assert result.scan_target == "messages"

    def test_multiple_messages(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubPhoneDetector())
        messages = [
            Message(role="user", content="email email@test.com"),
            Message(role="assistant", content="call 555-1234"),
        ]
        result = reg.scan_messages(messages)
        assert result.match_count == 2
        assert result.categories_found == {"pii"}

    def test_clean_messages(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        messages = [Message(role="user", content="nothing here")]
        result = reg.scan_messages(messages)
        assert result.has_sensitive_content is False
        assert result.match_count == 0
        assert result.highest_severity == SensitivityLevel.NONE

    def test_empty_messages(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        result = reg.scan_messages([])
        assert result.has_sensitive_content is False
        assert result.match_count == 0

    def test_direction_param(self):
        reg = DetectorRegistry()
        result = reg.scan_messages(
            [Message(role="assistant", content="ok")],
            direction="output",
        )
        assert result.scanned_direction == "output"

    def test_summary_across_messages(self):
        reg = DetectorRegistry()
        reg.register(StubEmailDetector())
        reg.register(StubSecretDetector())
        messages = [
            Message(role="user", content="key: AKIAIOSFODNN7EXAMPLE"),
            Message(role="user", content="send to email@test.com"),
        ]
        result = reg.scan_messages(messages)
        assert result.match_count == 2
        assert result.categories_found == {"pii", "secret"}
        assert result.highest_severity == SensitivityLevel.CRITICAL
        assert result.summary == {"aws_access_key": 1, "email_address": 1}


# ---------------------------------------------------------------------------
# get_default_registry
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_returns_registry_instance(self):
        reg = get_default_registry()
        assert isinstance(reg, DetectorRegistry)

    def test_is_fresh_instance(self):
        """Each call returns a new registry (no shared global state)."""
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is not r2

    def test_has_built_in_detectors(self):
        """Default registry contains all built-in detectors.

        Count breakdown (ner_enabled=True default):
          29 always-on detectors
          +1 PresidioDetector (when ``presidio_analyzer`` importable)
          +1 ThaiNameNoPrefixDetector (regex fallback — registered
             regardless of ner_enabled since the Thai-name fix; see
             registry.py for the rationale)
          +1 ThaiNerDetector (FIX37 — Thai NER + context enhancer,
             registered when ``dlp_thai_ner_enabled`` is on, which is
             the shipped default)
          = 32
        """
        reg = get_default_registry()
        assert len(reg) == 32
        assert len(get_default_registry(thai_ner=False)) == 31
