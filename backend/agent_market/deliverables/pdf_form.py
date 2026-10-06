"""A PDF form with fields to type in (an AcroForm), read and filled (AM12).

A PDF that is only a picture of a form — printed, scanned, or exported
without fields — has nothing that takes a value, and is refused as that. One
with text fields is read like any form that carries no marker: each field is
a blank, and its label is what the form prints for it — the field's own
description when it has one, else the words printed just left of the field
on its page, else those just above it. The field's internal name ("Text7")
is shown only as where the field is; it is not offered as a label.

Filling writes the value into the field (so it is still a form: a person
can go on typing in it) and draws it in the field's box with a font that has
Thai in it — a form's own font is usually one that does not, and a viewer
asked to draw the value itself shows boxes. Check boxes, radio buttons,
choice lists and signature fields are not written.

``append_gaps`` adds a last page naming what could not be filled and why;
``gaps_printed`` and ``holds`` open the delivered file again.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from typing import Any

FONT_DIRS = ("/sandbox/scripts/fonts", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "services",
                                                     "chat-edge-api", "scripts", "fonts"))
FONT, FONT_BOLD = "AMSarabun", "AMSarabun-Bold"
KEY = "pdf:"                 # a field's slot key: "pdf:<the field's full name>"
MAX_FIELDS, MAX_LABEL = 200, 60
READ_ONLY, MULTILINE, PASSWORD = 1, 1 << 12, 1 << 13


def _fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    for d in FONT_DIRS:
        regular, bold = os.path.join(d, "Sarabun-Regular.ttf"), os.path.join(d, "Sarabun-Bold.ttf")
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont(FONT, regular))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold if os.path.exists(bold) else regular))
            return
    raise RuntimeError("no Thai font to draw a PDF form's values with")


def _squash(text) -> str:
    """Text as it compares between what was written and what a PDF gives back: no spacing, one form of each character."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", "" if text is None else str(text)))


def _label_of(text) -> str:
    return (text or "").strip().rstrip(":：").strip()


def _full_name(obj) -> str:
    parts = []
    while obj is not None:
        t = obj.get("/T")
        if t is not None:
            parts.append(str(t))
        parent = obj.get("/Parent")
        obj = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))


def _inherited(obj, name: str):
    while obj is not None:
        if name in obj:
            return obj[name]
        parent = obj.get("/Parent")
        obj = parent.get_object() if parent is not None else None
    return None


def _widgets(reader) -> list[dict]:
    """Every widget of a text field a person may type in: its field's name, page, box, and description."""
    out = []
    for pi, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            a = ref.get_object()
            if a.get("/Subtype") != "/Widget":
                continue
            kind, flags = _inherited(a, "/FT"), int(_inherited(a, "/Ff") or 0)
            name = _full_name(a)
            if not name:
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            out.append({"name": name, "page": pi, "box": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], "widget": a,
                        "text": kind == "/Tx" and not flags & (READ_ONLY | PASSWORD), "kind": str(kind or ""),
                        "multiline": bool(flags & MULTILINE), "said": str(_inherited(a, "/TU") or "").strip(),
                        "value": str(_inherited(a, "/V") or "")})
    return out


def _printed_labels(path: str, widgets: list[dict]) -> dict[int, str]:
    """For each widget, the words the page prints for it: on its line just to its left, else just above it."""
    import pdfplumber

    out: dict[int, str] = {}
    with pdfplumber.open(path) as pdf:
        for pi, page in enumerate(pdf.pages):
            mine = [(n, w) for n, w in enumerate(widgets) if w["page"] == pi]
            if not mine:
                continue
            words = page.extract_words(keep_blank_chars=True, use_text_flow=False) or []
            height = float(page.height)
            for n, w in mine:
                x0, y0, x1, y1 = w["box"]
                top, bottom = height - y1, height - y0
                on_line = [t for t in words if t["x1"] <= x0 + 2 and t["bottom"] > top and t["top"] < bottom]
                if on_line:
                    near = max(on_line, key=lambda t: t["x1"])
                    # the words are this field's when no other field stands between them and it
                    between = [o for m, o in mine if m != n and o["box"][1] < y1 and o["box"][3] > y0
                               and near["x1"] - 2 <= o["box"][0] < x0]
                    if not between:
                        out[n] = _label_of(near["text"])
                        continue
                above = [t for t in words if t["bottom"] <= top + 2 and top - t["bottom"] <= 24 and t["x0"] < x1 and t["x1"] > x0 - 4]
                if above:
                    out[n] = _label_of(max(above, key=lambda t: t["bottom"])["text"])
    return out


def inspect(path: str) -> dict[str, Any]:
    """What a PDF form asks for, in the shape template_fill.inspect gives: targets (one per text field) with
    target_info, and what it has that is not written (``not_offered``)."""
    from pypdf import PdfReader

    out: dict[str, Any] = {"kind": "pdf", "markers": [], "controls": [], "names": [], "tables": [], "has_markers": False,
                           "targets": [], "target_info": {}, "regions": [], "lines": [], "not_offered": []}
    reader = PdfReader(path)
    if reader.is_encrypted:
        out["refused"] = [{"key": "pdf:encrypted", "label": os.path.basename(path), "reason": "protected", "where": "PDF"}]
        return out
    widgets = _widgets(reader)
    printed = _printed_labels(path, widgets) if widgets else {}
    seen = set()
    for n, w in enumerate(widgets):
        if w["name"] in seen:
            continue
        seen.add(w["name"])
        if not w["text"]:
            out["not_offered"].append({"name": w["name"], "kind": w["kind"]})
            continue
        label = w["said"] or printed.get(n) or ""
        if not label or len(label) > MAX_LABEL:
            # a field nothing on the page names is still a place to write: it is offered under its own name,
            # for a person to decide — it is not counted as a blank the form expects the agent to fill
            label, required = w["name"], False
        else:
            required = True
        key = KEY + w["name"]
        out["targets"].append({"cell": key, "label": label})
        out["target_info"][key] = {"required": required, "side": "field", "label_at": ""}
        if len(out["targets"]) >= MAX_FIELDS:
            break
    return out


def _appearance(writer, width: float, height: float, text: str, multiline: bool):
    """A drawing of ``text`` in a box of the field's size, as the field's appearance."""
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    _fonts()

    def wrapped(size: float) -> list[str]:
        lines, line = [], ""
        for ch in text:
            if ch == "\n" or stringWidth(line + ch, FONT, size) > width - 6:
                lines.append(line)
                line = "" if ch == "\n" else ch
            else:
                line += ch
        return lines + [line]

    cut = False
    if multiline:
        # the largest size at which every line of the value is inside the box; at the smallest, what does
        # not fit is not drawn — and that is said (``cut``), never left to look complete
        for size in (10.0, 9.0, 8.0, 7.0, 6.0):
            lines = wrapped(size)
            if len(lines) * size * 1.25 + 2 <= height:
                break
        else:
            cut = True
    else:
        size, lines = max(6.0, min(12.0, height - 5.0)), [text]
        while size > 5.0 and stringWidth(text, FONT, size) > width - 6:
            size -= 0.5
        cut = stringWidth(text, FONT, size) > width - 6
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(FONT, size)
    c.setFillColorRGB(0, 0, 0)
    if multiline:
        y = height - size - 2
        for line in lines:
            if y < 1:
                break
            c.drawString(3, y, line)
            y -= size * 1.25
    else:
        c.drawString(3, max(2.0, (height - size) / 2 + size * 0.22), text)
    c.save()
    page = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data())
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/BBox")] = ArrayObject([FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)])
    stream[NameObject("/Resources")] = page["/Resources"].clone(writer)
    return writer._add_object(stream), size, cut


def fill(path: str, out_path: str, values: dict | None) -> dict[str, Any]:
    """Fill the PDF form's fields ({"pdf:<name>": text}). Returns the filler's report."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import BooleanObject, DictionaryObject, NameObject, TextStringObject

    report: dict[str, Any] = {"kind": "pdf", "filled": [], "refused": {}, "not_filled_explained": [], "needs_recalc": False,
                              "formulas_kept": [], "mapping_screen": False, "targets": [], "rows": {}, "rows_short": {},
                              "parts_dropped": [], "cut": []}
    reader = PdfReader(path)
    writer = PdfWriter(clone_from=reader)
    widgets = _widgets(writer)
    by_name: dict[str, list[dict]] = {}
    for w in widgets:
        by_name.setdefault(w["name"], []).append(w)
    for key, value in (values or {}).items():
        if not str(key).startswith(KEY):
            continue
        name = str(key)[len(KEY):]
        text = None if value is None or isinstance(value, (list, dict, tuple)) else str(value)
        if text is None or not text.strip():
            report["not_filled_explained"].append({"target": key, "reason": "no_value"})
            continue
        mine = by_name.get(name)
        if not mine:
            report["refused"][key] = "unknown_target"
            continue
        if not mine[0]["text"]:
            report["refused"][key] = "not_plain_text"
            continue
        for w in mine:
            a = w["widget"]
            field = a
            while "/T" not in field and field.get("/Parent") is not None:
                field = field["/Parent"].get_object()
            field[NameObject("/V")] = TextStringObject(text)
            x0, y0, x1, y1 = w["box"]
            ref, _, cut = _appearance(writer, x1 - x0, y1 - y0, text, w["multiline"])
            a[NameObject("/AP")] = DictionaryObject({NameObject("/N"): ref})
            if cut:
                report["cut"].append(key)
        report["filled"].append(key)
    form = writer._root_object.get("/AcroForm")
    if form is not None:
        # the values are drawn here, with a font that has their letters: a viewer must not draw them again its own way
        form.get_object()[NameObject("/NeedAppearances")] = BooleanObject(False)
    with open(out_path, "wb") as f:
        writer.write(f)
    if not report["filled"] and not report["refused"] and not report["not_filled_explained"]:
        report["not_filled_explained"].append({"reason": "no_markers"})
    return report


def field_values(path: str) -> dict[str, str]:
    from pypdf import PdfReader

    return {KEY + w["name"]: w["value"] for w in _widgets(PdfReader(path)) if w["text"]}


def holds(path: str, slot: dict, value) -> bool:
    """The filled file, opened again: the field holds the value, and has a drawing of it."""
    from pypdf import PdfReader

    want = _squash(value)
    hits = [w for w in _widgets(PdfReader(path)) if KEY + w["name"] == slot["key"]]
    return bool(want) and bool(hits) and all(_squash(w["value"]) == want and "/AP" in w["widget"] for w in hits)


def still_blank(path: str, slot: dict) -> bool:
    values = field_values(path)
    return slot["key"] in values and not values[slot["key"]].strip()


def pages(path: str) -> int:
    from pypdf import PdfReader

    return len(PdfReader(path).pages)


def append_gaps(path: str, gaps: dict) -> None:
    """A last page (or pages) naming what could not be filled and why, in the size of the form's own first page."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _fonts()
    reader = PdfReader(path)
    box = reader.pages[0].mediabox
    size = (float(box.width), float(box.height))
    body = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=15, wordWrap="CJK")
    head = ParagraphStyle("head", parent=body, fontName=FONT_BOLD, fontSize=14, leading=20)
    bold = ParagraphStyle("bold", parent=body, fontName=FONT_BOLD)

    def esc(s) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = [[Paragraph(esc(c), bold) for c in gaps["columns"]]] + [[Paragraph(esc(what), body), Paragraph(esc(why), body)]
                                                                 for what, why in gaps.get("rows") or []]
    width = size[0] - 36 * mm
    table = Table(rows, colWidths=[width * 0.36, width * 0.64], repeatRows=1)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=size, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm).build(
        [Paragraph(esc(gaps["title"]), head), Spacer(1, 4), Paragraph(esc(gaps["lead"]), body), Spacer(1, 8), table])
    writer = PdfWriter(clone_from=reader)
    for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
        writer.add_page(page)
    with open(path, "wb") as f:
        writer.write(f)


def gaps_printed(path: str, gaps: dict, first_page: int) -> list[str]:
    """The filled file, opened again: the gaps that are NOT on the pages after the form's own (none, when it
    is as it should be)."""
    import pdfplumber

    have = set()
    with pdfplumber.open(path) as pdf:
        added = pdf.pages[first_page:]
        text = _squash("".join((p.extract_text() or "") for p in added))
        # cell by cell: a cell's lines are read together, whatever the cell beside it wraps to
        for p in added:
            for table in p.extract_tables() or []:
                for row in table:
                    if row and len(row) >= 2:
                        have.add((_squash(row[0]), _squash(row[1])))
    wanted = [str(what) for what, _ in gaps.get("rows") or []]
    if _squash(gaps["title"]) not in text:
        return wanted or [gaps["title"]]
    return [str(what) for what, why in gaps.get("rows") or [] if (_squash(what), _squash(why)) not in have or not _squash(why)]
