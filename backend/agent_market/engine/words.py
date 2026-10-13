"""The language a run is written in, and a package's sentences in it (AM14).

A sentence a person reads is not written in the code. It has a key; its Thai is in the package's
manifest under ``strings`` and its English under ``en``, found by the Thai — the place and the rule of
what a manifest says before a run exists (AM11) and of every word of the procurement package (AM12) —
so the one lint that fails a manifest string without its English fails a question, a check's line or
the reason a file was left out too. A sentence exists in both languages or in neither.

One step of one run is one process (engine.step), and a run is written in one language from its first
question to its last line: the step says which (``use``), and every sentence asked for after that is
in it. The shared engine's own sentences — a matching card, what a file read back showed — are in
words.json beside this file, by the same rule.

Customer data — a file's name, what a cell holds, what a person typed — is never looked up here: it is
put into a sentence as it is.

THE DOCUMENTS. What a package writes into its files is not yet in two languages (the documents' own
pass). Until it is, a file is written in Thai whatever the run's language, and a run in another
language carries the computed table twice: as the screen reads it, and under ``document`` as its files
are written from it (``with_document`` / ``for_document``, ``speaking``).
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping, Sequence

LANGS = ("th", "en")
DOCUMENT = "th"

_lang = "th"


def lang_of(value) -> str:
    return "en" if str(value or "").lower() == "en" else "th"


def use(value) -> str:
    """The language of this run — said once by the step, before the package is asked anything."""
    global _lang
    _lang = lang_of(value)
    return _lang


def lang() -> str:
    return _lang


@contextlib.contextmanager
def speaking(value):
    """Another language for the length of a block: the Thai a file is still written in, a test's."""
    global _lang
    was, _lang = _lang, lang_of(value)
    try:
        yield
    finally:
        _lang = was


def _has_thai(text: str) -> bool:
    return any(0x0E00 <= ord(c) <= 0x0E7F for c in text)


class Words:
    """The sentences of one manifest (or of the shared engine), said in the run's language.

    ``english`` is found by the Thai sentence. Two things are not: words with no Thai in them (“VAT”,
    “PDF”) are said as they are in both languages; and where the same Thai words are said two ways in
    English — the name of a field in a sentence, and the twelve characters of a card's heading —
    ``by_key`` holds the English of the one key that differs ("en_by_key")."""

    def __init__(self, strings: dict, english: dict, by_key: dict | None = None):
        self.strings: dict[str, str] = strings or {}
        self.english: dict[str, str] = english or {}
        self.by_key: dict[str, str] = by_key or {}

    def english_of(self, key: str) -> str:
        thai = self.strings[key]
        if key in self.by_key:
            return self.by_key[key]
        return self.english[thai] if _has_thai(thai) else thai

    def __call__(self, key: str, **values) -> str:
        """The sentence ``key``, with its {places} filled."""
        text = self.english_of(key) if _lang == "en" else self.strings[key]
        return text.format(**values) if values else text

    def both(self, key: str) -> dict:
        """A sentence in both languages (words a file is read back by)."""
        return {"th": self.strings[key], "en": self.english_of(key)}

    def table(self, keys: dict, then=None) -> "Table":
        """Codes → sentences, each said when it is read (see Table). ``then`` is done to each as it is said
        (``cap`` for a table of headings)."""
        return Table(self, keys, then)

    def heads(self, keys: list) -> "Heads":
        """A table's column headings, by key, each said when it is read (AM18: a document's own words)."""
        return Heads(self, keys)


class Heads(Sequence):
    """A list of headings kept by key and said when read — made when a module is imported, like Table.
    ``index(text)`` finds a heading by its words in the language being spoken; ``of(key)`` by its key."""

    def __init__(self, words: Words, keys: list):
        self._words, self._keys = words, list(keys)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self._words(k) for k in self._keys[i]]
        return self._words(self._keys[i])

    def __len__(self):
        return len(self._keys)

    def __add__(self, other):
        return list(self) + list(other)

    def __radd__(self, other):
        return list(other) + list(self)

    def of(self, key: str) -> int:
        return self._keys.index(key)

    @property
    def keys(self) -> list:
        return list(self._keys)


class Table(Mapping):
    """A module's table of codes and their words. A table is made when its module is imported — before
    the step has said the run's language — so it keeps keys and says each sentence when it is read."""

    def __init__(self, words: Words, keys: dict, then=None):
        self._words, self._keys, self._then = words, dict(keys), then

    def __getitem__(self, code):
        text = self._words(self._keys[code])
        return self._then(text) if self._then else text

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


def clauses(*parts) -> str:
    """Short clauses side by side, as each language sets them: a space in Thai, a comma in English."""
    return (", " if _lang == "en" else " ").join(p for p in parts if p)


def cap(text: str) -> str:
    """Words at the head of a line or standing as a label: the first letter a capital (Thai has none)."""
    return text[:1].upper() + text[1:]


def and_(parts) -> str:
    """A list of two or three names in a sentence: “ก และข”, “a and b”."""
    word = SHARED("list.and")
    return (f" {word} " if _lang == "en" else f" {word}").join(parts)


def names(make) -> tuple:
    """A figure's name and the other names a question may ask about it by: ``make()`` gives (label, [names])
    in the language being spoken. In a run that is not in the documents' language a person may still ask
    by the words its document uses, so those are names of the figure too."""
    label, others = make()
    if _lang != DOCUMENT:
        with speaking(DOCUMENT):
            doc_label, doc_others = make()
        others = list(others) + [doc_label] + list(doc_others)
    return label, others


def date(value) -> str:
    """A day as a sentence says it — “5 ก.ย. 2569”, “5 Sep 2026”; "" when there is none."""
    import datetime as _dt

    from .thai import BE_OFFSET, MONTHS_EN, MONTHS_TH_ABBR

    if not value:
        return ""
    d = _dt.date.fromisoformat(value) if isinstance(value, str) else value
    if _lang == "en":
        return f"{d.day} {MONTHS_EN[d.month - 1][:3]} {d.year}"
    return f"{d.day} {MONTHS_TH_ABBR[d.month - 1]} {d.year + BE_OFFSET}"


def date_read(text) -> tuple | None:
    """A day as a document prints it in either language (“5 ก.ย. 2569”, “5 Sep 2026”), read back as
    (year, month, day) in the common era; None for anything else."""
    from .thai import BE_OFFSET, MONTHS_EN, MONTHS_TH_ABBR

    bits = str(text or "").split()
    if len(bits) != 3:
        return None
    try:
        day, year = int(bits[0]), int(bits[2])
    except ValueError:
        return None
    if bits[1] in MONTHS_TH_ABBR:
        return (year - BE_OFFSET, MONTHS_TH_ABBR.index(bits[1]) + 1, day)
    short = [m[:3] for m in MONTHS_EN]
    if bits[1] in short:
        return (year, short.index(bits[1]) + 1, day)
    return None


def period(value: str) -> str:
    """A month as a sentence says it: '2026-09' → “กันยายน 2569”, “September 2026”."""
    from .thai import MONTHS_EN, period_label_th

    if _lang == "en":
        y, m = (int(x) for x in value.split("-"))
        return f"{MONTHS_EN[m - 1]} {y}"
    return period_label_th(value)


def period_short(value: str) -> str:
    """A month as a chart's axis or a narrow column says it: '2026-09' → “ก.ย. 69”, “Sep 2026”."""
    from .thai import MONTHS_EN, period_short_th

    if _lang == "en":
        y, m = (int(x) for x in value.split("-"))
        return f"{MONTHS_EN[m - 1][:3]} {y}"
    return period_short_th(value)


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def of(package: str) -> Words:
    """The words of a package: its manifest's ``strings`` and ``en``."""
    try:
        from importlib.resources import files

        m = json.loads(files(package).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — run from a checkout whose package is not importable by that name (the tests)
        import importlib

        m = _read(os.path.join(os.path.dirname(os.path.abspath(importlib.import_module(package).__file__)), "agent.json"))
    return Words(m.get("strings"), m.get("en"), m.get("en_by_key"))


def _shared() -> Words:
    try:
        from importlib.resources import files

        m = json.loads(files(__package__).joinpath("words.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        m = _read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.json"))
    return Words(m.get("strings"), m.get("en"), m.get("en_by_key"))


SHARED = _shared()


def with_document(computed: dict, again) -> dict:
    """The computed table of a run that is not in the documents' language carries, under ``document``,
    the same table as its files are written from it: ``again`` computes it once more, in that language."""
    if _lang != DOCUMENT:
        with speaking(DOCUMENT):
            computed["document"] = again()
    return computed


def for_document(computed: dict) -> dict:
    """The computed table a file is written from."""
    return computed.get("document") or computed
