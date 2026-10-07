"""Agent 6 — procurement: a comparison of what can be bought, and a purchase request drafted from the choice.

A person says what they need to buy. The platform's web research (the
runner: it searches, opens the pages a model picked and stores each as a
snapshot) hands this package two things: what a model understood of the
request (the brief), and — per page captured — what a model says it read
there (a reading), with the snapshot itself. This package never sees a URL
to open and never sees the live web.

    brief    → checks/need.py      what was asked, and the questions a search cannot start without
    readings → scripts/compare.py  every claim found again in its snapshot, or it is not in the table
    choice   → scripts/request.py  the purchase request, with everything it could not fill in
    files    → scripts/workbook.py the comparison (.xlsx) · scripts/draft.py the request (.docx)

The model proposes; this code disposes. Nothing here knows what kind of thing
is being bought: there is no word of a product, a shop or a category in this
package's code, its prompts or its sample data's handling — a requirement is
the person's own words, and a value is a quote from a page.

The package's Python surface, called by agent_market.engine.step:
parse · preflight · compute · postcompute · summarize · render · template_values · template_gaps.
"""

from __future__ import annotations

import datetime as _dt
import os

from .checks import need as _need
from .scripts import compare as _compare
from .scripts import request as _request
from .scripts import wording as _wording
from .words import MANIFEST, lang_of, t

VERSION = MANIFEST.get("version")
PICK = "proc_pick"


def parse(path: str, name: str, mapping: dict):
    """This agent reads no file as data: its input is what the person says. (The organisation's own form is
    not data either — the platform keeps it apart and hands it to render.)"""
    raise ValueError("this agent takes a request in words, not a file")


def _vat_rate(defaults: dict | None) -> str:
    return str((defaults or {}).get("proc_vat_rate") or "7.00")


def preflight(files, *, message: str = "", defaults: dict | None = None, answers: dict | None = None,
              today: _dt.date | None = None, proposals: dict | None = None, lang: str | None = None) -> dict:
    lang = lang_of(lang)
    today = today or _dt.date.today()
    settled = _need.settle((proposals or {}).get("brief"), defaults=defaults, answers=answers, lang=lang)
    return {"roles": {}, "period": f"{today.year:04d}-{today.month:02d}", "period_source": "today", "today": today.isoformat(),
            "findings": [f.to_dict() for f in settled["findings"]], "assumptions": settled["assumptions"],
            "stats": {"items": len(settled["need"]["items"])}, "files": [],
            # what the platform's research works from, once nothing is left to ask
            "web": {"ready": settled["ready"], "understood": bool(settled["need"]["items"]), "need": settled["need"]}}


def compute(files, roles, period, *, answers=None, defaults=None, today: _dt.date | None = None, proposals=None,
            in_dir: str | None = None, out_dir: str | None = None, lang: str | None = None) -> dict:
    from ...engine.e4 import snapshot as _snap

    research = proposals or {}
    lang = lang_of(lang or research.get("lang"))
    answers, defaults = answers or {}, defaults or {}
    settled = _need.settle(research.get("brief"), defaults=defaults, answers=answers, lang=lang)
    need = settled["need"]
    snaps = {}
    for s in research.get("sources") or []:
        if s.get("outcome") == "captured" and in_dir:
            snaps[str(s["id"])] = _snap.load(in_dir, s)
    rate = _vat_rate(defaults)
    items = _compare.build(need, research, snaps, vat_rate=rate, suppliers=research.get("suppliers"), out_dir=out_dir,
                           jurisdiction=str(MANIFEST.get("jurisdiction") or "TH"))
    picks = {}
    sample = defaults.get("proc_sample_pick") == "first"
    for it in items:
        given = answers.get(f"{PICK}:{it['key']}")
        if isinstance(given, str) and (given == "none" or any(c["key"] == given for c in it["candidates"])):
            picks[it["key"]] = given
        elif sample and it["candidates"]:
            # the bundled sample has nobody to choose: its first row, said on the documents
            picks[it["key"]] = it["candidates"][0]["key"]
    assumptions = list(settled["assumptions"])
    if sample and picks:
        assumptions.append({"code": "proc_sample", "source": "rule", "text": t(lang, "sample.pick")})
    used = {str(c["source"]) for it in items for c in it["candidates"]}
    sources = []
    for s in research.get("sources") or []:
        cap = s.get("capture") or {}
        sources.append({"id": s.get("id"), "item": s.get("item"), "url": s.get("url") or "", "host": s.get("host") or "",
                        "outcome": s.get("outcome") or "failed", "reason": s.get("reason") or "", "detail": s.get("detail") or {},
                        "fetched_at": cap.get("finished_at") or "", "snapshot_sha256": cap.get("snapshot_sha256") or "",
                        "body_sha256": cap.get("body_sha256") or "", "final_url": cap.get("final_url") or "",
                        "status": (cap.get("http") or {}).get("status"), "bytes": (cap.get("bytes") or {}).get("transfer"),
                        "rule": (cap.get("robots") or {}).get("rule") or "", "fetcher": cap.get("fetcher_version") or "",
                        "read_by": s.get("read_by") or "", "used": str(s.get("id")) in used})
    today = today or _dt.date.today()
    request = _request.build(need, items, picks, vat_rate=rate)
    tot = (request or {}).get("totals") or {}
    return {"kind": "procurement", "schema": 1, "lang": lang, "period": period, "today": today.isoformat(), "vat_rate": rate,
            "need": need, "items": items, "picks": picks, "request": request,
            # the round in a few counts and amounts: what a self-test's expectations are read from
            "totals": {"offers": sum(len(it["candidates"]) for it in items), "dropped": sum(len(it["dropped"]) for it in items),
                       "chosen": len((request or {}).get("lines") or []), "gaps": len((request or {}).get("gaps") or []),
                       "subtotal": tot.get("subtotal"), "net": tot.get("net"), "vat": tot.get("vat"), "total": tot.get("total")},
            "round": {"caps": research.get("caps") or {}, "used": research.get("used") or {}, "cap_hit": research.get("cap_hit") or [],
                      "search": research.get("search") or "ok", "searches": research.get("searches") or []},
            "sources": sources, "assumptions": assumptions,
            # the pictures cut from the pages' screenshots: the runner keeps them with the run and hands them
            # to the step that makes the documents
            "evidence_files": sorted({c["picture"]["name"] for it in items for c in it["candidates"] if c.get("picture")})}


def view(lang: str, it: dict) -> dict:
    """One item's candidates as the choice card carries them: what was found, as data, and — for what
    the screen has no words of its own for (why a point is open, why a source was not used, what the page
    says of tax) — this package's words, in the round's language. The screen has the headings."""
    def said(ev):
        return {"quote": ev["quote"], "block": ev["block"]} if ev else None

    rows = []
    for c in it["candidates"]:
        price = c.get("price") or {}
        rows.append({
            "no": c["no"], "key": c["key"], "host": c["host"], "url": c["url"], "kind": c["kind"], "observed_at": c["observed_at"],
            "name": c["name"]["quote"], "model": said(c.get("model")), "variant": said(c.get("variant")), "seller": said(c.get("seller")),
            "price": {"display": price.get("display"), "amount": price.get("amount"), "currency": c.get("currency"),
                      "block": price.get("block"), "beside": price.get("beside") or []} if price else None,
            "price_basis": said(c.get("price_basis")), "subtotal": c.get("subtotal"), "net": c.get("net"), "vat_part": c.get("vat_part"),
            "total": c.get("total"), "vat": {"says": c["vat"]["says"], "quote": c["vat"]["quote"]} if c.get("vat") else None,
            "shipping": said(c.get("shipping")), "lead_time": said(c.get("lead_time")), "minimum_order": said(c.get("minimum_order")),
            "stock": said(c.get("stock")), "warranty": said(c.get("warranty")), "tax_id": said(c.get("tax_id")),
            "legal_name": said(c.get("legal_name")), "on_list": c.get("on_list"),
            "requirements": [{"need": r["need"], "verdict": r["verdict"], "quote": (r.get("said") or {}).get("quote")} for r in c["requirements"]],
            "open": c["open"], "not_found": c.get("not_found") or [], "picture": c.get("picture"), "source": c["source"],
            "words": {
                "kind": t(lang, f"kind.{c['kind']}"), "vat": t(lang, f"vat.{(c.get('vat') or {}).get('says') or 'unknown'}"),
                "on_list": _wording.on_list(lang, c.get("on_list")), "requirements": _wording.requirements(lang, c["requirements"]),
                "open": [{"code": p, "what": t(lang, f"open.{p}"), "why": _wording.open_why(lang, c, p)} for p in c["open"]],
                "not_found": [{"field": t(lang, f"field.{x['field']}"), "why": t(lang, f"notfound.{x['why']}", digits=str(x.get("digits") or ""))}
                              for x in c.get("not_found") or []]}})
    return {"item": {"key": it["key"], "name": it["name"], "meant": it.get("meant") or "", "quantity": it.get("quantity"),
                     "quantity_assumed": it.get("quantity_assumed"), "unit": it.get("unit") or ""},
            "candidates": rows,
            "dropped": [{"host": d.get("host") or "", "url": d.get("url") or "", "why": d.get("why") or "", "detail": d.get("detail") or {},
                         "words": _wording.dropped(lang, d)} for d in it["dropped"]],
            "sorted_by": "fewest_open", "unknown": t(lang, "unknown")}


def postcompute(files, roles, period, computed, *, answers=None, defaults=None, lang: str | None = None) -> dict:
    """The one thing asked after the search: which candidate the request is drafted from — or none of them."""
    from ...engine.findings import finding

    lang = lang_of(lang or computed.get("lang"))
    findings = []
    picks = computed.get("picks") or {}
    for it in computed["items"]:
        if it["key"] in picks or not it["candidates"]:
            continue
        # under each option, where it is from and the price its page shows — a sentence, with the page's own
        # words quoted (a bare address and a figure are not words written for a person, and are not shown)
        options = [(t(lang, "pick.option", n=str(c["no"])), c["key"],
                    t(lang, "pick.option.note.priced", source=c["host"], price=c["price"]["display"]) if (c.get("price") or {}).get("display")
                    else t(lang, "pick.option.note.unpriced", source=c["host"])) for c in it["candidates"]]
        options.append((t(lang, "pick.option.none"), "none", t(lang, "pick.option.none.note")))
        rnd = computed.get("round") or {}
        findings.append(finding(PICK, "blocking", "postcompute", t(lang, "pick.header"),
                                t(lang, "pick.ask", item=it["name"], n=str(len(it["candidates"]))), options, "none", subject=it["key"],
                                kind="pick", evidence={"comparison": {**view(lang, it), "round": {
                                    "cap_hit": rnd.get("cap_hit") or [], "search": rnd.get("search") or "ok",
                                    "used": rnd.get("used") or {}, "caps": rnd.get("caps") or {},
                                    "lines": _wording.round_lines(lang, rnd)}}}))
    return {"findings": [f.to_dict() for f in findings]}


def summarize(computed: dict) -> dict:
    req = computed.get("request") or {}
    offers = sum(len(it["candidates"]) for it in computed["items"])
    out = {"kind": "procurement", "items": len(computed["items"]), "offers": offers,
           "dropped": sum(len(it["dropped"]) for it in computed["items"]), "sources": len(computed.get("sources") or []),
           "chosen": len(req.get("lines") or []), "gaps": len(req.get("gaps") or []),
           "open_points": sum(len(c["open"]) for it in computed["items"] for c in it["candidates"]),
           "cap_hit": len((computed.get("round") or {}).get("cap_hit") or [])}
    tot = req.get("totals") or {}
    if tot.get("currency") == "THB" and (tot.get("total") or tot.get("subtotal")):
        out["total_thb"] = tot.get("total") or tot.get("subtotal")
    return out


# AM13 — what a person asked of the comparison (agent.json "instructions"): the options from the lowest price.
# An order, and nothing that weighs one option against another: the table still marks none as best, every
# option keeps its number and its row of what is not known about it, and the face of the workbook says the
# order is by price only. Prices in two currencies are not compared: the table then stays as it was and the
# directive is refused with the reason.
DIRECTIVES = ("sort_by_price",)


def _priced(c: dict) -> bool:
    return bool(c.get("price")) and bool(c.get("currency"))


def _as_asked(computed: dict, req: dict) -> tuple[dict, list[str], list[str]]:
    """The computed table as the comparison is to show it, the directives applied, and those this
    round's data could not take."""
    from ...deliverables import asked

    if asked.one_order(req, DIRECTIVES) is None:
        return computed, [], []
    items = computed.get("items") or []
    for it in items:
        if len({c["currency"] for c in it["candidates"] if _priced(c)}) > 1:
            return computed, [], ["sort_by_price"]
    every = [c for it in items for c in it["candidates"] if _priced(c)]
    # like with like: the total with tax when every priced option states its tax; otherwise the price as
    # the page shows it, and the face says which
    basis = "total" if every and all(c.get("total") for c in every) else "shown"

    def figure(c):
        if not _priced(c):
            return None
        return asked.number(c.get("total")) if basis == "total" else asked.number(c["price"].get("amount"))

    out = [dict(it, candidates=asked.smallest_first(it["candidates"], figure)) for it in items]
    return dict(computed, items=out, shown_order={"by": "price", "basis": basis}), ["sort_by_price"], []


def _asked_check(path: str, view: dict) -> dict:
    """What was asked, read back out of the finished workbook: each item's options run from the lowest
    price by the numbers in its own cells; every option still shows how many points are not known about
    it, and each of those points is on the sheet of what to ask; the face says how the table is ordered."""
    from openpyxl import load_workbook

    from ...deliverables import asked
    from .scripts.workbook import MAIN, PRICE_ORDER, TOP

    lang = view.get("lang") or "th"
    w = lambda key, **kw: t(lang, key, **kw)  # noqa: E731
    wb = load_workbook(path)
    basis = view["shown_order"]["basis"]
    col = {k: i for i, k in enumerate(MAIN)}
    listed = lambda name: [r for r in wb[name].iter_rows(min_row=TOP + 1, values_only=True) if r and r[0] not in (None, "")] \
        if name in wb.sheetnames else []  # noqa: E731
    rows = listed(w("sheet.candidates"))
    want = [(it, c) for it in view["items"] for c in it["candidates"]]
    bad = []
    if len(rows) != len(want):
        bad.append(w("check.asked.rows", file=str(len(rows)), computed=str(len(want))))
    at = 0
    cell = col["total"] if basis == "total" else col["unit_price"]
    for it in view["items"]:
        mine = rows[at: at + len(it["candidates"])]
        at += len(it["candidates"])
        figures = [asked.number(r[cell]) if isinstance(r[cell], (int, float)) and not isinstance(r[cell], bool) else None for r in mine]
        if not asked.runs_up(figures):
            bad.append(w("check.asked.order", item=it["name"]))
    for (it, c), r in zip(want, rows):
        if r[col["open"]] != len(c["open"]):
            bad.append(w("check.asked.open", no=str(r[col["no"]])))
    points = sum(len(c["open"]) + len(c.get("not_found") or []) for _, c in want)
    asks = listed(w("sheet.ask"))
    if len(asks) != points:
        bad.append(w("check.asked.points", file=str(len(asks)), computed=str(points)))
    face = {c for row in wb[w("sheet.summary")].iter_rows(values_only=True) for c in row if isinstance(c, str)}
    if w(PRICE_ORDER[basis]) not in face or w("face.not_advice") not in face:
        bad.append(w("check.asked.face"))
    out = asked.check(not bad, "; ".join(bad[:4]) if bad else w("check.asked.ok", n=str(len(rows))), len(rows))
    out["label"] = w("check.asked.label")
    return out


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    import hashlib

    from ...deliverables.formats import wanted_formats
    from ...deliverables.theme import resolve
    from .scripts.draft import RequestDraft
    from .scripts.draft import check as check_draft
    from .scripts.workbook import ComparisonWorkbook
    from .scripts.workbook import check as check_workbook

    view, applied, refused = _as_asked(computed, req)
    theme = resolve(req.get("theme"))
    names = req.get("names") or {}
    run = req.get("run") or {}
    wanted = wanted_formats(req, ["xlsx", "docx"])
    request = computed.get("request") or {}
    artifacts, all_ok = [], True

    def done(path: str, fmt: str, kind: str, checks: list, meta: dict) -> None:
        nonlocal all_ok
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        artifacts.append({"name": os.path.basename(path), "format": fmt, "kind": kind, "size": os.path.getsize(path), "sha256": sha,
                          "quality": {"passed": ok, "checks": checks}, "meta": meta, "chart_bindings": [], "citations": 0})

    if "xlsx" in wanted:
        path = os.path.join(out_dir, os.path.basename(names.get("xlsx") or "comparison.xlsx"))
        meta = ComparisonWorkbook(view, theme, run).write(path)
        checks = check_workbook(path, view, run)
        if applied:
            checks.append(_asked_check(path, view))
        done(path, "xlsx", "table", checks, meta)
    # the request as a plain document — unless the organisation's own form is being filled this round, or
    # nothing was chosen (then there is no request to draft, and the comparison says so)
    if "docx" in wanted and request.get("lines") and not req.get("template"):
        path = os.path.join(out_dir, os.path.basename(names.get("docx") or "purchase_request.docx"))
        meta = RequestDraft(computed, theme, run).write(path, in_dir if os.path.isdir(in_dir) else None)
        done(path, "docx", "request", check_draft(path, computed), meta)
    return {"passed": all_ok, "artifacts": artifacts, "theme": {"id": theme["id"], "version": theme.get("version", 1)},
            "asked": {"applied": applied, "refused": refused}}


def template_values(computed: dict, req: dict | None = None) -> dict:
    """What the organisation's own purchase-request form can take (the manifest's templates block). A value
    that is not known is not in the dictionary at all: its slot stays blank and is named as a gap."""
    lang = computed.get("lang") or "th"
    run = (req or {}).get("run") or {}
    req = computed.get("request") or {}
    need = computed.get("need") or {}
    tot = req.get("totals") or {}
    lines = req.get("lines") or []
    out = {"request_date": _wording.date(lang, computed.get("today")), "lines": [
        {"no": str(l["no"]), "description": l["description"], "quantity": l["quantity"], "unit": l["unit"],
         "unit_price": _wording.amount(l["unit_price"]) if l["unit_price"] else "",
         "amount": _wording.amount(l["amount"]) if l["amount"] else ""} for l in lines]}

    def put(key: str, value) -> None:
        if value not in (None, ""):
            out[key] = value

    put("requester", run.get("preparer"))
    put("company", (((req or {}).get("theme") or {}).get("company")) or run.get("organisation"))
    put("delivery_place", need.get("delivery_place"))
    put("needed_by", need.get("needed_by"))
    put("seller_as_shown", req.get("seller"))
    if len(lines) == 1:
        put("item_description", lines[0]["description"])
        put("quantity", lines[0]["quantity"])
        put("unit_price", _wording.amount(lines[0]["unit_price"]) if lines[0]["unit_price"] else None)
        put("source_page", lines[0]["url"])
    if lines and req.get("one_seller"):
        # one seller for every line: what its page prints of who it is (several sellers do not fit one blank)
        put("seller_tax_id", lines[0]["tax_id"])
        put("seller_legal_name", lines[0]["legal_name"])
    put("currency", tot.get("currency"))
    put("subtotal", _wording.amount(tot["subtotal"]) if tot.get("subtotal") else None)
    put("net_amount", _wording.amount(tot["net"]) if tot.get("net") else None)
    put("vat_amount", _wording.amount(tot["vat"]) if tot.get("vat") else None)
    put("total", _wording.amount(tot["total"]) if tot.get("total") else None)
    return out


def template_gaps(computed: dict) -> dict:
    """What is printed on the face of a filled form (the engine appends it): the heading, the lead, and
    every gap of the request with why — beside whatever the form's own blanks still lack."""
    lang = computed.get("lang") or "th"
    req = computed.get("request") or {}
    many = len(req.get("lines") or []) > 1
    rows = []
    for g in req.get("gaps") or []:
        what, why = _wording.gap(lang, g)
        rows.append([(t(lang, "gaps.line", n=str(g["line"])) + " · " if many and g.get("line") else "") + what, why])
    return {"title": t(lang, "gaps.title"), "lead": t(lang, "gaps.lead"), "columns": [t(lang, "gaps.col.what"), t(lang, "gaps.col.why")],
            "rows": rows, "form_blank": t(lang, "gaps.form_blank"), "form_why": {
                "no_value": t(lang, "gaps.why.no_value"), "not_mapped": t(lang, "gaps.why.not_mapped"),
                "wrong_label": t(lang, "gaps.why.wrong_label"), "not_in_file": t(lang, "gaps.why.not_in_file"),
                "rows_short": t(lang, "gaps.why.rows_short"), "other": t(lang, "gaps.why.other")},
            "sheet": t(lang, "gaps.sheet"),
            # the engine's check that all of this is on the file's face, in this package's words ({n}, {list}: its own)
            "check": {"label": t(lang, "check.gaps_printed.label"), "ok": t(lang, "check.gaps_printed.ok", n="{n}"),
                      "missing": t(lang, "check.gaps_printed.missing", list="{list}")}}


def template_wanted(computed: dict) -> bool:
    """The organisation's form is filled only when something was chosen: with no choice there is no request,
    and a form with nothing but its gaps is not a draft of anything."""
    return bool((computed.get("request") or {}).get("lines"))
