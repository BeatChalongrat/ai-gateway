"""The passes, and the four groups they leave.

``run(left, right, passes=, tolerance=, decisions=)`` walks the passes in
order over the rows no earlier pass has taken:

  * a pass of the ``exact`` tier accepts what it finds (``confirmed: rule``);
  * a pass of any other tier only proposes. A proposal is accepted when
    ``decisions`` holds ``match`` for its pair (``confirmed: person``), never
    offered again when it holds ``separate``, and otherwise held as a suspect
    — its rows wait for a person and are offered to no later pass.

``decisions`` is keyed by the pair — the ids of its rows, ``L1+L2~R7`` — so a
decision belongs to those rows whatever pass proposed them, and to no others:
an answer that confirms several pairs at once (cards.py) names each of them.
``keys`` says which answer a decision came from, for the record.

A pass that is not ``exact`` cannot be automatic: ``Pass`` refuses it.
Nothing here reads a clock, a random number or the order of a set.
"""

from __future__ import annotations

import bisect
import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Callable

from .rows import Row, check_rows, name_key, q2, similarity

SCHEMA = "e3.match.v1"
TIERS = ("exact", "amount_date", "fuzzy")
TIER_TH = {"exact": "ตรงกันทุกตัว", "amount_date": "ยอดและวันที่ใกล้กัน", "fuzzy": "ชื่อหรือเลขอ้างอิงคล้ายกัน"}
MATCH, SEPARATE, UNDECIDED = "match", "separate", "undecided"
DECISIONS = (MATCH, SEPARATE, UNDECIDED)
ZERO = Decimal("0.00")
POOL = 12          # the most rows a sum is looked for among
DAY_GROUP = 12     # the most rows of one day, both sides together, offered as one group
ALTERNATIVES = 5   # the most other partners a proposal names
_EPOCH = _dt.date(1, 1, 1)


# ── tolerance ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Tolerance:
    """How far apart two rows may be and still be offered as a pair. An
    install's settings; these are the defaults."""
    amount: Decimal = Decimal("1.00")         # baht
    days: int = 3                             # calendar days
    fuzzy: Decimal = Decimal("0.80")          # least resemblance of names or references
    fuzzy_days: int = 35
    ask_above: Decimal = Decimal("50000.00")  # a row without a partner is asked about from this amount
    group: int = 4                            # the most rows on one side of a sum
    same_day: bool = True                     # the same date and amount, one row each side: accepted by rule

    def to_dict(self) -> dict:
        return {"amount_tolerance": f"{self.amount:.2f}", "date_window_days": self.days,
                "fuzzy_threshold": f"{self.fuzzy:.2f}", "fuzzy_window_days": self.fuzzy_days,
                "ask_above": f"{self.ask_above:.2f}", "max_group": self.group, "auto_same_day": self.same_day}


# setting → (field, kind, least, most)
SETTINGS = {
    "amount_tolerance": ("amount", "money", Decimal("0"), Decimal("1000")),
    "date_window_days": ("days", "int", 0, 31),
    "fuzzy_threshold": ("fuzzy", "ratio", Decimal("0.50"), Decimal("1.00")),
    "fuzzy_window_days": ("fuzzy_days", "int", 0, 366),
    "ask_above": ("ask_above", "money", Decimal("0"), Decimal("1000000000000")),
    "max_group": ("group", "int", 2, 6),
    "auto_same_day": ("same_day", "bool", None, None),
}
SETTING_TH = {"amount_tolerance": "ผลต่างของยอดที่ยอมให้เสนอเป็นคู่ (บาท)", "date_window_days": "วันที่ห่างกันได้ไม่เกิน (วัน)",
              "fuzzy_threshold": "ความคล้ายของชื่อหรือเลขอ้างอิงอย่างน้อย", "fuzzy_window_days": "วันที่ห่างกันได้เมื่อจับคู่จากชื่อ (วัน)",
              "ask_above": "ถามเมื่อรายการที่ไม่มีคู่มียอดตั้งแต่ (บาท)", "max_group": "จำนวนรายการมากที่สุดที่รวมยอดเป็นคู่เดียว",
              "auto_same_day": "จับคู่เองเมื่อวันที่และยอดตรงกันและมีรายการเดียว"}


def _setting(kind: str, v, lo, hi):
    if kind == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            return v.strip().lower() == "true"
        return None
    if isinstance(v, bool) or v is None:
        return None
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d < lo or d > hi:
        return None
    if kind == "int":
        return int(d) if d == d.to_integral_value() else None
    return q2(d)


def tolerance_from(settings) -> tuple[Tolerance, list[str]]:
    """The install's settings as a Tolerance. A value that cannot be used (not
    a number, outside what the setting allows) takes its default, and the
    setting is named in the second value so that the run can say so."""
    kw, unusable = {}, []
    for key, (attr, kind, lo, hi) in SETTINGS.items():
        if not isinstance(settings, dict) or key not in settings:
            continue
        v = _setting(kind, settings[key], lo, hi)
        if v is None:
            unusable.append(key)
        else:
            kw[attr] = v
    return Tolerance(**kw), unusable


# ── proposals ────────────────────────────────────────────────────────────

def pair_id(left, right) -> str:
    ls = sorted(left, key=lambda r: r.order)
    rs = sorted(right, key=lambda r: r.order)
    return "+".join(r.id for r in ls) + "~" + "+".join(r.id for r in rs)


def shape_of(n_left: int, n_right: int) -> str:
    if n_left == 1 and n_right == 1:
        return "1:1"
    if n_left == 1:
        return "1:N"
    if n_right == 1:
        return "N:1"
    return "N:M"


def _total(rows) -> Decimal:
    return sum((r.amount for r in rows), ZERO)


def _days(a: Row, b: Row) -> int | None:
    return abs((b.date - a.date).days) if a.date and b.date else None


def _span(left, right) -> int | None:
    ds = [r.date for r in tuple(left) + tuple(right) if r.date]
    if len(ds) < 2 or not all(r.date for r in tuple(left) + tuple(right)):
        return None
    l = [r.date for r in left]
    r = [x.date for x in right]
    return max(abs((b - a).days) for a in l for b in r)


def _same_sign(a: Row, b: Row) -> bool:
    return a.amount != ZERO and b.amount != ZERO and (a.amount > ZERO) == (b.amount > ZERO)


@dataclass
class Proposal:
    left: tuple
    right: tuple
    similarity: Decimal | None = None
    unique: bool = True
    alternatives: list = field(default_factory=list)

    @property
    def pair(self) -> str:
        return pair_id(self.left, self.right)

    @property
    def difference(self) -> Decimal:
        return _total(self.right) - _total(self.left)

    @property
    def days(self) -> int | None:
        return _span(self.left, self.right)

    @property
    def shape(self) -> str:
        return shape_of(len(self.left), len(self.right))


@dataclass(frozen=True)
class Pass:
    id: str
    tier: str
    label: str
    find: Callable            # (free_left, free_right, tolerance, refused) → (proposals, duplicates)
    auto: bool = False
    batch: bool = False       # its sure proposals may be confirmed by one answer that names each of them

    def __post_init__(self):
        if self.tier not in TIERS:
            raise ValueError(f"pass {self.id!r}: tier {self.tier!r}")
        if self.auto and self.tier != "exact":
            raise ValueError(f"pass {self.id!r}: only a pass of the exact tier may accept a pair by itself")
        if not self.id.replace("_", "").isalnum():
            raise ValueError(f"bad pass id {self.id!r}")


def _by_date(rows):
    return sorted(rows, key=lambda r: (r.date or _EPOCH, r.order))


# ── the exact tier ───────────────────────────────────────────────────────

def _find_key(keyfn, *, lone: bool = False):
    """Rows that share a key. ``lone``: only a key one row holds on each side."""
    def find(L, R, tol, refused):
        right: dict = {}
        for r in R:
            k = keyfn(r)
            if k is not None:
                right.setdefault(k, []).append(r)
        left: dict = {}
        for l in L:
            k = keyfn(l)
            if k is not None and k in right:
                left.setdefault(k, []).append(l)
        out, dup = [], {}
        for k, ls in left.items():          # in the order the left table holds them
            rs = right[k]
            if lone and (len(ls) != 1 or len(rs) != 1):
                continue
            ls, rs = _by_date(ls), _by_date(rs)
            n = min(len(ls), len(rs))
            for l, r in zip(ls, rs):
                out.append(Proposal((l,), (r,)))
            for x in ls[n:]:
                dup[x.id] = [l.id for l in ls[:n]]
            for x in rs[n:]:
                dup[x.id] = [r.id for r in rs[:n]]
        return out, dup
    return find


def _key_ref_amount_date(r: Row):
    return (r.ref_key, r.amount, r.date) if r.ref_key and r.date else None


def _key_ref_amount(r: Row):
    return (r.ref_key, r.amount) if r.ref_key else None


def _key_date_amount(r: Row):
    return (r.date, r.amount) if r.date and r.amount != ZERO else None


def _find_same_day(L, R, tol, refused):
    """The same date and the same amount, one row on each side — and no other
    row of that amount within the date window that could have been meant."""
    if not tol.same_day:
        return [], {}
    props, _ = _find_key(_key_date_amount, lone=True)(L, R, tol, refused)

    def rivals(rows, p):
        l = p.left[0]
        return sum(1 for x in rows if x.amount == l.amount and x.date and abs((x.date - l.date).days) <= tol.days)
    return [p for p in props if rivals(L, p) == 1 and rivals(R, p) == 1], {}


def _find_ref_sum(L, R, tol, refused):
    """Several rows under one reference whose sum is the sum of the rows under
    that reference in the other table."""
    right: dict = {}
    for r in R:
        if r.ref_key:
            right.setdefault(r.ref_key, []).append(r)
    left: dict = {}
    for l in L:
        if l.ref_key and l.ref_key in right:
            left.setdefault(l.ref_key, []).append(l)
    out = []
    for k, ls in left.items():
        rs = right[k]
        if (len(ls), len(rs)) == (1, 1):
            continue
        if _total(ls) == _total(rs):
            out.append(Proposal(tuple(_by_date(ls)), tuple(_by_date(rs))))
    return out, {}


# ── the tiers a person confirms ──────────────────────────────────────────

def _greedy(found: list, L, R) -> list:
    """From every possible pair, the best first; a row is in one pair only.
    ``found``: (rank…, left row, right row, similarity)."""
    partners_l: dict = {}
    partners_r: dict = {}
    for *_, l, r, _s in found:
        partners_l.setdefault(l.id, []).append(r.id)
        partners_r.setdefault(r.id, []).append(l.id)
    taken, out = set(), []
    for *_, l, r, s in sorted(found, key=lambda t: t[:-3]):
        if l.id in taken or r.id in taken:
            continue
        taken.update((l.id, r.id))
        others = [x for x in partners_l[l.id] if x != r.id] + [x for x in partners_r[r.id] if x != l.id]
        out.append(Proposal((l,), (r,), similarity=s, unique=not others, alternatives=others[:ALTERNATIVES]))
    return sorted(out, key=lambda p: (p.left[0].order, p.right[0].order))


def best_pairs(found: list, L, R) -> list:
    """For a package's own pass: from every possible pair ``(rank…, left row, right row,
    similarity)``, the best first, a row in one pair only — what the engine's own passes use."""
    return _greedy(found, L, R)


def _near_amount(R_sorted, amounts, lo: Decimal, hi: Decimal):
    return R_sorted[bisect.bisect_left(amounts, lo):bisect.bisect_right(amounts, hi)]


def _find_amount_date(L, R, tol, refused):
    rs = sorted(R, key=lambda r: (r.amount, r.order))
    amounts = [r.amount for r in rs]
    found = []
    for l in L:
        if l.date is None:
            continue
        for r in _near_amount(rs, amounts, l.amount - tol.amount, l.amount + tol.amount):
            if r.date is None or not _same_sign(l, r):
                continue
            d = abs((r.date - l.date).days)
            if d > tol.days or pair_id((l,), (r,)) in refused:
                continue
            found.append((abs(r.amount - l.amount), d, l.order, r.order, l, r, None))
    return _greedy(found, L, R), {}


def resemblance(l: Row, r: Row) -> Decimal:
    """How much two rows resemble each other in their names or references."""
    best = similarity(l.name, r.name)
    a, b = l.ref_key, r.ref_key
    if a and b:
        if a == b:
            return Decimal("1.00")
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 4 and short in long_:
            best = max(best, Decimal("0.90"))
        else:
            from difflib import SequenceMatcher

            ratio = Decimal(str(SequenceMatcher(None, a, b, autojunk=False).ratio())).quantize(Decimal("0.01"))
            if ratio >= Decimal("0.85"):
                best = max(best, ratio)
    # a reference written inside the other row's text ("โอนชำระ INV2569/0940")
    for ref, text in ((a, r.name), (b, l.name)):
        if len(ref) >= 5 and ref.lower() in name_key(text):
            best = max(best, Decimal("0.90"))
    return best


def _find_fuzzy(L, R, tol, refused):
    rs = sorted(R, key=lambda r: (r.amount, r.order))
    amounts = [r.amount for r in rs]
    found = []
    for l in L:
        for r in _near_amount(rs, amounts, l.amount - tol.amount, l.amount + tol.amount):
            if not _same_sign(l, r):
                continue
            d = _days(l, r)
            if (d is not None and d > tol.fuzzy_days) or pair_id((l,), (r,)) in refused:
                continue
            s = resemblance(l, r)
            if s < tol.fuzzy:
                continue
            found.append((-s, abs(r.amount - l.amount), d if d is not None else 10 ** 6, l.order, r.order, l, r, s))
    return _greedy(found, L, R), {}


def _find_ref_only(L, R, tol, refused):
    """One reference, one row on each side, and amounts that do not agree."""
    props, _ = _find_key(lambda r: r.ref_key or None, lone=True)(L, R, tol, refused)
    out = []
    for p in props:
        if abs(p.difference) > tol.amount and _same_sign(p.left[0], p.right[0]) and p.pair not in refused:
            p.similarity = Decimal("1.00")
            out.append(p)
    return out, {}


def _find_group(L, R, tol, refused):
    """One row whose amount is the sum of several rows of the other table
    (a deposit of three cheques; one transfer paying four bills)."""
    out, used = [], set()

    def look(ones, many, one_is_left: bool):
        for t in ones:
            if t.id in used or t.date is None or t.amount == ZERO:
                continue
            pool = [x for x in many if x.id not in used and x.date is not None and _same_sign(t, x)
                    and abs((x.date - t.date).days) <= tol.days and abs(x.amount) < abs(t.amount)][:POOL]
            hit = []
            for n in range(2, min(tol.group, len(pool)) + 1):
                for combo in combinations(pool, n):
                    if _total(combo) != t.amount:
                        continue
                    left, right = ((t,), combo) if one_is_left else (combo, (t,))
                    if pair_id(left, right) in refused:
                        continue
                    hit.append((left, right))
                    if len(hit) == 2:
                        break
                if hit:
                    break
            if hit:
                left, right = hit[0]
                used.update(r.id for r in left + right)
                out.append(Proposal(tuple(left), tuple(right), unique=len(hit) == 1))

    look(L, R, True)
    look(R, L, False)
    return sorted(out, key=lambda p: (min(r.order for r in p.left), min(r.order for r in p.right))), {}


def _find_day_sum(L, R, tol, refused):
    """Several rows of one day on each side, and the day's sums agree."""
    def days_of(rows):
        by: dict = {}
        for r in rows:
            if r.date and r.amount != ZERO:
                by.setdefault((r.date, r.amount > ZERO), []).append(r)
        return by
    left, right = days_of(L), days_of(R)
    out = []
    for k in sorted(k for k in left if k in right):
        ls, rs = left[k], right[k]
        if len(ls) < 2 or len(rs) < 2 or len(ls) + len(rs) > DAY_GROUP or _total(ls) != _total(rs):
            continue
        p = Proposal(tuple(ls), tuple(rs))
        if p.pair not in refused:
            out.append(p)
    return out, {}


DEFAULT_PASSES = (
    Pass("ref_amount_date", "exact", "เลขอ้างอิง ยอด และวันที่ตรงกัน", _find_key(_key_ref_amount_date), auto=True),
    Pass("ref_amount", "exact", "เลขอ้างอิงและยอดตรงกัน", _find_key(_key_ref_amount), auto=True),
    Pass("ref_sum", "exact", "เลขอ้างอิงเดียวกัน ยอดรวมตรงกัน", _find_ref_sum, auto=True),
    Pass("date_amount", "exact", "วันที่และยอดตรงกัน มีรายการเดียวทั้งสองฝั่ง", _find_same_day, auto=True),
    Pass("amount_date", "amount_date", "ยอดและวันที่อยู่ในช่วงที่ตั้งไว้", _find_amount_date, batch=True),
    Pass("group_sum", "amount_date", "หลายรายการรวมกันเท่ากับหนึ่งรายการ", _find_group),
    Pass("day_sum", "amount_date", "ยอดรวมของวันเดียวกันตรงกัน", _find_day_sum),
    Pass("ref_only", "fuzzy", "เลขอ้างอิงตรงกัน ยอดไม่ตรงกัน", _find_ref_only),
    Pass("fuzzy_name", "fuzzy", "ชื่อหรือเลขอ้างอิงคล้ายกัน", _find_fuzzy),
)


def passes_by_id(*ids: str) -> tuple:
    known = {p.id: p for p in DEFAULT_PASSES}
    return tuple(known[i] for i in ids)


# ── the run ──────────────────────────────────────────────────────────────

def _entry(n: int, prefix: str, p: Pass, pr: Proposal) -> dict:
    return {"id": f"{prefix}{n:04d}", "pair": pr.pair, "pass": p.id, "pass_label": p.label, "tier": p.tier,
            "tier_label": TIER_TH[p.tier], "shape": pr.shape,
            "left": [r.ref_out() for r in pr.left], "right": [r.ref_out() for r in pr.right],
            "left_total": f"{_total(pr.left):.2f}", "right_total": f"{_total(pr.right):.2f}",
            "difference": f"{pr.difference:.2f}", "days_apart": pr.days,
            "similarity": None if pr.similarity is None else f"{pr.similarity:.2f}"}


def sure(pr: Proposal) -> bool:
    """A proposal one answer may confirm with the others of its pass: no row
    of it has another possible partner, and the amounts are equal."""
    return pr.unique and pr.difference == ZERO


def run(left: list, right: list, *, passes=None, tolerance: Tolerance | None = None, decisions: dict | None = None,
        keys: dict | None = None) -> dict:
    L, R = check_rows(left, "left"), check_rows(right, "right")
    both = {r.id for r in L} & {r.id for r in R}
    if both:
        raise ValueError(f"the two tables share row ids: {sorted(both)[:3]}")
    tol = tolerance or Tolerance()
    passes = tuple(passes if passes is not None else DEFAULT_PASSES)
    if len({p.id for p in passes}) != len(passes):
        raise ValueError("two passes share an id")
    said = {k: v for k, v in (decisions or {}).items() if isinstance(k, str) and v in DECISIONS and "~" in k}
    keys = keys or {}
    refused = {k for k, v in said.items() if v == SEPARATE}
    free_l, free_r = list(L), list(R)
    matched, suspect, stats, duplicates = [], [], [], {}

    def take(pr: Proposal):
        gone = {r.id for r in pr.left + pr.right}
        free_l[:] = [r for r in free_l if r.id not in gone]
        free_r[:] = [r for r in free_r if r.id not in gone]

    for p in passes:
        props, dup = p.find(free_l, free_r, tol, refused)
        duplicates.update(dup)
        n_rule = n_person = n_open = n_sure = 0
        for pr in props:
            one = said.get(pr.pair)
            if p.auto:
                e = _entry(len(matched) + 1, "m", p, pr)
                e.update(confirmed="rule", decision_key=None)
                matched.append(e)
                n_rule += 1
            elif one == MATCH:
                e = _entry(len(matched) + 1, "m", p, pr)
                e.update(confirmed="person", decision_key=keys.get(pr.pair, pr.pair))
                matched.append(e)
                n_person += 1
            else:
                e = _entry(len(suspect) + 1, "s", p, pr)
                together = p.batch and sure(pr)
                n_sure += 1 if together else 0
                e.update(kind="candidate", unique=pr.unique, alternatives=pr.alternatives, decision=one,
                         decision_key=keys.get(pr.pair) if one else None, together=together)
                suspect.append(e)
                n_open += 1
            take(pr)
        stats.append({"id": p.id, "tier": p.tier, "tier_label": TIER_TH[p.tier], "label": p.label, "auto": p.auto,
                      "by_rule": n_rule, "by_person": n_person, "waiting": n_open, "waiting_sure": n_sure})

    # a row left over under a key other rows were matched by: the same line written twice?
    for rows in (free_l, free_r):
        for r in list(rows):
            if r.id in duplicates:
                suspect.append({"id": f"s{len(suspect) + 1:04d}", "kind": "possible_duplicate", "pair": r.id, "pass": None,
                                "tier": None, "shape": None, "left": [r.ref_out()] if r.side == "left" else [],
                                "right": [r.ref_out()] if r.side == "right" else [], "same_as": duplicates[r.id],
                                "left_total": f"{r.amount if r.side == 'left' else ZERO:.2f}",
                                "right_total": f"{r.amount if r.side == 'right' else ZERO:.2f}",
                                "decision": None, "decision_key": None, "together": False})
                rows.remove(r)

    def lone(r: Row) -> dict:
        out = r.ref_out()
        out["refused"] = sorted(k for k in refused if r.id in k.replace("~", "+").split("+"))
        return out

    missing, extra = [lone(r) for r in free_l], [lone(r) for r in free_r]
    tiers = []
    for t in TIERS:
        ms = [m for m in matched if m["tier"] == t]
        tiers.append({"tier": t, "label": TIER_TH[t], "groups": len(ms),
                      "by_rule": sum(1 for m in ms if m["confirmed"] == "rule"),
                      "by_person": sum(1 for m in ms if m["confirmed"] == "person"),
                      "rows_left": sum(len(m["left"]) for m in ms), "rows_right": sum(len(m["right"]) for m in ms),
                      "waiting": sum(1 for s in suspect if s["tier"] == t)})
    in_left = lambda es: sum(len(e["left"]) for e in es)     # noqa: E731
    in_right = lambda es: sum(len(e["right"]) for e in es)   # noqa: E731
    return {
        "schema": SCHEMA,
        "tolerance": tol.to_dict(),
        "passes": stats,
        "tiers": tiers,
        "groups": {"matched": matched, "missing": missing, "extra": extra, "suspect": suspect},
        "counts": {"left": len(L), "right": len(R), "matched": len(matched),
                   "matched_left": in_left(matched), "matched_right": in_right(matched),
                   "missing": len(missing), "extra": len(extra), "suspect": len(suspect),
                   "suspect_left": in_left(suspect), "suspect_right": in_right(suspect), "refused": len(refused)},
        "totals": {"left": f"{_total(L):.2f}", "right": f"{_total(R):.2f}",
                   "missing": f"{_total(free_l):.2f}", "extra": f"{_total(free_r):.2f}"},
    }
