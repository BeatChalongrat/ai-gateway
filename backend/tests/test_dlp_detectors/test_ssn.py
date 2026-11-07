"""US SSN detector — dashed XXX-XX-XXXX with SSA validation.

The detector intentionally requires dashes. Bare 9-digit runs collide
with account numbers, zip-codes, and other identifiers — they're left
to the phone detector (which gates on a context keyword) or to
specialised detectors. The spec's space-separated and
no-separator variants therefore must NOT detect.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestSsnHappyPath:
    def test_dashed_ssn(self):
        _, _, matches = mask("SSN: 123-45-6789")
        assert any(ma.match.detector_name == "ssn" for ma in matches)

    def test_label_preserved_en(self):
        masked, _, _ = mask("Social Security: 123-45-6789")
        assert "Social Security:" in masked
        assert "<r0>" in masked


class TestSsnNegative:
    def test_all_zero_area(self):
        # Area = 000 is invalid per SSA rules.
        assert "ssn" not in detector_names_for("000-00-0000")

    def test_invalid_area_666(self):
        # Area = 666 is invalid.
        assert "ssn" not in detector_names_for("666-45-6789")

    def test_eight_digit_run(self):
        # 8 digits total — wrong shape.
        assert "ssn" not in detector_names_for("123-45-678")

    def test_no_dashes_not_detected(self):
        # Bare 9-digit run intentionally not matched.
        assert "ssn" not in detector_names_for("123456789")

    def test_spaces_not_detected(self):
        # Spaces also intentionally not matched.
        assert "ssn" not in detector_names_for("123 45 6789")
