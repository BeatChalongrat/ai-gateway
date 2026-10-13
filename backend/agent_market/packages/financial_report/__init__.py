"""Agent 9 — Financial Report + Executive Deck.

The package's Python surface, called by agent_market.engine.step:
parse · preflight · compute · postcompute · summarize · render.
The manifest (agent.json), instructions (AGENT.md), templates, sample data
and eval set live beside this file.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

from ...engine import words as _words   # (this package has a module of its own named words)
from ...engine.ledger import ParsedFile, parse_file
from .checks import postcompute as _post
from .checks import preflight as _pre
from .scripts import metrics as _metrics

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _manifest() -> dict:
    try:
        from importlib.resources import files

        return json.loads(files(__name__).joinpath("agent.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


VERSION = _manifest().get("version")


def parse(path: str, name: str, mapping: dict) -> ParsedFile:
    return parse_file(path, name, install_map=mapping or None)


def preflight(files: list[ParsedFile], *, message: str = "", defaults: dict | None = None,
              answers: dict | None = None, today: _dt.date | None = None) -> dict:
    return _pre.run(files, message=message, defaults=defaults, answers=answers, today=today)


def compute(files, roles, period, *, answers=None, defaults=None) -> dict:
    """The computed table. A figure's own record (its citation), what was left out and the notes are said in
    the run's language; a run that is not in the documents' language also carries the table its files are
    written from (engine/words.with_document)."""
    def table():
        return _metrics.compute(files, roles, period, answers=answers, defaults=defaults)

    return table()


def postcompute(files, roles, period, computed, *, answers=None, defaults=None) -> dict:
    return _post.run(files, roles, period, computed, answers=answers, defaults=defaults)


def summarize(computed: dict) -> dict:
    """The few numbers the chat shows while the report is being made."""
    lines = {l["key"]: l for l in computed["lines"]}
    return {
        "period": computed["period"], "period_label": computed["period_label"],
        "revenue": lines["revenue"]["current"], "total_expenses": lines["total_expenses"]["current"],
        "net_profit": lines["net_profit"]["current"], "rows_used": computed["source"]["primary"]["rows_used"],
        "exceptions": len(computed["exceptions"]), "has_prior": computed["has_prior"],
        "has_budget": computed["has_budget"], "balance_diff": computed["balance"]["diff"],
        "excluded": [{"code": e["code"], "reason": e["reason"]} for e in computed["excluded"]],
    }


def template_values(computed: dict) -> dict:
    """The values a customer's template can take (the manifest's templates block): money as printed text."""
    from ...engine.thai import fmt_money

    lines = {l["key"]: l for l in computed["lines"]}

    def m(key):
        v = (lines.get(key) or {}).get("current")
        return fmt_money(v) if v is not None else ""
    return {"company": computed.get("company") or "", "period": computed["period_label"], "revenue": m("revenue"),
            "gross_profit": m("gross_profit"), "total_expenses": m("total_expenses"), "net_profit": m("net_profit"),
            "lines": [{"label": l["label"], "current": fmt_money(l["current"]) if l["current"] is not None else "",
                       "prior": fmt_money(l["prior"]) if l.get("prior") is not None else "",
                       "budget": fmt_money(l["budget"]) if l.get("budget") is not None else ""}
                      for l in computed["lines"]]}


def render(computed: dict, req: dict, *, in_dir: str, out_dir: str) -> dict:
    from ...deliverables import render as _render

    # AM18: the files are written in the run's language, like everything the run says
    return _render.render_all(computed, req, in_dir=in_dir, out_dir=out_dir)
