"""Database connection string detector.

Matches DB URLs that embed credentials in the userinfo segment:

    postgresql://user:pass@host:5432/db
    mysql://user:pass@host/db
    mongodb://user:pass@host/db
    mongodb+srv://user:pass@cluster/db
    redis://:password@host:6379
    mssql://user:pass@host/db

A bare ``postgresql://host/db`` without credentials does NOT match —
these are safe to log and discussing them is common in support chat.
The detector only fires when ``user:pass`` (or ``:pass`` for Redis)
is present before the ``@``.

Covers the most common URI schemes; extending to more is additive.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# Core rule: scheme://[user]:password@host...
# - Two-part userinfo (user:pass) for postgresql/mysql/mssql/mongodb
# - Optional user for redis (":password" is valid)
# Host/path captured up to (but not including) the query-string `?` or
# fragment `#` — those components are not credentials and may contain
# unrelated data we shouldn't mask.
_CONN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql)://"
    r"[^@\s/?#:]*:[^@\s/?#]+"         # userinfo WITH password (': password' required)
    r"@[^\s/?#]+"                     # host (stops before :port, /path, ?, #)
    r"(?:[:/][^\s?#]*)?"              # optional :port and/or /path (no query/fragment)
)


class DbConnectionStringDetector(BaseDetector):
    name = "db_connection_string"
    category = "credential"
    default_sensitivity = SensitivityLevel.CRITICAL

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _CONN_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="db_connection_string",
                    category=self.category,
                    start=m.start(),
                    end=m.end(),
                    matched_text=m.group(),
                    severity=self.default_sensitivity,
                )
            )
        return matches
