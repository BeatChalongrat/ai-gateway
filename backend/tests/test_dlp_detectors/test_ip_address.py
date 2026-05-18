"""IP address detector — IPv4 + IPv6."""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in


class TestIpAddressHappyPath:
    def test_ipv4_lan(self):
        _, span_map, _ = mask("192.168.1.105")
        assert tokens_in(span_map, "IP_ADDRESS") == ["[IP_ADDRESS_0]"]

    def test_two_distinct_ipv4s(self):
        _, span_map, _ = mask("Servers 10.0.0.254 and 172.16.0.1")
        ips = tokens_in(span_map, "IP_ADDRESS")
        assert len(ips) == 2

    def test_thai_label(self):
        # ``อยู่ที่`` is also a Thai address keyword — keep it out of
        # this case so the IP detector wins the span unambiguously.
        text = "เซิร์ฟเวอร์ IP 192.168.1.105"
        _, span_map, _ = mask(text)
        assert any("IP_ADDRESS" in t for t in span_map)


class TestIpAddressBoundary:
    def test_ipv6_loopback(self):
        # `::1` is the canonical IPv6 loopback.
        _, span_map, _ = mask("::1")
        assert any("IP_ADDRESS" in t for t in span_map)

    def test_ipv6_documentation_prefix(self):
        _, span_map, _ = mask("2001:db8::1")
        assert any("IP_ADDRESS" in t for t in span_map)

    def test_label_preserved_en(self):
        masked, span_map, _ = mask("IP: 172.16.0.1")
        assert "IP:" in masked
        assert "[IP_ADDRESS_0]" in masked


class TestIpAddressNegative:
    def test_invalid_octet(self):
        # 256 is out of range for IPv4 — the bounded regex must reject.
        assert "ip_address" not in detector_names_for("256.0.0.1")

    def test_incomplete_ipv4(self):
        assert "ip_address" not in detector_names_for("192.168.1")

    def test_ip_inside_credential_url(self):
        # Overlap resolution: when the IP lives inside a DB URL with
        # embedded credentials, CREDENTIAL wins. The IP must NOT be
        # double-tagged as its own span.
        text = "postgresql://admin:pass@192.168.1.105:5432/prod"
        _, span_map, _ = mask(text)
        cred_tokens = [t for t in span_map if "CREDENTIAL" in t]
        ip_tokens = [t for t in span_map if "IP_ADDRESS" in t]
        assert len(cred_tokens) == 1
        assert len(ip_tokens) == 0
