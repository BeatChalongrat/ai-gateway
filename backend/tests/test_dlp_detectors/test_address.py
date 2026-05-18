"""English address detector — label + address-like value.

The regex requires both a label (``address``, optionally prefixed
``home``/``billing``/``shipping``) AND a colon between the label and
the value. Bare prose like "I walked on Sukhumvit Road" does not fire.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in


class TestAddressHappyPath:
    def test_thai_bangkok_address(self):
        _, span_map, matches = mask(
            "address: 99/88 Sukhumvit Road, Bangkok 10110"
        )
        assert tokens_in(span_map, "ADDRESS", matches)

    def test_us_style_address(self):
        masked, _, _ = mask(
            "Address: 123 Main Street, New York, NY 10001"
        )
        assert "Address:" in masked
        assert "<r0>" in masked


class TestAddressNegative:
    def test_verb_usage(self):
        # "address the issue" — verb, not a postal field — must not
        # fire.
        assert "address" not in detector_names_for(
            "Please address the issue with the team."
        )

    def test_label_without_value(self):
        assert "address" not in detector_names_for("address:")
