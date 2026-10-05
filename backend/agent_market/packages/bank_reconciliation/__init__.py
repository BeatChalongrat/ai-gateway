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

from .checks import inputs as _in
from .checks import preflight as _pre
from .checks.inputs import FORMAT, Upload  # noqa: F401
from .scripts import reconcile as _rec

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
    return _rec.compute(files, roles, period, answers=answers, defaults=defaults, today=today, answered_by=answered_by)


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything a person decides about a match is asked before compute; what is left for a person
    after it is a run exception. Nothing is asked after compute."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _rec.summarize(computed)


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables.formats import wanted_formats
    from ...deliverables.theme import resolve
    from .scripts.workbook import BankWorkbook, check

    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, all_ok = [], True
    for fmt in [f for f in wanted_formats(req, ["xlsx"]) if f == "xlsx"]:
        name = os.path.basename(names.get(fmt) or "bank_reconciliation.xlsx")
        path = os.path.join(out_dir, name)
        meta = BankWorkbook(computed, theme, req.get("run")).write(path)
        checks = check(path, computed, req.get("run"))
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        artifacts.append({"name": name, "format": fmt, "kind": "table", "size": os.path.getsize(path), "sha256": sha,
                          "quality": {"passed": ok, "checks": checks}, "meta": meta, "chart_bindings": [],
                          "citations": len(computed["citations"])})
    return {"passed": all_ok, "artifacts": artifacts, "theme": {"id": theme["id"], "version": theme.get("version", 1)}}
