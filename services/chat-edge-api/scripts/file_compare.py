#!/usr/bin/env python3
"""File version comparator — structural diff between two artefacts.

Sibling of the Phase-4 inspectors. Called by WorkspaceService.Compare
to produce a structural delta the frontend renders as a list of
"+ Sheet added: Foo", "~ Rows changed: Sales (+45)" lines.

Supports xlsx / docx / pdf / pptx. The two files must be the same
extension — comparing across formats isn't supported (and isn't
useful: a docx vs pdf "diff" would just be size).

Usage:  python3 file_compare.py <file1> <file2>
Output: JSON report on stdout
"""

import json
import os
import sys
from typing import Any


# Bounded change-list — past N entries we just append a "+M more"
# tail so a 200-row table swap doesn't drown the comparison card.
MAX_DETAIL_CHANGES = 80


def _humanize_bytes(n: int) -> str:
    sign = "+" if n >= 0 else "-"
    n = abs(n)
    units = ["B", "KB", "MB", "GB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{sign}{n:.1f} {units[i]}" if i > 0 else f"{sign}{int(n)} {units[i]}"


def _change(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    out = {"type": kind, "detail": detail}
    out.update(extra)
    return out


def compare_xlsx(p1: str, p2: str) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    wb1 = load_workbook(p1, data_only=True)
    wb2 = load_workbook(p2, data_only=True)
    changes: list[dict[str, Any]] = []
    s1, s2 = set(wb1.sheetnames), set(wb2.sheetnames)
    for s in sorted(s2 - s1):
        changes.append(_change("sheet_added", s))
    for s in sorted(s1 - s2):
        changes.append(_change("sheet_removed", s))
    for s in sorted(s1 & s2):
        ws1 = wb1[s]
        ws2 = wb2[s]
        r1 = ws1.max_row or 0
        r2 = ws2.max_row or 0
        c1 = ws1.max_column or 0
        c2 = ws2.max_column or 0
        if r2 != r1:
            d = r2 - r1
            changes.append(_change("rows_changed", f"{'+' if d > 0 else ''}{d} rows", sheet=s))
        if c2 != c1:
            d = c2 - c1
            changes.append(_change("columns_changed", f"{'+' if d > 0 else ''}{d} columns", sheet=s))
    ch1 = sum(len(getattr(wb1[s], "_charts", []) or []) for s in wb1.sheetnames)
    ch2 = sum(len(getattr(wb2[s], "_charts", []) or []) for s in wb2.sheetnames)
    if ch2 > ch1:
        changes.append(_change("chart_added", f"+{ch2 - ch1} chart(s)"))
    elif ch2 < ch1:
        changes.append(_change("chart_removed", f"-{ch1 - ch2} chart(s)"))
    wb1.close()
    wb2.close()
    return changes


def compare_docx(p1: str, p2: str) -> list[dict[str, Any]]:
    from docx import Document

    d1 = Document(p1)
    d2 = Document(p2)
    changes: list[dict[str, Any]] = []

    p_n1, p_n2 = len(d1.paragraphs), len(d2.paragraphs)
    if p_n1 != p_n2:
        changes.append(_change("paragraphs_changed", f"{p_n1} → {p_n2}"))

    t_n1, t_n2 = len(d1.tables), len(d2.tables)
    if t_n1 != t_n2:
        changes.append(_change("tables_changed", f"{t_n1} → {t_n2}"))

    def _headings(doc):
        return [
            (p.text or "").strip()[:80]
            for p in doc.paragraphs
            if p.style and (p.style.name or "").startswith("Heading") and p.text.strip()
        ]

    h1 = _headings(d1)
    h2 = _headings(d2)
    for h in [x for x in h2 if x not in h1]:
        changes.append(_change("heading_added", h))
    for h in [x for x in h1 if x not in h2]:
        changes.append(_change("heading_removed", h))

    w1 = sum(len((p.text or "").split()) for p in d1.paragraphs)
    w2 = sum(len((p.text or "").split()) for p in d2.paragraphs)
    if w1 != w2:
        d = w2 - w1
        changes.append(_change("word_count_changed", f"{w1} → {w2} ({'+' if d > 0 else ''}{d})"))
    return changes


def compare_pdf(p1: str, p2: str) -> list[dict[str, Any]]:
    from pypdf import PdfReader

    r1 = PdfReader(p1)
    r2 = PdfReader(p2)
    changes: list[dict[str, Any]] = []
    n1, n2 = len(r1.pages), len(r2.pages)
    if n1 != n2:
        changes.append(_change("pages_changed", f"{n1} → {n2}"))
    t1 = " ".join((pg.extract_text() or "") for pg in r1.pages)
    t2 = " ".join((pg.extract_text() or "") for pg in r2.pages)
    w1, w2 = len(t1.split()), len(t2.split())
    if w1 != w2:
        d = w2 - w1
        changes.append(_change("word_count_changed", f"{w1} → {w2} ({'+' if d > 0 else ''}{d})"))
    return changes


def compare_pptx(p1: str, p2: str) -> list[dict[str, Any]]:
    from pptx import Presentation

    pr1 = Presentation(p1)
    pr2 = Presentation(p2)
    changes: list[dict[str, Any]] = []
    n1, n2 = len(pr1.slides), len(pr2.slides)
    if n1 != n2:
        changes.append(_change("slides_changed", f"{n1} → {n2}"))

    def _titles(prs):
        out = []
        for slide in prs.slides:
            title = ""
            for shape in slide.shapes:
                if shape.has_text_frame and (shape.text_frame.text or "").strip():
                    title = shape.text_frame.text.strip().split("\n")[0][:80]
                    break
            out.append(title or "(no title)")
        return out

    titles1 = _titles(pr1)
    titles2 = _titles(pr2)
    set1 = set(titles1)
    set2 = set(titles2)
    for t in [x for x in titles2 if x not in set1]:
        changes.append(_change("slide_added", t))
    for t in [x for x in titles1 if x not in set2]:
        changes.append(_change("slide_removed", t))
    return changes


COMPARATORS = {
    ".xlsx": compare_xlsx,
    ".xls":  compare_xlsx,
    ".docx": compare_docx,
    ".pdf":  compare_pdf,
    ".pptx": compare_pptx,
}


def compare_files(p1: str, p2: str) -> dict[str, Any]:
    ext1 = os.path.splitext(p1)[1].lower()
    ext2 = os.path.splitext(p2)[1].lower()
    size1 = os.path.getsize(p1)
    size2 = os.path.getsize(p2)

    out: dict[str, Any] = {
        "v1_size_bytes": size1,
        "v2_size_bytes": size2,
        "size_change": _humanize_bytes(size2 - size1),
        "file_type": ext1.lstrip("."),
        "structural_changes": [],
        "content_diff_summary": "",
    }

    if ext1 != ext2:
        out["content_diff_summary"] = (
            f"Different file types ({ext1} vs {ext2}); only size compared."
        )
        return out

    comparator = COMPARATORS.get(ext1)
    if comparator is None:
        out["content_diff_summary"] = (
            f"No structural comparator for {ext1}; size changed by {out['size_change']}."
        )
        return out

    try:
        changes = comparator(p1, p2)
        truncated = changes[:MAX_DETAIL_CHANGES]
        more = max(0, len(changes) - MAX_DETAIL_CHANGES)
        out["structural_changes"] = truncated
        if not changes:
            out["content_diff_summary"] = (
                "No structural changes detected (cell-level content may still differ)."
            )
        else:
            head = ", ".join(
                f"{c['type'].replace('_', ' ')}: {c['detail']}" for c in truncated[:5]
            )
            tail = f". +{more} more changes" if more else ""
            out["content_diff_summary"] = head + tail
    except Exception as exc:  # noqa: BLE001
        out["structural_changes"] = [_change("error", str(exc))]
        out["content_diff_summary"] = f"Comparison failed: {exc}"
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: file_compare.py <file1> <file2>"}))
        return 1
    try:
        result = compare_files(sys.argv[1], sys.argv[2])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
