"""Tests for the fallback ``thai_name_no_prefix`` detector and the
``ner_enabled`` registry gate that controls whether it is registered.

Companion to ``test_dlp_all_detectors.py`` / ``test_new_detectors.py``
but kept separate so the fallback semantics — only-when-NER-is-off
plus context-keyword gating — are documented in one place.
"""

from __future__ import annotations

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_name_no_prefix import (
    ThaiNameNoPrefixDetector,
)


detector = ThaiNameNoPrefixDetector()


# ---------------------------------------------------------------------------
# Detector regex + context-gate behaviour
# ---------------------------------------------------------------------------


class TestDetection:
    def test_name_with_context_keyword_detected(self):
        # ``ลูกค้า`` is a context keyword — appears AFTER the two-word
        # Thai name, so the bare-name regex fires.
        matches = detector.detect("สมชาย ใจดี ลูกค้า")
        assert len(matches) == 1
        assert matches[0].matched_text == "สมชาย ใจดี"
        assert matches[0].detector_name == "thai_name_no_prefix"
        assert matches[0].entity_type == "thai_name"
        assert matches[0].priority == -5

    def test_name_with_keyword_before_match_detected(self):
        # Keyword on the left side of the match is also enough — the
        # context window scans both sides.
        matches = detector.detect("ชื่อ สมชาย ใจดี")
        assert len(matches) == 1
        assert matches[0].matched_text == "สมชาย ใจดี"

    def test_bare_name_without_context_not_detected(self):
        # No keyword anywhere → the regex hit is discarded. This is the
        # core false-positive guard: arbitrary two-word Thai phrases
        # must not be masked.
        assert detector.detect("สมชาย ใจดี") == []

    def test_keyword_outside_window_not_detected(self):
        # Keyword sits past the ±25-char context window from the name
        # so the gate refuses the match.
        text = "สมชาย ใจดี " + ("ก" * 40) + " ลูกค้า"
        assert detector.detect(text) == []

    def test_prefixed_name_without_context_not_detected(self):
        # ``นายสมชาย`` is 8 Thai chars (fits the 2-15 word range),
        # paired with ``ใจดี``. Without a context keyword the detector
        # stays silent — the existing ``thai_name`` detector handles
        # the prefix case at higher priority + sensitivity.
        assert detector.detect("นายสมชาย ใจดี") == []


# ---------------------------------------------------------------------------
# Registry gate — only registered when ner_enabled is False
# ---------------------------------------------------------------------------


class TestRegistryGate:
    def test_disabled_when_ner_enabled_default(self):
        registry = get_default_registry()
        assert "thai_name_no_prefix" not in registry

    def test_disabled_when_ner_enabled_explicit(self):
        registry = get_default_registry(ner_enabled=True)
        assert "thai_name_no_prefix" not in registry

    def test_enabled_when_ner_disabled(self):
        registry = get_default_registry(ner_enabled=False)
        assert "thai_name_no_prefix" in registry

    def test_disabled_detectors_still_honoured_when_ner_off(self):
        # The ner_enabled=False path adds thai_name_no_prefix on top
        # of the standard set, but the disabled_detectors list must
        # still be able to remove it (operator escape hatch).
        registry = get_default_registry(
            disabled_detectors=["thai_name_no_prefix"],
            ner_enabled=False,
        )
        assert "thai_name_no_prefix" not in registry
