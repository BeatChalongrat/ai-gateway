"""Agent 4 — Expense Audit.

A month's expense claims (an .xlsx/.csv sheet) and their receipts in; every claim checked against
the company's policy *table* out, as an .xlsx audit: per claim, every deterministic fact with its
evidence and an outcome — ``pass``, ``exception`` (a rule failed: a person decides, from the run
exceptions) or ``question`` (judgement: a clarification card). Nothing is ever rejected
automatically, no limit ever comes from a KB policy document (context only), and no model writes a
number: receipts are read by the E2 engine (a vision model may only propose a value for a scan or
photo, a person decides).

The package's Python surface, called by agent_market.engine.step:
parse · dump · load · parse_extras · preflight · compute · postcompute · summarize · render;
and ``policy_from_xlsx`` for the install step (an owner's policy sheet → the stored table).
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from .checks import inputs as _in
from .checks import preflight as _pre
from .checks.inputs import FORMAT, Upload  # noqa: F401
from .checks.policy import policy_from_xlsx  # noqa: F401
from .scripts import audit as _audit

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _manifest() -> dict:
    try:
        from importlib.resources import files

        return json.loads(files(__name__).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


VERSION = _manifest().get("version")


def parse(path: str, name: str, mapping: dict) -> Upload:
    """Read one upload once: a claims sheet, a receipt (E2), or a KB document (context)."""
    return _in.parse(path, name, mapping)


def dump(u: Upload) -> dict:
    return _in.dump(u)


def load(d: dict) -> Upload:
    return _in.load(d)


def parse_extras(u: Upload, path: str, out_dir: str) -> dict:
    """Page images of a scanned or photographed receipt for the runner's vision step (a second reader,
    never the source of a number) — Agent 1's rule. Sheets, KB documents and digital receipts: none."""
    if u.kind != "receipt":
        return {"vision_pages": []}
    from ..invoice_processing import parse_extras as vision_pages

    return vision_pages(u, path, out_dir)


def preflight(files: list[Upload], *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
              today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    return _pre.run(files, message=message, defaults=defaults, answers=answers, today=today, history=history,
                    proposals=proposals)


def compute(files, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None) -> dict:
    return _audit.compute(files, roles, period, answers=answers, defaults=defaults, today=today, history=history,
                          proposals=proposals)


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything a person must decide is either asked before compute (judgement cards) or handed to
    them as a run exception after it; nothing is asked after compute."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _audit.summarize(computed)


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables.theme import resolve
    from .scripts.workbook import ExpenseWorkbook, check

    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, all_ok = [], True
    for fmt in [f for f in (req.get("formats") or ["xlsx"]) if f == "xlsx"]:
        name = os.path.basename(names.get(fmt) or "expense_audit.xlsx")
        path = os.path.join(out_dir, name)
        meta = ExpenseWorkbook(computed, theme, req.get("run")).write(path)
        checks = check(path, computed, req.get("run"))
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        artifacts.append({"name": name, "format": fmt, "kind": "table", "size": os.path.getsize(path), "sha256": sha,
                          "quality": {"passed": ok, "checks": checks}, "meta": meta, "chart_bindings": [],
                          "citations": len(computed["run_exceptions"])})
    return {"passed": all_ok, "artifacts": artifacts, "theme": {"id": theme["id"], "version": theme.get("version", 1)}}
