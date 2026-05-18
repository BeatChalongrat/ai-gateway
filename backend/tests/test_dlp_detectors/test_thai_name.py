"""Thai personal name detector — title-prefix gated.

The dedicated ``thai_name`` regex REQUIRES a Thai or English title
prefix (นาย, นาง, นางสาว, ดร., Mr., Mrs., …). Bare names without a
title are picked up by Presidio NER (if the spaCy model is available),
not by this detector — so single-word names are tested via the
overall masking pipeline rather than the named detector itself.
"""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask


_NAME_DETECTORS = ("thai_name", "thai_name_no_prefix", "presidio_ner")


class TestThaiNameHappyPath:
    def test_title_nai(self):
        text = "นาย สมศักดิ์ มีสุข"
        _, _, matches = mask(text)
        assert has_match(matches, *_NAME_DETECTORS)

    def test_title_nang_sao(self):
        text = "นางสาว วิไลพร สุขใส"
        _, _, matches = mask(text)
        assert has_match(matches, *_NAME_DETECTORS)

    def test_academic_title(self):
        text = "ดร.สมชาย ใจดี"
        _, _, matches = mask(text)
        assert has_match(matches, *_NAME_DETECTORS)


class TestThaiNameBoundary:
    def test_name_in_sentence_with_phone(self):
        # Both the name and the phone should be masked — overlap
        # resolution must not collapse them into one token.
        text = "ติดต่อ นายสมชาย ใจดี โทร 081-234-5678"
        _, _, matches = mask(text)
        assert has_match(matches, *_NAME_DETECTORS)
        assert has_match(matches, "phone")


class TestThaiNameNegative:
    def test_place_name(self):
        # "กรุงเทพมหานคร" alone — no title prefix, so thai_name does
        # not fire. NER may flag it as LOCATION/GPE, but those use
        # the [NAME] base — we assert the regex-based detector stays
        # quiet.
        assert "thai_name" not in detector_names_for("กรุงเทพมหานคร")

    def test_common_noun(self):
        assert "thai_name" not in detector_names_for("ธนาคาร")
