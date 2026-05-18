"""Thai bank account detector — context-gated, 10-12 digit accounts."""

from __future__ import annotations

from ._helpers import mask


class TestBankAccountHappyPath:
    def test_dashed_form_with_label(self):
        _, span_map, _ = mask("bank account: 123-4-56789-0")
        assert any("BANK_ACCOUNT" in t for t in span_map)

    def test_label_preserved_en(self):
        masked, span_map, _ = mask("Bank account: 123-4-56789-0")
        assert masked.startswith("Bank account:")
        assert "[BANK_ACCOUNT_0]" in masked

    def test_thai_label(self):
        text = "เลขบัญชี: 123-4-56789-0"
        _, span_map, _ = mask(text)
        assert any("BANK_ACCOUNT" in t for t in span_map)


class TestBankAccountBoundary:
    def test_no_separators_with_keyword(self):
        # 10-digit run + ``bank account`` keyword inside the 60-char
        # window — must fire.
        _, span_map, _ = mask("My bank account 1234567890 is closed.")
        assert any("BANK_ACCOUNT" in t for t in span_map)

    def test_thai_keyword_without_separators(self):
        text = "บัญชีธนาคาร 1234567890"
        _, span_map, _ = mask(text)
        assert any("BANK_ACCOUNT" in t for t in span_map)
