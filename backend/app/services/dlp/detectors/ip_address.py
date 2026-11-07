"""IP address detector (IPv4 + IPv6).

Matches well-formed IPv4 (dotted-quad) and IPv6 (full or compressed) addresses.
Does NOT exclude localhost or RFC1918 ranges — the gateway's job is to redact
all IPs; downstream analytics can always derive reachability from the raw log
fields, not from message content.

Caveats:
- IPv4 regex is octet-bounded (0-255 per octet) to avoid matching arbitrary
  dotted numbers.
- IPv6 uses a conservative pattern that accepts full form and the most common
  "::" compressions. It deliberately does not try to validate every edge case
  of RFC 4291 — precision over recall to keep false positives low in prose.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# IPv4: four octets, each 0-255, dot-separated, at word boundaries.
_IPV4_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV4_RE = re.compile(
    rf"(?<!\d)(?:{_IPV4_OCTET}\.){{3}}{_IPV4_OCTET}(?!\d)"
)

# IPv6: full, leading-zero, and common compressed forms (incl. ::).
# Conservative: requires at least one colon-group and either full/compressed form.
# Boundary guards reject ANY adjacent identifier character (letters beyond
# hex, digits, underscore), not just hex: an "address" that stops mid-word
# is a token inside code, not an address. Without this, SQL's ``::DATE``
# matches as the IPv6 ``::DA`` (the regex halts at the non-hex ``T``) and
# C++/Rust ``ns::addr`` scoping matches ``::add`` — the mask+orphan-redact
# pipeline then deletes the cast from the user's code (FIX50 P1-B,
# reproduced live in the 2026-09-02 FIX48 run as ``'2068-01-01'TE``).
_IPV6_RE = re.compile(
    r"(?<![0-9A-Za-z_:])"
    r"(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"          # full (8 groups)
    r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"                      # trailing ::
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"      # one :: gap
    r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}"
    r"|::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}"     # leading ::
    r")"
    r"(?![0-9A-Za-z_:])"
)


class IPAddressDetector(BaseDetector):
    name = "ip_address"
    category = "network"
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []

        for m in _IPV4_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="ipv4_address",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )

        for m in _IPV6_RE.finditer(text):
            # Require at least one ':' to filter pure-hex runs that the pattern
            # can occasionally accept at the edge cases.
            value = m.group()
            if ":" not in value:
                continue
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="ipv6_address",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=value,
                    severity=self.default_sensitivity,
                )
            )

        return matches
