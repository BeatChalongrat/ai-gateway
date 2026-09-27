"""Phase B accuracy, per fixture class, the way a customer would be hurt or not (AM3; note-AM2 §1).

    (inside the sandbox image, backend on the path)
    python3 phaseb_accuracy.py <out.md> [vision_results.jsonl]

Runs the E2 engine over the Phase A pack (backend/agent_market/fixtures/e2) and the held-out set
(fixtures/e2_holdout) against their golden output, and counts, for every field in each document's
scope (plus its document type):

  correct            recorded with the golden value, no question
  asked              a question card is raised about it (the person decides; nothing is recorded silently)
  wrong_silent       recorded WRONG while every deterministic check naming it passed and no card asked —
                     the only outcome that can hurt a customer
  wrong_flagged      recorded wrong, a check naming it failed, but no card asked (should be 0)
  missing_silent     the document prints it, the engine has no value, and no card asked

Digital PDFs and e-Tax files run on rules only, as in production. Scans and photos run twice: rules
only, and with the self-hosted vision model's reading as a second reader (the engine's "proposals"),
which is how production runs them. The vision readings are the ones AM2 recorded in Phase B for this
pack (qwen3.8-27b-fp8, the runner's request shape, reasoning off) — passed as the second argument;
the held-out scan has no recorded reading and runs rules only.
"""

from __future__ import annotations

import collections
import json
import os
import sys

from agent_market.engine.e2 import extract
from agent_market.tests import e2kit

ROOT = os.path.dirname(e2kit.E2)
PROPOSAL_FIELDS = ("seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id", "document_number", "document_date",
                   "subtotal", "vat_amount", "total", "wht_amount", "net_payable", "currency")
SCAN_CLASSES = ("scan300", "scan200", "photo")
CATS = ("correct", "asked", "wrong_silent", "wrong_flagged", "missing_silent")


def vision_readings(path: str | None) -> dict:
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("path") == "vision" and str(r.get("model", "")).startswith("qwen") and r.get("pred") and not r.get("error"):
                out[r["id"]] = {k: r["pred"].get(k) for k in PROPOSAL_FIELDS if r["pred"].get(k) not in (None, "")}
    return out


def classify(out: dict, exp: dict) -> dict:
    truth = exp["truth"]
    scope = list(exp["scope_fields"] or e2kit.FIELDS)
    keys = {q["key"] for q in out["questions"]}
    fields_asked = {q.get("field") for q in out["questions"]} | {q.get("subject") for q in out["questions"]}
    failed = [c for c in out["deterministic_checks"] if c["status"] == "fail"]
    res = {c: [] for c in CATS}
    for f in scope + ["document_type"]:
        if f == "document_type":
            ok = out["document_type"]["normalized"] == truth["document_type"]
            asked = "document_type_unknown" in keys
            got_value = out["document_type"]["normalized"] not in (None, "unknown")
        else:
            want = truth.get(f)
            if f == "vat_amount" and want is None and truth.get("vat_mode") == "none":
                want = None
            ok = e2kit.compare(f, out["fields"].get(f), want)
            asked = f in fields_asked or any(k.endswith(":" + f) for k in keys) or any(
                f in c["fields"] and ("check_failed:" + c["code"]) in keys for c in failed)
            got_value = ((out["fields"].get(f) or {}).get("normalized")) is not None
        if asked:
            res["asked"].append(f)
        elif ok or ok is None:
            res["correct"].append(f)
        elif got_value:
            named = any(f in c["fields"] for c in failed)
            res["wrong_flagged" if named else "wrong_silent"].append(f)
        else:
            res["missing_silent"].append(f)
    return res


def run_set(folder: str, readings: dict, with_vision: bool) -> list[dict]:
    base = os.path.join(ROOT, folder)
    org = e2kit._load(os.path.join(base, "org.json"))
    rows = []
    for fx in e2kit._load(os.path.join(base, "index.json"))["fixtures"]:
        exp = e2kit._load(os.path.join(base, "expected", fx["id"] + ".json"))
        hist = []
        for hid in exp.get("history") or []:
            h = e2kit._load(os.path.join(base, "expected", hid + ".json"))["truth"]
            hist.append({"name": hid, "seller_tax_id": (h["seller_tax_id"] or {}).get("value"),
                         "document_number": h["document_number"], "document_date": h["document_date"], "total": h["total"]})
        props = None
        if with_vision and exp["class"] in SCAN_CLASSES and fx["id"] in readings:
            props = [{"method": "vision:qwen3.8-27b-fp8", "fields": readings[fx["id"]]}]
        out = extract(os.path.join(base, fx["files"][0]), os.path.basename(fx["files"][0]), org=org, history=hist,
                      today=e2kit.TODAY, proposals=props)
        rows.append({"id": fx["id"], "class": exp["class"], "set": folder, "vision": bool(props), **classify(out, exp)})
    return rows


def label(r: dict) -> str:
    if r["set"] == "e2_holdout":
        return "held-out (unseen layouts)" + (" · scan 300 DPI" if r["class"] == "scan300" else " · digital PDF")
    c = r["class"]
    if c == "digital":
        return "digital PDF · designed failures (f01–f07)" if r["id"].startswith("f") else "digital PDF · clean (d01–d09)"
    return {"etax_pdf": "e-Tax (PDF with XML)", "etax_xml": "e-Tax (XML)", "scan300": "scan 300 DPI",
            "scan200": "scan 200 DPI", "photo": "phone photo"}.get(c, c) + (" + vision (production)" if r["vision"] else
                                                                        (" · rules only" if c in SCAN_CLASSES else ""))


def table(rows: list[dict]) -> list[str]:
    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault(label(r), []).append(r)
    out = ["| class | docs | fields scored | recorded correctly | became a question card | WRONG, every check passed (silent) | wrong, a check failed but not asked | missing, not asked |",
           "|---|---|---|---|---|---|---|---|"]
    for name, rs in groups.items():
        n = {c: sum(len(r[c]) for r in rs) for c in CATS}
        total = sum(n.values())
        out.append(f"| {name} | {len(rs)} | {total} | {n['correct']} ({100 * n['correct'] // max(total, 1)}%) | {n['asked']} | "
                   f"**{n['wrong_silent']}** | {n['wrong_flagged']} | {n['missing_silent']} |")
    return out


def main(out_md: str, vision_path: str | None = None):
    readings = vision_readings(vision_path)
    rows = run_set("e2", readings, with_vision=False)
    scans = [r for r in rows if r["class"] in SCAN_CLASSES]
    rows_v = [r for r in run_set("e2", readings, with_vision=True) if r["class"] in SCAN_CLASSES]
    hold = run_set("e2_holdout", readings, with_vision=True)
    ordered = [r for r in rows if r["class"] not in SCAN_CLASSES]
    for c in ("scan300", "scan200", "photo"):
        ordered += [r for r in scans if r["class"] == c] + [r for r in rows_v if r["class"] == c]
    ordered += hold
    lines = ["# Phase B accuracy per fixture class (AM3)", "",
             "E2 engine against the Phase A golden output and the held-out set. A field counts once, in the first",
             "column that applies: a card about it → asked; else the golden value → correct; else recorded wrong",
             "(silent when no check naming it failed); else missing. Scans and photos: rules only, then with the",
             f"AM2 Phase B vision reading as the second reader (production). Vision readings found for: "
             f"{', '.join(sorted(readings)) or 'none — the vision rows need the test stack'}.", ""] + table(ordered)
    silent = [(r["id"], r["vision"], r["wrong_silent"]) for r in ordered if r["wrong_silent"]]
    lines += ["", "Silent errors (document, with vision, fields): " + (json.dumps(silent, ensure_ascii=False) if silent else "none"), "",
              "Per document:", "", "| document | class | vision | correct | asked | wrong silent | wrong flagged | missing |",
              "|---|---|---|---|---|---|---|---|"]
    for r in ordered:
        lines.append(f"| {r['id']} | {r['class']} | {'yes' if r['vision'] else 'no'} | {len(r['correct'])} | "
                     f"{', '.join(r['asked']) or '-'} | {', '.join(r['wrong_silent']) or '-'} | "
                     f"{', '.join(r['wrong_flagged']) or '-'} | {', '.join(r['missing_silent']) or '-'} |")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(table(ordered)))
    print("silent:", silent or "none")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/phaseb_accuracy.md", sys.argv[2] if len(sys.argv) > 2 else None)
