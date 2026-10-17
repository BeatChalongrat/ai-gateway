"""A document of a working paper's own, for the agents whose job is the workbook (AM23 §1.2).

A package says what its content *is* — who and what period, the figures that matter, what the run found, the one
table a reader needs, its commentary and its closing — and this module writes a Word report and a PowerPoint deck from
that, through the writers the financial report uses (report_docx.ReportWriter, deck_pptx.DeckWriter) and the layout
catalogue (look/catalogue.py: it chooses, steps down the scale, breaks across pages or slides, or refuses naming the
section). A package sets no font, size, colour, coordinate or box (test_am21_contract).

A document is not a conversion of the workbook: nothing here reads a sheet. It is written from the run's computed
table:

  * every figure from the run's one ``figures`` table, printed as it says, with its own citation (AM22) — the same
    strings the workbook's face and commentary print (AM23 Part 2 compares them on the finished files);
  * what the run found from the package's own facts (``narrative_facts``, the code's sentences, the first heading);
  * the commentary (a model's, or the rules' — the rules' sentence under it, AM23 §3.2);
  * where a document shows a table it shows the part a reader needs and says the workbook has the rest: the
    workbook stays the authority;
  * the closing block: the files the run read with their SHA-256, the assumptions, the references printed, and the
    one neutral line about how the commentary was written (AM23 §3.2) — never on the cover.

A package's ``document_spec(view)`` returns:

    title, subtitle          the cover's two lines (the catalogue's title limits hold them)
    cover_lines              at most two lines under them (an account, a source)
    tiles                    two to three figure keys for the cover and the deck's figures slide
    headline                 the figure key of the deck's own figure slide
    table                    {"title", "columns", "rows" (every row, in the reader's order), "numeric_from",
                              "deck_columns" (indexes a slide holds), "sheet" (where the workbook has every row),
                              "order" (how the rows are ordered), "amounts" (each row's amount, for an alternative)}
    statement                optional: one more small table (the bank's reconciliation statement)
"""

from __future__ import annotations

import os
from decimal import Decimal

from ..engine.words import SHARED as S
from .look import catalogue, commentary

FORMATS = {"docx": "report", "pptx": "deck"}
DOCX_ROWS = 20   # the rows of the items table a report shows; the workbook has every one


def _printed(text: str, view: dict) -> tuple[str, list[str]]:
    return commentary.printed(text, view.get("figures") or {})


def _sources(view: dict) -> list[list[str]]:
    """Every file the run read, with its role and SHA-256 (the run's own record of its inputs)."""
    rows, seen = [], set()
    src = view.get("source") or {}
    for role, files in (src.items() if isinstance(src, dict) else []):
        for f in files if isinstance(files, list) else []:
            name, sha = str(f.get("name") or ""), str(f.get("sha256") or "")
            if name and (name, sha) not in seen:
                seen.add((name, sha))
                rows.append([name, str(role), sha])
    return rows


def deck_capacity() -> int:
    """The most rows a deck's table holds: a slide's rows times the slides the catalogue lets a table break across."""
    from .look.tokens import LOOK

    takes = LOOK.layout("pptx", "table")["takes"]
    return int(takes["rows"]) * int(takes.get("parts_max", 1))


_ROUND = [Decimal(m) * (Decimal(10) ** k) for k in range(0, 10) for m in (1, 2, 5)]


def threshold_for(amounts: list, cap: int) -> Decimal | None:
    """The smallest round amount (1, 2 or 5 times a power of ten) whose rows at or above it fit ``cap``, with at least
    one row — or, when no round amount does, the amount of the cap-th largest row: the alternative a refusal offers is
    computed from the run's own amounts, never invented."""
    vals = sorted((a for a in (amount(x) for x in amounts) if a is not None), reverse=True)
    for t in _ROUND:
        n = sum(1 for v in vals if v >= t)
        if 0 < n <= cap:
            return t
    # no round amount leaves between one row and a deck's worth (amounts spread evenly: 500 leaves too many, 1,000
    # none): the amount of the cap-th largest row itself, raised past a tie with the rows below it
    for i in range(min(cap, len(vals)) - 1, -1, -1):
        n = sum(1 for v in vals if v >= vals[i])
        if 0 < n <= cap:
            return vals[i]
    return None


def plan(fmt: str, view: dict, req: dict, spec: dict) -> dict | None:
    """Whether the run's content can be the document asked for (AM23 §1.5), before any file is written: None when it
    can; else what cannot be made and why, in the run's language, with what can be made instead — one question with
    options a person picks in one tap ({"formats", "deck_over"} each), the first of them the workbook alone (what the
    round makes anyway, never a new file). A deck asked for with more rows than a deck holds offers a deck of the
    rows at or above a round amount the run's own amounts give; a document asked for from a run with nothing to say
    offers the workbook."""
    from ..engine.thai import fmt_money

    if fmt not in FORMATS:
        return None
    doc = S(f"doc.plan.doc.{fmt}")
    narrative = req.get("narrative") or view.get("fallback_narrative") or {}
    said = any(narrative.get(k) for k in commentary.LISTS)
    if spec.get("nothing_to_say") and not said:
        # everything matched, no exception, and the commentary has no point to make (the card's three): a document
        # would be a heading and white space
        return {"format": fmt, "code": "nothing_to_say", "header": S("doc.plan.header"), "question": S("doc.plan.nothing", doc=doc),
                "options": [{"label": S("doc.plan.opt.workbook_only", doc=doc), "value": {"formats": ["xlsx"]}}]}
    t = spec.get("table") or {}
    rows = t.get("rows") or []
    cap = deck_capacity()
    if fmt != "pptx" or len(rows) <= cap or req.get("deck_over") not in (None, ""):
        return None
    unit = S(f"doc.unit.{t.get('unit') or 'items'}")
    first = {"label": S("doc.plan.opt.workbook", doc=doc), "value": {"formats": ["xlsx"]}}
    th = threshold_for(t.get("amounts") or [], cap)
    if th is None:
        return {"format": fmt, "code": "deck_long", "header": S("doc.plan.header"),
                "question": S("doc.plan.deck_long_none", unit=unit, n=len(rows), cap=cap),
                "options": [first, {"label": S("doc.plan.opt.report"), "value": {"formats": ["docx"]}}]}
    m = sum(1 for a in t.get("amounts") or [] if amount(a) is not None and amount(a) >= th)
    money = fmt_money(th, 0)
    return {"format": fmt, "code": "deck_long", "header": S("doc.plan.header"),
            "question": S("doc.plan.deck_long", unit=unit, n=len(rows), cap=cap, m=m, threshold=money),
            "options": [first, {"label": S("doc.plan.opt.deck_over", m=m, threshold=money), "value": {"formats": ["pptx"], "deck_over": str(th)}}]}


def over(spec: dict, threshold) -> dict:
    """The spec with its table held to the rows at or above ``threshold`` — the deck a person picked when every row
    would not go on slides — its title and caption saying so."""
    from ..engine.thai import fmt_money

    t = dict(spec.get("table") or {})
    th = amount(threshold)
    if not t or th is None:
        return spec
    keep = [i for i, a in enumerate(t.get("amounts") or []) if amount(a) is not None and amount(a) >= th]
    money = fmt_money(th, 0)
    t.update(rows=[t["rows"][i] for i in keep], amounts=[t["amounts"][i] for i in keep],
             title=S("doc.plan.over_title", title=t["title"], threshold=money),
             caption=S("doc.plan.over_caption", m=len(keep), n=len(spec["table"]["rows"]), threshold=money, sheet=t["sheet"]))
    return dict(spec, table=t)


def content(view: dict, req: dict, spec: dict) -> dict:
    """The one content model a report and a deck are written from."""
    figures = view.get("figures") or {}
    narrative = req.get("narrative") or dict(view.get("fallback_narrative") or {}, written_by="rules")
    run = req.get("run") or {}
    facts = (view.get("narrative_facts") or [{}])[0]
    found = [_printed(line, view) for line in facts.get("lines") or []]
    fig_rows = [[f["label"], f["text"], f["cite"]] for f in figures.values()]
    tiles = [{"key": k, "label": figures[k]["label"], "value": figures[k]["text"], "value_short": figures[k]["text"],
              "cite": figures[k]["cite"], "delta": None, "tone": "flat", "good": None} for k in spec.get("tiles") or [] if k in figures]
    lines = commentary.rows(narrative, view)
    cited = [c for _, _, cs in lines for c in cs] + [c for _, cs in found for c in cs] + [r[2] for r in fig_rows]
    return {
        "title": spec["title"], "subtitle": spec["subtitle"], "company": (req.get("theme") or {}).get("company") or view.get("company") or "",
        "prepared": S("docs.report.prepared", today=_date(run.get("date") or view.get("prepared_on") or "")),
        "cover_lines": list(spec.get("cover_lines") or [])[:2],
        "tiles": tiles, "headline": spec.get("headline") or (tiles[0]["key"] if tiles else None),
        "found": {"heading": facts.get("heading") or S("doc.d.found"), "lines": found},
        "figures": fig_rows, "table": spec.get("table"), "statement": spec.get("statement"),
        "narrative": narrative, "commentary": lines,
        "sources": _sources(view), "assumptions": [a["text"] for a in view.get("assumptions") or [] if a.get("text")],
        "references": list(dict.fromkeys(c for c in cited if c)),
        "closing_line": commentary.closing_line(narrative, req),
        "rules_line": commentary.rules_line(narrative) if narrative.get("written_by") == "rules" else None,
        "source_note": S("doc.d.source_note", script=view.get("script") or ""),
        "never": list(spec.get("never") or []),
    }


def _date(iso: str) -> str:
    from ..engine import words

    return words.date(iso) if iso else ""


# ── the report ──────────────────────────────────────────────────────────────


def _runs(text: str, cites: list[str]) -> list[tuple[str, str | None]]:
    return [(text, None)] + [("", c) for c in cites]


def report_model(doc: dict) -> dict:
    """The content model in the shape report_docx.ReportWriter writes."""
    sections = [{"key": "found", "title": doc["found"]["heading"], "blocks": [
        {"type": "bullets", "items": [_runs(t, cs) for t, cs in doc["found"]["lines"]]}] if doc["found"]["lines"] else []}]
    sections.append({"key": "figures", "title": S("doc.d.figures"), "blocks": [
        {"type": "table", "columns": [S("docs.col.figure"), S("docs.col.value"), S("docs.col.ref")], "rows": doc["figures"],
         "strong_rows": [], "numeric_from": 1, "cite_col": 2}]})
    if doc.get("statement"):
        st = doc["statement"]
        sections.append({"key": "statement", "title": st["title"], "blocks": [
            {"type": "table", "columns": st["columns"], "rows": st["rows"], "strong_rows": st.get("strong_rows") or [],
             "numeric_from": st.get("numeric_from"), "cite_col": None}]})
    t = doc.get("table")
    if t:
        shown = t["rows"][:DOCX_ROWS]
        order = S("doc.d.as_asked") if t.get("order") == "asked" else S("doc.d.by_amount")
        note = (S("doc.d.part", n=len(shown), total=len(t["rows"]), order=order, sheet=t["sheet"]) if len(t["rows"]) > len(shown)
                else S("doc.d.all_in_workbook", sheet=t["sheet"]))
        blocks = [{"type": "table", "columns": t["columns"], "rows": shown, "strong_rows": [], "numeric_from": t.get("numeric_from"),
                   "cite_col": None, "empty_text": t.get("empty") or S("docs.table.empty")}]
        if t["rows"]:
            blocks.append({"type": "paragraph", "runs": [(note, None)], "muted": True})
        sections.append({"key": "table", "title": t["title"], "blocks": blocks})
    blocks = []
    for part, text, cites in doc["commentary"]:
        if part == "summary":
            blocks.append({"type": "paragraph", "runs": _runs(text, cites), "narrative": True})
    for key in commentary.LISTS:
        items = [_runs(text, cites) for part, text, cites in doc["commentary"] if part == key]
        if items:
            blocks += [{"type": "subheading", "text": S(commentary.HEADING[key])}, {"type": "bullets", "items": items, "narrative": True}]
    if doc.get("rules_line"):
        blocks.append({"type": "paragraph", "runs": [(doc["rules_line"], None)], "muted": True})
    sections.append({"key": "commentary", "title": S("doc.sheet.commentary"), "blocks": blocks})
    closing = [{"type": "table", "columns": [S("docs.col.file"), S("docs.col.role"), "SHA-256"], "rows": doc["sources"],
                "strong_rows": [], "numeric_from": None, "cite_col": None, "small": True}]
    if doc["assumptions"]:
        closing += [{"type": "subheading", "text": S("docs.section.assumptions")},
                    {"type": "bullets", "items": [[(a, None)] for a in doc["assumptions"]]}]
    closing.append({"type": "paragraph", "runs": [(S("docs.refs", ids=", ".join(doc["references"])), None)], "muted": True})
    if doc.get("closing_line"):
        closing.append({"type": "paragraph", "runs": [(doc["closing_line"], None)], "muted": True})
    sections.append({"key": "closing", "title": S("doc.d.closing"), "blocks": closing})
    return {"title": doc["title"], "subtitle": doc["subtitle"], "company": doc["company"], "prepared": doc["prepared"],
            "cover_lines": doc["cover_lines"], "kpis": doc["tiles"], "charts": [], "sections": sections,
            "source_note": doc["source_note"], "cite_note": S("doc.d.cite_note")}


def write_docx(doc: dict, theme: dict, path: str, logo: str | None = None, organisation: str = "") -> dict:
    from .report_docx import ReportWriter

    return ReportWriter(report_model(doc), theme, {}).write(path, logo, organisation=organisation)


# ── the deck ────────────────────────────────────────────────────────────────


class _Deck:
    """The slides of a working paper's deck, on the layouts of deck_pptx.DeckWriter."""

    def __init__(self, doc: dict, theme: dict):
        from .deck_pptx import DeckWriter

        class Deck(DeckWriter):
            def __init__(self, d, t):
                # (DeckWriter reads the financial report's computed table for its footer: this deck has its own)
                super().__init__({"company": d["company"], "prepared": d["prepared"], "cover_lines": d["cover_lines"]},
                                 {"source": {"primary": {"file": d["sources"][0][0] if d["sources"] else ""}}}, t)

        self.d = doc
        self.w = Deck(doc, theme)

    def title(self, logo):
        w, d = self.w, self.d
        lines = [d["prepared"]] + d["cover_lines"] + ([d["company"]] if d["company"] else [])
        content = {"title": d["title"], "subtitle": d["subtitle"], "lines": lines}
        w._choose("title", content, d["title"])
        s = w._slide("title")
        w._text(w._ph(s, 0), d["title"])
        w._text(w._ph(s, 1), d["subtitle"])
        if d["company"]:
            w._text(w._ph(s, 10), d["company"])
        w._lines(w._ph(s, 11), lines)
        if logo and os.path.exists(logo):
            from .look import potx

            w._ph(s, potx.FIELDS["title"]["logo"]).insert_picture(logo)
        w._finish(s, "title", [], d["title"])

    def figure(self, tile: dict, sentence: str):
        w = self.w
        content = {"title": S("docs.deck.stats"), "label": tile["label"], "value": tile["value_short"], "sentence": sentence}
        w._choose("figure", content, S("docs.deck.stats"))
        s = w._slide("figure")
        w._text(w._ph(s, 0), content["title"])
        w._text(w._ph(s, 10), tile["label"])
        w._text(w._ph(s, 11), tile["value_short"])
        w._text(w._ph(s, 12), sentence)
        w._finish(s, "figure", [tile["cite"]], S("docs.deck.stats"))

    def figures(self, tiles: list[dict]):
        from .look import potx

        w = self.w
        refs = [S("doc.d.deck.ref", cite=k["cite"]) for k in tiles]
        content = {"title": S("docs.deck.stats"), "items": [k["label"] for k in tiles], "label": max((k["label"] for k in tiles), key=len),
                   "value": max((k["value_short"] for k in tiles), key=len), "delta": max(refs, key=len)}
        w._choose("figures", content, S("docs.deck.stats"))
        s = w._slide("figures")
        w._text(w._ph(s, 0), content["title"])
        f = potx.FIELDS["figures"]
        for i, k in enumerate(tiles):
            w._text(w._ph(s, f["label"][i]), k["label"])
            w._text(w._ph(s, f["value"][i]), k["value_short"])
            w._text(w._ph(s, f["delta"][i]), refs[i])
        w._finish(s, "figures", [k["cite"] for k in tiles], S("docs.deck.stats"))

    def write(self, path: str, organisation: str = "", logo: str | None = None) -> dict:
        from .look import potx
        from .look.tokens import LOOK

        w, d = self.w, self.d
        self.title(logo)
        tiles = d["tiles"]
        head = next((t for t in tiles if t["key"] == d["headline"]), tiles[0] if tiles else None)
        if head and d["found"]["lines"]:
            self.figure(head, d["found"]["lines"][0][0])
        if 2 <= len(tiles):
            self.figures(tiles[:LOOK.layout("pptx", "figures")["takes"]["items"]])
        found = d["found"]["lines"]
        if found:
            w.bullets(d["found"]["heading"], [t for t, _ in found], [c for _, cs in found for c in cs])
        t = d.get("table")
        if t and t["rows"]:
            cols = t.get("deck_columns") or list(range(len(t["columns"])))
            heads = [t["columns"][j] for j in cols]
            rows = [[r[j] for j in cols] for r in t["rows"]]
            nf = t.get("numeric_from")
            num_from = next((i for i, j in enumerate(cols) if nf is not None and j >= nf), len(cols))
            w.table(t["title"], heads, rows, [], numeric_from=num_from, caption=t.get("caption") or S("doc.d.all_in_workbook", sheet=t["sheet"]))
        for part, text, cites in d["commentary"]:
            if part == "summary":
                w.statement(S("doc.commentary.summary"), text, cites)
        for key in commentary.LISTS:
            items = [(text, cites) for part, text, cites in d["commentary"] if part == key]
            if items:
                w.bullets(S(commentary.HEADING[key]), [x for x, _ in items], [c for _, cs in items for c in cs])
        closing = [S("doc.d.deck.source", file=f, role=role, sha=sha[:16] + "…") for f, role, sha in d["sources"]]
        closing += [d["source_note"]] + d["assumptions"]
        if d.get("rules_line"):
            closing.append(d["rules_line"])
        if d.get("closing_line"):
            closing.append(d["closing_line"])
        closing.append(S("docs.refs", ids=", ".join(d["references"])))
        w.closing(closing)
        props = w.prs.core_properties
        props.title = f"{d['title']} {d['subtitle']}".strip()
        props.author = d["company"] or organisation or ""
        w.prs.save(path)
        embedded = potx.embed_fonts(path, LOOK.family)
        return {"path": path, "slides": len(w.prs.slides), "chart_bindings": [], "slide_cites": w.cites, "layouts": w.layouts,
                "embedded_fonts": embedded}


def write_pptx(doc: dict, theme: dict, path: str, logo: str | None = None, organisation: str = "") -> dict:
    return _Deck(doc, theme).write(path, organisation=organisation, logo=logo)


# ── reading the finished file back ──────────────────────────────────────────


def check(path: str, fmt: str, doc: dict, req: dict, view: dict) -> list[dict]:
    """The finished report or deck, opened again: every figure prints as the run's figures table says, next to its
    own reference; what the run found and the commentary are there as printed; the table's rows are there; the
    closing block carries the files' hashes, the rules' sentence exactly when they composed the words and the neutral
    line exactly when it is on; no model's name anywhere."""
    texts = commentary.file_texts(path)
    joined = "\n".join(texts)
    squashed = "".join(joined.split())

    def has(s: str) -> bool:
        return "".join(str(s).split()) in squashed

    out = []
    # the report prints every figure in its table; a deck the figures of its own slides (one idea per slide)
    shown = doc["figures"] if fmt == "docx" else [[t["label"], t["value"], t["cite"]] for t in doc["tiles"]]
    missing = [f"{label} {text}" for label, text, cite in shown if not (has(text) and has(label) and has(cite))]
    out.append({"name": f"{fmt}.figures", "ok": not missing, "label": S("check.d.figures.label"),
                "detail": S("check.d.figures.missing", text=missing[0][:80]) if missing else S("check.d.figures.ok", n=len(shown)),
                "values": len(shown)})
    said = [t for _, t, _ in doc["commentary"]] + [t for t, _ in doc["found"]["lines"]]
    lost = [t for t in said if not has(t)]
    out.append({"name": f"{fmt}.words", "ok": not lost, "label": S("check.d.words.label"),
                "detail": S("check.d.words.missing", text=lost[0][:80]) if lost else S("check.d.words.ok", n=len(said))})
    t = doc.get("table")
    if t:
        want = t["rows"] if fmt == "pptx" else t["rows"][:DOCX_ROWS]
        cols = (t.get("deck_columns") or list(range(len(t["columns"])))) if fmt == "pptx" else range(len(t["columns"]))
        gone = [r for r in want if not all(has(r[j]) for j in cols if str(r[j]).strip())]
        out.append({"name": f"{fmt}.table", "ok": not gone, "label": S("check.d.table.label"),
                    "detail": S("check.d.table.missing", row=" · ".join(str(x) for x in gone[0])[:80]) if gone else S("check.d.table.ok", n=len(want))})
    closing_ok = all(has(sha[:16]) for _, _, sha in doc["sources"])
    rules, line = doc.get("rules_line"), doc.get("closing_line")
    rules_ok = has(rules) if rules else not has(commentary.rules_line())
    line_ok = has(line) if line else not has(S("doc.assistant.line"))
    out.append({"name": f"{fmt}.closing", "ok": closing_ok and rules_ok and line_ok, "label": S("check.d.closing.label"),
                "detail": S("check.d.closing.ok") if closing_ok and rules_ok and line_ok else S("check.d.closing.bad")})
    out.append(commentary.no_model_name(texts, commentary.model_names(doc["narrative"], view, req)))
    if fmt == "pptx" and t and len(t["rows"]) > deck_capacity():
        # (never written: the deck's table holds every row or the deck is refused — §1.5, the catalogue's parts_max)
        out.append({"name": "pptx.table_fits", "ok": False, "label": S("check.d.table.label"), "detail": S("check.d.table.missing", row="…")})
    if doc.get("never"):
        # the words the package's paper never says (the PO matching's: approved, paid) — nor does its document
        low = joined.lower()
        said_never = [w for w in doc["never"] if w.lower() in low]
        out.append({"name": f"{fmt}.never", "ok": not said_never, "label": S("check.d.never.label"),
                    "detail": S("check.d.never.found", word=said_never[0]) if said_never else S("check.d.never.ok")})
    return out


def render(fmt: str, view: dict, req: dict, spec: dict, path: str, *, in_dir: str = "") -> tuple[dict | None, dict | None]:
    """One report or deck written and read back: (artifact, None), or (None, refusal) when the content fits no layout
    of the catalogue — the section named, and no file left behind."""
    import hashlib

    from . import quality
    from .theme import resolve

    theme = resolve(req.get("theme"))
    logo = os.path.join(in_dir, os.path.basename(req["logo"])) if req.get("logo") and in_dir else None
    if fmt == "pptx" and req.get("deck_over") not in (None, ""):
        spec = over(spec, req["deck_over"])  # AM23 §1.5: the deck a person picked, of the rows over a threshold
    doc = content(view, req, spec)
    organisation = str((req.get("run") or {}).get("organisation") or "")
    try:
        meta = (write_docx if fmt == "docx" else write_pptx)(doc, theme, path, logo, organisation)
    except catalogue.DoesNotFit as e:
        if os.path.exists(path):
            os.remove(path)
        return None, commentary.refusal(fmt, os.path.basename(path), e)
    checks = check(path, fmt, doc, req, view) + [quality.fit_ok(meta.get("layouts") or [])]
    ok = all(c["ok"] for c in checks)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return {"name": os.path.basename(path), "format": fmt, "kind": FORMATS[fmt], "size": os.path.getsize(path), "sha256": sha,
            "quality": {"passed": ok, "checks": checks},
            "meta": {k: v for k, v in meta.items() if k in ("slides", "embedded_fonts", "layouts", "slide_cites")}, "chart_bindings": [],
            "citations": len(view.get("citations") or [])}, None


def into(fmt: str, view: dict, req: dict, spec: dict, names: dict, default: str, out_dir: str, artifacts: list, refused: list,
         *, in_dir: str = "") -> bool:
    """A package's render loop hands a report or a deck here: the file is made and read back and joins ``artifacts``, or
    it is refused with the section named and joins ``refused``. A kind of file no package writes is refused by name
    (the runner never asks for one: it holds every request to the manifest — AM23 §1.1). True when it passed."""
    if fmt not in FORMATS:
        refused.append({"format": fmt, "name": "", "check": {"name": "file.not_made", "ok": False, "label": S("check.d.not_made.label"),
                                                              "detail": S("check.d.not_made", fmt=fmt)}})
        return False
    name = os.path.basename(names.get(fmt) or f"{default}.{fmt}")
    art, refusal = render(fmt, view, req, spec, os.path.join(out_dir, name), in_dir=in_dir)
    if refusal:
        refused.append(refusal)
        return False
    artifacts.append(art)
    return bool(art["quality"]["passed"])


def amount(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None
