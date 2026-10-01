"""Working papers that print (AM8).

The page setup of every sheet is decided from the sheet itself — how many
columns, how wide, how many rows — not from a layout fixed per agent:

* paper A4; portrait when the columns fit across a portrait page, landscape
  otherwise;
* the columns are fitted to the page width when that keeps the print readable
  (scale ≥ ``MIN_SCALE``); a sheet too wide for that prints across pages with
  its identifying columns repeated on every page instead, so a row's
  identifier is always on the same page as its amounts;
* the header row (the row the panes are frozen under) repeats on every page;
* the face of a working paper (its first sheet) prints on one page;
* the print area is the sheet's used range;
* every page carries the document's name, the sheet and "หน้า x / y".

``check`` reads those settings back out of the finished file and asserts
them, with the same arithmetic — it is the print gate, deterministic, and it
does not render (Excel's own pagination can still differ by a row).
"""

from __future__ import annotations

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .reread import check as _check

# A4 in inches, and the margins set here
PAPER_W, PAPER_H, MARGIN_LR, MARGIN_TB = 8.27, 11.69, 0.5, 0.6
MIN_SCALE = 0.6           # below this a fitted page is too small to read: repeat the identifiers instead
DEFAULT_WIDTH = 8.43      # Excel's column width when none is set
ROW_PT = 15.0             # Excel's default row height


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
    return out


def setup(ws, *, doc_name: str, face: bool = False, id_cols: int = 1) -> dict:
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
    last_col = get_column_letter(ws.max_column or 1)
    ws.print_area = f"A1:{last_col}{ws.max_row or 1}"
    header = _header_row(ws)
    if header and not face:
        ws.print_title_rows = f"{header}:{header}"
    if p["repeat_cols"]:
        ws.print_title_cols = f"A:{get_column_letter(max(1, id_cols))}"
    ws.oddFooter.left.text = f"{doc_name} · {ws.title}"
    ws.oddFooter.left.size = 8
    ws.oddFooter.right.text = "หน้า &P / &N"
    ws.oddFooter.right.size = 8
    ws.evenFooter.left.text, ws.evenFooter.right.text = ws.oddFooter.left.text, ws.oddFooter.right.text
    ws.print_options.horizontalCentered = False
    return p


def setup_workbook(wb, *, doc_name: str, id_cols: dict | None = None) -> dict:
    """Every sheet of a working paper: the first is its face (one page)."""
    return {ws.title: setup(ws, doc_name=doc_name, face=(i == 0), id_cols=(id_cols or {}).get(ws.title, 1))
            for i, ws in enumerate(wb.worksheets)}


def _header_row(ws) -> int | None:
    """The table's header row: the row the panes are frozen under (the writers freeze below the header)."""
    fp = ws.freeze_panes
    if not fp:
        return None
    from openpyxl.utils.cell import coordinate_from_string

    _, row = coordinate_from_string(fp)
    return row - 1 if row > 1 else None


def _covers(area, max_col: int, max_row: int) -> bool:
    """The print area reaches the last used column and row (a reopened sheet may count one row fewer)."""
    import re

    from openpyxl.utils import column_index_from_string

    m = re.search(r"\$?([A-Z]+)\$?(\d+)\s*$", str(area or ""))
    return bool(m) and column_index_from_string(m.group(1)) >= max_col and int(m.group(2)) >= max_row


def check(path: str) -> dict:
    """The print gate on the finished file: every sheet's setup read back and asserted (see the docstring)."""
    import os

    from openpyxl import load_workbook

    wb = load_workbook(path)
    name = os.path.basename(path)
    bad: list[str] = []
    n = 0
    for i, ws in enumerate(wb.worksheets):
        face = i == 0
        p = plan(ws, face=face)
        where = f"ชีต “{ws.title}”"
        n += 1
        if str(ws.page_setup.paperSize) != str(ws.PAPERSIZE_A4):
            bad.append(f"{where}: ไม่ได้ตั้งกระดาษ A4")
        if ws.page_setup.orientation != p["orientation"]:
            bad.append(f"{where}: แนวกระดาษควรเป็น{'แนวตั้ง' if p['orientation'] == 'portrait' else 'แนวนอน'}")
        if not _covers(ws.print_area, ws.max_column or 1, ws.max_row or 1):
            bad.append(f"{where}: พื้นที่พิมพ์ไม่ครอบคลุมทั้งชีต")
        fitted = bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage) \
            and ws.page_setup.fitToWidth in (1, "1")
        if face:
            if not (fitted and ws.page_setup.fitToHeight in (1, "1")):
                bad.append(f"{where}: หน้าสรุปต้องพิมพ์จบในหน้าเดียว")
            elif p["scale"] < MIN_SCALE:
                bad.append(f"{where}: หน้าสรุปยาวเกินกว่าจะพิมพ์ในหน้าเดียวแล้วยังอ่านได้ (ย่อเหลือ {p['scale']:.0%})")
        elif p["fit_width"]:
            if not fitted:
                bad.append(f"{where}: ต้องย่อให้พอดีความกว้างกระดาษ เพื่อให้รายการกับจำนวนเงินอยู่หน้าเดียวกัน")
        elif not ws.print_title_cols:
            bad.append(f"{where}: กว้างเกินหนึ่งหน้า แต่ไม่ได้พิมพ์คอลัมน์ระบุรายการซ้ำทุกหน้า")
        header = _header_row(ws)
        if header and not face and str(ws.print_title_rows or "").replace("$", "") != f"{header}:{header}":
            bad.append(f"{where}: หัวตารางไม่พิมพ์ซ้ำทุกหน้า")
        foot = (ws.oddFooter.left.text or "") + " " + (ws.oddFooter.right.text or "")
        if "&P" not in foot or "&N" not in foot or name not in foot:
            bad.append(f"{where}: ทุกหน้าต้องมีชื่อเอกสารและเลขหน้า x / y")
    if bad:
        more = f" (และอีก {len(bad) - 4} ข้อ)" if len(bad) > 4 else ""
        return _check("xlsx.print", False, "; ".join(bad[:4]) + more, "พิมพ์ได้: หน้าสรุปหน้าเดียว รายการอยู่หน้าเดียวกับยอด มีเลขหน้า", n)
    return _check("xlsx.print", True, f"ตั้งค่าการพิมพ์ครบ {n} ชีต: A4 หน้าสรุปหน้าเดียว หัวตารางซ้ำทุกหน้า "
                  "รายการอยู่หน้าเดียวกับยอด ชื่อเอกสารและเลขหน้าทุกหน้า",
                  "พิมพ์ได้: หน้าสรุปหน้าเดียว รายการอยู่หน้าเดียวกับยอด มีเลขหน้า", n)
