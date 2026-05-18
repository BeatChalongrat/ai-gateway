"""Income / salary detector — label preserved on mask."""

from __future__ import annotations

from ._helpers import detector_names_for, mask


def _matches_for(matches, name: str):
    return [ma for ma in matches if ma.match.detector_name == name]


class TestIncomeHappyPath:
    def test_label_preserved_en(self):
        masked, _, _ = mask("Salary: 85,000 THB/month")
        assert masked.startswith("Salary:")
        assert "<r0>" in masked

    def test_income_label_en(self):
        # The detector matches the numeric value, optional currency
        # tail and optional /month — the dollar sign is not in the
        # label/value regex, so this test asserts the detector either
        # picks up the digit run via the "5000" branch or stays quiet.
        # At minimum the label must survive.
        masked, _, _ = mask("Income: 5000")
        assert masked.startswith("Income:")
        assert "<r0>" in masked

    def test_thai_label(self):
        masked, _, matches = mask("เงินเดือน: 85,000 บาท/เดือน")
        assert "เงินเดือน:" in masked
        assert _matches_for(matches, "income")

    def test_thai_rai_dai_label(self):
        text = "รายได้: 50,000 บาทต่อเดือน"
        _, _, matches = mask(text)
        assert _matches_for(matches, "income")


class TestIncomeBoundary:
    def test_does_not_swallow_password_line(self):
        # INCOME label fires on Salary and stops before the newline,
        # leaving Password: to its own detector.
        text = "Salary: 120,000 THB/month\nPassword: P@ssw0rd"
        _, _, matches = mask(text)
        assert _matches_for(matches, "income")
        assert _matches_for(matches, "password_field")

    def test_thai_two_amounts(self):
        # Only the salary amount carries an income label — the rent
        # amount is not labelled and must not fire.
        text = "เงินเดือน 85,000 บาท ค่าเช่าบ้าน 15,000 บาท"
        _, _, matches = mask(text)
        # Exactly one INCOME match.
        assert len(_matches_for(matches, "income")) == 1


class TestIncomeNegative:
    def test_refund_amount_no_label(self):
        assert "income" not in detector_names_for(
            "refund amount 85,000 THB"
        )

    def test_total_cost_no_label(self):
        assert "income" not in detector_names_for(
            "total cost 50,000 THB"
        )
