#!/usr/bin/env python3
"""Excel deep inspector — outputs a complete workbook structure as JSON.

Used by services/chat-edge-api ExcelService.InspectWorkbook. The output is
cached in chat.conversation_files.inspection_json so the message
service can feed the structure into the system prompt without
re-shelling on every send.

We load the workbook TWICE (data_only=False, then data_only=True) so the
report can show both the formula text and the last-known computed value
without losing either. The file is never modified — open is read-only by
default in openpyxl.

Usage:  python3 excel_inspector.py <path_to_xlsx>
Output: JSON on stdout (single line, default=str so dates serialise)
"""

import json
import sys
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter


# Sample budgets — bounded so the JSON we send into the LLM system
# prompt stays small even on million-row workbooks. Adjusted via
# experience: 5 sample rows is enough for the LLM to infer schema; 20
# formulas/sheet covers the "show me the formulas" intent without
# truncating typical models.
HEADER_PROBE_DEPTH = 10        # rows scanned looking for the first non-empty row
SAMPLE_ROW_COUNT = 5           # data rows shown verbatim per sheet
FORMULA_SAMPLE_PER_SHEET = 20  # formula cells listed verbatim per sheet


def _detect_header_row(ws) -> int | None:
    """Returns the 1-based row index of the first non-empty row, or None."""
    max_col = ws.max_column or 0
    if max_col == 0:
        return None
    for row_idx in range(1, min(ws.max_row or 1, HEADER_PROBE_DEPTH) + 1):
        row_values = [ws.cell(row=row_idx, column=c).value for c in range(1, max_col + 1)]
        if any(v is not None for v in row_values):
            return row_idx
    return None


def _column_info(ws, header_row: int, col_idx: int) -> dict[str, Any]:
    """Header label + format/formula fingerprint for one column."""
    col_letter = get_column_letter(col_idx)
    header_cell = ws.cell(row=header_row, column=col_idx)

    # Look for a formula in the next few data rows so we can hint the
    # LLM "column D is computed". Stop at first match — we only need
    # a single example to communicate the pattern.
    has_formula = False
    formula_example = None
    max_check = min((ws.max_row or 0) + 1, header_row + 5)
    for check_row in range(header_row + 1, max_check):
        check_cell = ws.cell(row=check_row, column=col_idx)
        if check_cell.value and str(check_cell.value).startswith("="):
            has_formula = True
            formula_example = str(check_cell.value)
            break

    sample_cell = ws.cell(row=min(header_row + 1, ws.max_row or 1), column=col_idx)
    return {
        "letter": col_letter,
        "index": col_idx,
        "header": str(header_cell.value) if header_cell.value else f"Column {col_letter}",
        "has_formula": has_formula,
        "formula_example": formula_example,
        "number_format": (
            sample_cell.number_format if sample_cell.number_format != "General" else None
        ),
        "width": (
            ws.column_dimensions[col_letter].width
            if col_letter in ws.column_dimensions
            else None
        ),
    }


def _numeric_summary(ws_values, header_row: int, col_idx: int) -> dict[str, Any] | None:
    """Sum / min / max / avg for cells in the column that parse as numbers."""
    values: list[float] = []
    for row_idx in range(header_row + 1, (ws_values.max_row or 0) + 1):
        v = ws_values.cell(row=row_idx, column=col_idx).value
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values.append(float(v))
    if not values:
        return None
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "sum": sum(values),
        "avg": round(sum(values) / len(values), 2),
    }


def _chart_title_text(chart) -> str:
    """openpyxl wraps chart titles in a rich-text tree. str(chart.title)
    dumps the whole object hierarchy (Parameters: tx=…) which is
    useless to the LLM and bloats the inspection JSON. Walk the tree
    and pull just the text runs."""
    title = getattr(chart, "title", None)
    if not title:
        return "Untitled"
    if isinstance(title, str):
        return title
    try:
        tx = getattr(title, "tx", None)
        if tx is None:
            return str(title)
        rich = getattr(tx, "rich", None)
        if rich is None:
            return str(title)
        text_chunks: list[str] = []
        for para in rich.p or []:
            for run in para.r or []:
                t = getattr(run, "t", None)
                if t:
                    text_chunks.append(t)
        return "".join(text_chunks).strip() or "Untitled"
    except Exception:
        return "Untitled"


def _defined_names(wb) -> list[str]:
    """Wrap defined-name iteration so older/newer openpyxl don't break us."""
    try:
        # openpyxl >= 3.1 exposes .definedName on DefinedNameDict
        return [str(nr) for nr in wb.defined_names.definedName]
    except AttributeError:
        try:
            return list(wb.defined_names)
        except Exception:
            return []


def inspect_workbook(path: str) -> dict[str, Any]:
    wb = openpyxl.load_workbook(path, data_only=False)
    wb_values = openpyxl.load_workbook(path, data_only=True)

    result: dict[str, Any] = {
        "filename": path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        "sheet_count": len(wb.sheetnames),
        "sheets": [],
        "named_ranges": _defined_names(wb),
        "has_macros": path.lower().endswith(".xlsm"),
        "properties": {
            "creator": wb.properties.creator or "",
            "title": wb.properties.title or "",
            "modified": str(wb.properties.modified) if wb.properties.modified else "",
        },
    }

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        ws_values = wb_values[sheet_name]

        sheet_info: dict[str, Any] = {
            "name": sheet_name,
            "is_hidden": ws.sheet_state == "hidden",
            "dimensions": ws.dimensions,
            "row_count": ws.max_row or 0,
            "col_count": ws.max_column or 0,
            "merged_cells": [str(m) for m in ws.merged_cells.ranges],
            "has_filters": bool(ws.auto_filter and ws.auto_filter.ref),
            "freeze_pane": str(ws.freeze_panes) if ws.freeze_panes else None,
            "charts": [],
            "tables": [],
            "images_count": len(getattr(ws, "_images", []) or []),
            "formulas": [],
            "columns": [],
            "sample_data": [],
            "total_formula_count": 0,
        }

        header_row = _detect_header_row(ws)
        max_col = ws.max_column or 0

        if header_row and max_col:
            for col_idx in range(1, max_col + 1):
                col_info = _column_info(ws, header_row, col_idx)
                summary = _numeric_summary(ws_values, header_row, col_idx)
                if summary is not None:
                    col_info["numeric_summary"] = summary
                sheet_info["columns"].append(col_info)

        # Sample data — first N data rows under the header, using
        # data_only so formulas show as their last computed value (not
        # the formula text, which would just duplicate column info).
        if header_row and (ws_values.max_row or 0) > header_row:
            last_row = min(ws_values.max_row, header_row + SAMPLE_ROW_COUNT)
            for row_idx in range(header_row + 1, last_row + 1):
                row_data: dict[str, Any] = {}
                for col_idx in range(1, (ws_values.max_column or 0) + 1):
                    if col_idx - 1 < len(sheet_info["columns"]):
                        header = sheet_info["columns"][col_idx - 1]["header"]
                    else:
                        header = f"Col{col_idx}"
                    val = ws_values.cell(row=row_idx, column=col_idx).value
                    row_data[header] = str(val) if val is not None else None
                sheet_info["sample_data"].append(row_data)

        # Formula sample. We walk every row to get an accurate
        # total_formula_count but only KEEP the first N for the JSON.
        formula_count = 0
        max_row = ws.max_row or 0
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                if cell.value and isinstance(cell.value, str) and cell.value.startswith("="):
                    if formula_count < FORMULA_SAMPLE_PER_SHEET:
                        sheet_info["formulas"].append(
                            {"cell": cell.coordinate, "formula": cell.value}
                        )
                    formula_count += 1
        sheet_info["total_formula_count"] = formula_count

        for chart in getattr(ws, "_charts", []) or []:
            sheet_info["charts"].append(
                {
                    "title": _chart_title_text(chart),
                    "type": chart.__class__.__name__,
                    "anchor": str(chart.anchor) if hasattr(chart, "anchor") else None,
                }
            )

        for table in (ws.tables or {}).values():
            sheet_info["tables"].append(
                {
                    "name": table.name,
                    "ref": table.ref,
                    "has_totals": getattr(table, "totalsRowShown", False),
                }
            )

        result["sheets"].append(sheet_info)

    wb.close()
    wb_values.close()
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: excel_inspector.py <file_path>"}))
        return 1
    try:
        result = inspect_workbook(sys.argv[1])
        # ensure_ascii=False so Thai/Japanese sheet names survive
        # round-tripping through the Go layer.
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 — surface every error to Go
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
