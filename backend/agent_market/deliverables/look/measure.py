"""Text measured in the real typeface at the real size, before it is placed (AM21).

``fit.py`` holds the font metrics (Pillow on the embedded Sarabun); this module turns them into what a
renderer asks: how wide a text is, how many lines it takes in a width, and the lines it breaks into
between words — Thai words found by libthai (thai_break), Latin at spaces — so that nothing is placed
wider than its frame and no Thai word is split by a reader's Word.
"""

from __future__ import annotations

import functools
import statistics
import unicodedata

from .. import thai_break
from ...columns import columns
from ..fit import SAFETY, text_width_pt, wrap_lines
from .tokens import LOOK


def is_thai(text: str) -> bool:
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in text)


# ── how wide a column of each script is (AM22 §1.4) ──────────────────────
#
# The catalogue's limits are counts of characters, and a count sees neither width nor height (W3C). The
# count itself is ``columns`` — the characters a reader sees, Thai marks taking none — but a Thai column
# in Sarabun is wider than a Latin one, so the same count is not the same width. The ratio is measured
# here, from the embedded font on the engine's own sentences in both languages (engine/words.json), not
# typed: a font change moves it, and the contract test pins it to what the font says.

def _corpus(lang: str) -> list[str]:
    from ...engine import words

    w = words.SHARED
    texts = list(w.english.values()) if lang == "en" else list(w.strings.values())
    return [t for t in texts if "{" not in t and columns(t) >= 12]


@functools.lru_cache(maxsize=4)
def column_width(lang: str) -> float:
    """The width of one column of a language's text in the family at 100 pt: the median over the
    engine's own sentences in that language."""
    size = 100.0
    widths = [text_width_pt(t, LOOK.family, size) / columns(t) for t in _corpus("en" if lang == "en" else "th")]
    return statistics.median(widths)


@functools.lru_cache(maxsize=4)
def width_ratio(lang: str) -> float:
    """How many Latin columns one column of ``lang`` is as wide as (1.0 for English)."""
    if lang == "en":
        return 1.0
    return column_width(lang) / column_width("en")


def latin_columns(text: str) -> float:
    """A text's width as a count of Latin-width columns: each character a reader sees counts one, a Thai
    one as many as it is wider — the one measure the catalogue compares its limits with. A Thai limit
    told to a model is the catalogue's limit divided by the same ratio (catalogue.writing_limits)."""
    ratio = width_ratio("th")
    out = 0.0
    for ch in text or "":
        if unicodedata.category(ch) in ("Mn", "Me"):
            continue
        out += ratio if 0x0E00 <= ord(ch) <= 0x0E7F else 1.0
    return out


def width_pt(text: str, size_pt: float, bold: bool = False) -> float:
    """The width of a text on one line, with the safety margin a Thai run needs."""
    return text_width_pt(text, LOOK.family, size_pt, bold) * SAFETY


def widest_word_pt(text: str, size_pt: float, bold: bool = False) -> float:
    return max((width_pt(w, size_pt, bold) for w, _ in thai_break.words(text)), default=0.0)


def lines_in(text: str, size_pt: float, width_pt_: float, bold: bool = False) -> int:
    """How many lines a text takes when a reader's application wraps it in a width."""
    return wrap_lines(text, LOOK.family, size_pt, width_pt_, bold)


def break_lines(text: str, size_pt: float, avail_pt: float, bold: bool = False) -> list[str]:
    """The text as lines broken between words only, each no wider than ``avail_pt`` (a single word wider
    than the frame stays whole: the caller steps the size down or refuses)."""
    lines, cur = [], ""
    for word, spaced in thai_break.words(text):
        cand = (cur + (" " if spaced else "") + word) if cur else word
        if cur and width_pt(cand, size_pt, bold) > avail_pt:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    return lines + ([cur] if cur else [])


def fit_role(fmt: str, text: str, role: str, avail_pt: float, max_lines: int, bold: bool | None = None) -> str | None:
    """The first role, from ``role`` down the scale, at which the text fits in ``max_lines`` of ``avail_pt``
    with no word wider than the frame — or None when none does (the caller refuses, naming the section)."""
    r: str | None = role
    while r is not None:
        size = LOOK.pt(fmt, r)
        b = LOOK.bold(r) if bold is None else bold
        if widest_word_pt(text, size, b) <= avail_pt and len(break_lines(text, size, avail_pt, b)) <= max_lines:
            return r
        r = LOOK.smaller(r)
    return None
