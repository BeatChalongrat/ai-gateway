"""Working papers that print (AM8).

The page setup of every sheet is decided from the sheet itself — how many
columns, how wide, how many rows — not from a layout fixed per agent:

* paper A4; portrait when the columns fit across a portrait page, landscape
  otherwise;
* the columns are fitted to the page width when that keeps the print readable
  (scale ≥ ``MIN_SCALE``); a sheet too wide for that prints across pages;
* AM9: a row's identity — the columns that name it, its number and its
  description, which the writer puts first (``lead``) — is repeated at the left
  of every page across, so a row's description is beside its amounts on every
  page, not only a number a machine can match;
* the header row (the row the panes are frozen under) repeats on every page;
* the face of a working paper (its first sheet) prints on one page;
* the print area is the sheet's used range;
* every page carries the document's name, the sheet and "หน้า x / y";
* AM10: every sheet prints one page across — pages only run downward. A table
  too wide for that is not printed across pages but written as a main sheet
  (what a reader follows: the row's identity, its amounts, its status and
  reason) and companion sheets right after it, "<sheet> · หลักฐาน" and the
  like, that start with the same identity columns and hold the evidence, one
  row per row, so the two are read side by side by row number (``pick``).

``check`` reads those settings back out of the finished file and asserts
them, with the same arithmetic (``bands``: which columns print on which page
across); ``one_across`` is the AM10 rule as its own check, ``checks`` both. It
does not render; AM9's proof that does is the runner's: it prints the finished
workbook through the document converter and looks for every row of
``print_rows`` — its identity and its amounts — on one rendered page.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .reread import check as _check

# A4 in inches, and the margins set here
PAPER_W, PAPER_H, MARGIN_LR, MARGIN_TB = 8.27, 11.69, 0.5, 0.6
MIN_SCALE = 0.6           # below this a fitted page is too small to read: repeat the identifiers instead
# AM9: a sheet that runs across pages prints at 80 % (10 pt text prints at 8 pt, a footnote's size): with
# the row's identity repeated on every page, the bank paper is 20 pages instead of 26 at 100 %
SPLIT_SCALE = 0.8
DEFAULT_WIDTH = 8.43      # Excel's column width when none is set
ROW_PT = 15.0             # Excel's default row height
COMPANION = " · "         # AM10: a companion sheet is named "<main sheet> · <what it holds>"


def col_inches(width: float | None) -> float:
    """An Excel column width (characters of the default font) as printed inches."""
    w = DEFAULT_WIDTH if width in (None, 0) else float(width)
    return (7 * w + 5) / 96


def _widths(ws) -> list[float]:
    last = ws.max_column or 1
    return [col_inches(ws.column_dimensions[get_column_letter(j)].width) for j in range(1, last + 1)]


def _rows_pt(ws) -> float:
    total = 0.0
    for r in range(1, (ws.max_row or 1) + 1):
        h = ws.row_dimensions[r].height
        total += float(h) if h else ROW_PT
    return total


def plan(ws, *, face: bool = False) -> dict:
    """The page setup this sheet needs, from its shape."""
    widths = _widths(ws)
    total = sum(widths) or 1.0
    portrait_w = PAPER_W - 2 * MARGIN_LR
    landscape_w = PAPER_H - 2 * MARGIN_LR
    orientation = "portrait" if total <= portrait_w else "landscape"
    printable_w = portrait_w if orientation == "portrait" else landscape_w
    printable_h = (PAPER_H if orientation == "portrait" else PAPER_W) - 2 * MARGIN_TB
    width_scale = min(1.0, printable_w / total)
    fit = width_scale >= MIN_SCALE
    out = {"orientation": orientation, "fit_width": fit, "scale": round(width_scale, 3), "repeat_cols": 0}
    if face:
        height_scale = min(1.0, printable_h * 72 / max(_rows_pt(ws), 1.0))
        out["fit_height"] = True
        out["scale"] = round(min(width_scale, height_scale), 3)
    if not fit:
        out["repeat_cols"] = 1     # the identifier column(s) repeat on every page across
        out["scale"] = SPLIT_SCALE
    return out


def lead(heads: list, rows: list, widths: list | None, first: list, **cols) -> tuple:
    """A table's columns reordered for paper: ``first`` (header names — the row's identity, then its
    amounts) at the left, every other column after them in its own order. ``cols`` are the writer's
    1-based column tuples (money_cols=…, wrap_cols=…), returned renumbered.

    The data stays the same; only where it prints changes. A check reads the table back by header
    name (``reread.table``), so it never depends on this order."""
    missing = [h for h in first if h not in heads]
    if missing:
        raise ValueError(f"lead: no column {missing!r}")
    order = [heads.index(h) for h in first] + [i for i, h in enumerate(heads) if h not in first]
    pos = {old: new for new, old in enumerate(order)}
    return ([heads[i] for i in order], [[row[i] for i in order] for row in rows],
            [widths[i] for i in order] if widths else widths,
            {k: tuple(pos[c - 1] + 1 for c in v) for k, v in cols.items()})


def pick(heads: list, rows: list, keep: list, **cols) -> tuple:
    """The columns ``keep`` (header names, in that order) of a table, and the writer's 1-based column tuples
    (money_cols=…, wrap_cols=…) renumbered to them — an entry for a column that is not kept drops out.

    AM10: a table too wide to print one page across is written as a main sheet and companion sheets read
    side by side by row number — the main holds the row's identity, its amounts, its status and reason; a
    companion the same identity columns and then the evidence — each picked from the one table the writer
    builds. Nothing is dropped: every column is on one of them, the identity on all. A check reads a column
    back from whichever sheet holds it (``reread.table`` on the sheets together)."""
    missing = [h for h in keep if h not in heads]
    if missing:
        raise ValueError(f"pick: no column {missing!r}")
    idx = [heads.index(h) for h in keep]
    pos = {old: new for new, old in enumerate(idx)}
    return ([heads[i] for i in idx], [[row[i] for i in idx] for row in rows],
            {k: tuple(pos[c - 1] + 1 for c in v if c - 1 in pos) for k, v in cols.items()})


def _say(lang: str | None, thai: str, english: str) -> str:
    """AM12: a working paper of a package that speaks English is checked, and paged, in English."""
    return english if lang == "en" else thai


def setup(ws, *, doc_name: str, face: bool = False, id_cols: int = 1, lang: str | None = None) -> dict:
    """Set the sheet's page setup from its shape (see the module docstring); returns the plan."""
    p = plan(ws, face=face)
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = p["orientation"]
    ws.page_margins.left = ws.page_margins.right = MARGIN_LR
    ws.page_margins.top = ws.page_margins.bottom = MARGIN_TB
    ws.page_margins.header = ws.page_margins.footer = 0.3
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=bool(p["fit_width"] or face))
    if p["fit_width"] or face:
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1 if face else 0
    else:
        ws.page_setup.scale = int(SPLIT_SCALE * 100)
    last_col = get_column_letter(ws.max_column or 1)
    ws.print_area = f"A1:{last_col}{ws.max_row or 1}"
    header = _header_row(ws)
    if header and not face:
        ws.print_title_rows = f"{header}:{header}"
    if not face:
        # AM9: the row's identity repeats on every page across (and says which columns it is: the check
        # and the print proof read it from here)
        ws.print_title_cols = f"A:{get_column_letter(max(1, id_cols))}"
    ws.oddFooter.left.text = f"{doc_name} · {ws.title}"
    ws.oddFooter.left.size = 8
    ws.oddFooter.right.text = _say(lang, "หน้า &P / &N", "Page &P / &N")
    ws.oddFooter.right.size = 8
    ws.evenFooter.left.text, ws.evenFooter.right.text = ws.oddFooter.left.text, ws.oddFooter.right.text
    ws.print_options.horizontalCentered = False
    return p


def setup_workbook(wb, *, doc_name: str, id_cols: dict | None = None, lang: str | None = None) -> dict:
    """Every sheet of a working paper: the first is its face (one page)."""
    return {ws.title: setup(ws, doc_name=doc_name, face=(i == 0), id_cols=(id_cols or {}).get(ws.title, 1), lang=lang)
            for i, ws in enumerate(wb.worksheets)}


def _header_row(ws) -> int | None:
    """The table's header row: the row the panes are frozen under (the writers freeze below the header)."""
    fp = ws.freeze_panes
    if not fp:
        return None
    from openpyxl.utils.cell import coordinate_from_string

    _, row = coordinate_from_string(fp)
    return row - 1 if row > 1 else None


def _identity_beside_amounts(ws, where: str, lang: str | None = None) -> list[str]:
    """AM9: on every page across that prints an amount column, the row's identity prints too — and on a
    sheet that runs across pages, that identity names the row in words (a description), not only by
    a number."""
    bad: list[str] = []
    top = (_header_row(ws) or 0) + 1
    money_cols = sorted({c.column for row in ws.iter_rows(min_row=top) for c in row if _money(c)})
    if not money_cols:
        return bad
    ident = title_cols(ws)
    if not ident or ident[0] != 1:
        return [_say(lang, f"{where}: ไม่ได้กำหนดคอลัมน์ระบุรายการที่พิมพ์ซ้ำทุกหน้า",
                     f"{where}: no identifying columns are set to repeat on every page")]
    pages = bands(ws)
    for b in pages:
        if any(c in b for c in money_cols) and not all(c in b for c in ident):
            cols = ", ".join(get_column_letter(c) for c in money_cols if c in b)
            bad.append(_say(lang, f"{where}: หน้าที่พิมพ์จำนวนเงิน (คอลัมน์ {cols}) ไม่มีคอลัมน์ระบุรายการ",
                            f"{where}: the page that prints amounts (columns {cols}) has no identifying columns"))
    if len(pages) > 1:
        worded = any(isinstance(ws.cell(row=r, column=c).value, str) and len(ws.cell(row=r, column=c).value.strip()) > 3
                     for c in ident for r in range(top, min((ws.max_row or 0), top + 50) + 1))
        if not worded:
            bad.append(_say(lang, f"{where}: พิมพ์หลายหน้าในแนวนอน แต่คอลัมน์ที่ซ้ำทุกหน้ามีแค่เลขลำดับ ไม่มีคำอธิบายรายการ",
                            f"{where}: prints several pages across, but the repeated columns hold only a number and no description"))
    return bad


def _covers(area, max_col: int, max_row: int) -> bool:
    """The print area reaches the last used column and row (a reopened sheet may count one row fewer)."""
    import re

    from openpyxl.utils import column_index_from_string

    m = re.search(r"\$?([A-Z]+)\$?(\d+)\s*$", str(area or ""))
    return bool(m) and column_index_from_string(m.group(1)) >= max_col and int(m.group(2)) >= max_row


def title_cols(ws) -> list[int]:
    """The columns repeated at the left of every page (the row's identity), as 1-based indexes."""
    import re

    from openpyxl.utils import column_index_from_string

    m = re.search(r"\$?([A-Z]+):\$?([A-Z]+)", str(ws.print_title_cols or ""))
    if not m:
        return []
    a, b = column_index_from_string(m.group(1)), column_index_from_string(m.group(2))
    return list(range(min(a, b), max(a, b) + 1))


def _fitted(ws) -> bool:
    return bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage) \
        and ws.page_setup.fitToWidth in (1, "1")


def bands(ws) -> list[list[int]]:
    """Which columns print on which page across, from the file's own setup: a sheet fitted to the page
    width is one band; otherwise columns are laid out at the sheet's scale until the printable width is
    full, and every following band starts with the repeated identity columns. A column is never split."""
    widths = _widths(ws)
    n = len(widths)
    if _fitted(ws):
        return [list(range(1, n + 1))]
    scale = float(ws.page_setup.scale or 100) / 100
    landscape = ws.page_setup.orientation == "landscape"
    printable = (PAPER_H if landscape else PAPER_W) - 2 * MARGIN_LR
    reps = [c for c in title_cols(ws) if c <= n]
    rep_w = sum(widths[c - 1] for c in reps) * scale
    out: list[list[int]] = []
    col = 1
    while col <= n:
        band = [] if not out else list(reps)
        used = 0.0 if not out else rep_w
        placed = 0
        while col <= n:
            if out and col in reps:
                col += 1
                continue
            w = widths[col - 1] * scale
            if placed and used + w > printable:
                break
            band.append(col)
            used += w
            placed += 1
            col += 1
        if placed:
            out.append(band)
    return out or [list(range(1, n + 1))]


def _money(cell) -> bool:
    return isinstance(cell.value, (int, float, Decimal)) and not isinstance(cell.value, bool) \
        and "#,##0.00" in str(cell.number_format or "")


def money_text(value) -> str:
    """An amount as the working papers' money format prints it: 1,234.50 · (1,234.50) · - for zero."""
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(value)
    if d == 0:
        return "-"
    s = f"{abs(d):,.2f}"
    return f"({s})" if d < 0 else s


def money_value(value) -> str | None:
    """An amount as a number, at the two decimals the money format prints: ``3600.00`` · ``-1234.50``.
    None for a zero (it prints as a dash: there is no number on the page) and for what is not a number."""
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return None if d == 0 else format(d, "f")


def _identity_text(value) -> str | None:
    import datetime as _dt

    if value is None or isinstance(value, (_dt.date, _dt.datetime, bool)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = " ".join(str(value).split())
    return s or None


def print_rows(path: str, limit: int = 3000) -> list[dict]:
    """Every row of every sheet that carries an amount, as the print proof looks for it on the rendered
    pages: the words of its identity columns (the repeated columns; the first column on the face), each
    of its amounts as a number (``values``) and as the money format prints it (``amounts``). The runner
    prints the finished workbook and asserts that each row's identity and amounts are on one page."""
    from openpyxl import load_workbook

    wb = load_workbook(path)
    out: list[dict] = []
    for i, ws in enumerate(wb.worksheets):
        ident = title_cols(ws) or [1]
        top = (_header_row(ws) or 0) + 1
        for r in range(top, (ws.max_row or 0) + 1):
            money = [c for c in ws[r] if _money(c)]
            if not money:
                continue
            amounts = [money_text(c.value) for c in money]
            # AM14: an amount is stated as a number. The runner reads the numbers a page prints and looks
            # for these among them; the printed form above stays for a runner older than this engine.
            values = [v for v in (money_value(c.value) for c in money) if v is not None]
            # … and a row is named by its words: a summary row holds its amount in a repeated column, and
            # naming the row by it (3600, as Python writes the cell) was looking for what no page prints
            named = [ws.cell(row=r, column=c) for c in ident]
            words = [t for t in (_identity_text(c.value) for c in named if not _money(c)) if t]
            if not words:
                continue
            out.append({"sheet": ws.title, "row": r, "identity": words, "amounts": amounts, "values": values})
            if len(out) >= limit:
                return out
    return out


def check(path: str, lang: str | None = None) -> dict:
    """The print gate on the finished file: every sheet's setup read back and asserted (see the docstring)."""
    import os

    from openpyxl import load_workbook

    def say(thai: str, english: str) -> str:
        return _say(lang, thai, english)

    wb = load_workbook(path)
    name = os.path.basename(path)
    bad: list[str] = []
    n = 0
    for i, ws in enumerate(wb.worksheets):
        face = i == 0
        p = plan(ws, face=face)
        where = say(f"ชีต “{ws.title}”", f"Sheet “{ws.title}”")
        n += 1
        if str(ws.page_setup.paperSize) != str(ws.PAPERSIZE_A4):
            bad.append(say(f"{where}: ไม่ได้ตั้งกระดาษ A4", f"{where}: the paper is not set to A4"))
        if ws.page_setup.orientation != p["orientation"]:
            bad.append(say(f"{where}: แนวกระดาษควรเป็น{'แนวตั้ง' if p['orientation'] == 'portrait' else 'แนวนอน'}",
                           f"{where}: the page should be {'portrait' if p['orientation'] == 'portrait' else 'landscape'}"))
        if not _covers(ws.print_area, ws.max_column or 1, ws.max_row or 1):
            bad.append(say(f"{where}: พื้นที่พิมพ์ไม่ครอบคลุมทั้งชีต", f"{where}: the print area does not cover the whole sheet"))
        fitted = bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage) \
            and ws.page_setup.fitToWidth in (1, "1")
        if face:
            if not (fitted and ws.page_setup.fitToHeight in (1, "1")):
                bad.append(say(f"{where}: หน้าสรุปต้องพิมพ์จบในหน้าเดียว", f"{where}: the summary must print on one page"))
            elif p["scale"] < MIN_SCALE:
                bad.append(say(f"{where}: หน้าสรุปยาวเกินกว่าจะพิมพ์ในหน้าเดียวแล้วยังอ่านได้ (ย่อเหลือ {p['scale']:.0%})",
                               f"{where}: the summary is too long to print on one page and stay readable (scaled to {p['scale']:.0%})"))
        elif p["fit_width"]:
            if not fitted:
                bad.append(say(f"{where}: ต้องย่อให้พอดีความกว้างกระดาษ เพื่อให้รายการกับจำนวนเงินอยู่หน้าเดียวกัน",
                               f"{where}: must be fitted to the page width, so that each item is on the same page as its amounts"))
        elif not ws.print_title_cols:
            bad.append(say(f"{where}: กว้างเกินหนึ่งหน้า แต่ไม่ได้พิมพ์คอลัมน์ระบุรายการซ้ำทุกหน้า",
                           f"{where}: wider than one page, but the identifying columns do not repeat on every page"))
        elif float(ws.page_setup.scale or 100) / 100 < MIN_SCALE:
            bad.append(say(f"{where}: ย่อเหลือ {float(ws.page_setup.scale) / 100:.0%} เล็กเกินกว่าจะอ่านได้เมื่อพิมพ์",
                           f"{where}: scaled to {float(ws.page_setup.scale) / 100:.0%}, too small to read in print"))
        if not face:
            bad += _identity_beside_amounts(ws, where, lang)
        header = _header_row(ws)
        if header and not face and str(ws.print_title_rows or "").replace("$", "") != f"{header}:{header}":
            bad.append(say(f"{where}: หัวตารางไม่พิมพ์ซ้ำทุกหน้า", f"{where}: the header row does not repeat on every page"))
        foot = (ws.oddFooter.left.text or "") + " " + (ws.oddFooter.right.text or "")
        if "&P" not in foot or "&N" not in foot or name not in foot:
            bad.append(say(f"{where}: ทุกหน้าต้องมีชื่อเอกสารและเลขหน้า x / y", f"{where}: every page must carry the document's name and page x / y"))
    label = say("พิมพ์ได้: หน้าสรุปหน้าเดียว รายการอยู่หน้าเดียวกับยอด มีเลขหน้า",
                "Prints: the summary on one page, each item on the same page as its amounts, with page numbers")
    if bad:
        more = say(f" (และอีก {len(bad) - 4} ข้อ)", f" (and {len(bad) - 4} more)") if len(bad) > 4 else ""
        return _check("xlsx.print", False, "; ".join(bad[:4]) + more, label, n)
    return _check("xlsx.print", True, say(
        f"ตั้งค่าการพิมพ์ครบ {n} ชีต: A4 หน้าสรุปหน้าเดียว หัวตารางซ้ำทุกหน้า รายการอยู่หน้าเดียวกับยอด ชื่อเอกสารและเลขหน้าทุกหน้า",
        f"Page setup complete on {n} sheets: A4, the summary on one page, the header row repeated on every page, each item on the same "
        "page as its amounts, the document's name and a page number on every page"), label, n)


def companions(wb) -> list[tuple]:
    """(main sheet, companion) pairs of a workbook: a companion is named "<main> · <what>" (``COMPANION``)."""
    out = []
    for ws in wb.worksheets:
        main = ws.title.split(COMPANION)[0]
        if main != ws.title and main in wb.sheetnames:
            out.append((wb[main], ws))
    return out


def _side_by_side(main, aside, lang: str | None = None) -> list[str]:
    """AM10: a companion is read beside its main sheet by row number — it repeats the main sheet's identity
    columns: the same headers, and on every row the same text."""
    def say(thai: str, english: str) -> str:
        return _say(lang, thai, english)

    where = say(f"ชีต “{aside.title}”", f"Sheet “{aside.title}”")
    ident = title_cols(main)
    if not ident or title_cols(aside) != ident:
        return [say(f"{where}: คอลัมน์ระบุรายการไม่ใช่ชุดเดียวกับชีต “{main.title}”",
                    f"{where}: its identifying columns are not the same as those of sheet “{main.title}”")]
    top = _header_row(main)
    if top is None or _header_row(aside) != top:
        return [say(f"{where}: หัวตารางไม่อยู่แถวเดียวกับชีต “{main.title}”",
                    f"{where}: its header row is not on the same row as sheet “{main.title}”")]
    for c in ident:
        if _identity_text(aside.cell(row=top, column=c).value) != _identity_text(main.cell(row=top, column=c).value):
            return [say(f"{where}: หัวคอลัมน์ระบุรายการ ({get_column_letter(c)}) ไม่ตรงกับชีต “{main.title}”",
                        f"{where}: the heading of identifying column {get_column_letter(c)} differs from sheet “{main.title}”")]
    bad: list[str] = []
    for r in range(top + 1, max(main.max_row or 0, aside.max_row or 0) + 1):
        for c in ident:
            a, b = _identity_text(main.cell(row=r, column=c).value), _identity_text(aside.cell(row=r, column=c).value)
            if a != b:
                bad.append(say(f"{where} แถว {r}: คอลัมน์ {get_column_letter(c)} “{b or 'ว่าง'}” ไม่ตรงกับชีต “{main.title}” “{a or 'ว่าง'}”",
                               f"{where} row {r}: column {get_column_letter(c)} “{b or 'blank'}” differs from sheet “{main.title}” “{a or 'blank'}”"))
                break
    return bad


def one_across(path: str, lang: str | None = None) -> dict:
    """AM10's rule as a gate check: every sheet prints one page across (``bands`` gives one band), so pages
    only run downward and a reader follows one line on one page; a companion sheet lines up with its main
    sheet row for row (``_side_by_side``). Fails naming the sheet and how many pages across it runs."""
    from openpyxl import load_workbook

    def say(thai: str, english: str) -> str:
        return _say(lang, thai, english)

    wb = load_workbook(path)
    bad: list[str] = []
    n = 0
    for ws in wb.worksheets:
        n += 1
        pages = bands(ws)
        if len(pages) > 1:
            bad.append(say(f"ชีต “{ws.title}” พิมพ์กว้าง {len(pages)} หน้า ({ws.max_column} คอลัมน์) ต้องกว้างหน้าเดียว: "
                           "ย้ายคอลัมน์หลักฐานไปชีตคู่",
                           f"Sheet “{ws.title}” prints {len(pages)} pages across ({ws.max_column} columns) and must be one page wide: "
                           "move the evidence columns to a companion sheet"))
    for main, aside in companions(wb):
        bad += _side_by_side(main, aside, lang)
    label = say("ทุกชีตพิมพ์กว้างหน้าเดียว อ่านรายการหนึ่งบรรทัดจบในหน้าเดียว", "Every sheet prints one page wide; one line is read on one page")
    if bad:
        more = say(f" (และอีก {len(bad) - 4} ข้อ)", f" (and {len(bad) - 4} more)") if len(bad) > 4 else ""
        return _check("xlsx.print.one_across", False, "; ".join(bad[:4]) + more, label, n)
    k = len(companions(wb))
    pair = say(f" ชีตคู่ {k} ชีตเริ่มด้วยคอลัมน์ระบุรายการเดียวกับชีตหลักทุกแถว",
               f" The {k} companion sheets begin with the same identifying columns as their main sheet on every row.") if k else ""
    return _check("xlsx.print.one_across", True, say(f"ทุกชีต ({n}) พิมพ์กว้างหน้าเดียว หน้าต่อกันลงล่างเท่านั้น{pair}",
                                                     f"Every sheet ({n}) prints one page wide; pages only run downward.{pair}"), label, n)


def checks(path: str, lang: str | None = None) -> list[dict]:
    """The print gate's checks on a finished file: the page setup (``check``) and AM10's one page across."""
    return [check(path, lang), one_across(path, lang)]
