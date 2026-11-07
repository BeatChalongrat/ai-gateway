"""Agent 1 — Invoice Processing.

Thai purchase documents in (digital PDF, e-Tax PDF/XML, scans; photos as triage), a register out:
every value with its provenance, every document either recorded or left out with a reason code.
Reading is the E2 engine (engine/e2); a model may propose a value for a scan or photo, a person
decides — no model finalises a financial number.

The package's Python surface, called by agent_market.engine.step:
parse · dump · load · parse_extras · preflight · compute · postcompute · summarize · render.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from ...engine import words as _words   # (this package has a module of its own named words)
from ...engine.e2.extract import extract as _extract
from .checks import preflight as _pre
from .checks.review import SCAN_TIERS, Doc
from .scripts import register as _register
from .words import W

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
FORMAT = "am.e2doc.v1"
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


def parse(path: str, name: str, mapping: dict) -> Doc:
    """Read one upload once (the expensive part); later steps judge the stored reading."""
    out = _extract(path, name)
    sha = out["document"]["sha256"]
    return Doc(key="d" + sha[:10], name=name, sha256=sha, extraction=out)


def dump(doc: Doc) -> dict:
    return {"format": FORMAT, "key": doc.key, "name": doc.name, "sha256": doc.sha256, "extraction": doc.extraction}


def load(d: dict) -> Doc:
    if d.get("format") != FORMAT:
        raise ValueError(f"unknown parsed format {d.get('format')!r}")
    return Doc(key=d["key"], name=d["name"], sha256=d["sha256"], extraction=d["extraction"])


def parse_extras(doc: Doc, path: str, out_dir: str) -> dict:
    """Page images of a scan or photo for the runner's vision step (a second reader, never the source
    of a number). Digital and e-Tax documents get none: their text is exact."""
    if doc.tier not in SCAN_TIERS:
        return {"vision_pages": []}
    from PIL import Image, ImageOps

    images = []
    if path.lower().endswith(".pdf"):
        import extract_text  # /sandbox/scripts

        for i in range(min(doc.extraction["document"]["pages"], VISION_MAX_PAGES)):
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


def preflight(files: list[Doc], *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
              today: _dt.date | None = None, history: list | None = None, proposals: dict | None = None) -> dict:
    return _pre.run(files, message=message, defaults=defaults, answers=answers, today=today, history=history,
                    proposals=proposals)


def compute(files, roles, period, *, answers=None, defaults=None, today=None, history=None, proposals=None) -> dict:
    def table():
        return _register.compute(files, roles, period, answers=answers, defaults=defaults, today=today, history=history,
                                 proposals=proposals)

    return table()


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    """Everything was asked before compute; nothing is asked after it."""
    return {"findings": []}


def summarize(computed: dict) -> dict:
    return _register.summarize(computed)


def template_values(computed: dict) -> dict:
    """The values a customer's template can take (the manifest's templates block): money as printed text."""
    from ...engine.thai import fmt_money

    t = computed["totals"]
    thb = t["thb"]

    def m(v):
        return fmt_money(v) if v not in (None, "") else ""
    rows = [d for d in computed["documents"] if d["status"] == "included"]
    return {"company": computed.get("company") or "", "company_tax_id": computed.get("company_tax_id") or "",
            "period": computed["period_label"], "prepared_on": computed["prepared_on"], "documents": str(t["documents"]),
            "included": str(t["included"]), "subtotal_thb": m(thb["subtotal"]), "vat_thb": m(thb["vat_amount"]),
            "total_thb": m(thb["total"]), "wht_thb": m(thb["wht_amount"]),
            "net_thb": m(thb.get("payable") or thb["net_payable"]),
            "lines": [{"date": d["document_date"] or "", "number": d["document_number"] or "", "seller": d["seller_name"] or "",
                       "seller_tax_id": d["seller_tax_id"] or "",
                       "description": " · ".join(x for x in (d["seller_name"], d["document_number"]) if x),
                       "quantity": "1", "subtotal": m(d["thb_subtotal"]), "vat": m(d["thb_vat_amount"]),
                       "total": m(d["thb_total"]), "amount": m(d["thb_total"])} for d in rows]}


# AM13 — what a person asked of the register (agent.json "instructions"). An order for its rows, applied here
# at render time and nowhere else: the register and its evidence sheet are made from the one list of recorded
# documents, so they stay row for row. Every value of every row is the computed one, and the totals do not move.
# The register stays one table: a sheet per seller would need a subtotal for each, which is arithmetic.
DIRECTIVES = ("sort_by_seller", "sort_by_date", "sort_by_amount")


def _as_asked(computed: dict, req: dict) -> tuple[dict, list[str]]:
    """The computed table as the register is to show it, and the directives applied."""
    from ...deliverables import asked

    order = asked.one_order(req, DIRECTIVES)
    if order is None:
        return computed, []
    docs = computed["documents"]
    if order == "sort_by_seller":
        docs = asked.by_name(docs, lambda d: d.get("seller_name"))
    elif order == "sort_by_date":
        docs = sorted(docs, key=lambda d: (0, str(d["document_date"])) if d.get("document_date") else (1, ""))
    else:
        docs = asked.largest_first(docs, lambda d: asked.number(d.get("thb_total")))
    return dict(computed, documents=docs), [order]


def _asked_check(path: str, view: dict, applied: list[str]) -> dict:
    """What was asked, read back out of the finished workbook: the sellers, the dates or the totals of
    the register from top to bottom, by what is in its own cells."""
    from openpyxl import load_workbook

    from ...deliverables import asked
    from ...deliverables.reread import table
    from .scripts.workbook import H_REGISTER, S_REGISTER, parts

    rows = table(parts(load_workbook(path), S_REGISTER), H_REGISTER)
    same = len(rows) == len([d for d in view["documents"] if d["status"] == "included"])
    col = {k: i for i, k in enumerate(H_REGISTER.keys)}   # by key: the heading is said in the run's language
    if applied == ["sort_by_seller"]:
        ok, what, said = asked.names_run_up([r[col["doc.col.seller"]] for r in rows]), W("asked.what.seller"), W("asked.said.seller")
    elif applied == ["sort_by_date"]:
        ok = asked.runs_up([str(r[col["doc.col.date"]]) if r[col["doc.col.date"]] not in (None, "") else None for r in rows])
        what, said = W("asked.what.date"), W("asked.said.date")
    else:
        c = col["doc.col.total_thb"]
        ok = asked.runs_down([asked.number(r[c]) if isinstance(r[c], (int, float)) and not isinstance(r[c], bool) else None for r in rows])
        what, said = W("asked.what.amount"), W("asked.said.amount")
    if not same or not ok:
        return asked.check(False, W("asked.not", what=what, sheet=W(S_REGISTER)))
    return asked.check(True, W("asked.ok", n=len(rows), said=said), len(rows))


def plan(computed: dict, req: dict) -> list:
    """AM23 §1.5: before any file is written, the documents asked for that this run's content cannot be — each with
    why and what can be made instead (deliverables/document.py plan)."""
    from ...deliverables import document as _document
    from ...deliverables.formats import wanted_formats
    from .scripts.document import spec as _doc_spec

    view, applied = _as_asked(computed, req)
    spec = _doc_spec(view, applied)
    return [p for p in (_document.plan(f, view, req, spec) for f in wanted_formats(req, ["xlsx"])) if p]


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables import quality
    from ...deliverables.formats import wanted_formats
    from ...deliverables import document as _document
    from .scripts.document import spec as _doc_spec
    from ...deliverables.look import commentary
    from ...deliverables.look.catalogue import DoesNotFit
    from ...deliverables.theme import resolve
    from .scripts.workbook import InvoiceWorkbook, check

    # AM18: the register is written in the run's language, like everything the run says
    view, applied = _as_asked(computed, req)
    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    artifacts, refused, all_ok = [], [], True
    for fmt in wanted_formats(req, ["xlsx"]):
        if fmt != "xlsx":
            # AM23 §1.2: a report or a deck a person asks for is a document of its own, written from the same
            # computed table through the same renderer (deliverables/document.py) — never the workbook's sheets laid
            # into Word; the workbook stays the authority. (Until AM23 this loop dropped every format but the workbook.)
            all_ok &= _document.into(fmt, view, req, _doc_spec(view, applied), names, "invoice_processing", out_dir, artifacts, refused,
                                     in_dir=in_dir)
            continue
        name = os.path.basename(names.get(fmt) or "register.xlsx")
        path = os.path.join(out_dir, name)
        # AM22 §1.1: the commentary — a model's, or the rules' saying so — on its own sheet, read back by the gate;
        # one too long for its layout is refused naming the section, and the file is not written
        narrative = req.get("narrative") or dict(view.get("fallback_narrative") or {}, written_by="rules")
        try:
            meta = InvoiceWorkbook(view, theme, req.get("run"), narrative=narrative, req=req).write(path)
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
                          "citations": len(computed["provenance"])})
    return {"passed": all_ok, "artifacts": artifacts, "refused": refused,
            "theme": {"id": theme["id"], "version": theme.get("version", 1)}, "asked": {"applied": applied, "refused": []}}
