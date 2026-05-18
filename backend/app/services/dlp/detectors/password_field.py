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


# Thai-locale variant. Real users write things like
# ``รหัสผ่านคือ P@ssw0rd123!`` — no ``=`` / ``:`` separator and no
# English label. We accept ``คือ`` / ``ว่า`` (or no connector at
# all) as the bridge between the keyword and the value. ``\S{6,}``
# floor keeps the trivial case ``รหัสผ่านสั้น`` (just "the password
# is short") from minting a spurious finding.
_THAI_PASSWORD_RE = re.compile(
    r"(?:รหัสผ่าน|พาสเวิร์ด)"
    r"\s*(?:คือ|ว่า|=|:)?\s*"
    r"(?P<value>\S{6,})",
)


# English no-separator variant — catches the prose forms the May 2026
# audit surfaced ("username admin password admin123", "SMTP password
# smtp_pass_123456") that the strict label+separator form above
# misses. The double-lookahead pins down two conditions on the value:
#
#   1. Minimum 8 non-whitespace characters
#   2. At least one digit OR special character
#
# The entropy gate keeps natural-English follow-words ("recovery",
# "protection", "verification") from firing as false positives —
# without it, every "password protection" / "password recovery"
# phrase in user prose would mask a benign noun.
_PASSWORD_NOSEP_RE = re.compile(
    r"\bpassword\s+"
    r"(?P<value>"
    r"(?=\S{8,})"                              # ≥ 8 chars
    r"(?=\S*[\d!@#$%^&*+\-=_./\\<>?,;:])"      # ≥ 1 digit or special
    r"\S+"
    r")",
    re.IGNORECASE,
)


class PasswordFieldDetector(BaseDetector):
    name = "password_field"
    category = "secret"
    default_sensitivity = SensitivityLevel.HIGH

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen: set[tuple[int, int]] = set()

        def _emit(start: int, end: int, entity: str) -> None:
            if (start, end) in seen:
                return
            seen.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type=entity,
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=text[start:end],
                    severity=self.default_sensitivity,
                )
            )

        for m in _PASSWORD_RE.finditer(text):
            _emit(m.start("value"), m.end("value"), "password_in_config")
        for m in _THAI_PASSWORD_RE.finditer(text):
            _emit(m.start("value"), m.end("value"), "password_in_text")
        for m in _PASSWORD_NOSEP_RE.finditer(text):
            _emit(m.start("value"), m.end("value"), "password_in_text")
        return matches
