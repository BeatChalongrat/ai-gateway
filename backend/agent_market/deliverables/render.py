"""render_all — one render step: the requested formats, each through the gate.

Request fields used: formats[], names{format: filename}, theme, narrative,
assumptions[], run{id, date, agent_version}, model_info{model, self_hosted},
logo (input file name, optional).
"""

from __future__ import annotations

import hashlib
import os

from . import content as content_mod
from . import quality
from .charts import render_png
from .theme import resolve

# No PDF: its Thai shaping was wrong (AM1b). It returns in AM-2 as the
# finished .docx converted by LibreOffice, not as a second renderer.
FORMATS = {"docx": "report", "pptx": "deck", "xlsx": "table"}


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def render_all(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    theme = resolve(req.get("theme"))
    req = {**req, "theme": theme}
    model = content_mod.build(computed, req)
    computed = model["computed"]  # with the cosmetic (presentation) answers applied
    formats = [f for f in (req.get("formats") or ["docx", "pptx"]) if f in FORMATS]
    names = req.get("names") or {}
    logo = os.path.join(in_dir, os.path.basename(req["logo"])) if req.get("logo") else None

    chart_files: dict[str, str] = {}
    chart_bindings: list[dict] = []
    if "docx" in formats:
        for spec in model["charts"]:
            path = os.path.join("/tmp", f"chart_{spec['id']}.png")
            chart_bindings.append(render_png(spec, theme, path))
            chart_files[spec["id"]] = path

    artifacts = []
    all_ok = True
    cit = quality.check_citations(model, computed)
    asm = quality.check_assumptions(model, req)
    for fmt in formats:
        name = os.path.basename(names.get(fmt) or f"output.{fmt}")
        path = os.path.join(out_dir, name)
        meta: dict = {}
        if fmt == "docx":
            from .report_docx import ReportWriter

            meta = ReportWriter(model, theme, chart_files).write(path, logo)
            checks = quality.check_docx(path, model, theme, req) + [
                quality.check_docx_charts(chart_bindings, computed, model["charts"]), cit, asm]
        elif fmt == "pptx":
            from .deck_pptx import DeckWriter

            meta = DeckWriter(model, computed, theme).write(path)
            checks = quality.check_pptx(path, meta, computed, theme) + [cit]
        else:
            from .table_xlsx import XlsxWriter

            meta = XlsxWriter(model, computed, theme).write(path)
            checks = quality.check_xlsx(path, computed)
        ok = all(c["ok"] for c in checks)
        all_ok &= ok
        meta.pop("path", None)
        artifacts.append({"name": name, "format": fmt, "kind": FORMATS[fmt], "size": os.path.getsize(path),
                          "sha256": _sha(path), "quality": {"passed": ok, "checks": checks},
                          "meta": {k: v for k, v in meta.items() if k in ("slides", "sheets", "embedded_fonts", "ledger_rows")},
                          "chart_bindings": meta.get("chart_bindings") or (chart_bindings if fmt == "docx" else []),
                          "citations": len(computed["citations"])})
    return {"passed": all_ok, "artifacts": artifacts, "theme": {"id": theme["id"], "version": theme.get("version", 1),
                                                               "name": theme["name"]},
            "assumptions": model["assumptions"], "sections": [s["key"] for s in model["sections"]]}
