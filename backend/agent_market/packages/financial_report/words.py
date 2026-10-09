"""What this package says, in the language of the run.

No sentence a person reads on a screen is written in this package's code: it has a key, its Thai is in
agent.json under "strings" and its English under "en" (engine/words.py). What the report, the deck and the
workbook themselves say — their headings, the names of a figure in a sentence, the commentary the rules
write when no model answers — is still written in their own code, in Thai: the documents' own pass.
"""

from ...engine import words as _words

W = _words.of(__package__)


def columns(*keys: str) -> list:
    """The headings of a card's table of rows, from the keys of their words."""
    return [_words.cap(W(k)) for k in keys]


def printed(key: str) -> str:
    """A name as the report prints it — the documents' language, whatever the run. The lines of the
    computed table are read by the files and by the model that writes their commentary, never by a screen;
    a figure's own record (its citation) names it in the run's language."""
    with _words.speaking(_words.DOCUMENT):
        return W(key)
