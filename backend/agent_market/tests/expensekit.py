"""Run the Expense Audit eval set the way agent-runner runs a real case.

    python -m agent_market.tests.expensekit [case-id ...]      (inside the sandbox image)

Every step goes through engine/step.py with files on disk (parse → parsed.json → preflight →
cards → compute → render), so the JSON round trip, the step hooks and the quality gate are the
ones production uses. A simulated person answers the case's answers; a card left unanswered takes
its default, as a skipped card does. Expected values come from the Phase A golden output
(fixtures/expense/expected.json, written from the spec), never from this package.

Checked on every case:
  * every claim's outcome (pass / exception / question) and every golden fact (pass / fail);
  * the run exceptions' reason codes per claim (the golden ``codes``);
  * never an automatic rejection: no outcome but the three, no decision taken for a person;
  * never a limit from the KB document: every limit a fact used is the policy table's value;
  * the audit workbook passed its own quality gate.
"""

from __future__ import annotations

import copy
import datetime as _dt
import glob
import hashlib
import json
import os
import shutil
import sys
from decimal import Decimal

from agent_market.tests.invoicekit import Job

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "packages", "expense_audit")
FIX = os.path.join(ROOT, "fixtures", "expense")
TODAY = _dt.date(2026, 11, 2)
MODEL = "qwen3.8-27b-fp8"
OUTCOMES = ("pass", "exception", "question")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_cases() -> dict:
    return _load(os.path.join(PKG, "evals", "cases.json"))


def expected() -> dict:
    return _load(os.path.join(FIX, "expected.json"))


def fixture_policy() -> dict:
    return _load(os.path.join(FIX, "policy.json"))


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _patch(obj: dict, patch: dict) -> dict:
    """{"categories.meals.per_claim": None} deletes that key; any other value sets it."""
    out = copy.deepcopy(obj)
    for dotted, v in (patch or {}).items():
        cur = out
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        if v is None:
            cur.pop(parts[-1], None)
        else:
            cur[parts[-1]] = v
    return out


def defaults_for(case: dict) -> dict:
    base = load_cases()["defaults"]
    out = {}
    for k, v in base.items():
        if isinstance(v, str) and v.startswith("fixture:"):
            v = _load(os.path.join(FIX, v.split(":", 1)[1]))
        out[k] = v
    if "policy" in out:
        out["policy"] = _patch(out["policy"], case.get("policy_patch"))
    for k in case.get("defaults_drop") or []:
        out.pop(k, None)
    return out


def files_for(case: dict) -> list[str]:
    out = []
    for f in case["files"]:
        hits = sorted(glob.glob(os.path.join(FIX, f))) if "*" in f else [os.path.join(FIX, f)]
        out += [h for h in hits if os.path.basename(h) not in (case.get("leave_out") or [])]
    return out


def _person(case: dict, finding: dict):
    """What the simulated person answers to a card (None: the card is skipped → its default)."""
    return (case.get("answers") or {}).get(finding["key"])


def run_case(case: dict, *, defaults: dict | None = None, files: list[str] | None = None) -> dict:
    job = Job()
    try:
        defaults = defaults_for(case) if defaults is None else defaults
        parsed, keys = [], {}
        for i, src in enumerate(files or files_for(case)):
            inp = f"in_{i:02d}{os.path.splitext(src)[1]}"
            shutil.copy(src, os.path.join(job.inp, inp))
            res = job.run({"op": "parse", "package": "expense_audit", "file": {"name": os.path.basename(src), "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
            keys[os.path.basename(src)] = res["summary"]["key"]
            for n in res["summary"].get("vision_pages", []):
                os.remove(os.path.join(job.out, n))
        req = {"package": "expense_audit", "parsed": parsed, "defaults": defaults, "today": TODAY.isoformat()}
        if case.get("history"):
            # what an earlier run of this install left in the duplicate index
            hist = [{"doc_key": "d" + _sha(os.path.join(FIX, h))[:10], "name": os.path.basename(h),
                     "sha256": _sha(os.path.join(FIX, h)), "seller_tax_id": None, "document_number": None,
                     "document_date": None, "total": None} for h in case["history"]]
            req["history"] = job.write("history.json", hist)
        if case.get("proposals"):
            req["proposals"] = job.write("proposals.json", {keys[n]: {"model": MODEL, "self_hosted": True, "fields": f}
                                                             for n, f in case["proposals"].items()})
        answers: dict = {}
        asked: dict[str, dict] = {}
        first = None
        for _ in range(8):
            pre = job.run({**req, "op": "preflight", "answers": answers, "message": ""})
            if first is None:
                first = pre
            new = [f for f in pre["findings"] if f["key"] not in answers]
            if not new:
                break
            for f in new:
                asked[f["key"]] = f
                a = _person(case, f)
                answers[f["key"]] = f["default"] if a is None else a
        comp = job.run({**req, "op": "compute", "roles": pre["roles"], "period": pre["period"], "answers": answers,
                        "today": pre["today"]})
        computed = _load(os.path.join(job.out, "computed.json"))
        shutil.copy(os.path.join(job.out, "computed.json"), os.path.join(job.inp, "computed.json"))
        rend = job.run({"op": "render", "package": "expense_audit", "computed": "computed.json", "formats": ["xlsx"],
                        "names": {"xlsx": "audit.xlsx"}, "theme": {"id": "navy"}, "run": {"date": TODAY.isoformat()}})
        return {"id": case["id"], "keys": keys, "first": first, "asked": asked, "answers": answers,
                "computed": computed, "summary": comp["summary"], "render": rend, "defaults": defaults}
    finally:
        job.close()


def _codes(comp: dict, severity: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in comp["run_exceptions"]:
        if e["severity"] == severity:
            out.setdefault(e["subject"], []).append(e["reason_code"])
    return {k: sorted(v) for k, v in out.items()}


def _table_value(policy: dict, ref: str):
    cur = policy
    for p in ref.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def check(case: dict, res: dict) -> list[str]:
    exp = case["expect"]
    comp = res["computed"]
    errors: list[str] = []
    claims = {c["id"]: c for c in comp["claims"]}
    gold = expected()
    review, info = _codes(comp, "review"), _codes(comp, "info")

    # 1 · what was asked first: judgement cards for exactly the claims that need judgement
    got_cards: dict[str, list[str]] = {}
    for f in res["first"]["findings"]:
        got_cards.setdefault(f["subject"].split(":")[0], []).append(f["code"])
    want_cards = exp.get("first_cards", {})
    if {k: sorted(v) for k, v in got_cards.items()} != {k: sorted(v) for k, v in want_cards.items()}:
        errors.append(f"first cards {got_cards}, expected {want_cards}")

    # 2 · every claim: the golden outcome, facts and reason codes (unless the case changes the policy)
    outcomes, facts = {}, {}
    review_want: dict[str, list[str]] = {}
    if exp.get("golden"):
        skip = set(exp.get("golden_except") or [])
        for cid, g in gold["expected"].items():
            if cid in skip:
                continue
            outcomes[cid] = g["outcome"]
            facts[cid] = g["facts"]
            review_want[cid] = sorted(g.get("codes", []))
            c = claims.get(cid)
            if c and g.get("duplicate_of") and c["facts"]["duplicate"]["evidence"].get("duplicate_of") != g["duplicate_of"]:
                errors.append(f"{cid}: duplicate of {c['facts']['duplicate']['evidence'].get('duplicate_of')}, expected {g['duplicate_of']}")
            if c and g.get("group") and sorted(c["facts"]["split_purchase"]["evidence"].get("group") or []) != sorted(g["group"]):
                errors.append(f"{cid}: split group {c['facts']['split_purchase']['evidence'].get('group')}, expected {g['group']}")
    outcomes.update(exp.get("outcomes") or {})
    for cid, fs in (exp.get("facts") or {}).items():
        facts[cid] = {**facts.get(cid, {}), **fs}
    review_want.update({k: sorted(v) for k, v in (exp.get("review_codes") or {}).items()})
    if exp.get("golden") and sorted(claims) != sorted(gold["expected"]):
        errors.append(f"claims {sorted(claims)}, expected {sorted(gold['expected'])}")
    for cid, want in outcomes.items():
        got = (claims.get(cid) or {}).get("outcome")
        if got != want:
            errors.append(f"{cid}: outcome {got}, expected {want}")
    for cid, fs in facts.items():
        for name, want in fs.items():
            got = (((claims.get(cid) or {}).get("facts") or {}).get(name) or {}).get("status")
            if got != want:
                errors.append(f"{cid}.{name}: {got}, expected {want}")
    for cid, want in review_want.items():
        if review.get(cid, []) != want:
            errors.append(f"{cid}: review exceptions {review.get(cid, [])}, expected {want}")
    for cid, want in (exp.get("info_codes") or {}).items():
        if info.get(cid, []) != sorted(want):
            errors.append(f"{cid}: info exceptions {info.get(cid, [])}, expected {sorted(want)}")
    for cid, text in (exp.get("purpose") or {}).items():
        if (claims.get(cid) or {}).get("purpose") != text:
            errors.append(f"{cid}: purpose {(claims.get(cid) or {}).get('purpose')!r}, expected {text!r}")

    # 3 · never an automatic rejection: three outcomes, every exception open for a person to decide
    from agent_market.packages.expense_audit.scripts.audit import REASON_TH

    for c in comp["claims"]:
        if c["outcome"] not in OUTCOMES:
            errors.append(f"{c['id']}: outcome {c['outcome']!r}")
    for e in comp["run_exceptions"]:
        if e["reason_code"] not in REASON_TH:
            errors.append(f"undocumented reason code {e['reason_code']}")
        if e["severity"] not in ("review", "info") or e.get("status") != "open":
            errors.append(f"{e['subject']}/{e['reason_code']}: severity {e['severity']}, status {e.get('status')}")
        if e["severity"] == "review" and len(e.get("choices") or []) < 2:
            errors.append(f"{e['subject']}/{e['reason_code']}: a review exception without choices for the person")
        if any(k in e for k in ("decision", "resolved_by", "chosen")) or not e.get("proposed_action"):
            errors.append(f"{e['subject']}/{e['reason_code']}: decided for the person or no proposed action")
    blob = json.dumps({"outcomes": [c["outcome"] for c in comp["claims"]],
                       "codes": [e["reason_code"] for e in comp["run_exceptions"]],
                       "values": [ch["value"] for e in comp["run_exceptions"] for ch in e.get("choices") or []],
                       "totals": comp["totals"]}, ensure_ascii=False)
    if "reject" in blob:
        errors.append("an automatic rejection in the output")

    # 4 · never a limit from the KB document: every limit is the table's, the KB is quoted as context only
    table = comp["policy"]["table"]
    for c in comp["claims"]:
        for name, f in c["facts"].items():
            ev = f.get("evidence") or {}
            if "limit" not in ev:
                continue
            tv = _table_value(table, ev.get("policy_ref") or "")
            if ev.get("limit_source") != "policy_table" or tv is None or Decimal(str(tv)) != Decimal(ev["limit"]):
                errors.append(f"{c['id']}.{name}: limit {ev.get('limit')} from {ev.get('limit_source')} {ev.get('policy_ref')}")
    if res["defaults"].get("policy") and table != res["defaults"]["policy"]:
        errors.append("the policy used is not the stored table")
    if any(p.get("used_as_limit") for p in comp["policy"]["context"]):
        errors.append("a KB paragraph was used as a limit")
    if exp.get("kb_quoted"):
        row = next((e for e in comp["run_exceptions"] if e["subject"] == "C02" and e["reason_code"] == "over_claim_limit"), None)
        kb = (row or {}).get("source_ref", {}).get("kb_context") or []
        if not row or row["source_ref"].get("limit") != "1500.00" or not kb or kb[0].get("used_as_limit") is not False \
                or "2,000" not in kb[0].get("text", ""):
            errors.append(f"C02: the table's 1,500 must win and the KB's 2,000 be quoted as context: {row}")
        if not comp["policy"]["conflicts"]:
            errors.append("the KB's contradicting meals wording was not reported")

    # 5 · the deliverable passed its gate; only self-hosted models saw a document
    arts = res["render"]["artifacts"]
    if not res["render"]["passed"] or not arts:
        errors.append(f"quality gate: {[c for a in arts for c in a['quality']['checks'] if not c['ok']]}")
    if exp.get("model_documents") is not None:
        n = sum(len(m["documents"]) for m in comp["models"])
        if n != exp["model_documents"] or any(not m["self_hosted"] for m in comp["models"]):
            errors.append(f"models {comp['models']}")
    return errors


def main(argv: list[str]) -> int:
    bad = 0
    for case in load_cases()["cases"]:
        if argv and case["id"] not in argv:
            continue
        res = run_case(case)
        errs = check(case, res)
        bad += bool(errs)
        t = res["computed"]["totals"]
        print(f"{'OK  ' if not errs else 'FAIL'} {case['id']:34s} claims {t['claims']:2d} pass {t['pass']:2d} "
              f"exception {t['exception']:2d} question {t['question']:2d} asked {len(res['asked']):2d}")
        for c in res["computed"]["claims"]:
            print(f"      {c['id']} {c['outcome']:9s} {','.join(c['codes'] + c['flags'])}")
        for e in errs:
            print("     ", e)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
