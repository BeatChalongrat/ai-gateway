"""Religion / belief detector — PDPA Section 26, personal-context gated.

The keyword list uses ``\\b`` anchors on every English alternation so
substrings like ``follow`` inside ``following`` no longer fire (the
2026-05-13 regression bug). Adjacent matches in the same span are
merged into a single token.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestReligionHappyPath:
    def test_english_conversion(self):
        text = "he converted to Islam after meeting his wife"
        _, span_map, _ = mask(text)
        assert any("RELIGION" in t for t in span_map)

    def test_english_practicing(self):
        text = "she is a practicing Buddhist and meditates daily"
        _, span_map, _ = mask(text)
        assert any("RELIGION" in t for t in span_map)

    def test_thai_personal_with_religion_word(self):
        text = "ผมนับถือศาสนาอิสลาม"
        _, span_map, _ = mask(text)
        assert any("RELIGION" in t for t in span_map)

    def test_thai_explicit_buddhism(self):
        text = "เขานับถือพระพุทธศาสนา"
        _, span_map, _ = mask(text)
        assert any("RELIGION" in t for t in span_map)


class TestReligionNegative:
    def test_the_following_en(self):
        # Regression: ``follow`` inside ``following`` used to trigger
        # the bare-verb branch.
        masked, span_map, _ = mask(
            "Please complete the following tasks"
        )
        assert masked == "Please complete the following tasks"
        assert not any("RELIGION" in t for t in span_map)

    def test_the_following_details(self):
        masked, _, _ = mask("See the following details")
        assert masked == "See the following details"

    def test_unrelated_verb_phrase(self):
        # The detector intentionally fires on ``I believe`` because
        # the keyword regex ties belief verbs to personal pronouns to
        # cover the "I worship", "I pray" cases. We assert here that
        # phrases without any pronoun or belief verb stay quiet.
        masked, span_map, _ = mask("this statement is correct")
        assert not any("RELIGION" in t for t in span_map)

    def test_thai_confidence_word(self):
        # ``ความเชื่อมั่น`` = confidence — substring of ``ความเชื่อ``
        # but personal context check filters it.
        # We assert the detector doesn't lock onto this in isolation
        # (no personal pronoun nearby).
        text = "ความเชื่อมั่นในผลิตภัณฑ์"
        assert "religion_belief" not in detector_names_for(text)
