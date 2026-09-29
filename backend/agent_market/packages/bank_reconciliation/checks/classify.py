"""What a row without a partner is.

A row of the company's ledger that the statement does not show is a matter of time: the bank has
not presented the cheque or credited the deposit yet. It adjusts the *bank's* balance.

A row of the statement that the ledger does not show is something the company has not recorded:
a fee, interest and the tax withheld on it, a cheque that came back, a direct debit. It adjusts
the *book* balance, and each one gets a drafted journal entry (scripts/journal.py) — a draft: this
agent writes nothing into an accounting system.

The words are read from the statement's own text by rule. A row no rule recognises is still a
reconciling item (an unrecorded receipt or payment, entered through a suspense account in the
draft), and from the install's amount upwards a person is asked what it is (checks/cards.py).
"""

from __future__ import annotations

import re
from decimal import Decimal

from ....engine.e2 import thai_text as T

ZERO = Decimal("0.00")
STALE_DAYS = 180       # a Thai cheque is stale six months after its date
BANK_SIDE, BOOK_SIDE = "bank", "book"

TYPES = {
    # adjust the bank's balance
    "outstanding_cheque": ("เช็คค้างจ่าย", BANK_SIDE),
    "outstanding_payment": ("รายการจ่ายที่ธนาคารยังไม่ตัดบัญชี", BANK_SIDE),
    "deposit_in_transit": ("เงินฝากระหว่างทาง", BANK_SIDE),
    "bank_error": ("ธนาคารบันทึกคลาดเคลื่อน", BANK_SIDE),
    # adjust the book balance
    "bank_fee": ("ค่าธรรมเนียมธนาคาร", BOOK_SIDE),
    "interest": ("ดอกเบี้ยรับ", BOOK_SIDE),
    "wht_interest": ("ภาษีหัก ณ ที่จ่ายของดอกเบี้ย", BOOK_SIDE),
    "returned_cheque": ("เช็คคืน", BOOK_SIDE),
    "direct_debit": ("หักบัญชีอัตโนมัติ", BOOK_SIDE),
    "unrecorded_receipt": ("เงินเข้าที่ยังไม่ได้บันทึกบัญชี", BOOK_SIDE),
    "unrecorded_payment": ("เงินออกที่ยังไม่ได้บันทึกบัญชี", BOOK_SIDE),
    "posting_error": ("บันทึกบัญชีคลาดเคลื่อน", BOOK_SIDE),
    "book_error": ("บันทึกบัญชีผิด ต้องกลับรายการ", BOOK_SIDE),
    # not yet either: a person has to say
    "unclassified": ("ยังไม่ได้จัดประเภท", None),
}
TYPE_TH = {k: v[0] for k, v in TYPES.items()}
SIDE_OF = {k: v[1] for k, v in TYPES.items()}

# (type, the sign the statement shows it with, words) — the first that fits; the tax before the interest it is charged on
_RULES = (
    ("wht_interest", -1, ("ภาษีหักณที่จ่าย", "ภาษีดอกเบี้ย", "ภาษีจากดอกเบี้ย", "หักภาษีดอกเบี้ย", "withholdingtax", "withholding",
                          "whtax", "w/htax", "w/t", "wht", "inttax", "taxoninterest")),
    ("interest", +1, ("ดอกเบี้ย", "interest", "intcredit", "creditint")),
    ("returned_cheque", -1, ("เช็คคืน", "คืนเช็ค", "เช็คเด้ง", "returnedcheque", "returnedcheck", "returncheque", "chequereturn",
                             "chqreturn", "returnchq", "rtnchq", "chqrtn", "nsf")),
    ("bank_fee", -1, ("ค่าธรรมเนียม", "ค่าบริการ", "ค่ารักษาบัญชี", "ค่าสมุดเช็ค", "fee", "charge", "commission", "svccharge")),
    ("direct_debit", -1, ("หักบัญชีอัตโนมัติ", "หักบัญชี", "directdebit", "autodebit", "standingorder")),
)
# words that say how money moved, not who it moved to: taken out before two texts are compared
_NOISE = ("โอนเงินเข้า", "โอนเงินออก", "โอนเงิน", "โอนเข้า", "โอนออก", "เงินโอนเข้า", "เงินโอนออก", "เงินโอน", "รับชำระหนี้", "รับชำระ",
          "จ่ายชำระหนี้", "จ่ายชำระ", "ชำระค่า", "ชำระ", "ฝากเงินสด", "ฝากเงิน", "ฝากเช็ค", "ถอนเงิน", "จ่ายเช็ค", "รับเช็ค", "เช็คเลขที่", "เช็ค",
          "จาก", "ให้แก่", "ให้", "ถึง", "ค่า", "transferfrom", "transferto", "transfer", "payment", "deposit", "withdrawal",
          "cheque", "chq", "from", "to", "trf", "tr")


def key(text) -> str:
    return re.sub(r"[\s.\-_:()#*,]", "", T.arabic(str(text or "")).lower())


def loose(text) -> str:
    """A text without its Thai marks: what a row read from a PDF is compared by (AM6 — a PDF's
    text layer often loses them, "คาธรรมเนยม")."""
    return T.skel(str(text or ""))


def of_bank_row(description: str, amount: Decimal, *, pdf: bool = False) -> str:
    """A row of the statement the ledger does not have. ``pdf``: the row was read from a PDF."""
    k = loose(key(description)) if pdf else key(description)
    sign = 1 if amount > ZERO else -1
    for code, want, words in _RULES:
        if sign == want and any((loose(w) if pdf else w) in k for w in words):
            return code
    return "unrecorded_receipt" if amount > ZERO else "unrecorded_payment"


TAX_WORDS = ("ภาษี", "tax")
WHT_RATES = (Decimal("1.00"), Decimal("15.00"))   # a company's deposit interest; a person's


def is_tax_on(description: str, amount: Decimal, interest: Decimal, *, pdf: bool = False) -> bool:
    """A line that only says "ภาษี" / "TAX", on the day interest was paid, and no more than the
    tax on that interest could be: the tax withheld on it, not a tax payment of another kind."""
    k = loose(key(description)) if pdf else key(description)
    return (amount < ZERO and interest > ZERO and any((loose(w) if pdf else w) in k for w in TAX_WORDS)
            and abs(amount) * 100 <= interest * 15 + Decimal("1.00"))


def of_book_row(cheque: str, amount: Decimal, carried_kind: str | None = None) -> str:
    """A row of the ledger the statement does not have."""
    if carried_kind:
        return carried_kind
    if amount > ZERO:
        return "deposit_in_transit"
    return "outstanding_cheque" if cheque else "outstanding_payment"


def who(text, *, pdf: bool = False) -> str:
    """What a line says once the words about how the money moved are taken out — what two
    texts are compared by when their names may resemble each other. ``pdf``: the words are found
    without their marks, in a text that may have lost them."""
    s = T.arabic(str(text or "")).lower()
    s = re.sub(r"(?:เช็?ค(?:เลขที่?)?|chq|cheque|check|cq)\s*(?:no\.?|เลขที่?|#|:)?\s*\d{5,10}", " ", s)
    noise = sorted(_NOISE, key=len, reverse=True)
    if pdf:
        marks = "[" + "".join(sorted(T._MARKS)) + "]*"
        for w in noise:
            if w.isascii():
                s = re.sub(rf"(?<![a-z]){re.escape(w)}(?![a-z])", " ", s)
            else:
                s = re.sub(marks.join(re.escape(ch) for ch in w if ch not in T._MARKS) + marks, " ", s)
        return " ".join(s.split())
    for w in noise:
        s = re.sub(rf"(?<![a-z]){re.escape(w)}(?![a-z])" if w.isascii() else re.escape(w), " ", s)
    return " ".join(s.split())


def bucket(days: int) -> str:
    if days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    return "61+"


BUCKET_TH = {"0-30": "ไม่เกิน 30 วัน", "31-60": "31–60 วัน", "61+": "เกิน 60 วัน"}
