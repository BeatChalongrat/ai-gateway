"""The commentary sheet of a working paper (AM22 §1.1).

Five agents compute a commentary (``fallback_narrative``: the summary, the points, what to watch, the
next steps) and, since AM22, a model may write it from the computed table; until now those words
reached nobody — the workbook writers never read them. This is the one place they are printed: a sheet
right after the face, every figure in a sentence named by its key (``{adjusted_bank}``) and printed with
its own citation beside it, the way the report prints its commentary (content.cite_runs), and under it
one line that says who wrote the words. When no model gave a usable answer the package's rules wrote
them and the sheet says so on its face (``written_by: rules``); a run a model wrote names the model and
never carries that line. The gate opens the file again and reads both back (``check``).

The sheet is a layout of the catalogue (look.json xlsx ``commentary``) with limits like any other; a
commentary too long for it is refused naming the section, and no file is written (``catalogue.DoesNotFit``
— the caller turns it into a refused format with ``refusal``). Nothing here trims, cuts or drops a word.
"""

from __future__ import annotations

from ...engine.words import SHARED as S
from ..content import FIGURE, figure_text, sentences
from . import catalogue

LISTS = ("movements", "watch", "next_steps")
HEADING = {"summary": "doc.commentary.summary", "movements": "doc.commentary.movements", "watch": "doc.commentary.watch",
           "next_steps": "doc.commentary.next_steps"}
CITE_COL = 5
TEXT_COLS = 4


def printed(text: str, figures: dict) -> tuple[str, list[str]]:
    """A sentence as the sheet prints it — each ``{key}`` its figure's text — and the citations it carries.
    A key the table does not have stays as written: the gate finds the brace in the finished file."""
    out, cites, pos = [], [], 0
    for m in FIGURE.finditer(text or ""):
        fig = figures.get(m.group(1))
        if fig is None:
            continue
        out.append(text[pos:m.start()] + figure_text(fig, m.group(2)))
        pos = m.end()
        if fig.get("cite") and fig["cite"] not in cites:
            cites.append(fig["cite"])
    out.append((text or "")[pos:])
    return "".join(out), cites


def rows(narrative: dict, computed: dict) -> list[tuple[str, str, list[str]]]:
    """(part, the printed sentence, its citations) for every sentence of the commentary, in print order."""
    figures = computed.get("figures") or {}
    out = []
    text, cites = printed(str(narrative.get("summary") or ""), figures)
    out.append(("summary", text, cites))
    for key in LISTS:
        for s in sentences(narrative, key):
            text, cites = printed(s, figures)
            out.append((key, text, cites))
    return out


def rules_line(narrative: dict | None = None) -> str:
    """The sentence a commentary the rules composed carries under its words (AM23 §3.2): it names no model, and it is
    in the file whatever the organisation's switch says, because it changes what a reader should trust. Why no
    model's words were used (none could be reached, or none of its answers could be) is the run's record."""
    return S("doc.commentary.rules_composed")


def assistant_on(req: dict | None) -> bool:
    """The organisation's switch for the neutral line (AM23 §3.2): on unless the runner says it is off."""
    return (req or {}).get("assistant_line") is not False


def closing_line(narrative: dict, req: dict | None) -> str | None:
    """The one neutral line a file's closing block carries about how its commentary was written (AM23 §3.2): when a
    model drafted it and the organisation has the line on. It names no model, no vendor and not the platform; it is
    never on the cover. None otherwise — the rules' own sentence stands under their words."""
    if narrative.get("written_by") == "rules" or not assistant_on(req):
        return None
    return S("doc.assistant.line")


def model_names(narrative: dict | None, computed: dict | None, req: dict | None) -> list[str]:
    """Every model this run used, by name: the one that wrote the words and any that read a scan (AM23 §3.1: none of
    them is printed in a file — the run's own record keeps them)."""
    names = {str((narrative or {}).get("model") or ""), str(((req or {}).get("model_info") or {}).get("model") or "")}
    for m in (computed or {}).get("models") or []:
        if isinstance(m, dict):
            names.add(str(m.get("model") or ""))
    return sorted(n for n in names if len(n.strip()) >= 3)


def no_model_name(texts: list[str], names: list[str], where: str = "") -> dict:
    """The finished file's text, read again: no model's name in it (AM23 §3.1)."""
    low = [t.lower() for t in texts]
    for n in names:
        hit = next((t for t, l in zip(texts, low) if n.lower() in l), None)
        if hit is not None:
            return {"name": "file.no_model", "ok": False, "label": S("check.no_model.label"),
                    "detail": S("check.no_model.found", model=n, where=where or hit[:60])}
    return {"name": "file.no_model", "ok": True, "label": S("check.no_model.label"), "detail": S("check.no_model.ok")}


def workbook_texts(path: str) -> list[str]:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    return [ws.title for ws in wb.worksheets] + [str(c.value) for ws in wb.worksheets for row in ws.iter_rows()
                                                 for c in row if isinstance(c.value, str)]


def file_texts(path: str) -> list[str]:
    """Every text a finished file holds, by its kind: a workbook's sheet names and cells, a document's paragraphs,
    tables, headers and footers, a deck's shapes, tables and notes (AM23: what the gates read back)."""
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("xlsx", "xlsm"):
        return workbook_texts(path)
    out: list[str] = []
    if ext == "docx":
        from docx import Document

        doc = Document(path)

        def walk(tables):
            for t in tables:
                for row in t.rows:
                    for cell in row.cells:
                        out.extend(p.text for p in cell.paragraphs)
                        walk(cell.tables)

        out.extend(p.text for p in doc.paragraphs)
        walk(doc.tables)
        for sec in doc.sections:
            for part in (sec.header, sec.footer):
                out.extend(p.text for p in part.paragraphs)
                walk(part.tables)
    elif ext == "pptx":
        from pptx import Presentation

        prs = Presentation(path)
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    out.extend(p.text for p in shape.text_frame.paragraphs)
                if getattr(shape, "has_table", False) and shape.has_table:
                    out.extend(c.text for row in shape.table.rows for c in row.cells)
            if slide.has_notes_slide:
                out.append(slide.notes_slide.notes_text_frame.text)
    return [t for t in out if t]


def sheet(book, narrative: dict, computed: dict, req: dict | None = None) -> dict:
    """Write the commentary sheet into ``book`` (a look.xlsx_styles.Book), placed right after the face.
    Raises catalogue.DoesNotFit when the commentary is too long for the layout."""
    name = S("doc.sheet.commentary")
    lines = rows(narrative, computed)
    # each heading's points measured whole, every one of them: the widest point, and how many there are
    choice = catalogue.choose("xlsx", {"title": name, "text": lines[0][1]}, name, kinds=("commentary",))
    for key in LISTS:
        points = [t for part, t, _ in lines if part == key]
        if points:
            got = catalogue.choose("xlsx", {"title": name, "items": points}, f"{name} · {S(HEADING[key])}", kinds=("commentary",))
            if len(got.parts) > 1:
                # a heading over the sheet's ceiling is refused, never broken or cut: a sheet has no next page
                raise catalogue.DoesNotFit(f"{name} · {S(HEADING[key])}", got.reason)
            if got.fill > choice.fill:
                choice = got
    ws = book.sheet(name)
    book.title(ws, name, S("doc.commentary.sub"))
    r = 4
    part_at = None
    for part, text, cites in lines:
        if part != part_at:
            book.section(ws, r, S(HEADING[part]))
            r += 1
            part_at = part
        book.note(ws, r, text, cols=TEXT_COLS)
        if cites:
            book.muted(ws, r, CITE_COL, ", ".join(cites))
        r += 1
    r += 1
    book.section(ws, r, S("doc.commentary.refs"))
    book.muted(ws, r, CITE_COL, S("doc.commentary.refs_note"))
    r += 1
    # AM23 §3.2: under the words the rules composed, their sentence (always); beside the references, the neutral line
    # when a model drafted the words and the organisation has it on; and never a model's name
    last = rules_line(narrative) if narrative.get("written_by") == "rules" else closing_line(narrative, req)
    if last:
        book.note(ws, r, last, cols=TEXT_COLS)
    wb = book.wb
    wb.move_sheet(ws, offset=-(len(wb.worksheets) - 2))
    return {"layout": {"section": name, "block": "commentary", "layout": choice.layout, "role": choice.role, "reason": choice.reason},
            "sentences": len(lines)}


def check(path: str, narrative: dict, computed: dict, req: dict | None = None) -> dict:
    """The finished file, opened again: every sentence of the commentary is there as printed, the sentence that says
    the rules composed it is there exactly when they did, the neutral line exactly when a model drafted it and the
    organisation has it on (AM23 §3.2), and no figure's name is left unreplaced."""
    from openpyxl import load_workbook

    name = S("doc.sheet.commentary")
    label = S("check.commentary.label")

    def ck(ok: bool, detail: str, n: int) -> dict:
        return {"name": "xlsx.commentary", "ok": bool(ok), "detail": detail, "label": label, "values": n}

    wb = load_workbook(path)
    if name not in wb.sheetnames:
        return ck(False, S("check.commentary.no_sheet", sheet=name), 0)
    texts = [str(c.value) for row in wb[name].iter_rows() for c in row if isinstance(c.value, str)]
    squashed = {"".join(t.split()) for t in texts}
    want = rows(narrative, computed)
    missing = [t for _, t, _ in want if "".join(t.split()) not in squashed]
    if missing:
        return ck(False, S("check.commentary.missing", n=len(missing), text=missing[0][:80]), len(want))
    braces = [t for t in texts if "{" in t or "}" in t]
    if braces:
        return ck(False, S("check.commentary.brace", text=braces[0][:80]), len(want))
    has_line = "".join(rules_line().split()) in squashed
    if narrative.get("written_by") == "rules" and not has_line:
        return ck(False, S("check.commentary.rules_line_missing"), len(want))
    if narrative.get("written_by") != "rules" and has_line:
        return ck(False, S("check.commentary.rules_line_extra"), len(want))
    has_assistant = "".join(S("doc.assistant.line").split()) in squashed
    wants_assistant = closing_line(narrative, req) is not None
    if wants_assistant and not has_assistant:
        return ck(False, S("check.commentary.assistant_missing"), len(want))
    if has_assistant and not wants_assistant:
        return ck(False, S("check.commentary.assistant_extra"), len(want))
    return ck(True, S("check.commentary.ok", n=len(want), sheet=name), len(want))


def refusal(fmt: str, name: str, exc: catalogue.DoesNotFit) -> dict:
    """A format the renderer did not write, as the render result reports it (deliver.go reads ``refused``)."""
    from .. import quality

    return {"format": fmt, "name": name, "check": quality.fit_refused(exc)}
