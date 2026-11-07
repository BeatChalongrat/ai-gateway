#!/usr/bin/env python3
"""Excel edit executor — applies a JSON plan to a workbook.

Operates strictly on the COPY passed as <source>; the original
upload is never touched. Output is written verbatim to <output>; the
Go caller (ExcelService.ExecutePlan) uploads that file to storage and
runs the validator before exposing a download URL.

Plan shape:
  {
    "operation": "create_workbook" | "modify_workbook",
    "actions": [ { "type": "...", ... }, ... ]
  }

Per-action failures are recorded in the change log as "ERROR in
<type>: <message>" instead of aborting the whole plan — the validator
catches downstream damage and the user sees which step failed without
losing the rest of the edit.

Named targets (AM2 templates): a customer's template names its input
cells ("CompanyName" = B2, "LinesTable" = A10:C13). The write_names action
fills them by defined name (workbook- or sheet-scoped) or by a
sheet-qualified reference, and write_cells accepts a defined name where it
takes a coordinate. A named write fills input cells only — it refuses a
formula cell, a locked cell of a protected sheet and the inside of a merge,
and reports each refusal under "named" in the result. Coordinate writes are
unchanged: they do exactly what the plan says, as before.

Usage:  python3 excel_executor.py <plan.json> <source.xlsx> <output.xlsx>
Output: JSON result on stdout
"""

import json
import re
import sys
from typing import Any

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.chart import AreaChart, BarChart, LineChart, PieChart, Reference, ScatterChart
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter  # noqa: F401 — re-exported for future actions
from openpyxl.utils.cell import range_boundaries

try:  # openpyxl >= 3.1 keeps array / data-table formulas as objects
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

    _FORMULA_OBJECTS: tuple = (ArrayFormula, DataTableFormula)
except ImportError:  # pragma: no cover — older openpyxl
    _FORMULA_OBJECTS = ()


CHART_CLASSES = {
    "bar": BarChart,
    "column": BarChart,
    "line": LineChart,
    "pie": PieChart,
    "area": AreaChart,
    "scatter": ScatterChart,
}


def _ws_for(wb, sheet_name: str):
    """Lookup a sheet; raises a friendly KeyError that the action loop
    converts into a "ERROR in <type>: sheet not found" change-log
    entry without aborting the whole plan."""
    if sheet_name not in wb.sheetnames:
        raise KeyError(f"sheet '{sheet_name}' not found")
    return wb[sheet_name]


def _split_ref(wb, ref: str):
    """Resolve a "Sheet!A1:B10" or "A1:B10" reference into
    (worksheet, start_cell, end_cell). When the sheet prefix is
    omitted the caller's worksheet must be substituted by the
    action handler — we return None for ws in that case."""
    if "!" in ref:
        sheet_name, cell_range = ref.split("!", 1)
        # Excel allows 'My Sheet'! when the name has spaces; strip
        # surrounding quotes so the lookup hits.
        sheet_name = sheet_name.strip().strip("'")
        ws = wb[sheet_name]
    else:
        ws = None
        cell_range = ref
    parts = cell_range.split(":")
    start = parts[0]
    end = parts[1] if len(parts) > 1 else parts[0]
    return ws, start, end


def _apply_create_sheet(wb, action: dict[str, Any]) -> str:
    pos = action.get("position", len(wb.sheetnames))
    wb.create_sheet(action["sheet_name"], pos)
    return f"Created sheet '{action['sheet_name']}' at position {pos}"


def _apply_delete_sheet(wb, action: dict[str, Any]) -> str:
    name = action["sheet_name"]
    if name in wb.sheetnames:
        del wb[name]
        return f"Deleted sheet '{name}'"
    return f"Skipped delete_sheet — '{name}' did not exist"


def _apply_rename_sheet(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    ws.title = action["new_name"]
    return f"Renamed sheet '{action['sheet_name']}' to '{action['new_name']}'"


def _style_cell(cell, spec: dict[str, Any]) -> None:
    # Font: collapse bold + font_size into a single Font(); fall
    # back to current font otherwise so we don't accidentally
    # nuke existing styling when only one of the two is set.
    bold = spec.get("bold")
    font_size = spec.get("font_size")
    if bold or font_size:
        cell.font = Font(
            bold=bool(bold),
            size=font_size if font_size else 11,
        )
    if spec.get("number_format"):
        cell.number_format = spec["number_format"]


def _apply_write_cells(wb, action: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    cells = action.get("cells", []) or []
    # A spec may name its cell ("name", or a defined name in "cell"); every
    # other spec is a coordinate on sheet_name, handled exactly as before.
    named = [_names_a_target(wb, spec, action.get("sheet_name")) for spec in cells]
    ws = None
    if "sheet_name" in action or not cells or not all(named):
        ws = _ws_for(wb, action["sheet_name"])
    refused: dict[str, str] = {}
    for spec, is_named in zip(cells, named):
        if is_named:
            _write_named_spec(wb, spec, action.get("sheet_name"), report, refused)
            continue
        cell = ws[spec["cell"]]
        if "value_formula" in spec:
            cell.value = spec["value_formula"]
        elif "value" in spec:
            cell.value = spec["value"]
        _style_cell(cell, spec)
    where = f" in '{action['sheet_name']}'" if "sheet_name" in action else ""
    return f"Wrote {len(cells) - len(refused)} cells{where}" + _refused_suffix(refused)


def _apply_write_table(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    start = action.get("start_cell", "A1")
    anchor = ws[start]
    row_offset = anchor.row
    col_offset = anchor.column

    headers = action.get("headers", []) or []
    for i, header in enumerate(headers):
        cell = ws.cell(row=row_offset, column=col_offset + i, value=header)
        cell.font = Font(bold=True)

    inline = action.get("data")
    if inline:
        for r_idx, row_data in enumerate(inline):
            for c_idx, value in enumerate(row_data):
                ws.cell(
                    row=row_offset + 1 + r_idx,
                    column=col_offset + c_idx,
                    value=value,
                )
        return (
            f"Wrote table ({len(inline)} rows) at {start} in '{action['sheet_name']}'"
        )
    return f"Wrote table headers at {start} in '{action['sheet_name']}'"


def _apply_create_chart(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    chart_type = (action.get("chart_type") or "bar").lower()
    cls = CHART_CLASSES.get(chart_type, BarChart)
    chart = cls()
    chart.title = action.get("title") or "Chart"
    chart.width = action.get("width", 15)
    chart.height = action.get("height", 10)

    data_range = action.get("data_range")
    if data_range:
        data_ws, start, end = _split_ref(wb, data_range)
        data_ws = data_ws or ws
        data = Reference(
            data_ws,
            min_col=data_ws[start].column,
            min_row=data_ws[start].row,
            max_col=data_ws[end].column,
            max_row=data_ws[end].row,
        )
        chart.add_data(data, titles_from_data=True)

    cat_range = action.get("category_range")
    if cat_range:
        cat_ws, c_start, c_end = _split_ref(wb, cat_range)
        cat_ws = cat_ws or ws
        # The chart's category labels skip the header row — start one
        # row down from the range's anchor.
        cats = Reference(
            cat_ws,
            min_col=cat_ws[c_start].column,
            min_row=cat_ws[c_start].row + 1,
            max_row=cat_ws[c_end].row,
        )
        chart.set_categories(cats)

    anchor = action.get("anchor_cell", "A1")
    ws.add_chart(chart, anchor)
    return (
        f"Created {chart_type} chart '{chart.title}' "
        f"in '{action['sheet_name']}' at {anchor}"
    )


def _apply_format_range(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    rng = action["range"]
    fill = None
    font = None

    if "fill_color" in action:
        fill = PatternFill(
            start_color=action["fill_color"],
            end_color=action["fill_color"],
            fill_type="solid",
        )
    if action.get("bold") or "font_color" in action or "font_size" in action:
        font = Font(
            bold=bool(action.get("bold", False)),
            color=action.get("font_color"),
            size=action.get("font_size", 11),
        )

    for row in ws[rng]:
        for cell in row:
            if fill is not None:
                cell.fill = fill
            if font is not None:
                cell.font = font
    return f"Formatted range {rng} in '{action['sheet_name']}'"


def _apply_set_column_width(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    cols = action.get("columns", {}) or {}
    for col, width in cols.items():
        ws.column_dimensions[col].width = width
    return f"Set {len(cols)} column widths in '{action['sheet_name']}'"


def _apply_merge_cells(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    ws.merge_cells(action["range"])
    return f"Merged cells {action['range']} in '{action['sheet_name']}'"


def _apply_auto_filter(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    ws.auto_filter.ref = action["range"]
    return f"Added filter to {action['range']} in '{action['sheet_name']}'"


def _apply_freeze_panes(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    ws.freeze_panes = action["cell"]
    return f"Froze panes at {action['cell']} in '{action['sheet_name']}'"


# ─── Named targets (AM2 templates) ─────────────────────────────
# Reasons a named write is refused. The target is reported, never partly
# written, and nothing else in the workbook is touched.
REFUSE_FORMULA = "formula_cell"        # the cell is computed by a formula
REFUSE_PROTECTED = "protected_sheet"   # a locked cell of a protected sheet
REFUSE_MERGED = "merged_cell"          # inside a merge, not its top-left cell
REFUSE_UNKNOWN = "unknown_name"        # no such name / reference
REFUSE_SHEET = "unknown_sheet"         # the name points at a missing sheet
REFUSE_AMBIGUOUS = "ambiguous_name"    # several sheets define it, none chosen
REFUSE_NOT_RANGE = "not_a_cell_range"  # a constant, a formula, a whole column
REFUSE_MULTI_AREA = "not_a_single_range"
REFUSE_SHAPE = "shape_mismatch"        # a list for one cell, one value for a range
REFUSE_ROWS = "too_many_rows"          # more rows than the named region holds
REFUSE_COLUMNS = "too_many_columns"


class _Refused(Exception):
    def __init__(self, reason: str, cell: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.cell = cell


_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NUMBER_RE = re.compile(r"^(\()?([+-])?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\))?$")


def as_number(value: Any) -> Any:
    """The number a value spells, for a cell that holds numbers.

    "12,850.70" -> 12850.7, "4" -> 4, "(1,200.00)" -> -1200.0, Thai digits
    too. A digit string with a leading zero, or of more than 9 digits with no
    separators, is an identifier (tax ID, phone, account number) and stays
    text; so does anything that is not a plain number. Non-strings are
    returned as they are."""
    if not isinstance(value, str):
        return value
    m = _NUMBER_RE.match(value.strip().translate(_THAI_DIGITS))
    if not m:
        return value
    opening, sign, whole, frac, closing = m.groups()
    if bool(opening) != bool(closing) or (opening and sign):
        return value
    digits = whole.replace(",", "")
    if len(digits) > 1 and digits[0] == "0":
        return value
    if "," not in whole and not frac and len(digits) > 9:
        return value
    number = float(digits + frac) if frac else int(digits)
    return -number if (opening or sign == "-") else number


def _get_name(names, key: str):
    """A DefinedName from a DefinedNameDict — exact, then ignoring case
    (Excel names are case-insensitive)."""
    dn = names.get(key)
    if dn is not None:
        return dn
    low = key.lower()
    for name, candidate in names.items():
        if name.lower() == low:
            return candidate
    return None


def _unquote_sheet(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _find_defined_name(wb, key: str, sheet_name: str | None = None):
    """The DefinedName a key means, or None. "Sheet!Name" picks that sheet's
    own name; a bare name is the sheet_name's own name, else the workbook's,
    else the one sheet that defines it (_Refused when several do)."""
    key = str(key).strip()
    if "!" in key:
        sheet_part, local = key.rsplit("!", 1)
        title = _unquote_sheet(sheet_part)
        if title not in wb.sheetnames or not hasattr(wb[title], "defined_names"):
            return None
        return _get_name(wb[title].defined_names, local)
    if sheet_name and sheet_name in wb.sheetnames and hasattr(wb[sheet_name], "defined_names"):
        dn = _get_name(wb[sheet_name].defined_names, key)
        if dn is not None:
            return dn
    dn = _get_name(wb.defined_names, key)
    if dn is not None:
        return dn
    local = [ws for ws in wb.worksheets if _get_name(ws.defined_names, key) is not None]
    if len(local) > 1:
        raise _Refused(REFUSE_AMBIGUOUS)
    return _get_name(local[0].defined_names, key) if local else None


def _name_bounds(wb, dn):
    """(worksheet, min_col, min_row, max_col, max_row) a name covers."""
    if dn.type != "RANGE":
        raise _Refused(REFUSE_NOT_RANGE)
    dests = list(dn.destinations)
    if len(dests) != 1:
        raise _Refused(REFUSE_MULTI_AREA if dests else REFUSE_NOT_RANGE)
    title, coord = dests[0]
    if title not in wb.sheetnames:
        raise _Refused(REFUSE_SHEET)
    bounds = range_boundaries(coord.replace("$", ""))
    if None in bounds:
        raise _Refused(REFUSE_NOT_RANGE)
    return (wb[title], *bounds)


def resolve_target(wb, key: str, sheet_name: str | None = None):
    """Where a defined name or a sheet-qualified reference ("Sheet1!B1",
    "'My Sheet'!A10:C13") points: (worksheet, min_col, min_row, max_col,
    max_row). Raises _Refused(reason) when it is not one writable range."""
    dn = _find_defined_name(wb, key, sheet_name)
    if dn is not None:
        return _name_bounds(wb, dn)
    key = str(key).strip()
    if "!" not in key:
        raise _Refused(REFUSE_UNKNOWN)
    sheet_part, coord = key.rsplit("!", 1)
    title = _unquote_sheet(sheet_part)
    if title not in wb.sheetnames:
        raise _Refused(REFUSE_SHEET)
    try:
        bounds = range_boundaries(coord.replace("$", ""))
    except (TypeError, ValueError):
        raise _Refused(REFUSE_UNKNOWN) from None
    if None in bounds:
        raise _Refused(REFUSE_NOT_RANGE)
    return (wb[title], *bounds)


def peek_cell(ws, row: int, column: int):
    """The cell the sheet holds at (row, column), or None for one it has
    never stored — without creating it (ws.cell() would)."""
    return ws._cells.get((row, column))


def cell_refusal(ws, cell) -> str | None:
    """Why a named write may not change this cell (None: a cell the sheet
    has never stored), or None when it may."""
    if isinstance(cell, MergedCell):
        return REFUSE_MERGED
    if cell is not None and (cell.data_type == "f" or (
            _FORMULA_OBJECTS and isinstance(cell.value, _FORMULA_OBJECTS))):
        return REFUSE_FORMULA
    # A cell never stored has the default style: locked.
    if ws.protection.sheet and (cell is None or cell.protection.locked):
        return REFUSE_PROTECTED
    return None


def _grid(value: Any, rows: int, cols: int, columns: list[str] | None = None) -> list[list[Any]]:
    """The value as rows of cell values for a rows x cols region.

    One value fills one cell. A list fills a region row by row: a list of
    dicts (columns in `columns` order, else in first-seen key order), a list
    of lists, or a list of values down one column / across one row."""
    if not isinstance(value, (list, tuple)):
        if rows * cols != 1:
            raise _Refused(REFUSE_SHAPE)
        return [[value]]
    items = list(value)
    if rows * cols == 1:
        raise _Refused(REFUSE_SHAPE)
    if items and all(isinstance(it, dict) for it in items):
        keys = list(columns or [])
        if not keys:
            for it in items:
                keys.extend(k for k in it if k not in keys)
        grid = [[it.get(k) for k in keys] for it in items]
    elif all(isinstance(it, (list, tuple)) for it in items):
        grid = [list(it) for it in items]
    elif not any(isinstance(it, (list, tuple, dict)) for it in items):
        if cols == 1:
            grid = [[it] for it in items]
        elif rows == 1:
            grid = [items]
        else:
            raise _Refused(REFUSE_SHAPE)
    else:
        raise _Refused(REFUSE_SHAPE)
    if len(grid) > rows:
        raise _Refused(REFUSE_ROWS)
    if any(len(r) > cols for r in grid):
        raise _Refused(REFUSE_COLUMNS)
    return grid


def _put(cell, value: Any, numbers: bool, formula: bool) -> None:
    if numbers and cell.number_format != "@":
        value = as_number(value)
    cell.value = value
    if not formula and isinstance(value, str) and value.startswith("="):
        # A value read from a document is text even when it starts with '='.
        cell.data_type = "s"


def write_name(wb, key: str, value: Any, sheet_name: str | None = None, *, numbers: bool = True,
               columns: list[str] | None = None, formula: bool = False) -> dict[str, Any]:
    """Fill one defined name or sheet-qualified reference.

    All or nothing: every cell the value reaches is checked first
    (cell_refusal) and a refused target writes no cell. None in a row leaves
    that cell as it is. Numbers given as text become numbers (as_number)
    unless numbers=False or the cell is formatted as text. Returns
    {"target", "ref", "status": "written"|"refused", "reason", "cells"}."""
    entry: dict[str, Any] = {"target": key, "ref": None, "status": "refused", "reason": None, "cells": []}
    try:
        ws, c1, r1, c2, r2 = resolve_target(wb, key, sheet_name)
        entry["ref"] = f"{ws.title}!{get_column_letter(c1)}{r1}" + (
            f":{get_column_letter(c2)}{r2}" if (c1, r1) != (c2, r2) else "")
        planned = []
        for i, row in enumerate(_grid(value, r2 - r1 + 1, c2 - c1 + 1, columns)):
            for j, v in enumerate(row):
                if v is None:
                    continue
                why = cell_refusal(ws, peek_cell(ws, r1 + i, c1 + j))
                if why:
                    raise _Refused(why, f"{ws.title}!{get_column_letter(c1 + j)}{r1 + i}")
                planned.append((r1 + i, c1 + j, v))
    except _Refused as r:
        entry["reason"] = r.reason
        if r.cell:
            entry["cell"] = r.cell
        return entry
    for r, c, v in planned:
        cell = ws.cell(row=r, column=c)
        _put(cell, v, numbers, formula)
        entry["cells"].append(f"{ws.title}!{cell.coordinate}")
    entry["status"] = "written"
    return entry


def write_names(wb, values: dict[str, Any], sheet_name: str | None = None, *, numbers: bool = True,
                columns: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Fill several named targets: {"written": [keys], "refused": {key:
    reason}, "targets": [write_name entries]}."""
    out: dict[str, Any] = {"written": [], "refused": {}, "targets": []}
    for key, value in (values or {}).items():
        entry = write_name(wb, key, value, sheet_name, numbers=numbers, columns=(columns or {}).get(key))
        out["targets"].append(entry)
        if entry["status"] == "written":
            out["written"].append(key)
        else:
            out["refused"][key] = entry["reason"]
    return out


def list_defined_names(wb) -> list[dict[str, Any]]:
    """Every user-facing defined name: {"key" (what write_name takes),
    "name", "scope" ("workbook" or the sheet), "ref", "cells", "fillable",
    "reason" (why not, e.g. formula_cell), "formula"}. Hidden and Excel's
    own reserved names are left out."""
    found = [(n, dn, None) for n, dn in wb.defined_names.items()]
    for ws in wb.worksheets:
        found.extend((n, dn, ws.title) for n, dn in ws.defined_names.items())
    out: list[dict[str, Any]] = []
    for name, dn, scope in found:
        if dn.hidden or dn.is_reserved:
            continue
        key = f"{scope}!{name}" if scope else name
        entry: dict[str, Any] = {"key": key, "name": name, "scope": scope or "workbook", "ref": dn.attr_text,
                                 "cells": 0, "fillable": False, "reason": None, "formula": False}
        try:
            ws, c1, r1, c2, r2 = _name_bounds(wb, dn)
            entry["ref"] = f"{ws.title}!{get_column_letter(c1)}{r1}" + (
                f":{get_column_letter(c2)}{r2}" if (c1, r1) != (c2, r2) else "")
            entry["cells"] = (c2 - c1 + 1) * (r2 - r1 + 1)
            reasons = [cell_refusal(ws, peek_cell(ws, r, c))
                       for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]
            entry["formula"] = REFUSE_FORMULA in reasons
            blocking = [r for r in reasons if r]
            entry["reason"] = blocking[0] if blocking else None
            entry["fillable"] = not blocking
        except _Refused as r:
            entry["reason"] = r.reason
        out.append(entry)
    return out


def _names_a_target(wb, spec: Any, sheet_name: str | None) -> bool:
    """True when a write_cells spec names its cell — "name", or a defined
    name in "cell" — instead of giving a coordinate."""
    if not isinstance(spec, dict):
        return False
    if "name" in spec:
        return True
    ref = spec.get("cell")
    if not isinstance(ref, str) or not ref.strip():
        return False
    try:
        range_boundaries(ref)
        return False  # a coordinate: the unchanged path
    except (TypeError, ValueError):
        pass
    try:
        return _find_defined_name(wb, ref, sheet_name) is not None
    except _Refused:
        return True  # ambiguous: write_name reports it
    except Exception:  # noqa: BLE001 — never fail here; the coordinate path reports it as before
        return False


def _write_named_spec(wb, spec: dict[str, Any], sheet_name: str | None, report: dict[str, Any] | None,
                      refused: dict[str, str]) -> None:
    key = spec.get("name") or spec.get("cell")
    if "value_formula" in spec:
        entry = write_name(wb, key, spec["value_formula"], sheet_name, numbers=False, formula=True)
    elif "value" in spec:
        entry = write_name(wb, key, spec["value"], sheet_name)
    else:  # style only
        entry = {"target": key, "status": "written", "cells": []}
        try:
            ws, c1, r1, c2, r2 = resolve_target(wb, key, sheet_name)
            entry["cells"] = [f"{ws.title}!{c.coordinate}" for row in ws.iter_rows(
                min_row=r1, max_row=r2, min_col=c1, max_col=c2) for c in row if not isinstance(c, MergedCell)]
        except _Refused as r:
            entry = {"target": key, "status": "refused", "reason": r.reason, "cells": []}
    if entry["status"] == "written":
        for ref in entry["cells"]:
            title, coord = ref.rsplit("!", 1)
            _style_cell(wb[title][coord], spec)
        if report is not None:
            report["written"].append(key)
    else:
        refused[key] = entry["reason"]
        if report is not None:
            report["refused"][key] = entry["reason"]


def _refused_suffix(refused: dict[str, str]) -> str:
    if not refused:
        return ""
    return "; refused " + ", ".join(f"{k} ({v})" for k, v in refused.items())


def _apply_write_names(wb, action: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    """{"type": "write_names", "values": {name_or_ref: value}, "sheet_name"?
    (scope for sheet-level names), "columns"? {name: [dict keys in column
    order]}, "numbers"? (default true: "12,850.70" is written as 12850.7)}"""
    sheet_name = action.get("sheet_name")
    if sheet_name is not None:
        _ws_for(wb, sheet_name)
    res = write_names(wb, action.get("values") or {}, sheet_name,
                      numbers=action.get("numbers", True) is not False, columns=action.get("columns"))
    if report is not None:
        report["written"].extend(res["written"])
        report["refused"].update(res["refused"])
    return f"Wrote {len(res['written'])} named targets" + _refused_suffix(res["refused"])


HANDLERS = {
    "create_sheet": _apply_create_sheet,
    "delete_sheet": _apply_delete_sheet,
    "rename_sheet": _apply_rename_sheet,
    "write_cells": _apply_write_cells,
    "write_table": _apply_write_table,
    "create_chart": _apply_create_chart,
    "format_range": _apply_format_range,
    "set_column_width": _apply_set_column_width,
    "merge_cells": _apply_merge_cells,
    "auto_filter": _apply_auto_filter,
    "freeze_panes": _apply_freeze_panes,
    "write_names": _apply_write_names,
}

# Handlers that also report named writes (execute_plan's "named").
_NAMED_REPORTING = {"write_cells", "write_names"}


def execute_plan(plan_path: str, source_path: str, output_path: str) -> dict[str, Any]:
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    operation = plan.get("operation", "modify_workbook")
    if operation == "create_workbook":
        wb = openpyxl.Workbook()
        if plan.get("actions"):
            # The default "Sheet" is in the way; downstream
            # create_sheet actions will add their own.
            wb.remove(wb.active)
    else:
        wb = openpyxl.load_workbook(source_path)

    changes: list[str] = []
    named: dict[str, Any] = {"written": [], "refused": {}}
    for action in plan.get("actions", []) or []:
        action_type = action.get("type", "")
        handler = HANDLERS.get(action_type)
        if handler is None:
            changes.append(f"ERROR in {action_type or '<missing type>'}: unknown action")
            continue
        try:
            if action_type in _NAMED_REPORTING:
                changes.append(handler(wb, action, named))
            else:
                changes.append(handler(wb, action))
        except Exception as exc:  # noqa: BLE001 — log + continue per action
            changes.append(f"ERROR in {action_type}: {exc}")

    wb.save(output_path)
    wb.close()

    result = {
        "success": True,
        "output_file": output_path,
        "changes": changes,
        "action_count": len(plan.get("actions", []) or []),
    }
    if named["written"] or named["refused"]:
        # Only a plan that wrote by name carries it; every other result is as before.
        result["named"] = named
    return result


def main() -> int:
    if len(sys.argv) < 4:
        print(
            json.dumps(
                {"error": "Usage: excel_executor.py <plan.json> <source.xlsx> <output.xlsx>"}
            )
        )
        return 1
    try:
        result = execute_plan(sys.argv[1], sys.argv[2], sys.argv[3])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — surface every error to Go
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__, "success": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
