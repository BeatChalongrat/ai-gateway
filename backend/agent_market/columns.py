"""Characters a reader sees (AM22 §1.4).

One count, everywhere: a string's length as the number of characters a reader sees on the page. A Thai
tone mark or an upper or lower vowel (Unicode categories Mn and Me) sits on the consonant before it and
takes no column of its own, so it is not counted — the way Netflix's Thai timed-text budget counts, and
the way a reader counts. ``len()`` on a Thai display string counts code points and overstates it by a
quarter; nothing a reader sees is measured with bare ``len()`` any more (tests/test_am21_contract.py).

This module imports nothing of the engine or the renderer, so both may import it without a cycle.
"""

from __future__ import annotations

import unicodedata

_NO_COLUMN = ("Mn", "Me")


def columns(s: str) -> int:
    """How many characters a reader sees in ``s``: combining marks take no column."""
    return sum(1 for ch in (s or "") if unicodedata.category(ch) not in _NO_COLUMN)
