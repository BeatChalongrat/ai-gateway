"""Tiny language detector for DLP.

Scope is deliberately narrow: tell Presidio which spaCy model to use
when scanning a chat message. Unicode range check is cheap, correct
for the two languages we currently configure (English / Thai), and
has no dependencies.

For anything more sophisticated (Chinese, Japanese, Korean, right-to-
left scripts, ...) swap this out for a real language identifier like
``langdetect`` or ``fasttext``. The public signature won't change.
"""

from __future__ import annotations


# Basic Thai block (U+0E00–U+0E7F). Covers every character defined by
# the modern Thai alphabet plus digits and tone marks.
_THAI_MIN = "\u0e00"
_THAI_MAX = "\u0e7f"


def detect_language(text: str) -> str:
    """Return ``"th"`` if the text contains any Thai character,
    otherwise ``"en"``.

    Single-codepoint check: this is intentionally cheap and biased
    toward Thai. For mixed Thai+English strings the Thai model is
    picked because Presidio's English recognizer still fires on the
    latin substrings inside a Thai document, but the reverse is not
    true.
    """
    for ch in text:
        if _THAI_MIN <= ch <= _THAI_MAX:
            return "th"
    return "en"
