"""What a person decides about a match → clarification cards, and their answers back.

Rule zero holds: no model decides what to ask, and none decides a pair. The
passes (match.py) propose; this module writes the question from the rows.

    e3_candidate:<pair>          one proposed pair (or group): the same thing, or not?
    e3_confirm_all:<pair>;<pair> several pairs a pass is sure about, confirmed by
                                 one answer — the answer names every pair it is
                                 about, so it can never cover one the person was
                                 not shown
    e3_unmatched:<row>           a row without a partner, from the install's amount up

A skipped card records ``undecided``: the rows stay in the suspect group for
a person to decide. A pair is never matched by default.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

from ..findings import FORBIDDEN_WORDS, Finding, finding
from ..thai import BE_OFFSET, MONTHS_TH_ABBR, fmt_money
from .match import DECISIONS, MATCH, SEPARATE, UNDECIDED, ZERO, Tolerance

CODE_PAIR = "e3_candidate"
CODE_ALL = "e3_confirm_all"
CODE_LONE = "e3_unmatched"
CODES = (CODE_PAIR, CODE_ALL, CODE_LONE)
ONE_BY_ONE = "one_by_one"
KEEP, REVIEW = "keep", "review"
ALL_MIN = 3     # fewer sure pairs than this are asked one by one
ALL_MAX = 20    # the most pairs one answer confirms
SEP = ";"
HEADER_PAIR = "จับคู่รายการ"       # ≤ 12 display characters
HEADER_ALL = "ยืนยันหลายคู่"
HEADER_LONE = "ไม่พบคู่"
COLUMNS = ["ตาราง", "วันที่", "รายการ", "เลขอ้างอิง", "จำนวนเงิน", "ไฟล์", "แถว"]
_FORBIDDEN = re.compile("|".join(re.escape(w) for w in FORBIDDEN_WORDS), re.IGNORECASE)


def safe(text) -> str:
    """Text from a file quoted on a card: a word no card may say (a card never
    asks for a secret) is masked, so a line reading "SHOPPING" cannot stop a run."""
    return _FORBIDDEN.sub("…", " ".join(str(text or "").split()))


def date_th(iso) -> str:
    if not iso:
        return "ไม่ระบุวันที่"
    d = _dt.date.fromisoformat(iso) if isinstance(iso, str) else iso
    return f"{d.day} {MONTHS_TH_ABBR[d.month - 1]} {d.year + BE_OFFSET}"


def money(v) -> str:
    return fmt_money(abs(Decimal(str(v))))


def line(row: dict) -> str:
    """A row in a sentence: when, what it says, how much."""
    what = safe(row.get("name") or row.get("ref") or "")[:60]
    return " ".join(x for x in (date_th(row.get("date")), f"“{what}”" if what else "", f"{money(row['amount'])} บาท") if x)


def table_row(side: str, row: dict) -> list:
    return [side, date_th(row.get("date")), safe(row.get("name"))[:80], safe(row.get("ref"))[:40],
            f"{Decimal(row['amount']):,.2f}", safe(row.get("file")), row.get("row")]


def _value(v):
    """An answer as the run holds it: a value, or {"typed": …} (never used for a match)."""
    return v if isinstance(v, str) else None


def decisions_from(answers: dict | None) -> tuple[dict, dict, set]:
    """The run's answers → the engine's decisions by pair, the answer each
    came from, and the pairs a person asked to see one by one. An answer about
    one pair has the last word over an answer about several."""
    decisions, keys, one_by_one = {}, {}, set()
    answers = answers or {}
    for k in sorted(answers):
        if not k.startswith(CODE_ALL + ":"):
            continue
        v = _value(answers[k])
        for pair in k[len(CODE_ALL) + 1:].split(SEP):
            if "~" not in pair:
                continue
            if v in (MATCH, UNDECIDED):
                decisions[pair], keys[pair] = v, k
            elif v == ONE_BY_ONE:
                one_by_one.add(pair)
    for k in sorted(answers):
        if not k.startswith(CODE_PAIR + ":"):
            continue
        v, pair = _value(answers[k]), k[len(CODE_PAIR) + 1:]
        if v in DECISIONS and "~" in pair:
            decisions[pair], keys[pair] = v, k
    return decisions, keys, one_by_one


def lone_answers(answers: dict | None) -> dict:
    """{row id: the answer} for the rows without a partner a person was asked about."""
    out = {}
    for k, v in (answers or {}).items():
        if k.startswith(CODE_LONE + ":"):
            out[k[len(CODE_LONE) + 1:]] = v
    return out


def _why(c: dict) -> str:
    diff = abs(Decimal(c["difference"]))
    days = c.get("days_apart")
    amount = "ยอดเท่ากัน" if diff == ZERO else f"ยอดต่างกัน {fmt_money(diff)} บาท"
    when = "" if days is None else ("วันที่เดียวกัน" if days == 0 else f"วันที่ห่างกัน {days} วัน")
    if c["pass"] == "ref_only":
        return f"เลขอ้างอิงเดียวกัน แต่{amount}"
    if c["tier"] == "fuzzy":
        pct = int((Decimal(c["similarity"] or "0") * 100).to_integral_value())
        return " ".join(x for x in (f"ชื่อหรือเลขอ้างอิงคล้ายกัน {pct}%", amount, when) if x)
    return " ".join(x for x in (amount, when) if x)


def _evidence(c: dict, names: dict) -> dict:
    rows = [table_row(names["left"], r) for r in c["left"]] + [table_row(names["right"], r) for r in c["right"]]
    return {"columns": COLUMNS, "rows": rows}


def pair_card(c: dict, names: dict, moment: str) -> Finding:
    n_l, n_r = len(c["left"]), len(c["right"])
    if (n_l, n_r) == (1, 1):
        what = f"{names['left']}: {line(c['left'][0])} กับ{names['right']}: {line(c['right'][0])}"
    else:
        what = (f"{names['left']} {n_l} รายการ รวม {money(c['left_total'])} บาท "
                f"กับ{names['right']} {n_r} รายการ รวม {money(c['right_total'])} บาท")
    other = f" (มีรายการอื่นที่อาจเป็นคู่ได้อีก {len(c['alternatives'])} รายการ)" if c.get("alternatives") else ""
    sure = bool(c.get("unique")) and Decimal(c["difference"]) == ZERO and c["tier"] == "amount_date"
    return finding(
        CODE_PAIR, "blocking", moment, HEADER_PAIR,
        f"{what} — {_why(c)}{other} เป็นรายการเดียวกันหรือไม่",
        [("เป็นรายการเดียวกัน", MATCH, "จับคู่ และบันทึกว่าคุณเป็นผู้ยืนยัน"),
         ("คนละรายการ", SEPARATE, "ไม่จับคู่ แต่ละรายการยังค้างอยู่ตามเดิม"),
         ("ยังไม่แน่ใจ", UNDECIDED, "เก็บไว้ในกลุ่มน่าสงสัย รอคนตัดสิน")],
        UNDECIDED, subject=c["pair"], evidence=_evidence(c, names), rememberable=False,
        recommended=MATCH if sure else None,
        facts={"pass": c["pass"], "tier": c["tier"], "shape": c["shape"], "difference": c["difference"],
               "days_apart": c.get("days_apart"), "similarity": c.get("similarity")})


def all_card(cs: list, names: dict, moment: str, tol: Tolerance) -> Finding:
    rows = []
    for c in cs:
        l, r = c["left"][0], c["right"][0]
        rows.append([date_th(l.get("date")), safe(l.get("name"))[:60], date_th(r.get("date")), safe(r.get("name"))[:60],
                     f"{Decimal(l['amount']):,.2f}"])
    n = len(cs)
    return finding(
        CODE_ALL, "blocking", moment, HEADER_ALL,
        f"พบ {n} คู่ที่ยอดเท่ากันทุกสตางค์ วันที่ห่างกันไม่เกิน {tol.days} วัน และแต่ละรายการมีคู่ที่เป็นไปได้คู่เดียว "
        f"ดูรายการทั้ง {n} คู่ในหลักฐาน แล้วยืนยันในครั้งเดียวได้หรือไม่",
        [(f"ยืนยันทั้ง {n} คู่", MATCH, "จับคู่ทุกคู่ในรายการนี้ และบันทึกว่าคุณเป็นผู้ยืนยัน"),
         ("ขอดูทีละคู่", ONE_BY_ONE, "ถามทีละคู่"),
         ("ยังไม่ยืนยัน", UNDECIDED, "ทุกคู่อยู่ในกลุ่มน่าสงสัย รอคนตัดสิน")],
        UNDECIDED, subject=SEP.join(c["pair"] for c in cs), rememberable=False,
        evidence={"columns": [f"วันที่ ({names['left']})", names["left"], f"วันที่ ({names['right']})", names["right"], "จำนวนเงิน"],
                  "rows": rows},
        facts={"pairs": [c["pair"] for c in cs], "pass": cs[0]["pass"]})


def lone_card(row: dict, names: dict, moment: str, *, options=None, default=REVIEW, question: str | None = None) -> Finding:
    """A row without a partner. A package that knows what such a row can be
    (a bank fee, a cheque not yet presented) passes its own question and options."""
    side = names[row["side"]]
    other = names["right" if row["side"] == "left" else "left"]
    return finding(
        CODE_LONE, "blocking", moment, HEADER_LONE,
        question or f"{side}: {line(row)} ไม่พบคู่ใน{other} รายการนี้ถูกต้องแล้วหรือไม่",
        options or [("ถูกต้อง ยังไม่มีคู่", KEEP, f"คงไว้เป็นรายการที่ยังไม่มีคู่ใน{other}"),
                    ("ควรมีคู่ ขอให้ตรวจ", REVIEW, "ส่งให้คนตรวจในรายการต้องตรวจของรอบนี้")],
        default, subject=row["id"], rememberable=False,
        evidence={"columns": COLUMNS, "rows": [table_row(side, row)]},
        facts={"row": row["id"], "amount": row["amount"]})


def findings(result: dict, answers: dict | None, names: dict, *, tolerance: Tolerance | None = None,
             moment: str = "preflight", lone=lone_card) -> list:
    """Every question the match leaves for a person: the pairs a pass is sure
    about together (when there are enough of them), every other proposal on
    its own, then the large rows without a partner."""
    tol = tolerance or Tolerance()
    _, _, one_by_one = decisions_from(answers)
    waiting = [c for c in result["groups"]["suspect"] if c["kind"] == "candidate" and c["decision"] is None]
    together = [c for c in waiting if c["together"] and c["pair"] not in one_by_one]
    out, asked = [], set()
    if len(together) >= ALL_MIN:
        for i in range(0, len(together), ALL_MAX):
            chunk = together[i:i + ALL_MAX]
            if len(chunk) < ALL_MIN:
                break
            out.append(all_card(chunk, names, moment, tol))
            asked.update(c["pair"] for c in chunk)
    out += [pair_card(c, names, moment) for c in waiting if c["pair"] not in asked]
    if lone is not None:
        for row in result["groups"]["missing"] + result["groups"]["extra"]:
            if abs(Decimal(row["amount"])) >= tol.ask_above:
                f = lone(row, names, moment)
                if f is not None:
                    out.append(f)
    return out
