"""Passport detector — context-keyword gated."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


def _has_passport(matches) -> bool:
    return any(ma.match.detector_name == "passport" for ma in matches)


class TestPassportHappyPath:
    def test_label_then_value(self):
        _, _, matches = mask("passport AA1234567")
        assert _has_passport(matches)

    def test_label_preserved_en(self):
        masked, _, _ = mask("Passport: AA1234567")
        assert "Passport:" in masked
        assert "<r0>" in masked

    def test_thai_label(self):
        text = "หนังสือเดินทาง: AA1234567"
        _, _, matches = mask(text)
        assert _has_passport(matches)


class TestPassportBoundary:
    def test_one_letter_eight_digits(self):
        _, _, matches = mask("passport A12345678")
        assert _has_passport(matches)

    def test_lowercase_value_en(self):
        # IGNORECASE regex — lowercase value is accepted when adjacent
        # to a context keyword.
        _, _, matches = mask("passport aa1234567")
        assert _has_passport(matches)

    def test_thai_short_label(self):
        text = "พาสปอร์ต AA1234567"
        _, _, matches = mask(text)
        assert _has_passport(matches)


class TestPassportNegative:
    def test_letters_only_no_context(self):
        assert "passport" not in detector_names_for("ABCDEFGHI")

    def test_bare_alphanumeric_no_context(self):
        # Without a passport keyword nearby, even a well-shaped value
        # must not fire — that's the whole point of the keyword gate.
        assert "passport" not in detector_names_for("AA1234567")
