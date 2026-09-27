"""Every question card the three packages raise on their eval sets, as a person reads it (AM3).

    (inside the sandbox image, backend on the path)  python3 question_dump.py <out.json>

Runs the financial report's eval cases, the E2 fixture pack and its held-out set (the invoice and
expense receipt cards), the invoice eval cases and the expense eval cases (plus the expense setup
cards: no policy, no claims sheet), and writes one row per distinct card text:
{source, code, key, header, question, options: [[label, description]], input_hint}. Used to review
every card for a stranger (before → after) — scripts/probes/AM3/results/python/question_review.md.
"""

import json
import os
import sys


def rows_of(source, findings):
    out = []
    for f in findings:
        f = f if isinstance(f, dict) else f.to_dict()
        out.append({"source": source, "code": f["code"], "key": f.get("key") or f["code"], "header": f["header"],
                    "question": f["question"],
                    "options": [[o["label"], o.get("description") or ""] for o in f["options"]],
                    "input_hint": (f.get("input") or {}).get("hint")})
    return out


def _expense_preflight(files, defaults):
    """The expense agent's first card set for these files (parse + preflight only: no compute)."""
    import shutil

    from agent_market.tests import expensekit
    from agent_market.tests.invoicekit import Job

    job = Job()
    try:
        parsed = []
        for i, rel in enumerate(files):
            src = os.path.join(expensekit.FIX, rel)
            inp = f"in_{i:02d}{os.path.splitext(src)[1]}"
            shutil.copy(src, os.path.join(job.inp, inp))
            job.run({"op": "parse", "package": "expense_audit", "file": {"name": os.path.basename(src), "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
        pre = job.run({"op": "preflight", "package": "expense_audit", "parsed": parsed, "defaults": defaults,
                       "answers": {}, "message": "", "today": expensekit.TODAY.isoformat()})
        return pre["findings"]
    finally:
        job.close()


def main(path):
    rows = []
    # 1 · the financial report (preflight + postcompute findings of every eval case)
    from agent_market.tests import evalkit

    for case in evalkit.load_cases():
        rows += rows_of(f"financial:{case['id']}", evalkit.run_case(case)["findings"])
    # 2 · E2 cards (invoice + expense receipts) on the fixture pack and the held-out set
    from agent_market.engine.e2 import cards, extract
    from agent_market.tests import e2kit

    for folder in ("e2", "e2_holdout"):
        base = os.path.join(os.path.dirname(e2kit.E2), folder)
        org = e2kit._load(os.path.join(base, "org.json"))
        for fx in e2kit._load(os.path.join(base, "index.json"))["fixtures"]:
            exp = e2kit._load(os.path.join(base, "expected", fx["id"] + ".json"))
            hist = []
            for hid in exp.get("history") or []:
                h = e2kit._load(os.path.join(base, "expected", hid + ".json"))["truth"]
                hist.append({"name": hid, "seller_tax_id": (h["seller_tax_id"] or {}).get("value"),
                             "document_number": h["document_number"], "document_date": h["document_date"], "total": h["total"]})
            out = extract(os.path.join(base, fx["files"][0]), os.path.basename(fx["files"][0]), org=org, history=hist,
                          today=e2kit.TODAY)
            rows += rows_of(f"e2:{folder}:{fx['id']}", cards.findings_for("d" + fx["id"][:10], out))
    # 3 · the invoice agent's eval cases (what the person is asked, answers included)
    from agent_market.tests import invoicekit

    for case in invoicekit.load_cases()["cases"]:
        rows += rows_of(f"invoice:{case['id']}", invoicekit.run_case(case)["asked"].values())
    # 4 · the expense agent's eval cases, and its setup cards
    from agent_market.tests import expensekit

    for case in expensekit.load_cases()["cases"]:
        rows += rows_of(f"expense:{case['id']}", expensekit.run_case(case)["asked"].values())
    d = expensekit.defaults_for({})
    bad = {**d, "policy": {**d["policy"], "categories": {**d["policy"]["categories"],
                                                          "meals": {"label": "ค่าอาหาร", "per_claim": "ประมาณ 2,000"}}}}
    rows += rows_of("expense:bad_policy", _expense_preflight(["claims_2569-10.xlsx"], bad))
    rows += rows_of("expense:no_claims", _expense_preflight(["receipts/r_c01.pdf"], d))
    # one row per distinct card text
    seen, uniq = set(), []
    for r in rows:
        k = (r["code"], r["header"], r["question"], json.dumps(r["options"], ensure_ascii=False))
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=1)
    print(f"{len(uniq)} distinct cards ({len(rows)} raised) → {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/questions.json")
