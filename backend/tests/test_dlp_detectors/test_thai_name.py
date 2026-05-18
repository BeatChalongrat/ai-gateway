"""Thai personal name detector — title-prefix gated.

The dedicated ``thai_name`` regex REQUIRES a Thai or English title
prefix (นาย, นาง, นางสาว, ดร., Mr., Mrs., …). Bare names without a
title are picked up by Presidio NER (if the spaCy model is available),
not by this detector — so single-word names are tested via the
overall masking pipeline rather than the named detector itself.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestThaiNameHappyPath:
    def test_title_nai(self):
        text = "นาย สมศักดิ์ มีสุข"
        _, span_map, _ = mask(text)
        assert any("THAI_NAME" in t or "NAME" in t for t in span_map)

    def test_title_nang_sao(self):
        text = "นางสาว วิไลพร สุขใส"
        _, span_map, _ = mask(text)
        assert any("THAI_NAME" in t or "NAME" in t for t in span_map)

    def test_academic_title(self):
        text = "ดร.สมชาย ใจดี"
        _, span_map, _ = mask(text)
        assert any("THAI_NAME" in t or "NAME" in t for t in span_map)


class TestThaiNameBoundary:
    def test_name_in_sentence_with_phone(self):
        # Both the name and the phone should be masked — overlap
        # resolution must not collapse them into one token.
        text = "ติดต่อ นายสมชาย ใจดี โทร 081-234-5678"
        _, span_map, _ = mask(text)
        has_name = any("NAME" in t for t in span_map)
        has_phone = any("PHONE_NUMBER" in t for t in span_map)
        assert has_name and has_phone


class TestThaiNameNegative:
    def test_place_name(self):
        # "กรุงเทพมหานคร" alone — no title prefix, so thai_name does
        # not fire. NER may flag it as LOCATION/GPE, but those use
        # the [NAME] base — we assert the regex-based detector stays
        # quiet.
        assert "thai_name" not in detector_names_for("กรุงเทพมหานคร")

    def test_common_noun(self):
        assert "thai_name" not in detector_names_for("ธนาคาร")
