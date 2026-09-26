"""Thai invoice text → normalised values (E2, AM2 Phase C).

Pure functions, no I/O. Everything a Thai invoice prints in more than one
way comes through here: Thai and Arabic digits, money with separators, the
13-digit tax ID in any grouping (with its mod-11 check digit), Buddhist-Era
and Common-Era dates with Thai or English month names, legal-form prefixes
(บริษัท … จำกัด, บจก., หจก., บมจ.), branch codes and document titles.

Written independently of the fixture generator (tools/e2fix): the eval
compares two code paths, so they share no tables.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
Q2 = Decimal("0.01")


def arabic(s: str) -> str:
    """Thai digits → Arabic digits (everything else untouched)."""
    return s.translate(_TH_DIGITS)


def q2(v) -> Decimal:
    return Decimal(v).quantize(Q2, rounding=ROUND_HALF_UP)


# ── money ─────────────────────────────────────────────────────────────────

# 1,234.56 · 1234.56 · 1,234 (only with a separator) · a leading minus or (…)
MONEY_RE = re.compile(r"(?<![\d.,/])[-(]?(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})\)?(?![\d])")


def parse_money(s: str) -> Decimal | None:
    t = arabic(str(s)).strip().replace(" ", "")
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("-()").replace(",", "")
    for cur in ("THB", "USD", "บาท", "฿", "$"):
        t = t.replace(cur, "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return q2(-v if neg else v)


# OCR reads a thousands comma as a period often enough ("50.000.00"): in OCR text a figure
# with 2 decimals may use either separator for thousands; the last separator is the decimal one.
MONEY_OCR_RE = re.compile(r"(?<![\d.,/])[-(]?(?:\d{1,3}(?:[.,]\d{3})+[.,]\d{2}|\d+[.,]\d{2})\)?(?![\d])")


def money_tokens(text: str, *, lenient: bool = False) -> list[tuple[Decimal, int, int]]:
    """Every money value in ``text`` with its (start, end) span. ``lenient`` (OCR text):
    accept a period or comma as the thousands separator."""
    t = arabic(text)
    out = []
    for m in (MONEY_OCR_RE if lenient else MONEY_RE).finditer(t):
        raw = m.group(0)
        if lenient:
            body = raw.strip("-()")
            digits = re.sub(r"[.,]", "", body)
            raw = raw.replace(body, digits[:-2] + "." + digits[-2:])
        v = parse_money(raw)
        if v is not None:
            out.append((v, m.start(), m.end()))
    return out


def parse_number(s: str) -> Decimal | None:
    t = arabic(str(s)).strip().replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    return Decimal(t)


# ── tax ID ──────────────────────────────────────────────────────────────────

# 13 digits, alone or grouped by spaces / hyphens (1-4-5-2-1 is the common grouping)
TAX_ID_RE = re.compile(r"(?<!\d)(\d(?:[ \-]?\d){12})(?![\d])")


def tax_id_check_ok(tid: str) -> bool:
    if len(tid) != 13 or not tid.isdigit():
        return False
    s = sum(int(c) * (13 - i) for i, c in enumerate(tid[:12]))
    return (11 - s % 11) % 10 == int(tid[12])


def tax_id_candidates(text: str) -> list[tuple[str, int, int]]:
    """13-digit tax IDs in ``text`` (Thai or Arabic digits, any grouping), with spans."""
    t = arabic(text)
    out = []
    for m in TAX_ID_RE.finditer(t):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 13:
            out.append((digits, m.start(1), m.end(1)))
    return out


# ── dates ──────────────────────────────────────────────────────────────────

MONTHS = {
    # Thai full, Thai abbreviations (with and without dots), English
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
    "มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6, "กค": 7, "สค": 8, "กย": 9, "ตค": 10, "พย": 11, "ธค": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_ALT = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
DATE_WORD_RE = re.compile(rf"(?<!\d)(\d{{1,2}})\s*[-\s]?\s*({_MONTH_ALT})\.?\s*[-\s]?\s*(\d{{2,4}})(?!\d)", re.I)
DATE_NUM_RE = re.compile(r"(?<!\d)(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})(?!\d)")


def _year(y: int, two_digit_era: str) -> int:
    if y < 100:
        # a 2-digit year on a Thai document is Buddhist Era (๖๙ = 2569) unless told otherwise
        y = y + 2500 if two_digit_era == "be" else y + 2000
    if y > 2400:
        y -= 543
    return y


def parse_date(text: str, *, two_digit_era: str = "be") -> tuple[_dt.date, int, int, str] | None:
    """The first date in ``text``: (CE date, start, end, style)."""
    t = arabic(text)
    cands = []
    for m in DATE_WORD_RE.finditer(t):
        mon = MONTHS.get(m.group(2).lower().rstrip("."), MONTHS.get(m.group(2).lower()))
        if mon is None:
            continue
        try:
            d = _dt.date(_year(int(m.group(3)), two_digit_era), mon, int(m.group(1)))
        except ValueError:
            continue
        cands.append((m.start(), d, m.end(), "word"))
    for m in DATE_NUM_RE.finditer(t):
        a, b, c = m.group(1), m.group(2), m.group(3)
        try:
            if len(a) == 4:            # 2026-09-15
                d = _dt.date(_year(int(a), "ce"), int(b), int(c))
            else:                      # 15/09/2569 · 15/09/2026 (Thai documents are day-first)
                d = _dt.date(_year(int(c), two_digit_era), int(b), int(a))
        except ValueError:
            continue
        cands.append((m.start(), d, m.end(), "numeric"))
    if not cands:
        return None
    s, d, e, style = min(cands)
    return d, s, e, style


# ── parties ────────────────────────────────────────────────────────────────

LEGAL_FORMS = [
    # (regex, legal_form) — the longest forms first
    (r"บริษัท\s*(.+?)\s*จำกัด\s*\(\s*มหาชน\s*\)", "public_company_limited"),
    (r"บมจ\.?\s*(.+)", "public_company_limited"),
    (r"ห้างหุ้นส่วนจำกัด\s*(.+)", "limited_partnership"),
    (r"หจก\.?\s*(.+)", "limited_partnership"),
    (r"ห้างหุ้นส่วนสามัญ\s*(.+)", "ordinary_partnership"),
    (r"หสน\.?\s*(.+)", "ordinary_partnership"),
    (r"บริษัท\s*(.+?)\s*จำกัด", "company_limited"),
    (r"บจก\.?\s*(.+)", "company_limited"),
    (r"(.+?)\s*(?:Pte\.?\s*Ltd\.?|Co\.?,?\s*Ltd\.?|Limited|Ltd\.?|Inc\.?|LLC|GmbH)\s*$", "foreign"),
    (r"ร้าน\s*(.+)", "sole_proprietor"),
]


# the same forms for skeleton matching (parse.py compiles them with S): the core is group 1
LEGAL_FORMS_SKEL = [
    (r"บริษัท(.+?)จ[ำา]?[กภถ]ด\(มหาชน\)", "public_company_limited"),
    (r"บมจ\.?(.+)", "public_company_limited"),
    (r"ห้างหุ้นส่วนจำกัด(.+)", "limited_partnership"),
    (r"หจก\.?(.+)", "limited_partnership"),
    (r"ห้างหุ้นส่วนสามัญ(.+)", "ordinary_partnership"),
    (r"หสน\.?(.+)", "ordinary_partnership"),
    (r"บริษัท(.+?)จ[ำา]?[กภถ]ด", "company_limited"),
    (r"บจก\.?(.+)", "company_limited"),
    (r"(.+?)(?:Pte\.?Ltd\.?|Co\.?,?Ltd\.?|Limited|Ltd\.?|Inc\.?|LLC|GmbH)$", "foreign"),
    (r"^ร้าน(.+)", "sole_proprietor"),
]
_LEGAL_COMPILED: list = []


def legal_form(name: str) -> dict | None:
    """``{"legal_form", "core", "key"}`` for a party name, or None when it has no recognised form.

    Matched on the skeleton, so a name whose Thai marks a text layer lost
    ("บรษิัท … จํากัด") is still a company; ``core`` is the original text
    between the form words, ``key`` its skeleton (what matching compares)."""
    if not _LEGAL_COMPILED:
        _LEGAL_COMPILED.extend((S(rx, re.I), form) for rx, form in LEGAL_FORMS_SKEL)
    n = re.sub(r"\s+", " ", arabic(fix_text_layer(name))).strip()
    sk, idx = skel_map(n)
    for rx, form in _LEGAL_COMPILED:
        m = rx.search(sk)
        if not m or m.start(1) == m.end(1):
            continue
        core = n[idx[m.start(1)]: idx[m.end(1) - 1] + 1]
        core = re.sub(r"\s+", " ", core).strip(" .,:()")
        if core:
            return {"legal_form": form, "core": core, "key": core_key(core)}
    return None


PARTY_WORD_RE = re.compile(r"บริษัท|บจก\.?|บมจ\.?|หจก\.?|หสน\.?|ห้างหุ้นส่วน|ร้าน|Pte\.?\s*Ltd|Co\.?,?\s*Ltd|Limited|Inc\.")


def core_key(core: str) -> str:
    """Matching key for a party core name: its skeleton (marks, spaces and dots ignored)."""
    return skel(arabic(core)).replace(".", "").lower()


_HEAD_OFFICE: list = []


def parse_branch(text: str) -> str | None:
    if not _HEAD_OFFICE:
        _HEAD_OFFICE.extend([S(r"สำนักงานใหญ่|HeadOffice", re.I), S(r"(?:สาขาที่|สาขา|BranchNo\.?|Branch)[:：]?(\d{1,5})", re.I)])
    sk = skel(arabic(text))
    if _HEAD_OFFICE[0].search(sk):
        return "00000"
    m = _HEAD_OFFICE[1].search(sk)
    if m:
        return m.group(1).zfill(5)
    return None


# ── document type ──────────────────────────────────────────────────────────

# Order matters: the specific title before the general one.
DOC_TYPES = [
    ("abbreviated_tax_invoice", r"ใบกำกับภาษีอย่างย่อ|TAX\s*INV\.?\s*\(?\s*ABB|ABBREVIATED\s+TAX\s+INVOICE"),
    ("credit_note", r"ใบลดหนี้|CREDIT\s+NOTE"),
    ("debit_note", r"ใบเพิ่มหนี้|DEBIT\s+NOTE"),
    ("wht_certificate", r"หนังสือรับรองการหักภาษี\s*ณ\s*ที่จ่าย"),
    ("full_tax_invoice", r"ใบกำกับภาษี|TAX\s+INVOICE"),
    ("receipt", r"ใบเสร็จรับเงิน|ใบรับเงิน|RECEIPT"),
    ("invoice", r"ใบแจ้งหนี้|ใบวางบิล|ใบเรียกเก็บเงิน|INVOICE|BILLING\s+NOTE"),
    ("quotation", r"ใบเสนอราคา|QUOTATION"),
]
# a sentence that mentions a tax invoice is not a title ("the tax invoice will be issued on payment")
NOT_A_TITLE_RE = re.compile(r"ใบกำกับภาษี\s*จะ|จะออกใบกำกับภาษี|ออกให้เมื่อ|เมื่อได้รับชำระ")


def classify_title(lines: list[str]) -> tuple[str, str] | None:
    """(document type, the title text that decided it) from title-candidate lines."""
    for dtype, rx in DOC_TYPES:
        for line in lines:
            if NOT_A_TITLE_RE.search(line):
                continue
            if re.search(rx, line, re.I):
                return dtype, line.strip()
    return None


ETAX_TYPE = {"388": "full_tax_invoice", "T02": "full_tax_invoice", "T03": "full_tax_invoice", "T04": "full_tax_invoice",
             "T01": "receipt", "380": "invoice", "81": "credit_note", "80": "debit_note"}


# ── amount in words (บาทถ้วน) ──────────────────────────────────────────────
#
# Parsed on the skeleton (a text layer loses the marks of หนึ่ง / ร้อย /
# ถ้วน), so the tokens are skeletons too. "ส" (สี่) and "ย" (ยี่) are one
# consonant each: the tokenizer takes the longest token first, so สอง,
# สาม, สิบ and สตางค์ are never read as สี่.

_W_DIGIT = {"ศนย": 0, "หนง": 1, "เอด": 1, "สอง": 2, "ย": 2, "สาม": 3, "ส": 4, "หา": 5, "หก": 6,
            "เจด": 7, "แปด": 8, "เกา": 9}
_W_UNIT = {"สบ": 10, "รอย": 100, "พน": 1000, "หมน": 10000, "แสน": 100000}
_W_TOKENS = sorted(list(_W_DIGIT) + list(_W_UNIT) + ["ลาน"], key=len, reverse=True)
_W_TOKEN_RE = re.compile("|".join(_W_TOKENS))
_W_PHRASE_RE = re.compile("([ก-ฮะ-๏]+?)บาท(?:([ก-ฮะ-๏]+?)สตางค|ถวน)")


def _words_int(s: str) -> int | None:
    pos, toks = 0, []
    while pos < len(s):
        m = _W_TOKEN_RE.match(s, pos)
        if not m:
            return None
        toks.append(m.group(0))
        pos = m.end()
    if not toks:
        return None
    total, group, digit = 0, 0, None
    for tok in toks:
        if tok in _W_DIGIT:
            if digit is not None:
                return None          # two digits in a row is not a number
            digit = _W_DIGIT[tok]
        elif tok in _W_UNIT:
            group += (1 if digit is None else digit) * _W_UNIT[tok]
            digit = None
        else:                        # ลาน (ล้าน)
            group += digit or 0
            total = (total + group) * 1_000_000
            group, digit = 0, None
    return total + group + (digit or 0)


def parse_amount_words(text: str) -> tuple[Decimal, str] | None:
    """'(หนึ่งหมื่นสองพันแปดร้อยห้าสิบบาทเจ็ดสิบสตางค์)' → Decimal('12850.70'), the phrase."""
    sk, idx = skel_map(fix_text_layer(text))
    for m in _W_PHRASE_RE.finditer(sk):
        baht_part = m.group(1)
        # drop leading words that are not number words ("ตัวอักษร", "จำนวนเงิน")
        baht = None
        for cut in range(len(baht_part)):
            baht = _words_int(baht_part[cut:])
            if baht is not None:
                break
        if baht is None:
            continue
        satang = 0
        if m.group(2):
            satang = _words_int(m.group(2))
            if satang is None or satang > 99:
                continue
        start = idx[m.start(1) + cut]
        end = idx[m.end() - 1] + 1
        return q2(Decimal(baht) + Decimal(satang) / 100), text[start:end]
    return None



# ── currency ──────────────────────────────────────────────────────────────

def detect_currency(text: str) -> str:
    t = text.upper()
    for code in ("USD", "EUR", "SGD", "JPY", "CNY", "GBP", "HKD", "MYR"):
        if re.search(rf"\b{code}\b", t):
            return code
    if "US DOLLAR" in t or "US$" in t:
        return "USD"
    return "THB"


# ── skeletons: Thai text without its combining marks ───────────────────────
#
# A PDF text layer written by Chrome/Skia (as many Thai accounting systems'
# PDFs are) loses or reorders Thai marks: a positional variant of a vowel
# above a tall consonant (ฟิ, ปี) is mapped to a zero-width space, sara am
# (ำ) comes out as nikhahit + sara aa, and pdfminer's x-sort moves marks
# past the next consonant. Consonants, leading vowels (เ แ โ ใ ไ) and
# spacing vowels keep their order. So labels are matched on the *skeleton*
# (marks and spaces removed, ำ → า) and mapped back to the original text.

_MARKS = set("ัิีึืฺุู็่้๊๋์ํ๎")


def skel_map(s: str) -> tuple[str, list[int]]:
    """(skeleton, index map skeleton-char → original index)."""
    out, idx = [], []
    for i, ch in enumerate(s):
        if ch in _MARKS or ch.isspace() or ord(ch) < 32 or ch in "​﻿":
            continue
        if ch == "ำ":   # ำ = ํ + า
            ch = "า"
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def skel(s: str) -> str:
    return skel_map(s)[0]


def S(pattern: str, flags: int = 0):
    """Compile a label pattern for skeleton matching: its Thai literals lose their marks
    (regex syntax is untouched; marks are never regex metacharacters)."""
    out = []
    for ch in pattern:
        if ch in _MARKS:
            continue
        out.append("า" if ch == "ำ" else ch)
    p = "".join(out)
    # spaces inside a pattern become optional (the skeleton has none)
    p = p.replace(r"\s*", "").replace(r"\s+", "").replace(r"\s", "").replace(" ", "")
    return re.compile(p, flags)


def ssearch(rx, text: str, pos: int = 0):
    """Search a skeleton-compiled regex in ``text``; returns (start, end) in ``text`` or None."""
    sk, idx = skel_map(text)
    # translate pos (original) → skeleton position
    spos = next((k for k, i in enumerate(idx) if i >= pos), len(sk))
    m = rx.search(sk, spos)
    if not m:
        return None
    if m.start() == m.end():
        return None
    return idx[m.start()], _end(text, idx[m.end() - 1] + 1)


def _end(text: str, e: int) -> int:
    # a match ends after the marks of its last consonant (ที่ ends after ่, not after ท)
    while e < len(text) and (text[e] in _MARKS or ord(text[e]) < 32):
        e += 1
    return e


def sfinditer(rx, text: str):
    sk, idx = skel_map(text)
    for m in rx.finditer(sk):
        if m.start() < m.end():
            yield idx[m.start()], _end(text, idx[m.end() - 1] + 1), m


def fix_text_layer(s: str) -> str:
    """Repair what a text layer does to Thai: ํ + า → ำ; lost-mark placeholders (NUL,
    zero-width space, BOM) out."""
    s = s.replace(chr(0x0E4D) + chr(0x0E32), chr(0x0E33))
    return "".join(ch for ch in s if ord(ch) >= 32 and ch not in (chr(0x200B), chr(0xFEFF)))
