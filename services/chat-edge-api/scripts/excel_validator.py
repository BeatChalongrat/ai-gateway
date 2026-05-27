#!/usr/bin/env python3
"""Excel validator — checks an xlsx file is well-formed and usable.

Called by ExcelService.ValidateWorkbook on every file produced by the
executor before that file is exposed for download. The validator
catches the common failure modes: corrupted save, broken sheet, and
cells whose computed value is an Excel error sentinel (#REF!, #N/A,
…). It is intentionally tolerant — warnings (formula errors) don't
flunk the file, only hard failures (cannot open / no sheets / sheet
corruption) do, because users may legitimately want to download a
workbook that has some #N/A cells from a lookup that didn't match.

Usage:  python3 excel_validator.py <path.xlsx>
Output: JSON validation report on stdout
"""

import json
import sys
from typing import Any

import openpyxl


# Excel formula error sentinels. Anything in this set found in a
# cell's computed value (data_only=True) becomes a warning entry.
ERROR_VALUES = {
    "#REF!",
    "#NAME?",
    "#VALUE!",
    "#DIV/0!",
    "#NULL!",
    "#N/A",
    "#NUM!",
    "#GETTING_DATA",
}

# Hard cap on rows scanned per sheet for broken-formula detection. A
# 1M-row dump shouldn't make validation take minutes; the first 5k
# rows catch every realistic mistake the executor or LLM might
# introduce.
ERROR_SCAN_ROW_LIMIT = 5000


def validate_workbook(path: str) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []

    try:
        wb = openpyxl.load_workbook(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "valid": False,
            "issues": [f"Cannot open file: {exc}"],
            "warnings": [],
            "stats": {},
        }

    total_charts = 0
    try:
        if not wb.sheetnames:
            issues.append("Workbook has no sheets")

        for name in wb.sheetnames:
            ws = wb[name]
            try:
                _ = ws.max_row
                _ = ws.max_column
            except Exception as exc:  # noqa: BLE001
                issues.append(f"Sheet '{name}' is corrupted: {exc}")
            total_charts += len(getattr(ws, "_charts", []) or [])
    except Exception as exc:  # noqa: BLE001
        issues.append(f"Validation error: {exc}")
    finally:
        wb.close()

    # Second pass with computed values to find error-marker cells.
    broken: list[str] = []
    try:
        wb_values = openpyxl.load_workbook(path, data_only=True)
        try:
            for name in wb_values.sheetnames:
                ws = wb_values[name]
                row_cap = min(ws.max_row or 0, ERROR_SCAN_ROW_LIMIT)
                for row in ws.iter_rows(max_row=row_cap):
                    for cell in row:
                        v = cell.value
                        if isinstance(v, str) and v in ERROR_VALUES:
                            broken.append(f"{name}!{cell.coordinate}: {v}")
        finally:
            wb_values.close()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Could not scan for formula errors: {exc}")

    if broken:
        sample = ", ".join(broken[:5])
        suffix = "" if len(broken) <= 5 else f" (+{len(broken) - 5} more)"
        warnings.append(
            f"Found {len(broken)} cells with formula errors: {sample}{suffix}"
        )

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "stats": {
            "sheets": len(wb.sheetnames),
            "charts": total_charts,
            "broken_formulas": len(broken),
        },
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: excel_validator.py <path.xlsx>"}))
        return 1
    try:
        result = validate_workbook(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
