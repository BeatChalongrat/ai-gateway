"""Thai national ID detector — checksum-validated + keyword-gated.

Verified-valid 13-digit IDs (mod-11 checksum passes):
- ``3100112345674``  used by ``test_dlp_all_detectors`` (TC1_THAI_MEGA)
- ``3100112345671``  same first 12 digits, wrong check digit — invalid

The well-known "1101700203451" example from training docs is actually
INVALID under the mod-11 check (expected check digit 0, supplied 1).
The keyword-gated path will still mask it because the operator
explicitly labelled the field — that branch is also tested below.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in

VALID_ID = "3100112345674"
INVALID_ID = "3100112345671"  # wrong check digit
NOT_A_VALID_ID_BUT_LABELLED = "1101700203451"  # fails checksum


class TestThaiIdHappyPath:
    def test_bare_valid_id_en(self):
        _, span_map, _ = mask(VALID_ID)
        assert tokens_in(span_map, "THAI_ID") == ["[THAI_ID_0]"]

    def test_label_preserved_en(self):
        masked, span_map, _ = mask(f"Thai ID: {VALID_ID}")
        assert "Thai ID:" in masked
        assert "[THAI_ID_0]" in masked

    def test_thai_label(self):
        text = f"เลขบัตรประชาชน: {VALID_ID}"
        _, span_map, _ = mask(text)
        assert any("THAI_ID" in t for t in span_map)

    def test_dashed_form_thai(self):
        text = "บัตรประชาชนเลข 3-1001-12345-67-4"
        _, span_map, _ = mask(text)
        assert any("THAI_ID" in t for t in span_map)


class TestThaiIdBoundary:
    def test_dashed_form_en(self):
        _, span_map, _ = mask("3-1001-12345-67-4")
        assert any("THAI_ID" in t for t in span_map)

    def test_thai_long_label(self):
        text = f"หมายเลขประจำตัวประชาชน {VALID_ID}"
        _, span_map, _ = mask(text)
        assert any("THAI_ID" in t for t in span_map)

    def test_keyword_overrides_checksum(self):
        # The keyword-gated path masks even when the checksum fails —
        # an operator-labelled ID field is itself the signal.
        masked, span_map, _ = mask(
            f"เลขบัตรประชาชน: {NOT_A_VALID_ID_BUT_LABELLED}"
        )
        assert any("THAI_ID" in t for t in span_map)


class TestThaiIdNegative:
    def test_invalid_checksum_bare(self):
        # Bare 13-digit run with no keyword — checksum gate must reject.
        assert "thai_national_id" not in detector_names_for(INVALID_ID)

    def test_twelve_digits(self):
        assert "thai_national_id" not in detector_names_for("310011234567")

    def test_fourteen_digits(self):
        assert "thai_national_id" not in detector_names_for("31001123456741")
