"""A document in two languages, compared cell for cell (AM18).

A package whose documents are written in the run's language makes, from identical inputs, a Thai file
and an English file. They must be the same document in two languages: the same sheets in the same order,
the same rows and columns, every number the same to the cell, every date and every period the same, the
person's own data in the same places — and the only thing that may differ is the words the document
itself says. Any other difference is a failure.

``same_but_words`` says every difference it finds, in plain words, for a test to assert empty; ``thai_left``
every piece of Thai in the English file that is not the person's own.
"""

from __future__ import annotations

import re
from decimal import Decimal

from agent_market.engine.thai import BE_OFFSET, MONTHS_EN, MONTHS_TH, MONTHS_TH_ABBR

THAI = re.compile(r"[฀-๿]")
THAI_RUN = re.compile(r"[฀-๿]+(?:[  ]*[฀-๿]+)*")
_ABBR = [m[:3] for m in MONTHS_EN]
_MONTH_WORDS = sorted(MONTHS_TH + MONTHS_TH_ABBR + MONTHS_EN + _ABBR, key=len, reverse=True)
_MONTHS = "|".join(re.escape(m) for m in _MONTH_WORDS)
DAY = re.compile(r"(?<!\d)(\d{1,2}) (%s) (\d{4})(?!\d)" % _MONTHS)
MONTH = re.compile(r"(?<!\d)(%s) (\d{4}|\d{2})(?!\d)" % _MONTHS)
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
WAYS = re.compile(r"(?<!\d)[23](?: ทาง|-way)")   # “3 ทาง” · “3-way”: the figure is the word's
PLACE = re.compile(r"\{[^{}]*\}")


def _month(word: str) -> int:
    for names in (MONTHS_TH, MONTHS_TH_ABBR, MONTHS_EN, _ABBR):
        if word in names:
            return names.index(word) + 1
    raise ValueError(word)


def _year(word: str, year: int) -> int:
    thai = word in MONTHS_TH or word in MONTHS_TH_ABBR
    if year < 100:
        year = (2500 + year) if thai else (2000 + year)
    return year - BE_OFFSET if thai else year


def numbers_of(text: str) -> list[str]:
    """The numbers a text holds, in order — a day or a period in either language read as the same day or
    month (“5 ก.ย. 2569” is “5 Sep 2026”, “กันยายน 2569” is “September 2026”), thousands separators dropped."""
    text = DAY.sub(lambda m: f"{_year(m.group(2), int(m.group(3))):04d}-{_month(m.group(2)):02d}-{int(m.group(1)):02d}", str(text))
    text = MONTH.sub(lambda m: f"{_year(m.group(1), int(m.group(2))):04d}-{_month(m.group(1)):02d}", text)
    # (the glossary's three-way / two-way match is a figure in Thai — “ตรวจ 3 ทาง” — and a word in English)
    text = WAYS.sub("way", text)
    return [n.replace(",", "") for n in NUMBER.findall(text)]


def _num(v):
    return Decimal(str(v)).quantize(Decimal("0.0001")) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) else None


def fragments_of(w) -> list[str]:
    """The pieces of Thai a manifest's sentences are made of — each string split at its {places}, spaces
    removed — longest first, so a cell's own words can be told from the document's."""
    from agent_market.engine.words import SHARED

    out = set()
    for text in list(w.strings.values()) + list(SHARED.strings.values()):
        for piece in PLACE.split(text):
            for run in THAI_RUN.findall(piece):
                s = "".join(run.split())
                if len(s) >= 2:
                    out.add(s)
    return sorted(out, key=len, reverse=True)


def own_runs(text, fragments: list[str]) -> list[str]:
    """The Thai a cell holds once the document's own words are taken out: what must be the person's."""
    out = []
    text = DAY.sub(" ", str(text))
    text = MONTH.sub(" ", text)   # (a day or a month written in Thai is not the person's words)
    for run in THAI_RUN.findall(text):
        s = "".join(run.split())
        for f in fragments:
            if f in s:
                s = s.replace(f, "\x00")
        # (a particle left between two of the document's words — นี้, ของ — is not the person's data)
        out += [p for p in s.split("\x00") if len(p) > 3]
    return out


def is_own(run: str, own: str) -> bool:
    """A run of Thai is the person's when it is among their Thai (``own``: letters only, as a test collects it
    from their files, their settings and their answers) — as a whole, or word by word when a document sets
    their words side by side (a line's kind and its description)."""
    squash = lambda t: "".join(THAI_RUN.findall("".join(str(t).split())))   # noqa: E731
    if squash(run) in own:
        return True
    pieces = [squash(p) for p in str(run).split()]
    return bool(pieces) and all(p in own for p in pieces if p)


def foreign_thai(text, own: str) -> list[str]:
    """Thai in a text that is not the person's own."""
    return [r for r in THAI_RUN.findall(str(text)) if not is_own(r, own)]


def same_but_words(th: dict, en: dict, w, sheet_names: dict | None = None, limit: int = 40) -> list[str]:
    """Every difference between a Thai workbook and an English one that is not the document's own words.

    ``th``/``en``: {sheet title: [[cell, …], …]} as a test reads a finished workbook. ``w``: the package's
    words (engine/words.of), whose sentences are what may differ. ``sheet_names``: Thai title → English
    title, when the document names its sheets (each pair must map)."""
    bad: list[str] = []
    fragments = fragments_of(w)

    def say(s):
        if len(bad) < limit:
            bad.append(s)

    th_sheets, en_sheets = list(th), list(en)
    if len(th_sheets) != len(en_sheets):
        say(f"{len(th_sheets)} sheets in Thai, {len(en_sheets)} in English: {th_sheets} / {en_sheets}")
    for t, e in zip(th_sheets, en_sheets):
        if sheet_names is not None and sheet_names.get(t) != e:
            say(f"sheet “{t}” is “{e}” in English; the manifest says “{sheet_names.get(t)}”")
        rows_t, rows_e = th[t], en[e]
        if len(rows_t) != len(rows_e):
            say(f"sheet “{t}”: {len(rows_t)} rows in Thai, {len(rows_e)} in English")
        for r, (row_t, row_e) in enumerate(zip(rows_t, rows_e), start=1):
            if len(row_t) != len(row_e):
                say(f"sheet “{t}” row {r}: {len(row_t)} cells in Thai, {len(row_e)} in English")
            for c, (a, b) in enumerate(zip(row_t, row_e), start=1):
                where = f"sheet “{t}” row {r} col {c}"
                na, nb = _num(a), _num(b)
                if na is not None or nb is not None:
                    if na != nb:
                        say(f"{where}: {a!r} in Thai, {b!r} in English")
                    continue
                if (a in (None, "")) != (b in (None, "")):
                    say(f"{where}: {a!r} in Thai, {b!r} in English (one is empty)")
                    continue
                if a in (None, ""):
                    continue
                if type(a) is not type(b):
                    say(f"{where}: {type(a).__name__} in Thai, {type(b).__name__} in English")
                    continue
                if not isinstance(a, str):
                    if a != b:
                        say(f"{where}: {a!r} in Thai, {b!r} in English")
                    continue
                # (the same numbers, whatever order the two languages' sentences put them in)
                if sorted(numbers_of(a)) != sorted(numbers_of(b)):
                    say(f"{where}: the numbers differ — {a[:120]!r} / {b[:120]!r}")
                elif own_runs(a, fragments) != own_runs(b, fragments):
                    say(f"{where}: the person's own data differs — {own_runs(a, fragments)} / {own_runs(b, fragments)} in {a[:120]!r} / {b[:120]!r}")
    return bad


def thai_left(en: dict, own: str, limit: int = 40) -> list[str]:
    """Every cell of an English workbook holding Thai that is not the person's own data."""
    out = []
    for title, rows in en.items():
        if foreign_thai(title, own):
            out.append(f"sheet name “{title}”")
        for r, row in enumerate(rows, start=1):
            for c, v in enumerate(row, start=1):
                if isinstance(v, str) and foreign_thai(v, own):
                    out.append(f"sheet “{title}” row {r} col {c}: {v[:80]!r}")
                    if len(out) >= limit:
                        return out
    return out
