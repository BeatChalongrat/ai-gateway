"""Tests for the fallback ``thai_name_no_prefix`` detector and the
``ner_enabled`` registry gate that controls whether it is registered.

Companion to ``test_dlp_all_detectors.py`` / ``test_new_detectors.py``
but kept separate so the fallback semantics are documented in one place.

Rules in force (see the detector's header for the measurements):
  Pass 1  two Thai tokens with a name label / title IMMEDIATELY before
          them (FIX40 — no more ±25-char window);
  Pass 2  gazetteer name + sensitive keyword within ±100 chars;
  Pass 3  person-list items;
  Pass 4  a 10k-lexicon given name at a word boundary, NO context gate
          (FIX29).
Registered regardless of ``ner_enabled`` (see TestRegistryGate).
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
    # FIX44 Part D — these tests last changed in 17eb0c0 (May 2026) and had
    # been describing rules the detector dropped on purpose in FIX26 / 29 /
    # 40. Each one below names the rule that holds TODAY and the round that
    # set it, so the file is documentation again, not a record of what the
    # detector used to do.

    def test_keyword_after_the_pair_no_longer_gates_pass1(self):
        # FIX40 Part C: a context keyword ANYWHERE in ±25 chars used to make
        # two Thai tokens a "given + surname" — which ate ``12 ใบ แตก 3 ใบ``
        # and 29 more shapes in the FIX40 corpus. The anchor must now sit
        # IMMEDIATELY BEFORE the pair (_PASS1_ANCHOR_RE), so ``ลูกค้า``
        # after the pair no longer fires Pass 1. What still catches the
        # given name is Pass 4 (FIX29 lexicon): ``สมชาย`` alone. The
        # surname is deliberately not taken here — FIX40 §3 measured that
        # narrowing Pass 1 costs exactly one item (the ``ผู้รับ:`` shape,
        # which the anchor keeps) and nothing else.
        matches = detector.detect("สมชาย ใจดี ลูกค้า")
        assert [m.matched_text for m in matches] == ["สมชาย"]
        assert matches[0].detector_name == "thai_name_no_prefix"
        assert matches[0].entity_type == "thai_name"
        assert matches[0].priority == -5

    def test_name_label_immediately_before_takes_the_full_name(self):
        # FIX40: a name label right before the pair is the anchor Pass 1
        # keeps. The detector returns the full pair from Pass 1 AND the
        # given name from Pass 4 (FIX29) as two overlapping spans; the
        # transformer's _resolve_overlaps keeps the first of equal
        # action + priority, which is the full name because Pass 1 runs
        # first. Pin both facts: the full name is present, and it comes
        # first.
        matches = detector.detect("ชื่อ สมชาย ใจดี")
        assert matches[0].matched_text == "สมชาย ใจดี"
        assert {m.matched_text for m in matches} == {"สมชาย ใจดี", "สมชาย"}

    def test_pass1_anchor_shape_from_fix40(self):
        # The one shape FIX40 measured Pass 1 still earning its keep on.
        matches = detector.detect("ผู้รับ: สุรชัย มั่นคง")
        assert [m.matched_text for m in matches] == ["สุรชัย มั่นคง"]

    def test_lexicon_name_fires_without_context(self):
        # FIX29 §1 / FIX27: Pass 4 has NO context gate — the measurement
        # showed a gate cost more than half the recall while the
        # dictionary-word right boundary already keeps the 120-sentence
        # corpus at zero fires. So a bare lexicon name IS detected; the
        # old "no keyword anywhere → nothing" rule is gone. The
        # two-word Pass 1 still stays silent here (no left anchor).
        matches = detector.detect("สมชาย ใจดี")
        assert [m.matched_text for m in matches] == ["สมชาย"]

    def test_keyword_far_to_the_right_changes_nothing(self):
        # Follows from the two rules above: distance to a trailing
        # keyword is irrelevant now (FIX40 dropped the window; FIX29's
        # lexicon does not look at context). Same answer as with no
        # keyword at all.
        text = "สมชาย ใจดี " + ("ก" * 40) + " ลูกค้า"
        assert [m.matched_text for m in detector.detect(text)] == ["สมชาย"]

    def test_non_name_pair_with_left_anchor_still_rejected(self):
        # FIX26 §1.2 stop rules survive FIX40: an anchor before a pair
        # whose tokens are plainly not names must not mask them.
        assert detector.detect("ลูกค้า สั่ง ของ") == []

    def test_prefixed_name_without_context_not_detected(self):
        # ``นายสมชาย`` is 8 Thai chars (fits the 2-15 word range),
        # paired with ``ใจดี``. Without a context keyword the detector
        # stays silent — the existing ``thai_name`` detector handles
        # the prefix case at higher priority + sensitivity.
        assert detector.detect("นายสมชาย ใจดี") == []


# ---------------------------------------------------------------------------
# Registry gate — registered REGARDLESS of ``ner_enabled`` since the
# Thai-name fix. Original gate (``not ner_enabled``) was dropped
# because the backend's English-only spaCy model misses bare Thai
# names. The regex is context-gated and ``priority=-5`` so it never
# beats Presidio on overlap — see ``registry.py`` for the rationale.
# ---------------------------------------------------------------------------


class TestRegistryGate:
    def test_enabled_when_ner_enabled_default(self):
        registry = get_default_registry()
        assert "thai_name_no_prefix" in registry

    def test_enabled_when_ner_enabled_explicit(self):
        registry = get_default_registry(ner_enabled=True)
        assert "thai_name_no_prefix" in registry

    def test_enabled_when_ner_disabled(self):
        registry = get_default_registry(ner_enabled=False)
        assert "thai_name_no_prefix" in registry

    def test_disabled_detectors_still_honoured_when_ner_off(self):
        # The operator escape hatch must still work — listing the
        # detector in ``disabled_detectors`` removes it even though it
        # is now always-on by default.
        registry = get_default_registry(
            disabled_detectors=["thai_name_no_prefix"],
            ner_enabled=False,
        )
        assert "thai_name_no_prefix" not in registry

    def test_disabled_detectors_still_honoured_when_ner_on(self):
        # Same escape hatch on the NER-on path — the new always-on
        # registration is overridable via the same mechanism.
        registry = get_default_registry(
            disabled_detectors=["thai_name_no_prefix"],
            ner_enabled=True,
        )
        assert "thai_name_no_prefix" not in registry
