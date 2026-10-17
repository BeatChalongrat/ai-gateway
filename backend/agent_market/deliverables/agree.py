"""Every file of a run says the same number (AM23 Part 2).

Since AM23 an agent makes two or more kinds of file with figures in each — a workbook, a report, a deck, the
customer's own form — and a figure printed one way in one file and another way in the next is the worst bug this
product can have (AM14's: 3600 in one place, 3,600.00 in another). This check runs on the finished files, read back
after they are written — never on the data that went in — and it picks no winner: a figure printed two ways fails the
files that print it, naming the figure, the two places and the two strings.

Where a figure is printed is what each file says:

  * beside its label — the figure's own label (the report's and the deck's figures, the cover's tiles) or the label
    the package's workbook face gives it (``face(view)`` in its scripts/document.py: figure key → the face's label,
    and which number of that row it is): the number printed next to it, whatever it looks like, is that figure;
  * in a line that cites it — a commentary point with its references, a table row with a reference column, a slide
    with the references its writer recorded: a number there is the figure when it is the figure's value, or the same
    amount printed with more or fewer decimals (3,820.55 and 3,821; 12,480,300.00 and 12,480,300 — "same rounding" is
    the card's word). A small whole number — a count, a day — is the figure only when it prints exactly the same, so a
    date's day or another count is never taken for one;
  * in the customer's form: each field filled with a figure, as the form's own gate found it in the filled file.

A workbook's number is read as Excel prints it under its number format; a zero its format prints as "-" is the
accounting dash, not a second way of printing the figure, and is not compared. The PDF a document prints is the
runner's to compare with that document (runner/pdf.go: its text is the converter's).
"""

from __future__ import annotations

import importlib
import os
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..engine.words import SHARED as S

NUMBER = re.compile(r"\(?-?\d(?:,?\d)*(?:\.\d+)?\)?")
# a number standing on its own in a text: not a piece of a date, a document number or a code ("07" of 07/09/2569,
# "0034" of PV256909-0034, "5200" of 5200-01), and not written with a leading zero
TOKEN = re.compile(r"(?<![\w/.\-])(\(?-?(?!0\d)\d(?:,?\d)*(?:\.\d+)?\)?)(?![\w/\-]|\.\d)")
# a line that is a number (and at most a short unit after it: a per cent sign, M, the Thai for millions or baht)
NUMLINE = re.compile(r"^\s*(\(?-?\d(?:,?\d)*(?:\.\d+)?\)?)\s*(?:%|[^\s\d]{1,8})?\s*$")


def value(text) -> Decimal | None:
    """A printed number's value: "1,530,280.15", "(0.50)", "-12", "12.48 M" — None when it holds none."""
    m = NUMBER.search(str(text or ""))
    if not m:
        return None
    s = m.group(0)
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    try:
        v = Decimal(s.strip("()").lstrip("-").replace(",", ""))
    except InvalidOperation:
        return None
    return -v if neg else v


def _decimals(s: str) -> int:
    m = NUMBER.search(s or "")
    t = m.group(0).strip("()") if m else ""
    return len(t.split(".", 1)[1]) if "." in t else 0


def same_amount(tok: str, ftext: str) -> bool:
    """A printed number is the figure ``ftext`` (printed another way, perhaps): its value; or, for an amount (a hundred or
    more, or with decimals on both sides), the same amount rounded to the coarser of the two."""
    a, b = value(tok), value(ftext)
    if a is None or b is None:
        return False
    da, db = _decimals(tok), _decimals(ftext)
    if da == db:
        return a == b
    if not (abs(b) >= 100 or (da and db)):
        return False
    q = Decimal(1).scaleb(-min(da, db))
    return a.quantize(q, rounding=ROUND_HALF_UP) == b.quantize(q, rounding=ROUND_HALF_UP)


def _sections(fmt: str) -> list[str]:
    out, cur, quoted = [], "", False
    for ch in fmt or "General":
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def printed(v, fmt: str) -> str:
    """What Excel prints for a number under the formats the renderer writes (look/xlsx_styles.py: money, quantity,
    count, percent, General), the zero's own section ("-") included."""
    if not isinstance(v, (int, float, Decimal)) or isinstance(v, bool):
        return str(v)
    d = Decimal(str(v))
    secs = _sections(fmt)
    sec = secs[0]
    if d == 0 and len(secs) >= 3:
        sec = secs[2]
    elif d < 0 and len(secs) >= 2:
        sec = secs[1]
    lit = re.fullmatch(r'\s*"([^"]*)"\s*', sec)
    if lit:
        return lit.group(1)
    if sec.strip() in ("", "General"):
        return f"{d.normalize():f}" if d == d.to_integral_value() else f"{d:f}"
    m = re.search(r"0\.(0+|#+)", sec)
    places = len(m.group(1)) if m else 0
    pct = "%" in sec and '"%"' not in sec   # (a literal "%" after the number does not scale it)
    q = (d * 100 if pct else d).copy_abs().quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)   # as Excel rounds
    body = f"{q:,.{places}f}" if "," in sec else f"{q:.{places}f}"
    if '"%"' in sec:
        body += "%"
    if m and m.group(1).startswith("#"):
        body = body.rstrip("0").rstrip(".")
    if pct:
        body += "%"
    if sec.strip().startswith("(") and d < 0:
        return f"({body})"
    if sec.strip().startswith("+") and d > 0:
        return f"+{body}"
    return f"-{body}" if d < 0 else body


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


class _Seen:
    """Where each figure was found printed, and how: key → [(file, place, string)]."""

    def __init__(self, figures: dict, face: dict):
        self.figures = figures
        self.by_cite: dict[str, list[str]] = {}
        for k, f in figures.items():
            self.by_cite.setdefault(str(f.get("cite") or ""), []).append(k)
        # label → [(key, nth)]: the figure's own label, and the label the workbook's face gives it
        self.labels: dict[str, list[tuple[str, int]]] = {}
        for k, f in figures.items():
            self.labels.setdefault(_norm(f.get("label")), []).append((k, 1))
        for k, (label, nth) in face.items():
            if k in figures and (k, nth) not in self.labels.get(_norm(label), []):
                self.labels.setdefault(_norm(label), []).append((k, nth))
        self.at: dict[str, list[tuple[str, str, str]]] = {}

    def put(self, key: str, s: str, file: str, place: str) -> None:
        m = NUMBER.search(str(s or ""))
        if key in self.figures and m:
            self.at.setdefault(key, []).append((file, place, m.group(0)))

    def beside(self, lines: list[str], file: str, place: str, *, row: bool = True) -> None:
        """A figure's label, and the number printed after it (a label may wrap over two or three lines): in a table's
        row, its first cell, and the row's nth number after it (a status's count, then its value); in running text — a
        slide's lines, a cover tile's — any line, and only the line right after it."""
        for i in range(1 if row else len(lines)):   # (a row's label is its first cell)
            for span in ((1,) if row else (1, 2, 3)):
                if i + span > len(lines):
                    break
                for key, nth in self.labels.get(_norm("".join(lines[i:i + span])), []):
                    if row:
                        nums = [ln for ln in lines[i + span:] if NUMLINE.match(ln or "")]
                    else:
                        nums = [ln for ln in lines[i + span:i + span + 1] if NUMLINE.match(ln or "")] if nth == 1 else []
                    if len(nums) >= nth:
                        self.put(key, NUMLINE.match(nums[nth - 1]).group(1), file, place)

    def cited(self, cites, text: str, file: str, place: str) -> None:
        """A number of a text that cites a figure, when it is that figure's value printed with its shape."""
        keys = [k for c in cites for k in self.by_cite.get(str(c).strip(), [])]
        if not keys:
            return
        toks = TOKEN.findall(text or "")
        for key in dict.fromkeys(keys):
            ftext = self.figures[key]["text"]
            want = value(ftext)
            if want is None or want == 0:
                continue   # (a zero is printed too many ways that are not this figure: "0", "-", "0 รายการ")
            for tok in toks:
                if same_amount(tok, ftext):
                    self.at.setdefault(key, []).append((file, place, tok))


def _cites_in(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"[,\s]+", text or "") if c.strip()]


def _ref_heads() -> set:
    return {_norm(S("docs.col.ref")), _norm(S("doc.commentary.refs"))}


def _workbook(path: str, file: str, seen: _Seen) -> None:
    from openpyxl import load_workbook

    wb = load_workbook(path)
    heads = _ref_heads()
    for ws in wb.worksheets:
        ref_col = None
        for row in ws.iter_rows():
            line = [(c, printed(c.value, c.number_format) if isinstance(c.value, (int, float)) and not isinstance(c.value, bool)
                     else str(c.value)) for c in row if c.value is not None]
            if not line:
                continue
            seen.beside([s for _, s in line], file, S("check.agree.at.cell", file=file, sheet=ws.title, cell=line[0][0].coordinate))
            if ref_col is None:
                hit = [c.column for c, s in line if isinstance(c.value, str) and _norm(s) in heads]
                ref_col = hit[0] if hit else None
                continue
            refs = [s for c, s in line if c.column == ref_col]
            if refs:
                for c, s in line:
                    if c.column != ref_col:
                        seen.cited(_cites_in(refs[0]), s, file, S("check.agree.at.cell", file=file, sheet=ws.title, cell=c.coordinate))


def _report(path: str, file: str, seen: _Seen) -> None:
    from docx import Document

    doc = Document(path)
    heads = _ref_heads()
    for n, t in enumerate(doc.tables, start=1):
        rows = list(t.rows)
        if not rows:
            continue
        top = [_norm(c.text) for c in rows[0].cells]
        ref_col = next((j for j, h in enumerate(top) if h in heads), None)
        for r, row in enumerate(rows):
            cells = [c.text for c in row.cells]
            at = S("check.agree.at.table", file=file, n=n, row=r + 1)
            seen.beside(cells, file, at)
            for cell in cells:
                if "\n" in cell:
                    # a cover's tile: its label, its figure and its reference in one cell
                    seen.beside(cell.split("\n"), file, at, row=False)
                    cites = [c for x in re.findall(r"\[([^\]]+)\]", cell) for c in _cites_in(x)]
                    seen.cited(cites, re.sub(r"\[[^\]]+\]", " ", cell), file, at)
            if ref_col is not None and r > 0 and ref_col < len(cells):
                seen.cited(_cites_in(cells[ref_col]), " ".join(x for j, x in enumerate(cells) if j != ref_col), file, at)
    head = S("docs.refs", ids="").strip()
    buf: list[tuple[int, str]] = []
    for n, p in enumerate(doc.paragraphs, start=1):
        text = p.text
        if head and text.startswith(head):
            for m, b in buf:
                seen.cited(_cites_in(text[len(head):]), b, file, S("check.agree.at.para", file=file, n=m))
            buf = []
        elif p.style is not None and str(p.style.name).startswith("Heading"):
            buf = []
        elif text.strip():
            buf.append((n, text))


def _deck(path: str, file: str, seen: _Seen, slide_cites: list) -> None:
    from pptx import Presentation

    prs = Presentation(path)
    for i, slide in enumerate(prs.slides):
        at = S("check.agree.at.slide", file=file, n=i + 1)
        lines: list[str] = []
        for sh in slide.shapes:
            if sh.has_text_frame:
                lines += [p.text for p in sh.text_frame.paragraphs]
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    cells = [c.text for c in row.cells]
                    seen.beside(cells, file, at)
                    lines += cells
        seen.beside(lines, file, at, row=False)
        cites = slide_cites[i] if i < len(slide_cites or []) else []
        if cites:
            seen.cited(cites, "\n".join(lines), file, at)


def face_of(pkg, view: dict) -> dict:
    """The package's workbook face: figure key → (label, which number of its row) — {} for a package with none."""
    try:
        mod = importlib.import_module(getattr(pkg, "__name__", "") + ".scripts.document")
    except ImportError:
        return {}
    fn = getattr(mod, "face", None)
    out = {}
    for k, v in (fn(view) if fn else {}).items():
        out[k] = (v, 1) if isinstance(v, str) else (v[0], int(v[1]))
    return out


def form_printed(pkg, values: dict, saved: dict, report: dict, figures: dict) -> dict:
    """The figures a filled form holds, as its own gate found them in the filled file (template_out.verify): field
    key → (the slot's label, the value written) for every slot mapped to a figure that is not among the unfilled."""
    try:
        alias = getattr(importlib.import_module(getattr(pkg, "__name__", "") + ".scripts.document"), "FORM_FIGURES", {}) or {}
    except ImportError:
        alias = {}
    unfilled = {u.get("slot") for u in report.get("unfilled") or []}
    labels = {s["key"]: s.get("label") or s["key"] for s in saved.get("slots") or []}
    out = {}
    for slot, field in (saved.get("mapping") or {}).items():
        key = alias.get(field, field)
        if key in figures and slot not in unfilled and isinstance(values.get(field), str) and values[field].strip():
            out[key] = (labels.get(slot) or slot, values[field])
    return out


def check(out_dir: str, artifacts: list, view: dict, pkg, *, prior: list | None = None) -> dict | None:
    """The run's figures read back from every finished file — those of this render, and ``prior``: the round's files
    made before it (a deck asked of a finished round is compared with the round's workbook). None when the run has no
    figures table; else the check (``check``), the files read (``files``), the files that disagree (``bad``) and the
    counts: figures found in two or more files, the pairs of files compared, the places read."""
    figures = view.get("figures") or {}
    if not figures:
        return None
    seen = _Seen(figures, face_of(pkg, view))
    read: list[str] = []
    for a in list(artifacts) + list(prior or []):
        path = a.get("path") or os.path.join(out_dir, a.get("name") or "")
        if not os.path.exists(path):
            continue
        file, fmt, kind = a["name"], a.get("format"), a.get("kind")
        if kind == "template":
            filled = a.get("_printed") or {}
            if filled:
                read.append(file)
            for key, (where, s) in filled.items():
                seen.put(key, s, file, S("check.agree.at.form", file=file, field=where))
            continue
        read.append(file)
        if fmt == "xlsx":
            _workbook(path, file, seen)
        elif fmt == "docx":
            _report(path, file, seen)
        elif fmt == "pptx":
            _deck(path, file, seen, (a.get("meta") or {}).get("slide_cites") or [])
    bad, compared, pairs = None, 0, set()
    for key in figures:
        at = seen.at.get(key) or []
        files = sorted({f for f, _, _ in at})
        if bad is None and len({s for _, _, s in at}) > 1:
            first = at[0]
            bad = (key, first, next(x for x in at if x[2] != first[2]))
        if len(files) > 1:
            compared += 1
            pairs |= {(x, y) for x in files for y in files if x < y}
    counts = {"figures": compared, "pairs": len(pairs), "files": len(read), "places": sum(len(v) for v in seen.at.values())}
    where = {k: sorted({f for f, _, _ in v}) for k, v in seen.at.items()}
    if bad:
        key, (f1, p1, s1), (f2, p2, s2) = bad
        chk = {"name": "files.agree", "ok": False, "label": S("check.agree.label"),
               "detail": S("check.agree.bad", label=figures[key]["label"], place1=p1, s1=s1, place2=p2, s2=s2)}
        return {"ok": False, "check": chk, "files": read, "bad": sorted({f1, f2}), "counts": counts, "where": where,
                "figure": key, "places": [[p1, s1], [p2, s2]]}
    chk = {"name": "files.agree", "ok": True, "label": S("check.agree.label"),
           "detail": S("check.agree.ok", n=compared, files=len(read), pairs=len(pairs))}
    return {"ok": True, "check": chk, "files": read, "bad": [], "counts": counts, "where": where}


def attach(res: dict | None, artifacts: list) -> bool:
    """The check on the files: when they agree, once, on the first file read (a run-level check is counted once — AM8);
    when they do not, on each of the two files that disagree, which fail. True when they agree. A round of one file has
    nothing to compare, and its file is left as it was."""
    if not res or len(res["files"]) < 2:
        return True
    if res["ok"]:
        for a in artifacts:
            if a["name"] in res["files"]:
                a["quality"]["checks"].append(res["check"])
                break
        return True
    for a in artifacts:
        if a["name"] in res["bad"]:
            a["quality"]["checks"].append(res["check"])
            a["quality"]["passed"] = False
    return False
