"""Phone number detector.

Two-branch design to keep false positives low on bare digit runs:

1. Formatted branch — a separator-bearing pattern (dashes, dots, spaces,
   or parentheses) is enough to disambiguate the run from ordinary
   numeric noise. No surrounding context required.

2. Bare-digits branch — a 10-15 digit run with no separators is only
   accepted when a phone-context keyword (``call``, ``phone``, ``tel``,
   Thai ``โทร``, ``เบอร์`` …) appears within a short window around
   the match. Without this gate, the detector would (and historically
   did) false-positive on Unix epoch timestamps, order IDs, and any
   other bare digit run.

Supported formats:
    +1-555-123-4567   +66 81 234 5678   (555) 123-4567
    555-123-4567      555.123.4567       08-1234-5678
    02-234-5678       +66-81-234-5678
    0812345678         (bare, requires "โทร"/"phone"/"call"/… nearby)
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector

# Separator-bearing phone numbers. A leading digit-negative lookbehind and
# a trailing digit-negative lookahead keep the match from fusing with
# adjacent digit runs (e.g. the first 10 digits of a credit card).
_PHONE_FORMATTED_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s.\-]?)?"          # optional country code + optional separator
    r"\(?\d{2,4}\)?"                    # area code (optionally parenthesized)
    r"[\s.\-]\d{3,4}[\s.\-]\d{3,4}"    # two separator-delimited digit groups
    r"(?!\d)"
)

# A bare 10-15 digit run at word boundaries, with an optional leading plus.
# Only emitted when a phone-context keyword is present in the surrounding
# window — see ``_PHONE_CONTEXT_RE``.
_BARE_DIGITS_RE = re.compile(r"(?<!\d)\+?\d{10,15}(?!\d)")

# FIX27 Part 3 — a UUID's digit groups (``…7a86d915-4821-4469-9475-…``) look
# like a formatted phone number to the branch-1 regex. Attachment envelopes
# carry file UUIDs on every turn, so the file id was masked as <<PHONE_n>>.
# Any phone candidate that lies inside a UUID is rejected in both engines.
_UUID_RE = re.compile(
    r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9A-Za-z])"
)

# Thai landlines are 9 digits total (0 + 2-digit area + 3-digit exchange
# + 4-digit subscriber) — one digit shorter than the 10-digit floor in
# ``_PHONE_FORMATTED_RE``'s downstream filter, so they would otherwise
# be dropped. This branch captures them directly:
#
#   02-555-1234   02 555 1234   025551234   053-123-4567
#
# Area code is constrained to ``0[2-9]`` (Bangkok 02 + provincial
# 03x/04x/05x/07x). Trailing alphabetic lookahead rejects version
# strings like ``02-release``; digit-boundary lookbehind/lookahead
# prevent fusing with longer numeric runs.
_PHONE_THAI_LANDLINE_RE = re.compile(
    r"(?<!\d)"
    r"(0[2-9])"
    r"[\s\-]?\d{3}[\s\-]?\d{4}"
    r"(?!\d)(?![A-Za-z])"
)

# Words or partial-words in Thai and English that signal a nearby digit run
# is a phone number. Kept narrow on purpose: "number" alone matches too many
# unrelated contexts (order number, member number); we require a stronger
# phone-specific cue.
_PHONE_CONTEXT_RE = re.compile(
    r"(?:call|phone|tel(?:ephone)?|mobile|cell|line\s*id|whatsapp|dial|"
    r"contact\s+me|hotline|\+?66\s|"
    r"โทร|มือถือ|เบอร์(?:\s*โทร)?|ติดต่อ|สายด่วน|หมายเลข(?:\s*โทร)?)",
    re.IGNORECASE,
)

# Window of chars before/after a bare-digits match in which we look for a
# phone-context keyword. 25 covers "Please call me at 0812345678" with a
# comfortable margin without drifting into the next sentence.
_CONTEXT_WINDOW = 25


class PhoneDetector(BaseDetector):
    name = "phone"
    category = "pii"
    default_sensitivity = SensitivityLevel.HIGH
    ACCEPTS_DECODED = True
    # Beats Presidio NER's default priority=0 on the same span. A
    # formatted Thai-style number like ``+66 81-234-5678`` would
    # otherwise tie against an NER PERSON / PHONE_NUMBER match and
    # fall to "earlier start wins" — fragile when scan order shifts.
    priority = 5

    def detect(self, text: str, *, from_decoded: bool = False) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()
        uuid_spans = [(u.start(), u.end()) for u in _UUID_RE.finditer(text)]

        def _emit(start: int, end: int, matched_text: str) -> None:
            if (start, end) in seen_spans:
                return
            # FIX27: inside a UUID → not a phone number.
            if any(us <= start and end <= ue for us, ue in uuid_spans):
                return
            # Reject trivial all-same-digit numbers (000-000-0000,
            # 111-111-1111, …). Real phones never have this shape, but
            # prompts about *sample / example* phone numbers use them
            # routinely. The E2E audit (May 2026) caught
            # ``เบอร์ติดต่อคือ 000-000-0000 อันนี้เป็นข้อมูลตัวอย่าง``
            # masking the example number — the correct behaviour is to
            # leave it untouched so the LLM can explain the format.
            digits_only = "".join(c for c in matched_text if c.isdigit())
            if digits_only and len(set(digits_only)) == 1:
                return
            seen_spans.add((start, end))
            matches.append(
                DetectionMatch(
                    detector_name=self.name,
                    entity_type="phone_number",
                    category=self.category,
                    start=start,
                    end=end,
                    matched_text=matched_text,
                    severity=self.default_sensitivity,
                    priority=self.priority,
                )
            )

        # Branch 1 — formatted numbers. The separator inside the match is
        # disambiguation enough; no context keyword required. We still
        # enforce ≥10 digits to keep random "1-2-3" sequences out.
        for m in _PHONE_FORMATTED_RE.finditer(text):
            span_text = m.group()
            if sum(1 for c in span_text if c.isdigit()) < 10:
                continue
            # Reject runs that continue into another digit group (credit
            # card tail). Matches the original rule that protected against
            # credit_card priority collisions.
            tail = text[m.end():m.end() + 2]
            if len(tail) == 2 and tail[0] in " -." and tail[1].isdigit():
                continue
            _emit(m.start(), m.end(), span_text)

        # Branch 1b — Thai landlines (9 digits, area code 0[2-9]).
        # No context-keyword requirement: the 0[2-9] prefix plus the
        # 2+3+4 digit grouping is specific enough to disambiguate
        # from timestamps and bare numeric noise. Runs here before
        # the bare-digits branch so the formatted match wins over a
        # naked 10+ digit string that would otherwise need a context
        # keyword.
        for m in _PHONE_THAI_LANDLINE_RE.finditer(text):
            _emit(m.start(), m.end(), m.group())

        # Branch 2 — bare digits. Require a phone-context keyword inside
        # the ±_CONTEXT_WINDOW chars window. Without this gate, Unix epoch
        # timestamps, order numbers, and other arbitrary digit runs
        # would trigger false-positive masking.
        # When from_decoded=True (base64 decoded content), skip the context
        # requirement since the decoded text is isolated.
        for m in _BARE_DIGITS_RE.finditer(text):
            start, end = m.start(), m.end()
            if from_decoded:
                _emit(start, end, m.group())
                continue
            low = max(0, start - _CONTEXT_WINDOW)
            high = min(len(text), end + _CONTEXT_WINDOW)
            window = text[low:high]
            if _PHONE_CONTEXT_RE.search(window):
                _emit(start, end, m.group())

        matches.sort(key=lambda x: (x.start, x.end))
        return matches
