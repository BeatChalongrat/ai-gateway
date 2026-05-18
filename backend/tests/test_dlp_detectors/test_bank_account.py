"""Thai bank account detector — context-gated, 10-12 digit accounts."""

from __future__ import annotations

from ._helpers import mask


def _has_bank(matches) -> bool:
    return any(ma.match.detector_name == "thai_bank_account" for ma in matches)


class TestBankAccountHappyPath:
    def test_dashed_form_with_label(self):
        _, _, matches = mask("bank account: 123-4-56789-0")
        assert _has_bank(matches)

    def test_label_preserved_en(self):
        masked, _, _ = mask("Bank account: 123-4-56789-0")
        assert masked.startswith("Bank account:")
        assert "<r0>" in masked

    def test_thai_label(self):
        text = "เลขบัญชี: 123-4-56789-0"
        _, _, matches = mask(text)
        assert _has_bank(matches)


class TestBankAccountBoundary:
    def test_no_separators_with_keyword(self):
        # 10-digit run + ``bank account`` keyword inside the 60-char
        # window — must fire.
        _, _, matches = mask("My bank account 1234567890 is closed.")
        assert _has_bank(matches)

    def test_thai_keyword_without_separators(self):
        text = "บัญชีธนาคาร 1234567890"
        _, _, matches = mask(text)
        assert _has_bank(matches)
