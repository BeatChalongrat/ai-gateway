"""What this package says, in the language of the run.

No sentence a person reads on a screen is written in this package's code: it has a key, its Thai is in
agent.json under "strings" and its English under "en" (engine/words.py). What the report, the deck and the
workbook themselves say — their headings, the names of a figure in a sentence, the commentary the rules
write when no model answers — has a key too, since AM18, and is said in the run's language.
"""

from ...engine import words as _words

W = _words.of(__package__)


def columns(*keys: str) -> list:
    """The headings of a card's table of rows, from the keys of their words."""
    return [_words.cap(W(k)) for k in keys]


def printed(key: str) -> str:
    """A name as the report prints it — in the run's language, like everything else since AM18 (the name is
    from when the documents were Thai whatever the run)."""
    return W(key)
