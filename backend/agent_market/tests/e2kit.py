"""Score E2 against the Phase A golden output.

    python -m agent_market.tests.e2kit [fixture-id ...]      (inside the sandbox image)

For every fixture: the engine's value of each field in scope vs the golden
truth; a *silent error* is a field that is wrong (or missing) and not the
subject of any question — the one outcome the AM2 card forbids. Also:
questions that must appear, questions beyond ``max``, and each golden
check's status. The history is the golden ``history`` (f03 sees d01).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
E2 = os.path.join(os.path.dirname(HERE), "fixtures", os.environ.get("E2KIT_SET", "e2"))   # e2 | e2_holdout
TODAY = _dt.date(2026, 9, 30)
FIELDS = ["seller_name", "seller_tax_id", "seller_branch", "buyer_name", "buyer_tax_id", "document_number",
          "document_date", "due_date", "currency", "sum_lines", "discount", "subtotal", "vat_amount", "total",
          "wht_amount", "net_payable"]


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


_THAI_MARKS = set("ัิีึืฺุู็่้๊๋์ํ๎")


def _norm_core(s):
    """A party name's identity: its consonant skeleton (a PDF text layer can lose Thai
    marks — see engine/e2/thai_text.py — and the tax ID, not the spelling, identifies
    a party). Written here independently of the engine."""
    if s is None:
        return None
    s = str(s).replace("ำ", "า")
    return "".join(ch for ch in s if ch not in _THAI_MARKS and not ch.isspace() and ch != ".").lower()


def compare(field: str, got: dict | None, want) -> bool | None:
    """True / False, or None when the golden says the field is absent and the engine agrees."""
    g = (got or {}).get("normalized")
    if field in ("seller_name", "buyer_name"):
        if want is None:
            return g is None
        return g is not None and _norm_core(g.get("core")) == _norm_core(want["normalized"]["core"]) and \
            g.get("legal_form") == want["normalized"]["legal_form"]
    if field in ("seller_tax_id", "buyer_tax_id"):
        return g == (want["value"] if want else None)
    if field in ("sum_lines", "discount", "subtotal", "vat_amount", "total", "wht_amount", "net_payable"):
        if want is None:
            return g is None
        if field == "discount" and Decimal(want) == 0:
            return g is None or Decimal(g) == 0
        return g is not None and Decimal(g) == Decimal(want)
    if field == "document_number":
        return g is not None and re.sub(r"\s", "", g) == re.sub(r"\s", "", want)
    return g == want


def score(out: dict, exp: dict) -> dict:
    truth = exp["truth"]
    scope = exp["scope_fields"] or FIELDS
    asked = {q["key"] for q in out["questions"]}
    asked_fields = {q.get("field") for q in out["questions"]} | {q.get("subject") for q in out["questions"]}
    result = {"id": exp["id"], "class": exp["class"], "fields": {}, "silent_errors": [], "wrong_but_asked": []}
    for f in scope:
        want = truth.get(f)
        if f == "vat_amount" and want is None and truth.get("vat_mode") == "none":
            want = None
        ok = compare(f, out["fields"].get(f), want)
        result["fields"][f] = ok
        if not ok:
            if f in asked_fields or any(k.endswith(":" + f) for k in asked):
                result["wrong_but_asked"].append(f)
            else:
                # a failed check naming this field counts as asking about it
                named = any(c["status"] == "fail" and f in c["fields"] and
                            ("check_failed:" + c["code"]) in asked for c in out["deterministic_checks"])
                (result["wrong_but_asked"] if named else result["silent_errors"]).append(f)
    dt_ok = out["document_type"]["normalized"] == truth["document_type"]
    result["fields"]["document_type"] = dt_ok
    if not dt_ok and "document_type_unknown" not in asked:
        result["silent_errors"].append("document_type")
    # line items (full scope only)
    if exp["scope"] == "full":
        want_items = truth["line_items"]
        got = out["line_items"]
        li_ok = len(got) == len(want_items) and all(
            (g["amount"]["normalized"] is not None and Decimal(g["amount"]["normalized"]) == Decimal(w["amount"])) and
            (g["quantity"]["normalized"] is None or Decimal(g["quantity"]["normalized"]) == Decimal(w["quantity"]))
            for g, w in zip(got, want_items))
        result["line_items"] = {"ok": li_ok, "got": len(got), "want": len(want_items)}
        # AM8: an item's name as the page prints it, or a question about that row — a name cut short and
        # marked as read with confidence was the audit's finding (d01 row 3 lost "แฟ้มเอกสาร")
        if exp["class"] not in ("scan300", "scan200", "photo"):
            for i, (g, w) in enumerate(zip(got, want_items), start=1):
                if not w.get("description"):
                    continue
                if _norm_core((g.get("description") or {}).get("normalized")) != _norm_core(w["description"]) \
                        and f"item_text:line{g['row']}" not in asked:
                    result["silent_errors"].append(f"line_items[{i}].description")
    q = exp["questions"]
    must = q["must"]
    result["missing_questions"] = [m for m in must if not any(k == m or k.startswith(m + ":") or m.startswith(k) for k in asked)]
    allowed = set(must) | set(q.get("allowed_extra") or [])
    extra = [k for k in asked if not any(k == a or k.startswith(a + ":") or a.startswith(k.split(":")[0] + ":" + k.split(":", 1)[-1]) for a in allowed)]
    result["extra_questions"] = extra if not q["may"] else []
    result["questions"] = sorted(asked)
    too_many = q["max"] is not None and len(asked) > q["max"]
    result["too_many_questions"] = too_many
    checks = {c["code"]: c["status"] for c in out["deterministic_checks"]}
    result["check_mismatch"] = {k: (v, checks.get(k)) for k, v in exp["checks"].items()
                                if checks.get(k) != v and not (exp["class"] == "photo")}
    if exp["class"] in ("scan300", "scan200", "photo"):
        # a scan/photo may fail to evaluate a check (skip) or fail it and ask; the dangerous direction is
        # the engine passing a check the golden says fails (a designed defect waved through)
        result["check_mismatch"] = {k: v for k, v in result["check_mismatch"].items() if v[0] == "fail" and v[1] == "pass"}
    result["ok"] = (not result["silent_errors"] and not result["missing_questions"] and not too_many and
                    not result["extra_questions"] and not result["check_mismatch"] and
                    ("zero_questions" not in exp["rules"] or not asked))
    return result


def history_for(exp: dict, org: dict) -> list[dict]:
    out = []
    for hid in exp.get("history") or []:
        h = _load(os.path.join(E2, "expected", hid + ".json"))["truth"]
        out.append({"name": hid, "seller_tax_id": (h["seller_tax_id"] or {}).get("value"),
                    "document_number": h["document_number"], "document_date": h["document_date"], "total": h["total"]})
    return out


def run(ids: list[str] | None = None, verbose: bool = True) -> list[dict]:
    from agent_market.engine.e2 import extract

    org = _load(os.path.join(E2, "org.json"))
    index = _load(os.path.join(E2, "index.json"))["fixtures"]
    results = []
    for f in index:
        if ids and f["id"] not in ids:
            continue
        exp = _load(os.path.join(E2, "expected", f["id"] + ".json"))
        t0 = time.monotonic()
        out = extract(os.path.join(E2, f["files"][0]), os.path.basename(f["files"][0]), org=org,
                      history=history_for(exp, org), today=TODAY)
        r = score(out, exp)
        r["seconds"] = round(time.monotonic() - t0, 2)
        r["method"] = out["method"]
        r["out"] = out
        results.append(r)
        if verbose:
            okf = sum(1 for v in r["fields"].values() if v)
            print(f"{'OK ' if r['ok'] else 'BAD'} {f['id']:34s} fields {okf}/{len(r['fields'])} "
                  f"silent={r['silent_errors']} missing_q={r['missing_questions']} extra_q={r['extra_questions']} "
                  f"asked={r['questions']} checks≠={r['check_mismatch']} items={r.get('line_items')} {r['seconds']}s",
                  flush=True)
    return results


if __name__ == "__main__":
    res = run(sys.argv[1:] or None)
    ok = sum(r["ok"] for r in res)
    print(f"\n{ok}/{len(res)} fixtures OK; silent errors: {sum(len(r['silent_errors']) for r in res)}")
    out_path = os.environ.get("E2KIT_OUT")
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump([{k: v for k, v in r.items()} for r in res], fh, ensure_ascii=False, indent=1, default=str)
