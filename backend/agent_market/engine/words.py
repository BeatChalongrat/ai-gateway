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
from collections.abc import Mapping

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


class Words:
    """The sentences of one manifest (or of the shared engine), said in the run's language."""

    def __init__(self, strings: dict, english: dict):
        self.strings: dict[str, str] = strings or {}
        self.english: dict[str, str] = english or {}

    def __call__(self, key: str, **values) -> str:
        """The sentence ``key``, with its {places} filled."""
        thai = self.strings[key]
        text = self.english[thai] if _lang == "en" else thai
        return text.format(**values) if values else text

    def both(self, key: str) -> dict:
        """A sentence in both languages (words a file is read back by)."""
        thai = self.strings[key]
        return {"th": thai, "en": self.english[thai]}

    def table(self, keys: dict) -> "Table":
        """Codes → sentences, each said when it is read (see Table)."""
        return Table(self, keys)


class Table(Mapping):
    """A module's table of codes and their words. A table is made when its module is imported — before
    the step has said the run's language — so it keeps keys and says each sentence when it is read."""

    def __init__(self, words: Words, keys: dict):
        self._words, self._keys = words, dict(keys)

    def __getitem__(self, code):
        return self._words(self._keys[code])

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


def period(value: str) -> str:
    """A month as a sentence says it: '2026-09' → “กันยายน 2569”, “September 2026”."""
    from .thai import MONTHS_EN, period_label_th

    if _lang == "en":
        y, m = (int(x) for x in value.split("-"))
        return f"{MONTHS_EN[m - 1]} {y}"
    return period_label_th(value)


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
    return Words(m.get("strings"), m.get("en"))


def _shared() -> Words:
    try:
        from importlib.resources import files

        m = json.loads(files(__package__).joinpath("words.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        m = _read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.json"))
    return Words(m.get("strings"), m.get("en"))


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
