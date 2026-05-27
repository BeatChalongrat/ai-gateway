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

Usage:  python3 excel_executor.py <plan.json> <source.xlsx> <output.xlsx>
Output: JSON result on stdout
"""

import json
import sys
from typing import Any

import openpyxl
from openpyxl.chart import AreaChart, BarChart, LineChart, PieChart, Reference, ScatterChart
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter  # noqa: F401 — re-exported for future actions


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


def _apply_write_cells(wb, action: dict[str, Any]) -> str:
    ws = _ws_for(wb, action["sheet_name"])
    cells = action.get("cells", []) or []
    for spec in cells:
        cell = ws[spec["cell"]]
        if "value_formula" in spec:
            cell.value = spec["value_formula"]
        elif "value" in spec:
            cell.value = spec["value"]

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
    return f"Wrote {len(cells)} cells in '{action['sheet_name']}'"


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
}


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
    for action in plan.get("actions", []) or []:
        action_type = action.get("type", "")
        handler = HANDLERS.get(action_type)
        if handler is None:
            changes.append(f"ERROR in {action_type or '<missing type>'}: unknown action")
            continue
        try:
            changes.append(handler(wb, action))
        except Exception as exc:  # noqa: BLE001 — log + continue per action
            changes.append(f"ERROR in {action_type}: {exc}")

    wb.save(output_path)
    wb.close()

    return {
        "success": True,
        "output_file": output_path,
        "changes": changes,
        "action_count": len(plan.get("actions", []) or []),
    }


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
        print(json.dumps({"error": str(exc), "success": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
