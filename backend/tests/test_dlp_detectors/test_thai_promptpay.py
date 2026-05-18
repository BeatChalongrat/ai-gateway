"""Thai PromptPay detector — keyword-gated, mobile or 13-digit ID."""

from __future__ import annotations

from ._helpers import mask

VALID_ID = "3100112345674"  # passes Thai mod-11 (see test_thai_id.py)


def _has_match(matches, *names: str) -> bool:
    wanted = set(names)
    return any(ma.match.detector_name in wanted for ma in matches)


class TestPromptPayHappyPath:
    def test_en_keyword_with_mobile(self):
        _, _, matches = mask("PromptPay: 0812345678")
        assert _has_match(matches, "thai_promptpay")

    def test_thai_keyword_with_mobile(self):
        text = "พร้อมเพย์: 0812345678"
        _, _, matches = mask(text)
        assert _has_match(matches, "thai_promptpay")


class TestPromptPayBoundary:
    def test_id_linked_promptpay(self):
        # 13-digit Thai national ID can also serve as a PromptPay
        # identifier — the keyword gate plus the mod-11 check is
        # enough to disambiguate from arbitrary 13-digit runs.
        text = f"พร้อมเพย์เลขบัตร {VALID_ID}"
        _, _, matches = mask(text)
        # Either PROMPTPAY or THAI_ID may win the overlap; both prove
        # the number is masked.
        assert _has_match(matches, "thai_promptpay", "thai_national_id")


class TestPromptPayNegative:
    def test_bank_name_no_account(self):
        masked, span_map, _ = mask("ธนาคารกรุงไทย")
        # No digits, no PromptPay token, no mask.
        assert not span_map
