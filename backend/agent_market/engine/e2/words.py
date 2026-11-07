"""Words and lines with geometry — the one page model every E2 source feeds.

A source (the PDF text layer via pdfplumber, Tesseract via the sandbox's
``extract_text.ocr_image_words``) produces words; this module groups them
into lines by geometry (not by the source's own reading order, which splits
a label from its value when they sit in different columns) and keeps, for
every character of a line's text, the word it came from — so a value found
by a regex on the line text has a page, a bounding box, a span and a
per-word confidence.

Coordinates: PDF points (1/72 in) with the origin at the top-left for PDF
sources; pixels for an image (a photo) — ``Page.unit`` says which.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .thai_text import arabic


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int              # 1-based
    conf: float = 1.0      # 0..1 (1.0 for a text layer)
    method: str = "pdf_text"
    size: float = 0.0      # font size (pdf_text) or glyph height (ocr)
    # pdf_text only: the glyphs the text layer gave no text (a lost Thai mark), as
    # ((position in text, (font index, cid)), ...) — marks.restore puts them back
    lost: tuple = ()
    restored: int = 0      # how many lost glyphs marks.restore put back
    space_before: bool = False   # the text layer printed a space before this word

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(0.1, self.y1 - self.y0)


@dataclass
class Line:
    words: list[Word]
    page: int
    text: str = ""
    spans: list[tuple[int, int, int]] = field(default_factory=list)   # (start, end, word index)
    index: int = 0          # position in the document's line list

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def size(self) -> float:
        return max((w.size for w in self.words), default=0.0)

    def words_in(self, start: int, end: int) -> list[Word]:
        return [self.words[i] for s, e, i in self.spans if s < end and e > start]

    def bbox(self, start: int, end: int) -> list[float] | None:
        ws = self.words_in(start, end)
        if not ws:
            return None
        return [round(min(w.x0 for w in ws), 1), round(min(w.y0 for w in ws), 1),
                round(max(w.x1 for w in ws), 1), round(max(w.y1 for w in ws), 1)]

    def conf(self, start: int, end: int) -> float:
        ws = self.words_in(start, end)
        return min((w.conf for w in ws), default=0.0)


@dataclass
class Page:
    number: int
    width: float
    height: float
    unit: str               # "pt" | "px"
    method: str             # pdf_text | ocr
    ocr_conf: float | None = None   # mean 0-100 when OCR'd
    dpi: int | None = None


def _is_thai(ch: str) -> bool:
    return "฀" <= ch <= "๿"


def build_lines(words: list[Word], *, gap_ratio: float = 0.28) -> list[Line]:
    """Group words into lines: same page, vertical overlap, sorted left to right.

    Two words are on one line when their vertical centres are within half
    the smaller word's height. Within a line a space is inserted where the
    gap is wider than ``gap_ratio`` × the height, except between two Thai
    glyph runs that touch (Thai is written without word spaces; OCR splits a
    run into boxes, and a space inside a word would break the label match) —
    unless the PDF text layer printed a space there: "บริษัท ไทยออฟฟิศ ซัพพลาย
    จำกัด" keeps the spaces it was printed with (labels match on skeletons,
    which have none).
    """
    by_page: dict[int, list[Word]] = {}
    for w in words:
        if w.text.strip():
            by_page.setdefault(w.page, []).append(w)
    lines: list[Line] = []
    for page in sorted(by_page):
        ws = sorted(by_page[page], key=lambda w: (w.cy, w.x0))
        rows: list[list[Word]] = []
        for w in ws:
            placed = False
            for row in reversed(rows[-4:]):
                ref = statistics.median([x.cy for x in row])
                hh = min(w.h, statistics.median([x.h for x in row]))
                # same row: centres close, or the boxes overlap by most of the smaller height
                # (OCR boxes a Thai upper mark apart from its line: its centre sits higher)
                r0 = statistics.median([x.y0 for x in row])
                r1 = statistics.median([x.y1 for x in row])
                overlap = min(w.y1, r1) - max(w.y0, r0)
                if abs(w.cy - ref) <= hh * 0.5 or overlap >= 0.6 * min(w.h, r1 - r0):
                    row.append(w)
                    placed = True
                    break
            if not placed:
                rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w.x0)
            text, spans = "", []
            prev = None
            for i, w in enumerate(row):
                t = w.text
                if prev is not None:
                    gap = w.x0 - prev.x1
                    both_thai = _is_thai(text[-1:]) and _is_thai(t[:1])
                    printed = w.space_before and w.method == prev.method == "pdf_text"
                    if (printed or not (both_thai and gap < max(prev.h, w.h) * 0.8)) and gap > min(prev.h, w.h) * gap_ratio * 0.35:
                        text += " "
                start = len(text)
                text += t
                spans.append((start, len(text), i))
                prev = w
            lines.append(Line(words=row, page=page, text=text, spans=spans))
    lines.sort(key=lambda l: (l.page, l.y0, l.x0))
    for i, l in enumerate(lines):
        l.index = i
    return lines


def norm(s: str) -> str:
    """Label-matching form: Arabic digits, no spaces (OCR and PDF text split Thai differently)."""
    return "".join(arabic(s).split())


def find_in_line(line: Line, pattern) -> list[tuple[int, int]]:
    """Regex matches on a line's text (Arabic digits), as spans of the line text."""
    import re

    t = arabic(line.text)
    return [(m.start(), m.end()) for m in re.finditer(pattern, t)]


# ── sources ────────────────────────────────────────────────────────────────

_ZERO_WIDTH = (chr(0x200B), chr(0xFEFF))
_GLYPH_MARK = chr(0)          # a lost glyph's text while the page is read: NUL + font index + cid


def _placeholder(t: str, width: float) -> bool:
    """A character the text layer gave no text: NUL (or another control character), a zero-width
    space or BOM, or a space that takes no room."""
    return not t or ord(t[0]) < 32 or t in _ZERO_WIDTH or (t == " " and width < 0.3)


def _capture_lost_glyphs(fonts: dict):
    """Wrap pdfminer's character rendering so a character the text layer gave no text carries its
    glyph — ``NUL + chr(font index) + chr(cid)`` in the private-use planes — as far as the word
    builder (pdfplumber keeps only the text of a character). Returns the restore function."""
    from pdfminer.converter import PDFLayoutAnalyzer

    orig = PDFLayoutAnalyzer.render_char

    def render_char(self, *args, **kw):
        adv = orig(self, *args, **kw)
        try:
            font = args[1] if len(args) > 1 else kw["font"]
            cid = int(args[5] if len(args) > 5 else kw["cid"])
            item = self.cur_item._objs[-1]
            t = item.get_text()
            if _placeholder(t, item.width) and 0 <= cid < 0x10000:
                if id(font) not in fonts:
                    fonts[id(font)] = (len(fonts), font)
                item._text = _GLYPH_MARK + chr(0xF0000 + fonts[id(font)][0]) + chr(0x100000 + cid)
        except Exception:  # noqa: BLE001 — without its glyph a placeholder is still a placeholder
            pass
        return adv

    PDFLayoutAnalyzer.render_char = render_char

    def undo():
        PDFLayoutAnalyzer.render_char = orig
    return undo


def _identity_fonts(pdf) -> dict[str, bool]:
    """BaseFont → its CIDToGIDMap is Identity (the default), i.e. a cid is the glyph's index in the
    embedded font program."""
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import literal_name

    out: dict[str, bool] = {}
    for p in pdf.pages:
        res = resolve1(p.page_obj.resources) or {}
        for ref in (resolve1(res.get("Font")) or {}).values():
            try:
                spec = resolve1(ref) or {}
                desc = resolve1(spec.get("DescendantFonts"))
                d = resolve1(desc[0]) if desc else {}
                c2g = resolve1(d.get("CIDToGIDMap")) if d else None
                out[literal_name(spec.get("BaseFont"))] = c2g is None or (
                    not hasattr(c2g, "get_data") and literal_name(c2g) == "Identity")
            except Exception:  # noqa: BLE001
                continue
    return out


def split_lost(chars: list[tuple[str | None, object]]) -> tuple[str, tuple]:
    """[(character, None) | (None, glyph)] → (text, ((position, glyph), ...)).

    Sara am written as nikhahit + sara aa is rejoined (ํ + า → ำ), across lost glyphs between the
    two (they stay just before it: they belong to the same consonant)."""
    out: list[tuple[str | None, object]] = []
    i = 0
    while i < len(chars):
        t = chars[i][0]
        if t == "ํ":
            j = i + 1
            while j < len(chars) and chars[j][0] is None:
                j += 1
            if j < len(chars) and chars[j][0] == "า":
                out.extend(chars[i + 1:j])
                out.append(("ำ", None))
                i = j + 1
                continue
        out.append(chars[i])
        i += 1
    text: list[str] = []
    lost = []
    for t, g in out:
        if t is None:
            lost.append((len(text), g))
        else:
            text.append(t)
    return "".join(text), tuple(lost)


def pdf_text_words(path: str, fonts: dict | None = None) -> tuple[list[Word], list[Page]]:
    """Words of the PDF text layer, top-left origin, points.

    Built from the characters in *content-stream* order (the order the PDF
    writer emitted them, which is the logical Thai order), not sorted by x
    (which moves a vowel mark past the next consonant). A word ends at a
    real space, a horizontal gap or a change of row. A NUL or zero-width
    character is not a space: Chrome/Skia writes one where a positional Thai
    mark glyph had no Unicode mapping. The mark is lost from the text (label
    matching is done on skeletons, thai_text.S); the word keeps where it was
    and which glyph it was (``Word.lost``), for marks.restore. Sara am split
    into ํ + า is rejoined.

    ``fonts``, when given, is filled with what marks.restore needs to know of
    each font that has a lost glyph: {font index: {"basefont", "tounicode"
    {cid: text}, "program" (TrueType bytes or None), "identity"}}.
    """
    import pdfplumber

    seen: dict = {}
    anon = [0]
    words, pages = [], []
    undo = _capture_lost_glyphs(seen)
    try:
        with pdfplumber.open(path) as pdf:
            for n, p in enumerate(pdf.pages, start=1):
                pages.append(Page(number=n, width=float(p.width), height=float(p.height), unit="pt", method="pdf_text"))
                cur: list[tuple[dict | None, object]] = []
                space = [False]

                def flush(space_next: bool):
                    real = [c for c, _ in cur if c is not None]
                    if real:
                        text, lost = split_lost([(None, g) if c is None else (c["text"], None) for c, g in cur])
                        if text.strip():
                            words.append(Word(text=text, x0=min(float(c["x0"]) for c in real),
                                              y0=min(float(c["top"]) for c in real),
                                              x1=max(float(c["x1"]) for c in real), y1=max(float(c["bottom"]) for c in real),
                                              page=n, conf=1.0, method="pdf_text",
                                              size=max(float(c.get("size") or 0) for c in real),
                                              lost=lost, space_before=space[0]))
                    cur.clear()
                    space[0] = space_next

                prev = None
                for c in p.chars:
                    t = c["text"]
                    width = float(c["x1"]) - float(c["x0"])
                    if _placeholder(t, width):
                        # a lost mark, not a word break: remembered with its glyph when it has one
                        if cur:
                            if len(t) == 3 and t[0] == _GLYPH_MARK:
                                g = (ord(t[1]) - 0xF0000, ord(t[2]) - 0x100000)
                            else:
                                anon[0] += 1
                                g = ("?", anon[0])          # no glyph known: its own, never pooled
                            cur.append((None, g))
                        continue
                    if t.isspace():
                        flush(True)
                        prev = None
                        continue
                    if prev is not None:
                        same_row = abs(float(c["top"]) - float(prev["top"])) < max(2.0, 0.45 * float(prev.get("size") or 10))
                        gap = float(c["x0"]) - float(prev["x1"])
                        size = float(c.get("size") or prev.get("size") or 10)
                        zero = width < 0.3            # a combining mark sits over its base: never a break
                        if not same_row or (not zero and (gap > 0.25 * size or gap < -0.6 * size)):
                            flush(False)
                    cur.append((c, None))
                    if width >= 0.3:
                        prev = c
                flush(False)
            identity = _identity_fonts(pdf) if seen and fonts is not None else {}
    finally:
        undo()
    if fonts is not None:
        for i, font in seen.values():
            um = getattr(font, "unicode_map", None)
            ff = getattr(font, "fontfile", None)
            base = getattr(font, "basefont", None)
            try:
                program = ff.get_data() if ff is not None else None
            except Exception:  # noqa: BLE001
                program = None
            fonts[i] = {"basefont": base, "tounicode": dict(getattr(um, "cid2unichr", None) or {}),
                        "program": program, "identity": identity.get(base, False)}
    return words, pages


_NOISE_CHARS = set("|'\"`.,:;_-~=+*^¢©®°•·[](){}<>/\\")


def _is_noise(w: dict) -> bool:
    """A Tesseract "word" that is scan noise or a table border, not text: a short fragment
    read with very low confidence, or nothing but border / punctuation marks."""
    t = w["text"].strip()
    if not t or all(ch in _NOISE_CHARS for ch in t):
        return True
    return 0 <= w["conf"] < 35 and len(t) <= 3 and not any(ch.isdigit() for ch in t)


def ocr_words_from(result: dict, page: int, *, scale: float, method: str = "ocr") -> list[Word]:
    """``extract_text.ocr_image_words`` output → Words (pixels × ``scale`` → page units)."""
    out = []
    for w in result["words"]:
        if _is_noise(w):
            continue
        conf = w["conf"] / 100.0 if w["conf"] >= 0 else 0.0
        out.append(Word(text=w["text"], x0=w["left"] * scale, y0=w["top"] * scale,
                        x1=(w["left"] + w["width"]) * scale, y1=(w["top"] + w["height"]) * scale,
                        page=page, conf=conf, method=method, size=w["height"] * scale))
    return out
