"""Private key block detector.

Matches PEM-encoded private key headers.  These are extremely distinctive
markers with near-zero false positive rate in natural language.

Covered formats:
    -----BEGIN PRIVATE KEY-----
    -----BEGIN RSA PRIVATE KEY-----
    -----BEGIN EC PRIVATE KEY-----
    -----BEGIN DSA PRIVATE KEY-----
    -----BEGIN OPENSSH PRIVATE KEY-----

The match covers the BEGIN line only (not the entire base64 body),
since the header alone is sufficient evidence of a private key and
span-based masking only needs the identifying marker.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

_PEM_HEADER_RE = re.compile(
    r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
)


class PrivateKeyDetector(BaseDetector):
    name = "private_key"
    category = "secret"
    default_sensitivity = SensitivityLevel.CRITICAL

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _PEM_HEADER_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="private_key_header",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
