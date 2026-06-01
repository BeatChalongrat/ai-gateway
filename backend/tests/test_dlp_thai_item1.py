"""P1 Item 1 — Thai NER false-positive guard + assistant-role NER skip.

Covers:
* presidio_ner skips predominantly-Thai text when no Thai model is
  configured (dlp_ner_thai_enabled=False), so English-only spaCy NER
  never mis-tags Thai greetings/verbs as PERSON/NRP — while real English
  names embedded in mostly-English text still get detected.
* the regex Thai PII detectors (phone, email) still fire on Thai text.
* scan_messages(ner_skip_roles={"assistant"}) drops NER findings on
  assistant turns but keeps regex findings.
"""

from __future__ import annotations

import pytest

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.schemas.common import Message
from app.services.dlp.detectors.base import BaseDetector
from app.services.dlp.detectors.presidio_ner import PresidioDetector
from app.services.dlp.detectors.registry import DetectorRegistry


# --------------------------------------------------------------------------
# Fake Presidio analyzer — returns a PERSON span covering the whole text so
# we can prove the Thai guard fires *before* the analyzer is consulted.
# --------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, start, end, entity_type="PERSON", score=0.99):
        self.start = start
        self.end = end
        self.entity_type = entity_type
        self.score = score


class _FakeAnalyzer:
    supported_languages = ["en"]

    def analyze(self, *, text, language, entities, score_threshold):
        # Tag the first whitespace-delimited token as a PERSON.
        token = text.split(" ", 1)[0] if text else ""
        return [_FakeResult(0, len(token))] if token else []


@pytest.fixture
def _fake_analyzer(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ner_service_url", "", raising=False)
    monkeypatch.setattr(
        "app.services.dlp.ner_status.get_analyzer", lambda: _FakeAnalyzer()
    )


class TestThaiNerSkip:
    def test_predominantly_thai_text_skips_ner(self, _fake_analyzer, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "dlp_ner_thai_enabled", False, raising=False)
        thai = "สวัสดีครับ คุณสมชาย ช่วยดูอัตราแลกเปลี่ยนหน่อย"
        assert PresidioDetector().detect(thai) == []

    def test_english_text_still_runs_ner(self, _fake_analyzer, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "dlp_ner_thai_enabled", False, raising=False)
        # Mostly English → guard does not fire → fake analyzer tags "John".
        matches = PresidioDetector().detect("John went to the market today")
        assert any(m.matched_text == "John" for m in matches)

    def test_thai_ner_runs_when_enabled(self, _fake_analyzer, monkeypatch):
        from app.core.config import settings

        # Operator enabled a Thai model → guard is bypassed, NER runs.
        monkeypatch.setattr(settings, "dlp_ner_thai_enabled", True, raising=False)
        thai = "สมชาย ใจดี มาที่นี่"
        matches = PresidioDetector().detect(thai)
        assert matches, "with Thai NER enabled the analyzer should run"


class TestThaiRegexStillWorks:
    def test_thai_text_with_phone_and_email_detected(self):
        # ner_enabled=False keeps presidio out so this is a pure regex check;
        # the regex detectors are language-agnostic and must fire on Thai.
        from app.services.dlp.detectors.registry import get_default_registry

        reg = get_default_registry(ner_enabled=False)
        text = "ติดต่อ 081-234-5678 หรืออีเมล somchai@example.com"
        matches = reg.scan_text(text)
        detectors = {m.detector_name for m in matches}
        assert "phone" in detectors
        assert "email" in detectors


# --------------------------------------------------------------------------
# ner_skip_roles — drop NER on assistant turns, keep regex.
# --------------------------------------------------------------------------


class _FakeNer(BaseDetector):
    name = "presidio_ner"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text, **kwargs):
        if not text:
            return []
        return [
            DetectionMatch(
                detector_name=self.name,
                entity_type="person",
                category=self.category,
                start=0,
                end=len(text.split(" ", 1)[0]),
                matched_text=text.split(" ", 1)[0],
                severity=self.default_sensitivity,
                confidence=0.99,
            )
        ]


class _FakeEmail(BaseDetector):
    name = "email"
    category = "pii"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text, **kwargs):
        idx = text.find("@")
        if idx == -1:
            return []
        return [
            DetectionMatch(
                detector_name=self.name,
                entity_type="email",
                category=self.category,
                start=0,
                end=len(text),
                matched_text=text,
                severity=self.default_sensitivity,
                confidence=1.0,
            )
        ]


def _registry_with_fakes() -> DetectorRegistry:
    reg = DetectorRegistry()
    reg.register(_FakeNer())
    reg.register(_FakeEmail())
    return reg


class TestNerSkipRoles:
    def test_assistant_ner_dropped_regex_kept(self):
        reg = _registry_with_fakes()
        msgs = [Message(role="assistant", content="Bob said hi a@b.com")]
        result = reg.scan_messages(msgs, ner_skip_roles=frozenset({"assistant"}))
        names = {m.detector_name for m in result.matches}
        assert "presidio_ner" not in names, "assistant NER must be dropped"
        assert "email" in names, "regex detectors must still run on assistant"

    def test_user_ner_kept_by_default(self):
        reg = _registry_with_fakes()
        msgs = [Message(role="user", content="Bob said hi a@b.com")]
        result = reg.scan_messages(msgs, ner_skip_roles=frozenset({"assistant"}))
        names = {m.detector_name for m in result.matches}
        assert "presidio_ner" in names, "user NER must still run"
        assert "email" in names
