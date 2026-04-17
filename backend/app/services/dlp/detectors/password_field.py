"""Password-in-config detector.

Matches patterns where a password/secret label is followed by an
explicit assignment operator and a value.  Designed for config snippets,
environment variable dumps, and log fragments that users may paste
into chat messages.

Supported patterns:
    password=secret123
    password: "my secret"
    PASSWORD = 'changeme'
    DB_PASSWORD="hunter2"
    secret_key=abc123
    api_secret: xyz

Known limitations:
    - Requires an explicit label (password, secret, passwd, etc.).
      Will not detect a bare password string without context.
    - Value extraction stops at whitespace or quote boundary.
      Multi-word unquoted values are truncated.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# Labels that suggest the value is a password/secret.
_LABEL_ALTS = r"(?:password|passwd|pwd|secret_?key|api_?secret|auth_?token|db_?password)"

# Full pattern: label + separator + value (quoted or unquoted).
_PASSWORD_RE = re.compile(
    rf"(?i)"                       # case insensitive
    rf"\b{_LABEL_ALTS}"            # label keyword
    rf"\s*[:=]\s*"                 # separator
    rf"(?:"
    rf"\"([^\"]+)\""               # double-quoted value (group 1)
    rf"|'([^']+)'"                 # single-quoted value (group 2)
    rf"|(\S+)"                     # unquoted value (group 3)
    rf")",
)


class PasswordFieldDetector(BaseDetector):
    name = "password_field"
    category = "secret"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _PASSWORD_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="password_in_config",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
