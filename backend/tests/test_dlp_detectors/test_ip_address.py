"""IP address detector — IPv4 + IPv6."""

from __future__ import annotations

from ._helpers import detector_names_for, mask, tokens_in


def _has_match(matches, name: str) -> bool:
    return any(ma.match.detector_name == name for ma in matches)


def _count_match(matches, name: str) -> int:
    return sum(1 for ma in matches if ma.match.detector_name == name)


class TestIpAddressHappyPath:
    def test_ipv4_lan(self):
        _, span_map, matches = mask("192.168.1.105")
        assert tokens_in(span_map, "IP_ADDRESS", matches) == ["<r0>"]

    def test_two_distinct_ipv4s(self):
        _, span_map, matches = mask("Servers 10.0.0.254 and 172.16.0.1")
        ips = tokens_in(span_map, "IP_ADDRESS", matches)
        assert len(ips) == 2

    def test_thai_label(self):
        # ``อยู่ที่`` is also a Thai address keyword — keep it out of
        # this case so the IP detector wins the span unambiguously.
        text = "เซิร์ฟเวอร์ IP 192.168.1.105"
        _, _, matches = mask(text)
        assert _has_match(matches, "ip_address")


class TestIpAddressBoundary:
    def test_ipv6_loopback(self):
        # `::1` is the canonical IPv6 loopback.
        _, _, matches = mask("::1")
        assert _has_match(matches, "ip_address")

    def test_ipv6_documentation_prefix(self):
        _, _, matches = mask("2001:db8::1")
        assert _has_match(matches, "ip_address")

    def test_label_preserved_en(self):
        masked, span_map, _ = mask("IP: 172.16.0.1")
        assert "IP:" in masked
        assert "<r0>" in masked


class TestIpAddressNegative:
    def test_invalid_octet(self):
        # 256 is out of range for IPv4 — the bounded regex must reject.
        assert "ip_address" not in detector_names_for("256.0.0.1")

    def test_incomplete_ipv4(self):
        assert "ip_address" not in detector_names_for("192.168.1")

    def test_sql_date_cast_is_not_ipv6(self):
        # FIX50 P1-B — ``::DATE`` matched as the IPv6 ``::DA`` (the regex
        # halted at the non-hex ``T``), and the mask + orphan-redact
        # pipeline then deleted the cast from the user's SQL, leaving
        # ``'2068-01-01'TE`` (reproduced live in the FIX48 run). The
        # boundary guard must reject a match glued to more identifier
        # characters.
        assert "ip_address" not in detector_names_for(
            "SELECT * FROM GENERATE_SERIES('2568-01-01'::DATE, '2568-12-01'::DATE)"
        )
        assert "ip_address" not in detector_names_for("'2025-01-01'::date")

    def test_code_scope_operator_is_not_ipv6(self):
        # C++/Rust ``::`` scoping with hex-lookalike identifiers.
        assert "ip_address" not in detector_names_for("ns::addr and std::fabs(x)")

    def test_real_ipv6_still_matches_next_to_punctuation(self):
        # Tightening the boundary must not lose real addresses that touch
        # punctuation (sentence end, equals, comma).
        assert "ip_address" in detector_names_for("gateway=2001:db8::1.")
        assert "ip_address" in detector_names_for("ping ::1, then stop")

    def test_ip_inside_credential_url(self):
        # Overlap resolution: when the IP lives inside a DB URL with
        # embedded credentials, CREDENTIAL wins. The IP must NOT be
        # double-tagged as its own span — only one entry in span_map,
        # and its value covers the entire URL (with the IP inside).
        text = "postgresql://admin:pass@192.168.1.105:5432/prod"
        _, span_map, _ = mask(text)
        assert len(span_map) == 1, f"expected one mask span, got {len(span_map)}: {span_map}"
        [value] = span_map.values()
        assert value == text, f"expected full URL masked as one span, got {value!r}"
