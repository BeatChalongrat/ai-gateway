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


def rules_line(narrative: dict) -> str:
    """The line a commentary the rules wrote carries, saying why: no model was available, or the model's answers could
    not be used (the runner says which: narrative "why")."""
    return S("doc.commentary.rules_unusable") if narrative.get("why") == "unusable" else S("doc.commentary.rules_wrote")


def who_wrote(narrative: dict, model_info: dict | None) -> str:
    """The one line under the commentary that says who wrote it."""
    if narrative.get("written_by") == "rules":
        return rules_line(narrative)
    model = str(narrative.get("model") or (model_info or {}).get("model") or "")
    place = ""
    if (model_info or {}).get("self_hosted") is True:
        place = " " + S("docs.model.inside")
    elif (model_info or {}).get("self_hosted") is False:
        place = " " + S("docs.model.outside")
    return S("doc.commentary.model_wrote", model=model or S("docs.about.no_model"), place=place)


def sheet(book, narrative: dict, computed: dict, model_info: dict | None = None) -> dict:
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
    book.note(ws, r, who_wrote(narrative, model_info), cols=TEXT_COLS)
    wb = book.wb
    wb.move_sheet(ws, offset=-(len(wb.worksheets) - 2))
    return {"layout": {"section": name, "block": "commentary", "layout": choice.layout, "role": choice.role, "reason": choice.reason},
            "sentences": len(lines)}


def check(path: str, narrative: dict, computed: dict, model_info: dict | None = None) -> dict:
    """The finished file, opened again: every sentence of the commentary is there as printed, the line
    that says the rules wrote it is there exactly when they did, and no figure's name is left unreplaced."""
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
    lines = {"".join(S(k).split()) for k in ("doc.commentary.rules_wrote", "doc.commentary.rules_unusable")}
    has_line = bool(lines & squashed)
    if narrative.get("written_by") == "rules" and "".join(rules_line(narrative).split()) not in squashed:
        return ck(False, S("check.commentary.rules_line_missing"), len(want))
    if narrative.get("written_by") != "rules" and has_line:
        return ck(False, S("check.commentary.rules_line_extra"), len(want))
    return ck(True, S("check.commentary.ok", n=len(want), sheet=name), len(want))


def refusal(fmt: str, name: str, exc: catalogue.DoesNotFit) -> dict:
    """A format the renderer did not write, as the render result reports it (deliver.go reads ``refused``)."""
    from .. import quality

    return {"format": fmt, "name": name, "check": quality.fit_refused(exc)}
