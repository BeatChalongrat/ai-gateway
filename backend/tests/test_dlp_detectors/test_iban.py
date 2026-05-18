"""IBAN detector — ISO 13616 mod-97 validated.

The regex matches uppercase country code + check digits + alphanumeric
body. Lowercase IBANs are NOT supported (the body must match
``[A-Z0-9]`` — the regex is intentionally not case-insensitive so we
don't widen the false-positive surface for prose).
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestIbanHappyPath:
    def test_gb_with_spaces(self):
        _, span_map, _ = mask("GB82 WEST 1234 5698 7654 32")
        assert any("IBAN" in t for t in span_map)

    def test_label_preserved_en(self):
        masked, _, _ = mask("IBAN: DE89 3704 0044 0532 0130 00")
        assert "IBAN:" in masked
        assert "[IBAN_0]" in masked


class TestIbanBoundary:
    def test_no_spaces(self):
        _, span_map, _ = mask("GB82WEST12345698765432")
        assert any("IBAN" in t for t in span_map)


class TestIbanNegative:
    def test_invalid_checksum(self):
        # Same shape as GB82 example but corrupted final group →
        # mod-97 must reject.
        assert "iban" not in detector_names_for(
            "GB82 WEST 1234 5698 7654 99"
        )
