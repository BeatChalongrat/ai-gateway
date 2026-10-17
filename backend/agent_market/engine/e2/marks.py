"""Thai marks a PDF text layer lost, put back from the document itself (E2, AM4).

Chrome/Skia — the PDF writer behind many Thai accounting systems' PDFs —
draws a Thai mark in a shifted position with its own glyph (sara i moved
left over ฟ, a tone mark raised over an upper vowel, the nikhahit + tone
mark ligature of น้ำ) and its ToUnicode map gives that glyph no text: the
text layer has a NUL where the mark was. Consonants, vowels, digits and
letters are intact, so label matching (thai_text.S) and a party's key never
needed the marks — but the register recorded "ไทยออฟฟศ" for the seller
ไทยออฟฟิศ and "นิว" for นิ้ว.

A lost glyph is one glyph of one embedded font, and it shows the same
mark(s) wherever it is used. So the text layer is repaired glyph by glyph,
as a complete ToUnicode map would have read it (words.pdf_text_words keeps
each lost glyph's font and cid):

* the font names a glyph when it can: a composite glyph is its components
  (the ligature of น้ำ is nikhahit + a tone-mark glyph), and a glyph whose
  outline is a mapped mark glyph's outline, moved or uniformly scaled (a
  "small" tone mark), is that mark;
* otherwise the page names it. Each word holding the glyph is cut from the
  rendered page and read by Tesseract. A reading is a vote for the glyph
  only when it is the layer's word with marks added exactly where the layer
  lost a glyph: the same consonants, vowels, digits and letters in the same
  order (thai_text.skel), the same marks wherever the layer printed them,
  each added cluster well-formed Thai. A glyph needs two agreeing votes (at
  least 3 in 4 of its votes): one reading alone decides nothing, since
  Tesseract can read ่ as ้ with high confidence. A glyph used once has its
  word read again at other scales for a second vote.

A word is rewritten only when every glyph it lost is known, and then only
its lost glyphs become marks. A consonant, a digit or a letter can never
change — so neither a number, a tax ID nor the party a name identifies —
and a word the document cannot prove stays as the text layer wrote it.
"""

from __future__ import annotations

import io
from collections import Counter

from .thai_text import _MARKS, skel

NIKHAHIT, SARA_AA, SARA_AM = "ํ", "า", "ำ"
_UPPER = set("ัิีึื็")
_LOWER = set("ฺุู")
_TONE = set("่้๊๋์")
# a cluster's marks are written vowel first, then the tone mark, then the nikhahit, which meets
# the sara aa after it as sara am (น + ้ + ำ)
_ORDER = {m: i for i, m in enumerate("ัิีึื็ฺุู่้๊๋์๎ํ")}
RENDER_DPI = 300
SCALES = (1.5, 2.0, 1.0)     # reading passes over the 300 DPI crop (stacked marks are small)
OCR_CONFIG = "--oem 1 --psm 7"   # one line of text
OCR_LANG = "tha"             # the words are Thai; the Thai model alone reads them as well, 3× faster


def _ordered(ms) -> list[str]:
    return sorted(ms, key=lambda m: _ORDER.get(m, 99))


# ── clusters ────────────────────────────────────────────────────────────────

def clusters(text: str, lost: tuple = ()):
    """(bases, the marks of each base, the lost glyphs of each base) of ``text``, or (None, None,
    None) when a mark or a lost glyph comes before any base (never repaired).

    ``lost`` is ((position in text, glyph), ...): a glyph the layer gave no text, just before
    ``text[position]``. Sara am counts as nikhahit + sara aa; its sara aa is a base."""
    at: dict[int, list] = {}
    for pos, g in lost:
        at.setdefault(pos, []).append(g)
    bases: list[str] = []
    marks: list[list[str]] = []
    glyphs: list[list] = []
    head = False
    for i in range(len(text) + 1):
        for g in at.get(i, ()):
            if bases:
                glyphs[-1].append(g)
            else:
                head = True
        if i == len(text):
            break
        for c in ((NIKHAHIT, SARA_AA) if text[i] == SARA_AM else (text[i],)):
            if c.isspace() or ord(c) < 32:
                continue
            if c in _MARKS:
                if bases:
                    marks[-1].append(c)
                else:
                    head = True
            else:
                bases.append(c)
                marks.append([])
                glyphs.append([])
    return (None, None, None) if head else (bases, marks, glyphs)


def well_formed(base: str, ms: list[str], next_base: str | None) -> bool:
    """A Thai cluster: marks only on a consonant; at most one upper vowel, one lower vowel, one tone
    mark; a nikhahit only as the first half of sara am; mai taikhu takes no tone mark."""
    if not ms:
        return True
    if not ("ก" <= base <= "ฮ") or len(set(ms)) != len(ms):
        return False
    up = [m for m in ms if m in _UPPER]
    lo = [m for m in ms if m in _LOWER]
    to = [m for m in ms if m in _TONE]
    if len(up) > 1 or len(lo) > 1 or len(to) > 1:
        return False
    if NIKHAHIT in ms and (up or lo or next_base != SARA_AA):
        return False
    return not ("็" in ms and to)


def added_marks(text: str, lost: tuple, read: str) -> dict | None:
    """The marks a reading of the page adds where the layer lost a glyph: {glyph: marks}, or None
    when the reading is not the layer's word with marks added only there (another skeleton, a mark
    the layer printed missing, a mark added where nothing was lost, the lost glyph not seen, a
    cluster that is not Thai). A cluster that lost two glyphs gives no vote."""
    if not read or skel(read) != skel(text):
        return None
    lb, lm, lg = clusters(text, lost)
    rb, rm, _ = clusters(read)
    if lb is None or rb is None or lb != rb:
        return None
    out: dict = {}
    for i, (have, got, gs) in enumerate(zip(lm, rm, lg)):
        rest = list(got)
        for m in have:
            if m not in rest:
                return None
            rest.remove(m)
        if not gs:
            if rest:
                return None
            continue
        if not rest or not well_formed(lb[i], got, lb[i + 1] if i + 1 < len(lb) else None):
            return None
        if len(gs) == 1:
            out[gs[0]] = "".join(_ordered(rest))
    return out


def rewrite(text: str, lost: tuple, known: dict) -> str | None:
    """``text`` with each lost glyph replaced by its known marks, or None when one is not known or a
    cluster would not be Thai. Nothing else of the text changes."""
    if not lost or any(known.get(g) is None for _, g in lost):
        return None
    lb, lm, lg = clusters(text, lost)
    if lb is None:
        return None
    fill: list = []
    for i, (have, gs) in enumerate(zip(lm, lg)):
        if not gs:
            fill.append(None)
            continue
        ms = list(have) + [m for g in gs for m in known[g]]
        if not well_formed(lb[i], ms, lb[i + 1] if i + 1 < len(lb) else None):
            return None
        fill.append(_ordered(ms))
    out: list[str] = []
    bi, filled = -1, False
    for ch in text.replace(SARA_AM, NIKHAHIT + SARA_AA):
        if ch in _MARKS:
            if not filled:
                out.append(ch)
            continue
        out.append(ch)
        if not ch.isspace() and ord(ch) >= 32:
            bi += 1
            filled = fill[bi] is not None
            if filled:
                out.extend(fill[bi])
    return "".join(out).replace(NIKHAHIT + SARA_AA, SARA_AM)


# ── what the font says ──────────────────────────────────────────────────────

class FontMarks:
    """The marks an embedded TrueType font's glyphs show, from the font alone.

    ``info`` (words.pdf_text_words): {"tounicode": {cid: text}, "program": bytes | None,
    "identity": the CIDToGIDMap is Identity}. Without a readable program with an Identity map only
    the ToUnicode map is used."""

    def __init__(self, info: dict):
        self.tounicode = info.get("tounicode") or {}
        self.tt = None
        self.by_cmap: dict[int, str] = {}
        self._outline: dict = {}
        if info.get("program") and info.get("identity"):
            try:
                from fontTools.ttLib import TTFont

                tt = TTFont(io.BytesIO(info["program"]))
                self.order = tt.getGlyphOrder()
                self.gid = {n: i for i, n in enumerate(self.order)}
                cmap = (tt["cmap"].getBestCmap() or {}) if "cmap" in tt else {}
                self.by_cmap = {self.gid[n]: chr(u) for u, n in cmap.items() if n in self.gid}
                self.glyf = tt["glyf"]
                self.tt = tt
            except Exception:  # noqa: BLE001 — a font fontTools cannot read says nothing
                self.tt = None

    def mapped(self, gid: int) -> str | None:
        """The text the PDF or the font's own cmap gives a glyph, when it is Thai marks only."""
        for t in (self.tounicode.get(gid), self.by_cmap.get(gid)):
            if t and all(ch in _MARKS for ch in t):
                return t
        return None

    def components(self, gid: int) -> list[int] | None:
        try:
            g = self.glyf[self.order[gid]]
            return [self.gid[c.glyphName] for c in g.components] if g.isComposite() else None
        except Exception:  # noqa: BLE001
            return None

    def outline(self, gid: int):
        """(contour ends, on-curve flags, points moved to the origin and scaled to a unit size)."""
        if gid not in self._outline:
            shape = None
            try:
                g = self.glyf[self.order[gid]]
                if not g.isComposite() and g.numberOfContours > 0:
                    coords, ends, flags = g.getCoordinates(self.glyf)
                    x0 = min(x for x, _ in coords)
                    y0 = min(y for _, y in coords)
                    size = max(max(x for x, _ in coords) - x0, max(y for _, y in coords) - y0, 1)
                    shape = (tuple(ends), tuple(f & 1 for f in flags),
                             tuple(((x - x0) / size, (y - y0) / size) for x, y in coords))
            except Exception:  # noqa: BLE001
                shape = None
            self._outline[gid] = shape
        return self._outline[gid]

    @staticmethod
    def _same(a, b, tol: float = 0.01) -> bool:
        """One outline, moved or uniformly scaled: the same contours and on-curve points, every point
        within ``tol`` of the glyph's size."""
        if a is None or b is None or a[0] != b[0] or a[1] != b[1]:
            return False
        return all(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol for p, q in zip(a[2], b[2]))

    def same_outline(self, gid: int) -> str | None:
        """The mark whose mapped glyph has this glyph's outline, when exactly one mark does."""
        shape = self.outline(gid)
        if shape is None:
            return None
        hits = set()
        for other in set(self.tounicode) | set(self.by_cmap):
            t = self.mapped(other)
            if t and len(t) == 1 and other != gid and self._same(self.outline(other), shape):
                hits.add(t)
        return hits.pop() if len(hits) == 1 else None


# ── deciding a glyph ────────────────────────────────────────────────────────

def by_votes(vs: list) -> str | None:
    """The reading at least two votes agree on, when they are at least 3 in 4 of the votes."""
    if not vs:
        return None
    (best, n), = Counter(vs).most_common(1)
    return best if n >= 2 and n * 4 >= 3 * len(vs) else None


def decide(glyphs: set, votes: dict, fm: dict) -> dict:
    """{glyph: marks or None}; a glyph is (font index, cid); ``fm`` {font index: FontMarks}.

    The font first: the text it maps a glyph to, a composite as its parts, the outline of a mapped
    mark glyph. A composite is never decided by the votes for the whole glyph (OCR reads the
    nikhahit + tone ligature of น้ำ as the tone mark alone): the one part the font cannot name takes
    the composite's votes less the parts the font does name, with that part's own votes. Any other
    glyph is decided by its votes (``by_votes``)."""
    votes = {g: list(v) for g, v in votes.items()}
    font_known: dict = {}
    parts_of: dict = {}

    def from_font(g, depth=0):
        if g in font_known:
            return font_known[g]
        font_known[g] = None
        fi, cid = g
        f = fm.get(fi) if isinstance(cid, int) else None
        res = None
        if f is not None and depth < 4:
            res = f.mapped(cid)
            if res is None and f.tt is not None:
                parts = f.components(cid)
                if parts:
                    parts_of[g] = [(fi, p) for p in parts]
                    got = [from_font(p, depth + 1) for p in parts_of[g]]
                    if all(x is not None for x in got):
                        res = "".join(got)
                else:
                    res = f.same_outline(cid)
        font_known[g] = res
        return res

    for g in glyphs:
        from_font(g)
    for g, parts in parts_of.items():
        if font_known.get(g) is not None:
            continue
        unknown = [p for p in parts if font_known.get(p) is None]
        if len(unknown) != 1:
            continue
        named = [m for p in parts if p not in unknown for m in font_known[p]]
        for v in votes.get(g, ()):
            rest = list(v)
            for m in named:
                if m in rest:
                    rest.remove(m)
            if rest:
                votes.setdefault(unknown[0], []).append("".join(_ordered(rest)))
    known: dict = {}
    for g in glyphs:
        res = font_known.get(g)
        if res is None and g in parts_of:
            got = [font_known.get(p) or by_votes(votes.get(p, [])) for p in parts_of[g]]
            res = "".join(got) if all(x is not None for x in got) else None
        elif res is None:
            res = by_votes(votes.get(g, []))
        known[g] = "".join(_ordered(res)) if res and all(ch in _MARKS for ch in res) else None
    return known


# ── reading the page ────────────────────────────────────────────────────────

def _thai(s: str) -> bool:
    return any("฀" <= ch <= "๿" for ch in s)


def _crop(img, w, dpi: int):
    """The word's box on the rendered page, with room above for stacked marks."""
    s = dpi / 72.0
    h = max(1.0, w.y1 - w.y0)
    return img.crop((int((w.x0 - 0.12 * h) * s), int((w.y0 - 0.45 * h) * s),
                     int((w.x1 + 0.12 * h) * s) + 1, int((w.y1 + 0.3 * h) * s) + 1))


def _read(ocr_fn, crop, scale: float) -> str:
    """One line of text read from the crop (upscaled, on a white margin)."""
    from PIL import Image

    img = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    pad = int(0.4 * img.height)
    canvas = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 255)
    canvas.paste(img, (pad, pad))
    try:
        res = ocr_fn(canvas, OCR_CONFIG, lang=OCR_LANG)
    except TypeError:            # an extract_text without a language choice (older sandbox image)
        res = ocr_fn(canvas, OCR_CONFIG)
    return "".join(o["text"] for o in sorted(res.get("words") or [], key=lambda o: o["left"]))


def restore(path: str, words: list, fonts: dict, ocr_fn=None, render=None, *, dpi: int = RENDER_DPI) -> dict:
    """Put back the marks of the damaged words (in place: ``Word.text``, ``Word.lost``,
    ``Word.restored``). ``ocr_fn(image, config, lang=)`` / ``render(path, page index, dpi)`` are
    extract_text's; without them (or when they fail) only what the font names is restored.

    Words are read only for glyphs the font does not name: at 1.5× first, then — for glyphs still
    short of two agreeing votes — at 2× and at 1×. Returns {words, glyphs, decided, restored}."""
    damaged = [w for w in words if w.lost and _thai(w.text)]
    if not damaged:
        return {"words": 0, "glyphs": 0, "decided": 0, "restored": 0}
    glyphs = {g for w in damaged for _, g in w.lost}
    key = _memo_key(path, damaged, ocr_fn, render, dpi)
    known = _DECIDED.get(key) if key is not None else None
    if known is None:
        known = _decide_document(path, damaged, glyphs, fonts, ocr_fn, render, dpi)
        if key is not None:
            if len(_DECIDED) >= _DECIDED_MAX:
                _DECIDED.pop(next(iter(_DECIDED)))
            _DECIDED[key] = known
    restored = 0
    for w in damaged:
        new = rewrite(w.text, w.lost, known)
        if new is not None:
            w.restored, w.text, w.lost = len(w.lost), new, ()
            restored += 1
    return {"words": len(damaged), "glyphs": len(glyphs), "decided": sum(1 for g in glyphs if known.get(g)),
            "restored": restored}


# A document's lost glyphs are decided once per process: the same bytes, words and OCR give the
# same decisions (the eval set reads each fixture many times; a run reads each file once).
_DECIDED: dict = {}
_DECIDED_MAX = 256


def _memo_key(path: str, damaged: list, ocr_fn, render, dpi: int):
    try:
        import hashlib

        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        key = (digest, ocr_fn, render, dpi, tuple((w.page, w.text, w.lost) for w in damaged))
        hash(key)
        return key
    except Exception:  # noqa: BLE001 — unhashable or unreadable: decide without the memo
        return None


def _decide_document(path: str, damaged: list, glyphs: set, fonts: dict, ocr_fn, render, dpi: int) -> dict:
    """{glyph: marks or None} for one document: the font first, then readings of the page."""
    fm = {fi: FontMarks(info) for fi, info in (fonts or {}).items()}
    votes: dict = {}
    known = decide(glyphs, votes, fm)
    if ocr_fn is not None and render is not None:
        pages: dict = {}
        crops: dict = {}
        for scale in SCALES:
            todo = [w for w in damaged if any(known.get(g) is None for _, g in w.lost)]
            if not todo:
                break
            for w in todo:
                try:
                    if id(w) not in crops:
                        if w.page not in pages:
                            pages[w.page] = render(path, w.page - 1, dpi).convert("L")
                        crops[id(w)] = _crop(pages[w.page], w, dpi)
                    read = _read(ocr_fn, crops[id(w)], scale)
                except Exception:  # noqa: BLE001 — no reading is a reading that says nothing
                    continue
                for g, ms in (added_marks(w.text, w.lost, read) or {}).items():
                    votes.setdefault(g, []).append(ms)
            known = decide(glyphs, votes, fm)
        pages.clear()
        crops.clear()
    return known


# ── a text a model wrote (AM24) ─────────────────────────────────────────────
#
# The same rules read a commentary a model wrote: a mark written twice on one base (ร่่าง, กัับ) is a
# slip of the bytes with exactly one correct reading, and so is a cluster whose marks are all there in
# another order (a tone mark before its vowel). Anything else that is not Thai — two tone marks, a
# mark on a vowel or a space — has no reading the text proves, and is never mended.

def slips(text: str) -> list[dict]:
    """Each cluster of ``text`` whose marks are not as Thai writes them: {at, written, as}, where ``at``
    is the base's index, ``written`` the base and its marks as written, and ``as`` the same cluster with
    each mark once in Thai's order — or None when that is still not Thai."""
    out: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        j = i + 1 if text[i] not in _MARKS else i
        while j < n and text[j] in _MARKS:
            j += 1
        ms = list(text[i + 1:j]) if text[i] not in _MARKS else list(text[i:j])
        if text[i] in _MARKS or text[i].isspace():
            # marks with no base before them: nothing to read them as
            if ms:
                out.append({"at": i, "written": text[i:j], "as": None})
            i = j
            continue
        if ms:
            am = j < n and text[j] == SARA_AM
            nb = SARA_AA if am else (text[j] if j < n else None)
            if not _as_thai(text[i], ms, am, nb):
                once = _ordered(list(dict.fromkeys(ms)))
                out.append({"at": i, "written": text[i:j],
                            "as": text[i] + "".join(once) if _as_thai(text[i], once, am, nb) else None})
        i = j
    return out


def _as_thai(base: str, ms: list[str], am: bool, next_base: str | None) -> bool:
    """A cluster as Thai writes it: well formed (with the nikhahit of a sara am after it), in order."""
    full = list(ms) + ([NIKHAHIT] if am else [])
    return well_formed(base, full, next_base) and full == _ordered(full)


def mend(text: str) -> dict:
    """{slips, mended}: ``mended`` is ``text`` with every slip written as Thai writes it, or None when
    one of them has no such reading (``slips`` empty: the text is as Thai writes it)."""
    found = slips(text)
    if not found or any(s["as"] is None for s in found):
        return {"slips": found, "mended": None}
    out, last = [], 0
    for s in found:
        out.append(text[last:s["at"]])
        out.append(s["as"])
        last = s["at"] + len(s["written"])
    out.append(text[last:])
    return {"slips": found, "mended": "".join(out)}
