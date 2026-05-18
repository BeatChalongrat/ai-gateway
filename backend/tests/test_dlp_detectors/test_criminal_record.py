"""Criminal record detector — PDPA Section 26."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestCriminalRecordHappyPath:
    def test_english_prior_conviction(self):
        # Need pronoun-anchored personal context per detector design.
        text = "I was convicted of a felony in 2015"
        _, span_map, _ = mask(text)
        assert any("CRIMINAL_RECORD" in t for t in span_map)

    def test_thai_history_label(self):
        text = "ผมมีประวัติอาชญากรรม คดีลักทรัพย์"
        _, span_map, _ = mask(text)
        assert any("CRIMINAL_RECORD" in t for t in span_map)

    def test_thai_drug_case(self):
        text = "เขาเคยต้องหาคดียาเสพติด"
        _, span_map, _ = mask(text)
        assert any("CRIMINAL_RECORD" in t for t in span_map)


class TestCriminalRecordBoundary:
    def test_english_served_time(self):
        text = "he served time in prison for fraud"
        _, span_map, _ = mask(text)
        assert any("CRIMINAL_RECORD" in t for t in span_map)


class TestCriminalRecordNegative:
    def test_academic_law_phrase_en(self):
        # ``criminal law study`` — no personal context.
        assert "criminal_record" not in detector_names_for(
            "criminal law study group"
        )

    def test_law_subject_thai(self):
        # ``กฎหมายอาญา`` is a subject area, not a personal record.
        assert "criminal_record" not in detector_names_for(
            "กฎหมายอาญา"
        )
