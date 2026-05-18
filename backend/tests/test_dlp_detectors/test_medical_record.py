"""Medical record number detector — keyword-gated 6-10 digit IDs."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestMedicalRecordHappyPath:
    def test_hn_label(self):
        _, span_map, _ = mask("HN: 1234567")
        assert any("MEDICAL_RECORD" in t for t in span_map)

    def test_hn_prefix(self):
        _, span_map, _ = mask("HN 1234567")
        assert any("MEDICAL_RECORD" in t for t in span_map)

    def test_thai_label(self):
        text = "เลขที่ผู้ป่วย: 1234567"
        _, span_map, _ = mask(text)
        assert any("MEDICAL_RECORD" in t for t in span_map)


class TestMedicalRecordBoundary:
    def test_with_hospital_context(self):
        text = "ผู้ป่วย HN: 00123456 โรงพยาบาลสมิติเวช"
        _, span_map, _ = mask(text)
        assert any("MEDICAL_RECORD" in t for t in span_map)


class TestMedicalRecordNegative:
    def test_too_short_no_label(self):
        # 5 digits is below the floor, no keyword either.
        assert "medical_record" not in detector_names_for("12345")

    def test_six_digits_no_keyword(self):
        # 6 digits meets the length floor but the keyword gate must
        # still reject it without context.
        assert "medical_record" not in detector_names_for("123456")
