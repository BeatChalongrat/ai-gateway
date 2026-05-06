"""LINE messaging-app handle detector.

Thai-market PII: LINE handles like ``@somchai_dev`` are common contact
identifiers. The bare ``@username`` shape collides with Twitter/Slack
mentions, Thai @-as-price-marker convention ("ราคา @100 บาท"), and
incidental ``@`` chars in URLs/code. The detector requires the literal
``LINE`` keyword adjacent to the handle so non-LINE @-strings stay
quiet.

Pattern shape:

    \\bLINE\\s*(?:ID)?\\s*[:=]?\\s*@<alpha><body>

  - ``\\bLINE``           keyword required, word-boundary anchored.
  - ``\\s*(?:ID)?\\s*``   optional " ID" between keyword and handle.
  - ``[:=]?\\s*``         optional ":" or "=" separator.
  - ``@[a-zA-Z]``          handle must start with @ + alpha (rejects
                          ``@2x``, ``@100``, ``@_underscore``).
  - body chars ``[a-zA-Z0-9_.\\-]{2,19}``  allowed handle body, total
                          length 3-20 incl. lead alpha.
  - trailing ``\\b``       prevents trailing punct from sweeping in
                          extra characters.

Rejected by design (REPL-verified):
  - ``email @work mentions``           no LINE keyword
  - ``@everyone please review``        no LINE keyword
  - ``follow @username on twitter``    no LINE keyword
  - ``ราคา @100 บาท``                  no LINE keyword + digit-prefixed
  - ``code @ line 42``                 LINE not adjacent to a handle
  - ``@2x display scaling``            digit lead char rejected
  - URLs containing ``?@param=1``      no LINE keyword
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


_LINE_ID_RE = re.compile(
    r"\bLINE\s*(?:ID)?\s*[:=]?\s*"
    r"(?P<value>@[a-zA-Z][a-zA-Z0-9_.\-]{2,19})\b",
    re.IGNORECASE,
)


class LineIdDetector(BaseDetector):
    name = "line_id"
    category = "pii"
    # MEDIUM matches the email detector — LINE handles are contact
    # identifiers, not financial/medical data.
    default_sensitivity = SensitivityLevel.MEDIUM

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        for m in _LINE_ID_RE.finditer(text):
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="line_id",
                    category=self.category,
                    start=m.start("value"),
                    end=m.end("value"),
                    matched_text=m.group("value"),
                    severity=self.default_sensitivity,
                )
            )
        return matches
