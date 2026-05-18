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
# The value-with-delimiters is captured in the named ``value`` group so
# the masker can replace only the value (and any surrounding quotes)
# without swallowing the label. ``Password: secret123`` masks to
# ``Password: [PASSWORD_0]``, not ``[PASSWORD_0]``.
_PASSWORD_RE = re.compile(
    rf"(?i)"                       # case insensitive
    rf"\b{_LABEL_ALTS}"            # label keyword
    rf"\s*[:=]\s*"                 # separator
    rf"(?P<value>"
    rf"\"[^\"]+\""                 # double-quoted value (quotes included)
    rf"|'[^']+'"                   # single-quoted value (quotes included)
    rf"|\S+"                       # unquoted value
    rf")",
)


class PasswordFieldDetector(BaseDetector):
    name = "password_field"
    category = "secret"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _PASSWORD_RE.finditer(text):
            v_start, v_end = m.start("value"), m.end("value")
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="password_in_config",
                    category=self.category,
                    start=v_start,
                    end=v_end,
                    matched_text=text[v_start:v_end],
                    severity=self.default_sensitivity,
                )
            )
        return matches
