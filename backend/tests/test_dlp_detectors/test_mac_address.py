"""MAC address detector — 6-group colon- or hyphen-separated hex."""

from __future__ import annotations

from ._helpers import detector_names_for, has_match, mask


class TestMacAddressHappyPath:
    def test_colon_separated(self):
        masked, _, matches = mask("MAC: 00:1A:2B:3C:4D:5E")
        assert masked.startswith("MAC:")
        assert has_match(matches, "mac_address")

    def test_hyphen_separated(self):
        _, _, matches = mask("00-1A-2B-3C-4D-5E")
        assert has_match(matches, "mac_address")

    def test_thai_label(self):
        text = "MAC แอดเดรส: 00:1A:2B:3C:4D:5E"
        _, _, matches = mask(text)
        assert has_match(matches, "mac_address")


class TestMacAddressNegative:
    def test_no_separators_not_detected(self):
        # The detector requires colon or hyphen separators — bare hex
        # runs are too noisy to match safely.
        assert "mac_address" not in detector_names_for("001A2B3C4D5E")

    def test_null_mac(self):
        # Documented placeholder value — must not fire.
        assert "mac_address" not in detector_names_for("00:00:00:00:00:00")

    def test_broadcast_mac(self):
        assert "mac_address" not in detector_names_for("FF:FF:FF:FF:FF:FF")
