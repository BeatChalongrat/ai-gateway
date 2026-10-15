"""Text measured in the real typeface at the real size, before it is placed (AM21).

``fit.py`` holds the font metrics (Pillow on the embedded Sarabun); this module turns them into what a
renderer asks: how wide a text is, how many lines it takes in a width, and the lines it breaks into
between words — Thai words found by libthai (thai_break), Latin at spaces — so that nothing is placed
wider than its frame and no Thai word is split by a reader's Word.
"""

from __future__ import annotations

from .. import thai_break
from ..fit import SAFETY, text_width_pt, wrap_lines
from .tokens import LOOK


def is_thai(text: str) -> bool:
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in text)


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
