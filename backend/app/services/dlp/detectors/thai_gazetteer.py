"""FIX29 — Thai given-name gazetteer with a segmentation-aware right boundary.

Thai has no spaces between words, so a bare given name is usually glued to
the verb that follows it (``ณัฐพลบอกว่า…``). A plain gazetteer match with a
Thai-character boundary on the right never fires on that shape (FIX27
measured 11/40); the FIX23 shape (a span that swallows the next word) is
the failure we must never produce. This module gives BOTH engines the same
answer:

  * ``NAMES``  — ``data/thai_given_names.txt``: Thai given names from the
    Thai Male and Female Names Corpus (Korkeat Wannapat, CC BY-SA 4.0, via
    pythainlp 5.3.7), ≥3 codepoints, Thai script only, and with every entry
    that is also an ordinary dictionary word REMOVED (``ชนิด``, ``ประชุม``,
    ``ส่ง`` … are names too; keeping them put 12 false fires into the
    104-sentence corpus). A lexicon of names, not a list of people.
  * ``WORDS``  — ``data/thai_words.txt``: the pythainlp Thai word list
    (CC0 1.0), used ONLY to decide where a name ENDS.

A candidate is accepted when it starts at a left boundary (start of text or
a non-Thai character before it), is the LONGEST gazetteer name at that
position, and its right edge is a word edge: end of text, a non-Thai
character, or the start of a dictionary word (``ณัฐพล|บอก``). A candidate
whose text continues into a longer dictionary word is rejected (``ประ…``).
The span therefore ends at the name by construction.

The same rule trims the titled detector's over-capture
(``คุณมีชัยจองห้องประชุมไว้`` → ``คุณมีชัย``): see ``trim_run``.

The Rust sidecar embeds the SAME two files (``dlp-sidecar/data``); a test
compares their hashes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
NAME_MIN = 3
NAME_MAX = 20
WORD_MAX = 20  # longest dictionary lookup tried at a right edge
LONGER_MAX = 5  # a name that continues into a longer dictionary word is rejected


def _is_thai(ch: str) -> bool:
    return "ก" <= ch <= "๛"


@lru_cache(maxsize=1)
def names() -> frozenset[str]:
    try:
        return frozenset(
            line.strip()
            for line in (_DATA / "thai_given_names.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        return frozenset()


@lru_cache(maxsize=1)
def names_ambiguous() -> frozenset[str]:
    """Names that are ALSO dictionary words (``มีชัย``, ``จำลอง``, ``ชนิด``…),
    kept OUT of ``names()`` (they fire on prose) but valid for ``trim_run``:
    after a title the text is a name by construction, so ambiguity is no
    longer a risk there."""
    try:
        return frozenset(
            line.strip()
            for line in (_DATA / "thai_given_names_ambiguous.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        return frozenset()


@lru_cache(maxsize=1)
def words() -> frozenset[str]:
    try:
        return frozenset(
            line.strip()
            for line in (_DATA / "thai_words.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        return frozenset()


def word_starts_at(text: str, i: int) -> bool:
    """True when a dictionary word (≥2 chars) begins at ``text[i]``."""
    w = words()
    for length in range(min(WORD_MAX, len(text) - i), 1, -1):
        if text[i : i + length] in w:
            return True
    return False


def right_edge_ok(text: str, end: int) -> bool:
    return end >= len(text) or not _is_thai(text[end]) or word_starts_at(text, end)


def continues_into_word(text: str, start: int, end: int) -> bool:
    w = words()
    for k in range(1, LONGER_MAX + 1):
        if end + k <= len(text) and text[start : end + k] in w:
            return True
    return False


def name_at(text: str, i: int) -> int | None:
    """Length of the longest gazetteer name starting at ``i`` whose right edge
    is a word edge, or None."""
    n = names()
    if not n:
        return None
    for length in range(min(NAME_MAX, len(text) - i), NAME_MIN - 1, -1):
        cand = text[i : i + length]
        if cand in n:
            end = i + length
            if right_edge_ok(text, end) and not continues_into_word(text, i, end):
                return length
    return None


# A name label glued to the name (``ลูกค้าชื่อสมชาย``, ``นามว่าสมหญิง``) is a
# left boundary too — the commonest way a name enters a Thai sentence.
NAME_LABELS = ("ชื่อ", "นามว่า", "ชื่อว่า")


def left_boundary(text: str, i: int) -> bool:
    if i == 0 or not _is_thai(text[i - 1]):
        return True
    return any(text[max(0, i - len(lbl)) : i] == lbl for lbl in NAME_LABELS)


def find_names(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of gazetteer names at left word boundaries."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if _is_thai(text[i]) and left_boundary(text, i):
            length = name_at(text, i)
            if length is not None:
                out.append((i, i + length))
                i += length
                continue
        i += 1
    return out


def trim_run(text: str, start: int, end: int) -> int:
    """For a Thai run ``text[start:end]`` that is NOT itself a name: if it
    begins with a gazetteer name and the remainder begins a dictionary word
    (``มีชัย|จอง…``), return the name's end; otherwise ``end`` unchanged."""
    run = text[start:end]
    n = names() | names_ambiguous()
    if not n or run in n:
        return end
    for length in range(min(NAME_MAX, len(run)), NAME_MIN - 1, -1):
        if run[:length] in n and word_starts_at(text, start + length):
            return start + length
    return end
