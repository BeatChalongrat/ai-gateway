"""Finding a quoted value again in a stored snapshot.

``locate`` answers one question: is this quote in the block the reader named?
It tries the quote exactly as given first; then with spacing made uniform;
then with character forms made uniform (full-width digits and letters, Thai
digits, the several marks that print as a space or a hyphen). Those are the
only liberties taken, and none of them reads the words.

A quote that is not in the block named, but is in exactly one block of the
whole page, is taken to be that block — the place is still unambiguous. A
quote that is in several blocks and not in the one named is not found: the
reader did not say where it looked.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_QUOTE = 300
# marks that print as nothing, as a space, or as a plain hyphen or quote
_NOTHING = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_SAME = {ord(a): b for a, b in {
    " ": " ", " ": " ", " ": " ", " ": " ", "　": " ",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}.items()}
_THAI_DIGITS = {ord(c): str(i) for i, c in enumerate("๐๑๒๓๔๕๖๗๘๙")}


def spacing(s: str) -> str:
    """The text with every run of spacing as one space, and none at its ends."""
    return re.sub(r"\s+", " ", str(s).translate(_NOTHING)).strip()


def forms(s: str) -> str:
    """The text with character forms made uniform (see the module docstring) and no spacing at all."""
    s = unicodedata.normalize("NFKC", str(s).translate(_NOTHING)).translate(_SAME).translate(_THAI_DIGITS)
    return re.sub(r"\s+", "", s)


@dataclass
class Found:
    block: int          # the block's number in the snapshot
    how: str            # exact | spacing | forms
    start: int          # where in the block's text the quote begins (-1 when found only without spacing)
    relocated: bool     # the reader named another block; this is the only block of the page that holds the quote
    text: str           # the block's own text

    def as_dict(self) -> dict:
        return {"block": self.block, "how": self.how, "offset": self.start, "relocated": self.relocated}


def _whole(text: str, quote: str) -> int:
    """Where ``quote`` stands in ``text`` as a whole — a figure is not found inside a longer figure
    ("9" is not in "9,490", "12.5" is not in "112.50") — or -1."""
    at = text.find(quote)
    while at >= 0:
        end = at + len(quote)
        before_ok = not (quote[0].isdigit() and at > 0 and (text[at - 1].isdigit() or (text[at - 1] in ".," and at > 1 and text[at - 2].isdigit())))
        after_ok = not (quote[-1].isdigit() and end < len(text) and (text[end].isdigit() or (text[end] in ".," and end + 1 < len(text) and text[end + 1].isdigit())))
        if before_ok and after_ok:
            return at
        at = text.find(quote, at + 1)
    return -1


def _in_block(quote: str, text: str) -> tuple[str, int] | None:
    at = _whole(text, quote)
    if at >= 0:
        return "exact", at
    q, t = spacing(quote), spacing(text)
    if q and _whole(t, q) >= 0:
        return "spacing", _whole(t, q)
    q, t = forms(quote), forms(text)
    if q and _whole(t, q) >= 0:
        return "forms", -1
    return None


def locate(blocks: list[dict], quote, block) -> Found | None:
    """Where ``quote`` is in the snapshot, given the block the reader named; None when it is not re-findable."""
    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE:
        return None
    by_number = {b["i"]: b for b in blocks}
    named = by_number.get(block) if isinstance(block, int) and not isinstance(block, bool) else None
    if named is not None:
        hit = _in_block(quote, named["text"])
        if hit:
            return Found(named["i"], hit[0], hit[1], False, named["text"])
    holders = [(b, _in_block(quote, b["text"])) for b in blocks]
    holders = [(b, h) for b, h in holders if h]
    if len(holders) == 1:
        b, hit = holders[0]
        return Found(b["i"], hit[0], hit[1], True, b["text"])
    return None


def holders(blocks: list[dict], quote: str) -> list[int]:
    """Every block of the page that holds the quote."""
    return [b["i"] for b in blocks if _in_block(quote, b["text"])]


def distance(path_a: str, path_b: str) -> int:
    """How far apart two blocks sit in the page: the steps from one up to what holds both and down to the other."""
    a, b = path_a.split(">"), path_b.split(">")
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return (len(a) - common) + (len(b) - common)


def belongs_with(blocks: list[dict], quote: str, at: int, anchor: int) -> bool:
    """A figure that is printed in several places belongs to the thing it was read for only if no other place
    that prints it sits nearer to that thing (``anchor``: the block that names it) than the place named."""
    by_number = {b["i"]: b for b in blocks}
    if at not in by_number or anchor not in by_number:
        return False
    here = distance(by_number[at]["path"], by_number[anchor]["path"])
    return all(distance(by_number[i]["path"], by_number[anchor]["path"]) >= here for i in holders(blocks, quote))


def depth(path: str) -> int:
    return len(path.split(">"))


def shared(path_a: str, path_b: str) -> int:
    """How deep the smallest part of the page that holds both blocks is (the page itself is 1, its body 2)."""
    n = 0
    for x, y in zip(path_a.split(">"), path_b.split(">")):
        if x != y:
            break
        n += 1
    return n


def around(blocks: list[dict], at: int, reach: int = 2, limit: int = 160) -> list[str]:
    """The text printed just before and after a block — what a person reads beside the value."""
    out = []
    for b in blocks:
        if b["i"] != at and abs(b["i"] - at) <= reach:
            out.append(b["text"][:limit])
    return out
