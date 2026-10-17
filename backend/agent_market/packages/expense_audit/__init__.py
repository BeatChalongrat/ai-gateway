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

from ...engine import words as _words   # (this package has a module of its own named words)
from .checks import inputs as _in
from .checks import preflight as _pre
from .checks.inputs import FORMAT, Upload  # noqa: F401
from .checks.policy import policy_from_xlsx  # noqa: F401
from .scripts import audit as _audit
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
    def table():
        return _audit.compute(files, roles, period, answers=answers, defaults=defaults, today=today, history=history,
                              proposals=proposals)

    return table()


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything a person must decide is either asked before compute (judgement cards) or handed to
    them as a run exception after it; nothing is asked after compute."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _audit.summarize(computed)


# AM11b — what a person asked of the workbook (agent.json "instructions"; the runner sends the ids a model
# read out of the person's words as req["instructions"]["apply"]). Applied here, at render time, and nowhere
# else: the order of the claims on their sheets. The same claims, the same figures, the same outcomes —
# every claim is still there, passed or not, and each still names its row in the file it came from.
DIRECTIVES = ("sort_by_amount",)


def _as_asked(computed: dict, req: dict) -> tuple[dict, list[str]]:
    """The computed table as the workbook is to show it, and the directives applied."""
    from decimal import Decimal

    ids = (req.get("instructions") or {}).get("apply") or []
    applied = [i for i in DIRECTIVES if i in ids]
    if "sort_by_amount" not in applied:
        return computed, applied

    def size(c):
        return Decimal(c["amount"]) if c.get("amount") not in (None, "") else Decimal(-1)

    # stable: claims of the same amount keep the file's order
    return dict(computed, claims=sorted(computed["claims"], key=lambda c: -size(c))), applied


def _asked_check(path: str, view: dict, applied: list[str]) -> dict:
    """What was asked, read back out of the finished workbook: the amounts on the claims sheet, top to
    bottom, by the numbers in its own cells."""
    from openpyxl import load_workbook

    from ...deliverables import asked
    from ...deliverables.reread import table
    from .scripts.workbook import H_CLAIMS, S_CLAIMS, parts

    rows = table(parts(load_workbook(path), S_CLAIMS), H_CLAIMS)
    amounts = [float(r[9]) if isinstance(r[9], (int, float)) else -1.0 for r in rows]
    ordered = len(rows) == len(view["claims"]) and all(a >= b for a, b in zip(amounts, amounts[1:]))
    if not ordered:
        return asked.check(False, W("asked.not"))
    return asked.check(True, W("asked.ok", n=len(amounts)), len(amounts))


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables import quality
    from ...deliverables.formats import wanted_formats
    from ...deliverables import document as _document
    from .scripts.document import spec as _doc_spec
    from ...deliverables.look import commentary
    from ...deliverables.look.catalogue import DoesNotFit
    from ...deliverables.theme import resolve
    from .scripts.workbook import ExpenseWorkbook, check

    # AM18: the workbook is written in the run's language, like everything the run says
    view, applied = _as_asked(computed, req)

    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, refused, all_ok = [], [], True
    for fmt in wanted_formats(req, ["xlsx"]):
        if fmt != "xlsx":
            # AM23 §1.2: a report or a deck a person asks for is a document of its own, written from the same
            # computed table through the same renderer (deliverables/document.py) — never the workbook's sheets laid
            # into Word; the workbook stays the authority. (Until AM23 this loop dropped every format but the workbook.)
            all_ok &= _document.into(fmt, view, req, _doc_spec(view, applied), names, "expense_audit", out_dir, artifacts, refused,
                                     in_dir=in_dir)
            continue
        name = os.path.basename(names.get(fmt) or "expense_audit.xlsx")
        path = os.path.join(out_dir, name)
        # AM22 §1.1: the commentary — a model's, or the rules' saying so — on its own sheet, read back by the gate;
        # one too long for its layout is refused naming the section, and the file is not written
        narrative = req.get("narrative") or dict(view.get("fallback_narrative") or {}, written_by="rules")
        try:
            meta = ExpenseWorkbook(view, theme, req.get("run"), narrative=narrative, req=req).write(path)
        except DoesNotFit as e:
            if os.path.exists(path):
                os.remove(path)
            refused.append(commentary.refusal(fmt, name, e))
            all_ok = False
            continue
        checks = check(path, view, req.get("run"))
        checks.append(commentary.check(path, narrative, view, req))
        checks.append(commentary.no_model_name(commentary.workbook_texts(path), commentary.model_names(narrative, view, req)))  # AM23 §3.1
        checks.append(quality.fit_ok(meta.get("layouts") or []))
        if applied:
            checks.append(_asked_check(path, view, applied))
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        artifacts.append({"name": name, "format": fmt, "kind": "table", "size": os.path.getsize(path), "sha256": sha,
                          "quality": {"passed": ok, "checks": checks}, "meta": meta, "chart_bindings": [],
                          "citations": len(computed["run_exceptions"])})
    return {"passed": all_ok, "artifacts": artifacts, "refused": refused,
            "theme": {"id": theme["id"], "version": theme.get("version", 1)}, "asked": {"applied": applied, "refused": []}}


# ── AM22 §2.1: the customer's own form ────────────────────────────────────────
# Every value offered is one the round computed and cited (or a fact of the round: a name, a date); a form slot is
# not a new place to make a number. A value that is not known is "" — its slot is named as a gap on the form's face.


def _money(v) -> str:
    from decimal import Decimal

    from ...engine.thai import fmt_money

    return fmt_money(Decimal(str(v))) if v not in (None, "") else ""


def _day(v) -> str:
    return _words.date(v) if v else ""


def template_values(computed: dict, req: dict | None = None) -> dict:
    """The values a customer's expense-claim form can take: the round's counts (n1–n5) and amounts by outcome (e1–e4),
    the claimant when the round is one person's, and every claim as a line with the outcome the rules gave it."""
    t = computed["totals"]
    by = t["claimed_by_outcome"]
    run = (req or {}).get("run") or {}
    out = {"company": computed.get("company") or "", "period": computed["period_label"],
           "prepared_on": _day(run.get("date") or computed["prepared_on"]), "claims": str(t["claims"]), "passed": str(t["pass"]),
           "exceptions": str(t["exception"]), "judgement": str(t["question"]), "claimed_thb": _money(t["claimed"]),
           "pass_thb": _money(by["pass"]), "exception_thb": _money(by["exception"]), "question_thb": _money(by["question"]),
           "lines": [{"date": _day(c.get("date")) or (c.get("date_raw") or ""), "employee": c.get("employee_name") or c.get("employee_id") or "",
                      "category": c.get("category_label") or c.get("category_raw") or "", "description": c.get("description") or "",
                      "merchant": c.get("merchant") or "", "receipt": c.get("receipt") or c.get("receipt_ref") or "",
                      "amount": _money(c.get("amount")), "outcome": c.get("outcome_label") or ""} for c in computed["claims"]]}
    people = computed.get("by_employee") or []
    if len(people) == 1:
        # one claimant: their name and number fit the form's one blank (several do not)
        out["employee_name"] = people[0].get("employee_name") or ""
        out["employee_id"] = people[0].get("employee_id") or ""
    return out


def template_gaps(computed: dict) -> dict:
    """What the form's face says it lacks — and what this round has no concept of: the agent does not see cash
    advances, so an advance-cleared line is taken off the face rather than printed as a blank to fill."""
    from ...deliverables import form_words

    return form_words.gaps(not_applicable={"advance_cleared": W("form.na.advance")})


def template_wanted(computed: dict) -> bool:
    return bool(computed.get("claims"))


def template_why_not(computed: dict) -> str:
    return W("form.not_made")
