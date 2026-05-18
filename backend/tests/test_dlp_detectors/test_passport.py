"""Passport detector — context-keyword gated."""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in


class TestPassportHappyPath:
    def test_label_then_value(self):
        _, span_map, _ = mask("passport AA1234567")
        assert any("PASSPORT" in t for t in span_map)

    def test_label_preserved_en(self):
        masked, span_map, _ = mask("Passport: AA1234567")
        assert "Passport:" in masked
        assert "[PASSPORT_0]" in masked

    def test_thai_label(self):
        text = "หนังสือเดินทาง: AA1234567"
        _, span_map, _ = mask(text)
        assert any("PASSPORT" in t for t in span_map)


class TestPassportBoundary:
    def test_one_letter_eight_digits(self):
        _, span_map, _ = mask("passport A12345678")
        assert any("PASSPORT" in t for t in span_map)

    def test_lowercase_value_en(self):
        # IGNORECASE regex — lowercase value is accepted when adjacent
        # to a context keyword.
        _, span_map, _ = mask("passport aa1234567")
        assert any("PASSPORT" in t for t in span_map)

    def test_thai_short_label(self):
        text = "พาสปอร์ต AA1234567"
        _, span_map, _ = mask(text)
        assert any("PASSPORT" in t for t in span_map)


class TestPassportNegative:
    def test_letters_only_no_context(self):
        assert "passport" not in detector_names_for("ABCDEFGHI")

    def test_bare_alphanumeric_no_context(self):
        # Without a passport keyword nearby, even a well-shaped value
        # must not fire — that's the whole point of the keyword gate.
        assert "passport" not in detector_names_for("AA1234567")
