"""Phone detector — formatted, bare-with-context, Thai landline."""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in


class TestPhoneHappyPath:
    def test_formatted_thai_mobile(self):
        _, span_map, _ = mask("+66 81-234-5678")
        assert tokens_in(span_map, "PHONE_NUMBER") == ["[PHONE_NUMBER_0]"]

    def test_label_preserved_en(self):
        masked, span_map, _ = mask("Phone: +66 81-234-5678")
        assert masked.startswith("Phone:")
        assert "[PHONE_NUMBER_0]" in masked

    def test_thai_label(self):
        text = "เบอร์โทร: 081-234-5678"
        _, span_map, _ = mask(text)
        assert any("PHONE_NUMBER" in t for t in span_map)

    def test_thai_landline_with_context(self):
        text = "ติดต่อได้ที่ 02-123-4567"
        _, span_map, _ = mask(text)
        assert any("PHONE_NUMBER" in t for t in span_map)


class TestPhoneBoundary:
    def test_us_formatted(self):
        _, span_map, _ = mask("+1 (555) 123-4567")
        assert any("PHONE_NUMBER" in t for t in span_map)

    def test_bare_mobile_with_context_en(self):
        # 10-digit bare run only fires when a phone-context keyword
        # appears within ±25 chars of the digits.
        _, span_map, _ = mask("Please call 0812345678")
        assert any("PHONE_NUMBER" in t for t in span_map)

    def test_thai_landline_bangkok(self):
        _, span_map, _ = mask("Office: 02-123-4567")
        assert any("PHONE_NUMBER" in t for t in span_map)

    def test_thai_bare_mobile_with_keyword(self):
        text = "โทร 0812345678"
        _, span_map, _ = mask(text)
        assert any("PHONE_NUMBER" in t for t in span_map)

    def test_thai_mobile_with_plus_country_code(self):
        text = "เบอร์มือถือ +66812345678"
        _, span_map, _ = mask(text)
        assert any("PHONE_NUMBER" in t for t in span_map)


class TestPhoneNegative:
    def test_five_digits(self):
        assert "phone" not in detector_names_for("12345")

    def test_seven_digits_no_context(self):
        # 7 digits is below the bare-digit floor (10) AND has no context
        # keyword — must not fire.
        assert "phone" not in detector_names_for("1234567")
