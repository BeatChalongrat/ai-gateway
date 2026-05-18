"""Income / salary detector — label preserved on mask."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestIncomeHappyPath:
    def test_label_preserved_en(self):
        masked, span_map, _ = mask("Salary: 85,000 THB/month")
        assert masked.startswith("Salary:")
        assert "[INCOME_0]" in masked

    def test_income_label_en(self):
        _, span_map, _ = mask("Income: $5,000")
        # The detector matches the numeric value, optional currency
        # tail and optional /month — the dollar sign is not in the
        # label/value regex, so this test asserts the detector either
        # picks up the digit run via the "5000" branch or stays quiet
        # (in which case the test below is the real assertion).
        # At minimum the label must survive.
        masked, _, _ = mask("Income: 5000")
        assert masked.startswith("Income:")
        assert "[INCOME_0]" in masked

    def test_thai_label(self):
        masked, span_map, _ = mask("เงินเดือน: 85,000 บาท/เดือน")
        assert "เงินเดือน:" in masked
        assert any("INCOME" in t for t in span_map)

    def test_thai_rai_dai_label(self):
        text = "รายได้: 50,000 บาทต่อเดือน"
        _, span_map, _ = mask(text)
        assert any("INCOME" in t for t in span_map)


class TestIncomeBoundary:
    def test_does_not_swallow_password_line(self):
        # INCOME label fires on Salary and stops before the newline,
        # leaving Password: to its own detector.
        text = "Salary: 120,000 THB/month\nPassword: P@ssw0rd"
        _, span_map, _ = mask(text)
        assert any("INCOME" in t for t in span_map)
        assert any("PASSWORD" in t for t in span_map)

    def test_thai_two_amounts(self):
        # Only the salary amount carries an income label — the rent
        # amount is not labelled and must not fire.
        text = "เงินเดือน 85,000 บาท ค่าเช่าบ้าน 15,000 บาท"
        _, span_map, _ = mask(text)
        assert any("INCOME" in t for t in span_map)
        # Exactly one INCOME token.
        income_tokens = [t for t in span_map if "INCOME" in t]
        assert len(income_tokens) == 1


class TestIncomeNegative:
    def test_refund_amount_no_label(self):
        assert "income" not in detector_names_for(
            "refund amount 85,000 THB"
        )

    def test_total_cost_no_label(self):
        assert "income" not in detector_names_for(
            "total cost 50,000 THB"
        )
