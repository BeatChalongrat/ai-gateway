"""Field location on a Thai invoice or receipt (E2, AM2 Phase C).

Deterministic and model-free. Input: the document's lines (words.py) and
pages. Output: Fields with their sources. Nothing here decides whether a
value is *trusted* — confidence and the checks do that. Everything is
anchored on printed labels first; the few heuristics without a label (the
seller's name at the top, a POS date) are marked so and weigh less.

Thai labels are matched on skeletons (thai_text.S): a PDF text layer loses
or reorders Thai marks, OCR misreads them, and neither changes the
consonant skeleton of "จำนวนเงินรวมทั้งสิ้น".

Layout facts this relies on, all common to Thai invoices:
* the seller sits at the top of page 1; the buyer after a label
  (ลูกค้า, ผู้ซื้อ, นามผู้ซื้อ, ผู้ว่าจ้าง, ได้รับเงินจาก, Bill To, Customer);
* the title (ใบกำกับภาษี …) is on page 1, near the top or in the largest type;
* the totals are labelled rows below the item table, on the last page;
* the item table has a header row naming its columns.
Lines are geometric rows (words.build_lines), so a row can hold two
columns (the seller name and the title; a buyer box and the invoice meta):
labels are matched inside horizontal *segments*, split at wide gaps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from . import thai_text as T
from .schema import Field, Source
from .thai_text import S, sfinditer, ssearch
from .words import Line, Page, Word


# ── segments: a geometric line split at wide horizontal gaps ─────────────

@dataclass
class Seg:
    line: Line
    start: int          # span in line.text
    end: int

    @property
    def text(self) -> str:
        return self.line.text[self.start:self.end]

    @property
    def x0(self) -> float:
        ws = self.line.words_in(self.start, self.end)
        return min(w.x0 for w in ws) if ws else self.line.x0

    @property
    def x1(self) -> float:
        ws = self.line.words_in(self.start, self.end)
        return max(w.x1 for w in ws) if ws else self.line.x1

    def first_word(self) -> Word | None:
        ws = self.line.words_in(self.start, self.end)
        return ws[0] if ws else None


def segments(line: Line, gap_factor: float = 2.2) -> list[Seg]:
    out, cur_start, prev, prev_end = [], None, None, 0
    for s, e, i in line.spans:
        w = line.words[i]
        if prev is not None and w.x0 - prev.x1 > gap_factor * max(prev.h, w.h):
            out.append(Seg(line, cur_start, prev_end))
            cur_start = None
        if cur_start is None:
            cur_start = s
        prev, prev_end = w, e
    if cur_start is not None:
        out.append(Seg(line, cur_start, prev_end))
    return out


def _src(line: Line, s: int, e: int, raw: str | None = None) -> Source:
    ws = line.words_in(s, e)
    return Source(page=line.page, bbox=line.bbox(s, e), span=[s, e],
                  method=ws[0].method if ws else "pdf_text",
                  raw_text=(raw if raw is not None else line.text).strip()[:200], line=line.index)


def _field(value, normalized, line: Line, s: int, e: int, *, raw: str | None = None, anchored: bool = True) -> Field:
    conf = line.conf(s, e)
    checks = [] if anchored else ["unlabelled"]
    if any(w.restored for w in line.words_in(s, e)):
        checks.append("thai_marks_restored")      # marks the text layer lost, put back (marks.py)
    return Field(value=value, normalized=normalized, confidence=conf if anchored else conf * 0.9,
                 source=_src(line, s, e, raw=raw), checks=checks)


# ── document type and title ─────────────────────────────────────────────────

DOC_TYPES = [(d, S(rx, re.I)) for d, rx in T.DOC_TYPES]
NOT_A_TITLE = S(r"ใบกำกับภาษีจะ|จะออกใบกำกับภาษี|ออกให้เมื่อ|เมื่อได้รับชำระ")


def title_lines(lines: list[Line], pages: list[Page]) -> list[Line]:
    p1 = [l for l in lines if l.page == 1]
    if not p1:
        return []
    h = pages[0].height
    sizes = sorted({round(l.size, 1) for l in p1 if l.size}, reverse=True)
    big = sizes[min(1, len(sizes) - 1)] if sizes else 0
    top = [l for l in p1 if l.y0 < h * 0.35]
    large = [l for l in p1 if big and l.size >= big]
    seen, out = set(), []
    for l in sorted(large, key=lambda l: -l.size) + top:
        if l.index not in seen:
            seen.add(l.index)
            out.append(l)
    return out


def document_type(lines: list[Line], pages: list[Page]) -> Field:
    cands = title_lines(lines, pages)
    for dtype, rx in DOC_TYPES:
        for line in cands:
            for seg in segments(line):
                if ssearch(NOT_A_TITLE, seg.text):
                    continue
                hit = ssearch(rx, T.arabic(seg.text))
                if hit:
                    s, e = seg.start + hit[0], seg.start + hit[1]
                    return Field(value=seg.text.strip(), normalized=dtype, confidence=line.conf(seg.start, seg.end),
                                 source=_src(line, s, e, raw=seg.text))
    return Field(value=None, normalized="unknown", confidence=0.0)


COPY = S(r"สำเนา|COPY", re.I)
ORIGINAL = S(r"ต้นฉบับ|ORIGINAL", re.I)


def original_or_copy(lines: list[Line], pages: list[Page]) -> str:
    for l in title_lines(lines, pages) + [l for l in lines if l.page == 1][:40]:
        if ssearch(COPY, l.text):
            return "copy"
    for l in lines:
        if ssearch(ORIGINAL, l.text):
            return "original"
    return "unmarked"


# ── parties ──────────────────────────────────────────────────────────────────

BUYER_LABEL = S(r"ลูกค้า|ผู้ซื้อ|นามผู้ซื้อ|ชื่อลูกค้า|ผู้ว่าจ้าง|ได้รับเงินจาก|Bill\s*To|Sold\s*To|Customer", re.I)
META_LABEL = S(r"^(?:เลขที่|วันที่|เงื่อนไข|ครบกำหนด|วันครบกำหนด|กำหนดชำระ|Invoice\s*No|Invoice\s*Date|Date|Due)", re.I)


@dataclass
class Region:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def holds(self, w: Word | None) -> bool:
        return w is not None and w.page == self.page and self.x0 - 2 <= w.cx <= self.x1 + 2 and self.y0 - 2 <= w.cy <= self.y1 + 2


def buyer_region(lines: list[Line], pages: list[Page], table_top: float | None) -> tuple[Region | None, Seg | None]:
    for line in lines:
        if line.page != 1:
            continue
        for seg in segments(line):
            if not ssearch(BUYER_LABEL, seg.text):
                continue
            lh = max(1.0, line.y1 - line.y0)
            y1 = table_top - 1 if table_top and table_top > line.y0 else line.y0 + 7 * lh
            y1 = min(y1, line.y0 + 9 * lh)
            x1 = pages[0].width
            # a right-hand column of meta labels (เลขที่ / วันที่) at the same height bounds the box
            for l2 in lines:
                if l2.page == 1 and line.y0 - 2 <= l2.y0 <= y1:
                    for s2 in segments(l2):
                        if s2.x0 > seg.x0 + 40 and ssearch(META_LABEL, s2.text.strip()) and s2.x0 < x1:
                            x1 = s2.x0 - 1
            return Region(1, seg.x0 - 2, line.y0, x1, y1), seg
    return None, None


LEGAL = [(S(rx), form) for rx, form in T.LEGAL_FORMS_SKEL]
PARTY_WORD = S(r"บริษัท|บจก\.?|บมจ\.?|หจก\.?|หสน\.?|ห้างหุ้นส่วน|ร้าน|Pte\.?\s*Ltd|Co\.?,?\s*Ltd|Limited|Inc\.")
NAME_STOP = S(r"ที่อยู่|เลขประจำตัว|TaxID|โทร|Tel|สำนักงานใหญ่|สาขา")


def party_from(text: str) -> tuple[int, int, dict] | None:
    """(start, end, {legal_form, core, key}) of the party name inside ``text``."""
    hit = ssearch(PARTY_WORD, text)
    if not hit:
        return None
    s = hit[0]
    stop = ssearch(NAME_STOP, text, s + 1)
    e = stop[0] if stop else len(text)
    if re.match(r"(?:Pte|Co|Limited|Inc)", text[s:], re.I):
        # an English name puts its form last ("Cloudnine Software Pte. Ltd."): the name
        # starts at the beginning of the segment and ends with the form
        m = re.search(r"(?:Pte\.?\s*Ltd\.?|Co\.?,?\s*Ltd\.?|Limited|Inc\.?)", text[s:], re.I)
        e = s + m.end()
        s = 0
    # a name cut before "(สำนักงานใหญ่)" leaves its opening bracket behind
    name = re.sub(r"\s*\(\s*$", "", text[s:e]).strip()
    if not name:
        return None
    lf = T.legal_form(name)
    if not lf:
        return None
    return s, s + len(name), lf


def party_names(lines: list[Line], pages: list[Page], breg: Region | None, bseg: Seg | None) -> tuple[Field, Field]:
    seller, buyer = Field(), Field()
    p1 = [l for l in lines if l.page == 1]
    if bseg is not None and breg is not None:
        hit = ssearch(BUYER_LABEL, bseg.text)
        after_start = bseg.start + (hit[1] if hit else 0)
        after = bseg.line.text[after_start:bseg.end]
        cand = None
        found = party_from(after)
        if found:
            s, e, lf = found
            cand = (bseg.line, after_start + s, after_start + e, lf)
        if cand is None:
            for l in p1:
                if l.y0 < breg.y0 - 1 or l.y0 > breg.y1:
                    continue
                for seg in segments(l):
                    if not breg.holds(seg.first_word()):
                        continue
                    found = party_from(seg.text)
                    if found:
                        s, e, lf = found
                        cand = (l, seg.start + s, seg.start + e, lf)
                        break
                if cand:
                    break
        if cand:
            l, s, e, lf = cand
            buyer = _field(l.text[s:e].strip(), lf, l, s, e)
    limit = breg.y0 if breg else pages[0].height * 0.4
    for l in p1:
        if l.y0 >= limit:
            break
        for seg in segments(l):
            if breg and breg.holds(seg.first_word()):
                continue
            found = party_from(seg.text)
            if found:
                s, e, lf = found
                seller = _field(seg.text[s:e].strip(), lf, l, seg.start + s, seg.start + e)
                break
        if seller.present:
            break
    return seller, buyer


TAX_LABEL = S(r"เลขประจำตัวผู้เสียภาษี|เลขผู้เสียภาษี|เลขที่ผู้เสียภาษี|TAX\s*I\.?D|TAXID", re.I)


def tax_ids(lines: list[Line], breg: Region | None) -> tuple[Field, Field, list[dict]]:
    """Seller and buyer 13-digit tax IDs. Every 13-digit run is a candidate; one inside the
    buyer box is the buyer's, the first one on page 1 outside it the seller's."""
    seller, buyer, seen = Field(), Field(), []
    for l in lines:
        for digits, s, e in T.tax_id_candidates(l.text):
            ws = l.words_in(s, e)
            if not ws:
                continue
            labelled = bool(ssearch(TAX_LABEL, l.text[max(0, s - 60):s]))
            f = _field(l.text[s:e], digits, l, s, e)
            if not labelled:
                f.confidence *= 0.95
                f.checks.append("unlabelled")
            seen.append({"value": digits, "page": l.page, "line": l.index, "labelled": labelled})
            if breg is not None and breg.holds(ws[0]):
                if not buyer.present:
                    buyer = f
            elif not seller.present and l.page == 1:
                seller = f
    return seller, buyer, seen


def branches(lines: list[Line], seller_tid: Field, buyer_tid: Field, breg: "Region | None" = None) -> tuple[Field, Field]:
    """The branch printed next to a tax ID: after it on the same row (possibly wrapped onto
    the next rows of the same column: "สำนักงาน / ใหญ่"), or just before it (a POS slip
    prints "สาขาที่ 01234" above its TAX ID)."""
    out = []
    for tid in (seller_tid, buyer_tid):
        f = Field()
        if tid.present and tid.source is not None:
            l = lines[tid.source.line]
            seg = next((s for s in segments(l) if s.start <= tid.source.span[0] < s.end), None)
            x0, x1 = (seg.x0, seg.x1) if seg else (l.x0, l.x1)
            tail = l.text[tid.source.span[1]:seg.end if seg else len(l.text)]
            follow = []
            prev_line = l
            for nl in lines[l.index + 1:l.index + 3]:
                if nl.page != l.page or nl.y0 - prev_line.y1 > 1.2 * max(1.0, prev_line.y1 - prev_line.y0):
                    break
                if ssearch(BUYER_LABEL, nl.text) or any(ssearch(rx, nl.text) for _, rx in DOC_TYPES):
                    break         # the next block (the buyer, the title) is not this party's branch
                follow += [nl.text[s:e] for s, e, i in nl.spans if nl.words[i].x0 >= x0 - 5 and nl.words[i].x1 <= x1 + 40]
                prev_line = nl
            b = T.parse_branch((tail + " " + " ".join(follow))[:90])
            if b is None:
                b = T.parse_branch(l.text[:tid.source.span[0]][-40:])
            for back in range(1, 4):
                if b is not None or l.index - back < 0 or lines[l.index - back].page != l.page:
                    break
                if ssearch(BUYER_LABEL, lines[l.index - back].text):
                    break
                b = T.parse_branch(lines[l.index - back].text)
            if b:
                f = Field(value=b, normalized=b, confidence=tid.confidence,
                          source=_src(l, tid.source.span[1], len(l.text)))
        out.append(f)
    if not out[0].present and not seller_tid.present:
        # no seller tax ID to anchor on: a branch line in the seller's header block
        for l in lines:
            if l.page != 1 or l.y0 > 160 or (breg is not None and l.y0 >= breg.y0 - 1):
                continue
            for seg in segments(l):
                if ssearch(BUYER_LABEL, seg.text):
                    break
                b = T.parse_branch(seg.text)
                if b:
                    out[0] = Field(value=b, normalized=b, confidence=l.conf(seg.start, seg.end) * 0.95,
                                   source=_src(l, seg.start, seg.end), checks=["unlabelled"])
                    break
            if out[0].present:
                break
    return out[0], out[1]


# ── labelled values ──────────────────────────────────────────────────────────

DOCNO_LABEL = S(r"เลขที่ใบกำกับภาษี|เลขที่เอกสาร|เลขที่ใบเสร็จรับเงิน|เลขที่ใบเสร็จ|เลขที่|Invoice\s*No\.?|Receipt\s*No\.?|Doc(?:ument)?\s*No\.?|No\.", re.I)
DOCNO_VALUE_RE = re.compile(r"[A-Za-z0-9ก-ฮ๐-๙][A-Za-z0-9ก-ฮ๐-๙./\-#]*")
NOT_DOCNO = S(r"บัญชี|ธนาคาร|ผู้เสียภาษี|โทร|Tel|Account|POS|TaxID|TaxI\.D", re.I)


def document_number(lines: list[Line]) -> Field:
    """The value after a number label — in the same segment or the next one to the right
    (a meta box puts "เลขที่" and its value in two columns)."""
    for l in lines:
        for seg in segments(l):
            if ssearch(NOT_DOCNO, seg.text):
                continue
            # every number label in the segment: a bilingual "Invoice No. / เลขที่: …" has two
            for _s, e, _m in sfinditer(DOCNO_LABEL, seg.text):
                start = seg.start + e
                rest = l.text[start:]
                m = re.match(r"[\s:：/]*", rest)
                v = DOCNO_VALUE_RE.match(rest, m.end())
                if not v:
                    continue
                raw = v.group(0).rstrip(".")
                cut = raw.endswith(("-", "/")) and start + v.end() >= seg.end - 1
                raw = raw.rstrip("-/")
                if T.parse_date(raw) or len(T.arabic(raw)) < 3 or not re.search(r"\d", T.arabic(raw)):
                    continue
                if re.fullmatch(r"\d{13}", re.sub(r"[\s-]", "", T.arabic(raw))):
                    continue          # a 13-digit number is a tax ID, never a document number
                s = start + v.start()
                f = _field(raw, T.arabic(raw), l, s, s + len(raw))
                if cut:
                    # the number wraps in a narrow column ("SL26-09-" / "0456"): its rest is the next row's
                    # first word under it; without one the value is marked as possibly cut (→ a question)
                    x0 = l.words_in(s, s + len(raw))[0].x0
                    nxt = next((n for n in lines[l.index + 1:l.index + 2] if n.page == l.page), None)
                    tail = None
                    if nxt is not None:
                        for ns, ne, ni in nxt.spans:
                            w = nxt.words[ni]
                            # under the value, or back at the left edge of the value's cell
                            if (abs(w.x0 - x0) < 25 or abs(w.x0 - seg.x0) < 25) and DOCNO_VALUE_RE.fullmatch(w.text):
                                tail = w.text
                                break
                    if tail:
                        joined = v.group(0).rstrip(".") + tail
                        f.value, f.normalized = joined, T.arabic(joined)
                        f.checks.append("wrapped_onto_next_line")
                    else:
                        f.confidence = min(f.confidence, 0.6)
                        f.checks.append("possibly_cut")
                return f
    return Field()


DATE_LABEL = S(r"วันที่|ลงวันที่|Invoice\s*Date|Date", re.I)
DUE_LABEL = S(r"วันครบกำหนด|ครบกำหนดชำระ|ครบกำหนด|กำหนดชำระ|Due\s*Date", re.I)


def _date_after(lines: list[Line], label, *, skip_due: bool) -> Field:
    unreadable = None
    for l in lines:
        t = l.text
        for s, e, m in sfinditer(label, t):
            if skip_due and ssearch(DUE_LABEL, t[max(0, s - 12):e]):
                continue
            hit = T.parse_date(t[e:e + 40])
            if hit:
                d, ds, de, _ = hit
                return _field(t[e + ds:e + de], d, l, e + ds, e + de)
            if unreadable is None and re.search(r"\d", T.arabic(t[e:e + 25])):
                # the label is there and something follows it, but no date can be read from it
                unreadable = Field(value=t[e:e + 25].strip(), normalized=None, confidence=0.0,
                                   source=_src(l, e, min(len(t), e + 25)), checks=["unreadable"])
    return unreadable or Field()


def dates(lines: list[Line]) -> tuple[Field, Field]:
    doc = _date_after(lines, DATE_LABEL, skip_due=True)
    due = _date_after(lines, DUE_LABEL, skip_due=False)
    if due.present and doc.present and due.normalized == doc.normalized and due.source.line == doc.source.line:
        due = Field()
    if not doc.present:
        # a POS slip prints its date without a label: the first date that is not a due date
        for l in lines:
            hit = T.parse_date(l.text)
            if hit and not ssearch(DUE_LABEL, l.text):
                d, s, e, _ = hit
                doc = _field(l.text[s:e], d, l, s, e, anchored=False)
                break
    return doc, due


# totals, in priority order within each field
TOTAL_LABELS = {
    "net_payable": [r"ยอดชำระสุทธิ", r"ยอดสุทธิที่ต้องชำระ", r"จำนวนเงินที่ต้องชำระ", r"Net\s*(?:Amount\s*)?Payable", r"Amount\s*Payable"],
    "wht_amount": [r"หักภาษีณที่จ่าย", r"ภาษีหักณที่จ่าย", r"หักณที่จ่าย", r"Withholding\s*Tax", r"W\.?H\.?T\.?"],
    "vat_amount": [r"ภาษีมูลค่าเพิ่ม", r"VAT(?![A-Za-z])", r"ภาษีขาย"],
    "total": [r"จำนวนเงินรวมทั้งสิ้น", r"ยอดรวมทั้งสิ้น", r"รวมเงินทั้งสิ้น", r"รวมทั้งสิ้น", r"ยอดรวมสุทธิ",
              r"Grand\s*Total", r"Total\s*Due", r"Total\s*Amount", r"ยอดรวม(?![0-9]+รายการ)", r"^TOTAL"],
    "subtotal": [r"มูลค่าหลังหักส่วนลด", r"ยอดหลังหักส่วนลด", r"ราคาก่อนภาษี", r"มูลค่าก่อนภาษี", r"ยอดก่อนภาษี",
                 r"มูลค่าสินค้า", r"Sub\s*-?\s*Total", r"Vatable"],
    "discount": [r"หักส่วนลด", r"ส่วนลด", r"Discount"],
    "sum_lines": [r"รวมเป็นเงิน", r"รวมเงิน(?!ทั้งสิ้น)"],
}
TOTAL_RX = {k: [S(p, re.I) for p in v] for k, v in TOTAL_LABELS.items()}
TOTALS_ANY = S("|".join(p for v in TOTAL_LABELS.values() for p in v) + r"|ตัวอักษร|บาทถ้วน", re.I)
RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
INCLUSIVE = S(r"VAT\s*INCLUDED|รวมภาษีมูลค่าเพิ่มแล้ว|ราคารวม\s*VAT|รวม\s*VAT\s*แล้ว", re.I)


def _is_ocr(line: Line) -> bool:
    return any(w.method == "ocr" for w in line.words)


def _money_after(line: Line, lab_end: int) -> tuple[Decimal, int, int] | None:
    tail = T.arabic(line.text)[lab_end:]
    eq = tail.find("=")
    toks = [x for x in T.money_tokens(tail, lenient=_is_ocr(line)) if not tail[x[2]:x[2] + 2].lstrip().startswith("%")]
    if eq >= 0:
        toks = [x for x in toks if x[1] > eq] or toks
    if not toks:
        return None
    v, s, e = toks[0]
    return v, lab_end + s, lab_end + e


def totals(lines: list[Line], pages: list[Page]) -> dict[str, Field]:
    """Every labelled money figure; the last page is searched first (the totals live there)."""
    out: dict[str, Field] = {}
    order = sorted(lines, key=lambda l: (-l.page, l.y0))
    used: set[tuple[int, int]] = set()
    for name, alts in TOTAL_RX.items():
        for rx in alts:
            if name in out:
                break
            for l in order:
                t = T.arabic(l.text)
                for s, e, m in sfinditer(rx, t):
                    if name == "vat_amount" and ssearch(S(r"จะ|ไม่รวม|INCLUDED", re.I), t[e:e + 12]):
                        continue
                    hit = _money_after(l, e)
                    if hit is None:
                        continue
                    v, vs, ve = hit
                    if (l.index, vs) in used:
                        continue
                    used.add((l.index, vs))
                    f = _field(l.text[vs:ve], v, l, vs, ve, raw=l.text)
                    if not T.MONEY_RE.fullmatch(T.arabic(l.text[vs:ve])):
                        f.confidence *= 0.9              # OCR read a separator wrongly; repaired
                        f.checks.append("ocr_separator_repaired")
                    if name in ("vat_amount", "wht_amount"):
                        r = RATE_RE.search(t[s:vs])
                        if r:
                            out["vat_rate" if name == "vat_amount" else "wht_rate"] = _field(
                                r.group(0), f"{Decimal(r.group(1)):.0f}", l, s + r.start(), s + r.end())
                    out[name] = f
                    break
                if name in out:
                    break
    if "total" not in out:
        # a receipt that labels its only amount "จำนวนเงิน … บาท"
        rx = S(r"จำนวนเงิน(?!รวม)")
        for l in order:
            hit = ssearch(rx, l.text)
            if hit:
                mv = _money_after(l, hit[1])
                if mv:
                    v, vs, ve = mv
                    out["total"] = _field(l.text[vs:ve], v, l, vs, ve, raw=l.text)
                    break
    return out


BANK_LABEL = S(r"บัญชี|Account\s*No|A/C", re.I)
BANK_NO_RE = re.compile(r"(?<!\d)(\d{3}-?\d-?\d{5}-?\d|\d{3}-\d{6}-\d|\d{10,12})(?![\d-])")


def bank_account(lines: list[Line]) -> Field:
    """A payee bank account printed after an account label (money goes there: a strict field)."""
    for l in lines:
        hit = ssearch(BANK_LABEL, l.text)
        if not hit:
            continue
        m = BANK_NO_RE.search(T.arabic(l.text), hit[1])
        if m:
            raw = l.text[m.start():m.end()]
            return _field(raw, re.sub(r"\D", "", T.arabic(raw)), l, m.start(), m.end())
    return Field()


def vat_mode(lines: list[Line], found: dict[str, Field]) -> str:
    if any(ssearch(INCLUSIVE, l.text) for l in lines):
        return "inclusive"
    if "vat_amount" in found and found["vat_amount"].normalized is not None:
        return "exclusive"
    return "none"


def amount_in_words(lines: list[Line]) -> Field:
    for l in lines:
        hit = T.parse_amount_words(l.text)
        if hit:
            v, phrase = hit
            return Field(value=phrase, normalized=v, confidence=l.conf(0, len(l.text)),
                         source=_src(l, 0, len(l.text), raw=l.text))
    return Field()


# ── the item table ──────────────────────────────────────────────────────────

COLS = {
    "no": S(r"^(?:ลำดับ|ที่|No\.?|#)$", re.I),
    "code": S(r"รหัสสินค้า|รหัส|ItemCode|Code", re.I),
    "desc": S(r"รายการ|รายละเอียด|Description|สินค้า", re.I),
    "qty": S(r"จำนวน(?!เงิน)|Qty|Quantity", re.I),
    "unit": S(r"^หน่วย$|^Unit$", re.I),
    "price": S(r"ราคาต่อหน่วย|ราคา/หน่วย|หน่วยละ|UnitPrice|ราคา(?!รวม)", re.I),
    "amount": S(r"จำนวนเงิน|มูลค่า|Amount|รวมเงิน", re.I),
}
NUM_RE = re.compile(r"^[-(]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?$|^\d+(?:\.\d+)?$")
POS_QTY_FIRST_RE = re.compile(r"^\s*(\d{1,3})\s+(\S.*?)\s+((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*$")
POS_ITEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[xX×]\s*((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s+((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*$")


def _header_cols(line: Line) -> dict[str, float] | None:
    cols: dict[str, float] = {}
    for key, rx in COLS.items():
        if key in ("no", "unit"):
            for s, e, i in line.spans:
                if ssearch(rx, line.words[i].text):
                    cols[key] = line.words[i].cx
                    break
            continue
        hit = ssearch(rx, line.text)
        if hit:
            bb = line.bbox(*hit)
            if bb:
                cols[key] = (bb[0] + bb[2]) / 2
    have = set(cols)
    if len(have & {"desc", "qty", "price", "amount"}) >= 3 or ({"desc", "amount"} <= have and len(have) >= 3):
        if "qty" in cols and "amount" in cols and abs(cols["qty"] - cols["amount"]) < 5:
            return None
        return cols
    return None


def _num(text: str, lenient: bool = False) -> Decimal | None:
    t = T.arabic(text).strip()
    if lenient and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+[.,]\d{2}", t):
        d = re.sub(r"[.,]", "", t)
        t = d[:-2] + "." + d[-2:]
    if not NUM_RE.match(t):
        return None
    return T.parse_number(t.strip("()").replace(",", ""))


def line_items(lines: list[Line]) -> tuple[list[dict], dict]:
    """Rows of the item table(s); a POS slip's "qty x price  amount" pairs when there is no table."""
    items: list[dict] = []
    info = {"table": None, "pages": []}
    header, cols = None, None
    for l in lines:
        hc = _header_cols(l)
        if hc:
            header, cols = l, hc
            info["table"] = "columns"
            info["pages"].append(l.page)
            continue
        if header is None or l.page != header.page or l.y0 <= header.y0:
            continue
        if ssearch(TOTALS_ANY, l.text):
            header = None          # the totals block ends the table
            continue
        nums = _row_numbers(l, cols)
        if nums.get("amount") is None:
            desc = _desc_text(l, cols)
            if items and items[-1]["page"] == l.page and desc and items[-1]["description"].present:
                items[-1]["description"].value += " " + desc
                items[-1]["description"].normalized += " " + desc
            continue
        if ssearch(TOTALS_ANY, _desc_text(l, cols) or ""):
            header = None
            continue
        item = {"row": len(items) + 1, "page": l.page}
        for k, col in (("quantity", "qty"), ("unit_price", "price"), ("amount", "amount")):
            v = nums.get(col)
            if v is None:
                item[k] = Field()
            else:
                val, s, e = v
                item[k] = _field(l.text[s:e], val, l, s, e)
        desc = _desc_text(l, cols)
        ds = max(0, l.text.find(desc)) if desc else 0
        item["description"] = _field(desc, desc, l, ds, ds + len(desc)) if desc else Field()
        items.append(item)
    if items:
        return items, info
    for j, l in enumerate(lines):
        m = POS_ITEM_RE.match(T.arabic(l.text))
        if not m or j == 0:
            continue
        prev = lines[j - 1]
        qty, price, amount = Decimal(m.group(1)), T.parse_money(m.group(2)), T.parse_money(m.group(3))
        items.append({"row": len(items) + 1, "page": l.page,
                      "description": _field(prev.text.strip(), prev.text.strip(), prev, 0, len(prev.text)),
                      "quantity": _field(m.group(1), qty, l, m.start(1), m.end(1)),
                      "unit_price": _field(m.group(2), price, l, m.start(2), m.end(2)),
                      "amount": _field(m.group(3), amount, l, m.start(3), m.end(3))})
    if not items:
        # "1 ข้าวหอมมะลิ 5 กก.      189.00": quantity first, amount last, no unit price, between the
        # slip's header (its number / date) and the totals
        start = next((i for i, l in enumerate(lines) if ssearch(DOCNO_LABEL, l.text) or T.parse_date(l.text)), None)
        if start is not None:
            for l in lines[start + 1:]:
                if ssearch(TOTALS_ANY, l.text):
                    break
                m = POS_QTY_FIRST_RE.match(T.arabic(l.text))
                if not m or T.parse_date(l.text):
                    continue
                qty, amount = Decimal(m.group(1)), T.parse_money(m.group(3))
                items.append({"row": len(items) + 1, "page": l.page,
                              "description": _field(m.group(2).strip(), m.group(2).strip(), l, m.start(2), m.end(2)),
                              "quantity": _field(m.group(1), qty, l, m.start(1), m.end(1)), "unit_price": Field(),
                              "amount": _field(m.group(3), amount, l, m.start(3), m.end(3))})
    if items:
        info["table"] = "pos"
    return items, info


def _row_numbers(l: Line, cols: dict[str, float]) -> dict[str, tuple[Decimal, int, int]]:
    """Numbers in a row, each given to the nearest numeric column (qty / price / amount)."""
    targets = {k: cols[k] for k in ("qty", "price", "amount") if k in cols}
    if not targets:
        return {}
    money_cols = [k for k in ("price", "amount") if k in targets]
    left_edge = min(targets.values()) - (60 if "qty" in targets else 120)
    out: dict[str, tuple[Decimal, int, int]] = {}
    for s, e, i in l.spans:
        w = l.words[i]
        if w.cx < left_edge:
            continue
        v = _num(w.text, lenient=w.method == "ocr")
        if v is None:
            continue
        is_money = bool(re.search(r"[.,]\d{2}\)?$", T.arabic(w.text)))
        if "qty" not in targets and not is_money and money_cols and w.cx < min(targets[k] for k in money_cols) - 20:
            key = "qty"          # the header's qty label was lost (OCR of white-on-colour): an integer left of the prices
        else:
            pool = targets if is_money or "qty" in targets else {k: targets[k] for k in targets if k == "qty"}
            if not pool:
                continue
            key = min(pool, key=lambda k: abs(pool[k] - w.cx))
            if abs(pool[key] - w.cx) > 90:
                continue
            if key in money_cols and not is_money:
                continue         # a price or amount column takes a 2-decimal figure, never a bare integer
        if key in out:
            continue
        out[key] = (v, s, e)
    return out


def _desc_text(l: Line, cols: dict[str, float]) -> str:
    targets = [cols[k] for k in ("qty", "price", "amount") if k in cols]
    right = min(targets) - 25 if targets else l.x1
    code_x, desc_x, no_x = cols.get("code"), cols.get("desc"), cols.get("no")
    parts = []
    for s, e, i in l.spans:
        w = l.words[i]
        if w.cx >= right:
            continue
        if no_x is not None and abs(w.cx - no_x) < 14 and _num(w.text) is not None:
            continue
        if code_x is not None and desc_x is not None and abs(w.cx - code_x) < abs(w.cx - desc_x) and w.x1 < desc_x - 10:
            continue
        parts.append(l.text[s:e])
    return " ".join(parts).strip()
