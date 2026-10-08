"""Agent 3 — Bank Reconciliation (กระทบยอดธนาคาร).

The bank's statement and the company's ledger of that account in (Excel or CSV, the layouts Thai
banks and accounting programs export), a bank reconciliation statement out: whose account and
which period, the balances of both sides, every reconciling item with its evidence, the adjusted
bank balance and the adjusted book balance — and whether they tie.

The pairs come from the E3 engine (engine/e3): a rule accepts a pair only when it is exact; every
other pair is proposed and a person confirms it. No model adds a number or decides a pair, and
this agent writes nothing into an accounting system: the entries for the book side are drafts.

THE GATE (scripts/reconcile.py): the two adjusted balances are equal to the satang, or the round
is not a success and says "ยังไม่ลงตัว ต่างกัน X บาท".

The package's Python surface, called by agent_market.engine.step:
parse · dump · load · preflight · compute · postcompute · summarize · render.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from ...engine import words as _words   # (this package has a module of its own named words)
from .checks import inputs as _in
from .checks import preflight as _pre
from .checks.inputs import FORMAT, Upload  # noqa: F401
from .scripts import reconcile as _rec
from .words import W

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _manifest() -> dict:
    try:
        from importlib.resources import files

        return json.loads(files(__name__).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


VERSION = _manifest().get("version")


def parse(path: str, name: str, mapping: dict) -> Upload:
    """Read one upload once: a statement, a ledger, or the list of items carried in."""
    return _in.parse(path, name, mapping)


def dump(u: Upload) -> dict:
    return _in.dump(u)


def load(d: dict) -> Upload:
    return _in.load(d)


def preflight(files: list[Upload], *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
              today: _dt.date | None = None) -> dict:
    return _pre.run(files, message=message, defaults=defaults, answers=answers, today=today)


def compute(files, roles, period, *, answers=None, defaults=None, today=None, answered_by=None) -> dict:
    """``answered_by``: who answered each card ({answer key: {name}}), from the runner — a pair a
    person confirmed names that person."""
    def table():
        return _rec.compute(files, roles, period, answers=answers, defaults=defaults, today=today, answered_by=answered_by)

    return _words.with_document(table(), table)


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything a person decides about a match is asked before compute; what is left for a person
    after it is a run exception. Nothing is asked after compute."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _rec.summarize(computed)


# AM13 — what a person asked of the working paper (agent.json "instructions"). An order for the reconciling
# items, applied here at render time and nowhere else: the items sheet and its evidence sheet are made from the
# one list, so they stay row for row. The statement, the two adjusted balances, the journal drafts and the
# list carried to next month are made from what was computed and do not move.
DIRECTIVES = ("sort_by_amount", "sort_by_date")


def _as_asked(computed: dict, req: dict) -> tuple[dict, list[str]]:
    """The computed table as the working paper is to show it, and the directives applied."""
    from ...deliverables import asked

    order = asked.one_order(req, DIRECTIVES)
    if order is None:
        return computed, []
    if order == "sort_by_amount":
        def size(i):
            v = asked.number(i.get("amount"))
            return None if v is None else abs(v)

        items = asked.largest_first(computed["items"], size)
    else:
        # oldest first; an item with no date after them (stable: the file's order within a day)
        items = sorted(computed["items"], key=lambda i: (0, str(i["date"])) if i.get("date") else (1, ""))
    return dict(computed, items=items), [order]


def _date_read(text) -> tuple | None:
    """A date as the working paper prints it (“5 ก.ย. 2569”), read back."""
    from ...engine.thai import BE_OFFSET, MONTHS_TH_ABBR

    bits = str(text or "").split()
    try:
        return (int(bits[2]) - BE_OFFSET, MONTHS_TH_ABBR.index(bits[1]) + 1, int(bits[0])) if len(bits) == 3 else None
    except ValueError:
        return None


def _asked_check(path: str, view: dict, applied: list[str]) -> dict:
    """What was asked, read back out of the finished workbook: the amounts, or the dates, on the items
    sheet from top to bottom, by what is in its own cells."""
    from openpyxl import load_workbook

    from ...deliverables import asked
    from ...deliverables.reread import table
    from .scripts.workbook import H_ITEMS, S_ITEMS, parts

    rows = table(parts(load_workbook(path), S_ITEMS), H_ITEMS)
    same = len(rows) == len(view["items"])
    if applied == ["sort_by_amount"]:
        sizes = [abs(asked.number(r[6])) if isinstance(r[6], (int, float)) and not isinstance(r[6], bool) else None for r in rows]
        if not same or not asked.runs_down(sizes):
            return asked.check(False, W("asked.amount.not", sheet=S_ITEMS))
        return asked.check(True, W("asked.amount.ok", n=len(rows)), len(rows))
    dates = [_date_read(r[H_ITEMS.index("วันที่")]) for r in rows]
    if not same or not asked.runs_up(dates):
        return asked.check(False, W("asked.date.not", sheet=S_ITEMS))
    return asked.check(True, W("asked.date.ok", n=len(rows)), len(rows))


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables.formats import wanted_formats
    from ...deliverables.theme import resolve
    from .scripts.workbook import BankWorkbook, check

    # the working paper is written from the table in the documents' language; what the gate then says of
    # the file is said in the run's
    view, applied = _as_asked(_words.for_document(computed), req)
    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, all_ok = [], True
    for fmt in [f for f in wanted_formats(req, ["xlsx"]) if f == "xlsx"]:
        name = os.path.basename(names.get(fmt) or "bank_reconciliation.xlsx")
        path = os.path.join(out_dir, name)
        with _words.speaking(_words.DOCUMENT):
            meta = BankWorkbook(view, theme, req.get("run")).write(path)
        checks = check(path, view, req.get("run"))
        if applied:
            checks.append(_asked_check(path, view, applied))
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        artifacts.append({"name": name, "format": fmt, "kind": "table", "size": os.path.getsize(path), "sha256": sha,
                          "quality": {"passed": ok, "checks": checks}, "meta": meta, "chart_bindings": [],
                          "citations": len(computed["citations"])})
    return {"passed": all_ok, "artifacts": artifacts, "theme": {"id": theme["id"], "version": theme.get("version", 1)},
            "asked": {"applied": applied, "refused": []}}
