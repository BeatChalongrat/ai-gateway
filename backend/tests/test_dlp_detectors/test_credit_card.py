"""Credit card detector — Luhn-validated."""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in

VISA = "4111 1111 1111 1111"
VISA_NO_SPACES = "4111111111111111"
VISA_DASHED = "4111-1111-1111-1111"
AMEX = "3714 496353 98431"  # 15-digit Amex, valid Luhn

# Luhn-INVALID variants for the negative cases.
VISA_BAD = "4111 1111 1111 1112"
FAKE_CARD = "1234 5678 9012 3456"


class TestCreditCardHappyPath:
    def test_visa_with_spaces(self):
        _, span_map, _ = mask(VISA)
        assert any("CREDIT_CARD" in t for t in span_map)

    def test_label_preserved_en(self):
        masked, span_map, _ = mask(f"Credit card: {VISA}")
        assert masked.startswith("Credit card:")
        assert "[CREDIT_CARD_0]" in masked

    def test_thai_label(self):
        text = f"หมายเลขบัตรเครดิต: {VISA}"
        _, span_map, _ = mask(text)
        assert any("CREDIT_CARD" in t for t in span_map)


class TestCreditCardBoundary:
    def test_no_separators(self):
        _, span_map, _ = mask(VISA_NO_SPACES)
        assert any("CREDIT_CARD" in t for t in span_map)

    def test_dashed(self):
        _, span_map, _ = mask(VISA_DASHED)
        assert any("CREDIT_CARD" in t for t in span_map)

    def test_amex_15_digit(self):
        _, span_map, _ = mask(AMEX)
        assert any("CREDIT_CARD" in t for t in span_map)

    def test_thai_label_no_spaces(self):
        text = f"บัตรเครดิต {VISA_NO_SPACES}"
        _, span_map, _ = mask(text)
        assert any("CREDIT_CARD" in t for t in span_map)


class TestCreditCardNegative:
    def test_luhn_invalid_variant(self):
        assert "credit_card" not in detector_names_for(VISA_BAD)

    def test_luhn_invalid_random(self):
        assert "credit_card" not in detector_names_for(FAKE_CARD)
