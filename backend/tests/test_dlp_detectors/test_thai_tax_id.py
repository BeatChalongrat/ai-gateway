"""Thai tax ID detector — context-gated, checksum-validated.

Same regex + checksum as ``thai_national_id`` but only fires when a
tax-related keyword (``เลขประจำตัวผู้เสียภาษี``, ``tax id``, ``tin`` …)
appears within ~60 chars. Without that gate, every valid Thai national
ID would also surface as a tax ID — they share the 13-digit shape.

The detector inherits ``_thai_id_valid`` which rejects 13-digit runs
starting with ``0``, so the canonical "0105562000001" corporate-tax
example does NOT actually fire — we use a valid mod-11 ID instead.
"""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask

VALID_ID = "3100112345674"
_ID_DETECTORS = ("thai_tax_id", "thai_national_id")


class TestThaiTaxIdHappyPath:
    def test_en_tax_label(self):
        _, _, matches = mask(f"Tax ID: {VALID_ID}")
        # Either thai_tax_id or thai_national_id can mask this — both
        # paths target the same span.
        assert has_match(matches, *_ID_DETECTORS)

    def test_thai_tax_label(self):
        text = f"เลขประจำตัวผู้เสียภาษี: {VALID_ID}"
        _, _, matches = mask(text)
        assert has_match(matches, *_ID_DETECTORS)


class TestThaiTaxIdBoundary:
    def test_dashed_form(self):
        _, _, matches = mask("Tax ID: 3-1001-12345-67-4")
        assert has_match(matches, *_ID_DETECTORS)


class TestThaiTaxIdNegative:
    def test_too_short(self):
        # 12 digits — wrong shape regardless of keyword.
        assert "thai_tax_id" not in detector_names_for(
            "Tax ID: 310011234567"
        )

    def test_no_tax_keyword(self):
        # Bare valid Thai ID without tax context — must not be
        # categorized as a tax ID (thai_national_id may still fire,
        # which is fine, but thai_tax_id specifically must not).
        assert "thai_tax_id" not in detector_names_for(VALID_ID)
