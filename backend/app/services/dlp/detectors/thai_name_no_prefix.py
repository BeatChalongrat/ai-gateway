"""Fallback Thai-name detector — no title prefix required.

The default ``thai_name`` detector is title-prefix gated: it only fires
when a Thai honorific (``นาย``, ``นาง``, ``ดร.`` …) precedes the name.
When PresidioDetector is enabled the Presidio PERSON recognizer covers
the remaining bare-name cases, but when a tenant turns NER off (and on
deployments where ``presidio-analyzer`` is not installed at all) any
Thai personal name without an honorific slips through unmasked — a
direct PDPA exposure.

This detector closes that gap with a deliberately conservative regex:

* Matches **exactly two** consecutive Thai-script words separated by
  whitespace. Each word is 2-15 Thai characters. Three-plus-word
  sequences are skipped to keep precision high — bare-prose Thai is
  dense with two-word collocations that are not names.
* Requires a Thai phone-book style context keyword (``ชื่อ``,
  ``ผู้ใช้``, ``ลูกค้า``, ``พนักงาน``, ``ผู้ส่ง``, ``เจ้าของ``,
  ``ลงชื่อ``, ``ผู้รับ``) in the ±``_CONTEXT_WINDOW`` characters
  *outside* the match. Without the keyword the match is discarded —
  arbitrary two-word Thai noun phrases are rejected.
* Emits ``severity=MEDIUM`` (one level below the prefix-gated
  ``thai_name`` detector's ``HIGH``) and ``priority=-5`` so the overlap
  resolver always prefers the higher-precision detector on shared
  spans.

Registry-side activation lives in :func:`registry.get_default_registry`
— pass ``ner_enabled=False`` to include this detector. The default
keeps it out so Presidio remains the canonical NER path when available.
"""

from __future__ import annotations

import re

from app.domain.dlp import DetectionMatch, SensitivityLevel
from app.services.dlp.detectors.base import BaseDetector


# Same Thai-script character class used by ``thai_name`` and the
# ``thai_address`` detectors — covers base letters, combining marks,
# and tone marks. Digits and punctuation are intentionally excluded so
# the pattern cannot accidentally swallow numeric IDs or address bodies.
_THAI_CHAR = r"[ก-ฺเ-๎]"

# Surrounding-context keywords that signal a nearby Thai noun phrase
# is a person's name. Mirrors the gating approach used by the phone
# detector's bare-digits branch.
_CONTEXT_KEYWORDS = (
    "ชื่อ",
    "ผู้ใช้",
    "ลูกค้า",
    "พนักงาน",
    "ผู้ส่ง",
    "เจ้าของ",
    "ลงชื่อ",
    "ผู้รับ",
)
_CONTEXT_RE = re.compile("|".join(re.escape(k) for k in _CONTEXT_KEYWORDS))

# Exactly two whitespace-separated Thai-script words. Sticking to a
# two-word match keeps precision high; the context-keyword gate below
# kills the remaining false positives. Word length is capped at 15 to
# avoid swallowing run-on prose where word breaks are missing.
#
# A negative lookahead refuses to start the match on a context-keyword
# token (``ชื่อ`` / ``ผู้ใช้`` / …). Without it the greedy regex would
# grab ``ชื่อ สมชาย`` from ``ชื่อ สมชาย ใจดี`` and then fail the context
# gate, missing the real ``สมชาย ใจดี`` match entirely.
_CONTEXT_KEYWORD_ALT = "|".join(re.escape(k) for k in _CONTEXT_KEYWORDS)
_NAME_RE = re.compile(
    rf"(?<!{_THAI_CHAR})"
    rf"(?!(?:{_CONTEXT_KEYWORD_ALT})(?:\s|$|[^ก-ฺเ-๎]))"
    rf"({_THAI_CHAR}{{2,15}}\s+{_THAI_CHAR}{{2,15}})"
    rf"(?!{_THAI_CHAR})"
)

# Window of characters before/after the match span to scan for a
# context keyword. The keyword is searched *outside* the match span so
# that a noun phrase ending in (e.g.) ``ลูกค้า`` cannot self-validate.
_CONTEXT_WINDOW = 25


class ThaiNameNoPrefixDetector(BaseDetector):
    name = "thai_name_no_prefix"
    category = "pii"
    # MEDIUM (vs ``thai_name``'s HIGH) — bare-name matches are a
    # softer signal than the prefix-gated form. The classifier may
    # still escalate via the multi-category rule.
    default_sensitivity = SensitivityLevel.MEDIUM
    # Negative priority so the overlap resolver always picks
    # ``thai_name`` (priority 0) or any specific identity detector on
    # shared spans. This detector is the floor of last resort.
    priority = -5

    def detect(self, text: str) -> list[DetectionMatch]:
        matches: list[DetectionMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        # We can't use ``finditer`` here because it advances past the
        # full span of every match it returns — which means a rejected
        # first match like ``ลูกค้าชื่อ สมชาย`` would swallow the
        # downstream ``สมชาย ใจดี`` pair the context filter would
        # actually accept. Instead, walk the text with an explicit
        # cursor: on a context-filter reject, advance by **one
        # codepoint** (not the full match length) so the next search
        # position sits inside the rejected first word and can
        # re-anchor on the next valid two-word sequence.
        pos = 0
        n = len(text)
        while pos < n:
            m = _NAME_RE.search(text, pos)
            if m is None:
                break
            start, end = m.start(1), m.end(1)

            # Context window deliberately excludes the match itself —
            # a noun phrase whose final word happens to be a context
            # keyword must not bootstrap its own validity.
            low = max(0, start - _CONTEXT_WINDOW)
            high = min(len(text), end + _CONTEXT_WINDOW)
            before = text[low:start]
            after = text[end:high]

            if _CONTEXT_RE.search(before + after):
                if (start, end) not in seen_spans:
                    seen_spans.add((start, end))
                    matches.append(
                        DetectionMatch(
                            detector_name=self.name,
                            entity_type="thai_name",
                            category=self.category,
                            start=start,
                            end=end,
                            matched_text=m.group(1),
                            severity=self.default_sensitivity,
                            priority=self.priority,
                        )
                    )
                pos = end
            else:
                # Rejected — step forward by 1 so we can re-anchor on
                # the next token without losing inner two-word pairs.
                pos = start + 1

        return matches
