"""Agent 2 — Invoice ↔ Purchase Order (ตรวจใบกำกับภาษีกับใบสั่งซื้อ).

Purchase orders, goods receipts and what the supplier bills in; per invoice a matching result out:
its status, its reason codes, and from every line a link to the PO line and the receipts it was
tested against — and a summary of how many passed, how many are blocked and why, and the value in
each state.

Line by line, never header to header. Cumulatively, never document by document. Three-way where a
receipt exists, two-way where it does not — and every line, the summary and the file say which.

The links come from the E3 engine (engine/e3) with this agent's passes (checks/links.py): a rule
links only what is exact; every other link is proposed and a person confirms it. No model adds a
number, decides a link or reads a tolerance, and this agent approves nothing and posts nothing:
"ผ่าน" means ready to be put forward for approval.

The package's Python surface, called by agent_market.engine.step:
parse · dump · load · parse_extras · preflight · compute · postcompute · summarize · render.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from ...engine import words as _words   # (this package has a module of its own named words)
from .checks import inputs as _in
from .checks import preflight as _pre
from .checks.inputs import FORMAT, Upload  # noqa: F401
from .scripts import match as _match
from .words import W

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_TIERS = ("scan", "photo")
VISION_DPI = 150
VISION_MAX_PAGES = 3
VISION_MAX_SIDE = 1600


def _manifest() -> dict:
    try:
        from importlib.resources import files

        return json.loads(files(__name__).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


VERSION = _manifest().get("version")


def parse(path: str, name: str, mapping: dict) -> Upload:
    """Read one upload once: a workbook of PO, receipt or invoice lines, or a document the supplier sent."""
    return _in.parse(path, name, mapping)


def dump(u: Upload) -> dict:
    return _in.dump(u)


def load(d: dict) -> Upload:
    return _in.load(d)


def parse_extras(u: Upload, path: str, out_dir: str) -> dict:
    """Page images of a scanned or photographed invoice for the runner's vision step (a second
    reader, never the source of a number). A table, a digital PDF and an e-Tax file get none."""
    if u.kind != "document" or u.data["extraction"]["document"]["tier"] not in SCAN_TIERS:
        return {"vision_pages": []}
    from PIL import Image, ImageOps

    images = []
    if path.lower().endswith(".pdf"):
        import extract_text  # /sandbox/scripts

        for i in range(min(u.data["extraction"]["document"]["pages"], VISION_MAX_PAGES)):
            images.append(extract_text.render_page(path, i, VISION_DPI))
    else:
        images.append(ImageOps.exif_transpose(Image.open(path)))
    names = []
    for i, img in enumerate(images, start=1):
        img = img.convert("RGB")
        img.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
        n = f"vision_{i}.jpg"
        img.save(os.path.join(out_dir, n), "JPEG", quality=85)
        names.append(n)
    return {"vision_pages": names}


def preflight(files: list[Upload], *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
              today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    return _pre.run(files, message=message, defaults=defaults, answers=answers, today=today, history=history,
                    proposals=proposals)


def compute(files, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None,
            answered_by=None) -> dict:
    """``history``: the install's earlier invoices and receipts confirmed in this system (the
    cumulative figures start from them). ``answered_by``: who answered each card — a link a person
    confirmed names that person."""
    def table():
        return _match.compute(files, roles, period, answers=answers, defaults=defaults, today=today, history=history,
                              proposals=proposals, answered_by=answered_by)

    return table()


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything a person decides about a link is asked before compute; what is left for a person
    after it is a run exception. Nothing is asked after compute."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _match.summarize(computed)


# AM13 — what a person asked of the working paper (agent.json "instructions"; the runner sends the id a model
# read out of the person's words as req["instructions"]["apply"]). Applied here, at render time, and nowhere
# else: the order of the lines, once, at the one list the line sheet and its two companions are all made from —
# so the three stay row for row. The same lines, the same figures, the same statuses.
DIRECTIVES = ("sort_by_variance",)
# the heading of the column the lines are ordered by — the working paper's own word, and the one the sentences
# asked.variance.* of the manifest name it by
VARIANCE = "doc.col.variance"


def _as_asked(computed: dict, req: dict) -> tuple[dict, list[str]]:
    """The computed table as the working paper is to show it, and the directives applied."""
    from ...deliverables import asked

    order = asked.one_order(req, DIRECTIVES)
    if order is None:
        return computed, []

    def size(x):
        v = asked.number((x.get("price_variance") or {}).get("extended"))
        return None if v is None else abs(v)

    return dict(computed, lines=asked.largest_first(computed["lines"], size)), [order]


def _asked_check(path: str, view: dict, applied: list[str]) -> dict:
    """What was asked, read back out of the finished workbook: the price differences on the line sheet,
    top to bottom, by the numbers in its own cells."""
    from openpyxl import load_workbook

    from ...deliverables import asked
    from ...deliverables.reread import table
    from .scripts.workbook import H_LINES, S_LINES, parts

    rows = table(parts(load_workbook(path), S_LINES), H_LINES)
    col = H_LINES.of(VARIANCE)
    sizes = [abs(asked.number(r[col])) if isinstance(r[col], (int, float)) and not isinstance(r[col], bool) else None for r in rows]
    if len(rows) != len(view["lines"]) or not asked.runs_down(sizes):
        return asked.check(False, W("asked.variance.not", sheet=W(S_LINES)))
    return asked.check(True, W("asked.variance.ok", n=len(rows)), len(rows))


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables import quality
    from ...deliverables.formats import wanted_formats
    from ...deliverables.look import commentary
    from ...deliverables.look.catalogue import DoesNotFit
    from ...deliverables.theme import resolve
    from .scripts.workbook import MatchWorkbook, check

    # AM18: the working paper is written in the run's language, like everything the run says
    view, applied = _as_asked(computed, req)
    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, refused, all_ok = [], [], True
    for fmt in [f for f in wanted_formats(req, ["xlsx"]) if f == "xlsx"]:
        name = os.path.basename(names.get(fmt) or "po_matching.xlsx")
        path = os.path.join(out_dir, name)
        # AM22 §1.1: the commentary — a model's, or the rules' saying so — on its own sheet, read back by the gate;
        # one too long for its layout is refused naming the section, and the file is not written
        narrative = req.get("narrative") or dict(view.get("fallback_narrative") or {}, written_by="rules")
        try:
            meta = MatchWorkbook(view, theme, req.get("run"), narrative=narrative, req=req).write(path)
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
                          "citations": len(computed["citations"])})
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
    """The values a customer's PO–invoice match sheet can take: the counts (n1, s1–s3) and values by status (v1–v3),
    the GR/IR amounts (g1 g2), the supplier when the round is one supplier's, every invoice as a line, and the credit
    notes of the round when it has any."""
    t = computed["totals"]
    run = (req or {}).get("run") or {}
    invoices = computed["invoices"]
    out = {"company": computed.get("company") or "", "company_tax_id": computed.get("company_tax_id") or "",
           "period": computed["period_label"], "prepared_on": _day(run.get("date") or computed["prepared_on"]),
           "invoices": str(t["invoices"]), "passed": str(t["passed"]), "blocked": str(t["blocked"]), "person": str(t["person"]),
           "value_passed": _money(t["value_passed"]), "value_blocked": _money(t["value_blocked"]),
           "received_not_billed": _money(t["received_not_billed"]), "billed_not_received": _money(t["billed_not_received"]),
           "lines": [{"number": i.get("number") or "", "date": _day(i.get("date")), "supplier": (i.get("supplier") or {}).get("name") or "",
                      "supplier_tax_id": (i.get("supplier") or {}).get("tax_id") or "", "total": _money(i.get("total")),
                      "status": i.get("status_label") or "", "depth": i.get("depth_text") or ""} for i in invoices]}
    sellers = {((i.get("supplier") or {}).get("tax_id") or (i.get("supplier") or {}).get("name") or "") for i in invoices}
    if len(sellers) == 1 and invoices:
        out["supplier_name"] = (invoices[0].get("supplier") or {}).get("name") or ""
        out["supplier_tax_id"] = (invoices[0].get("supplier") or {}).get("tax_id") or ""
    if computed.get("notes"):
        out["credit_notes"] = [{"number": n.get("number") or "", "date": _day(n.get("date")), "supplier": n.get("supplier") or "",
                                "total": _money(n.get("total"))} for n in computed["notes"]]
    return out


def template_gaps(computed: dict) -> dict:
    """What the form's face says it lacks — and what this round has no concept of: a credit-note section when the
    round had no credit note is a scenario that did not occur, and is taken off the face."""
    from ...deliverables import form_words

    na = {} if computed.get("notes") else {"credit_notes": W("form.na.credit_notes")}
    return form_words.gaps(not_applicable=na)


def template_wanted(computed: dict) -> bool:
    return bool(computed.get("invoices"))


def template_why_not(computed: dict) -> str:
    return W("form.not_made")
