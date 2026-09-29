"""Run the bank reconciliation eval set the way agent-runner runs a real case.

    python -m agent_market.tests.bankkit [case-id ...]      (inside the sandbox image)

Every step goes through engine/step.py with files on disk (parse → parsed.json → preflight → cards
→ compute → render), so the JSON round trip and the quality gate are the ones production uses. A
simulated person answers the case's answers; a card left unanswered takes its default, as a skipped
card does; a request for files that is answered "wait" stops the case there, as a parked run does.
Expected values come from the scenario in tools/make_bank_fixtures.py, never from this package.

Checked on every case that reaches a statement, whatever it says:
  * every row of both tables is in exactly one of the four groups;
  * every pair below the exact tier was confirmed by a person;
  * the gate is passed exactly when the two adjusted balances are equal, and a round that does not
    tie says "ยังไม่ลงตัว";
  * every drafted entry balances, and the book side's entries add up to its adjustments;
  * the working paper passed its own quality gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from decimal import Decimal

from agent_market.tests.invoicekit import Job

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "packages", "bank_reconciliation")
FIX = os.path.join(PKG, "evals", "fixtures")
PACKAGE = "bank_reconciliation"
LETTER = {"bank": "b", "book": "k", "carried": "c"}


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_cases() -> dict:
    return _load(os.path.join(PKG, "evals", "cases.json"))


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def resolve(key: str, files: dict, roles: dict) -> str:
    """{file}.0.12 → the row's id; {file:key} → the upload's key."""
    def one(m):
        name, _, what = m.group(1).partition(":")
        sha = files[name]
        if what == "key":
            return "d" + sha[:10]
        return LETTER.get(roles.get("d" + sha[:10]), "?") + sha[:6]
    return re.sub(r"\{([^{}]+)\}", one, key)


def run_files(paths: list, *, defaults: dict | None = None, answers: dict | None = None, message: str = "",
              today: str = "2026-11-02", preparer: str = "ผู้จัดทำ ตัวอย่าง", answered_by: str | None = None) -> dict:
    """paths: [(path on disk, the name the person gave the file)]. ``answered_by``: the name of the
    person who answers the cards (the runner reads it from each card)."""
    job = Job()
    try:
        parsed, files = [], {}
        for i, (src, name) in enumerate(paths):
            inp = f"in_{i:02d}{os.path.splitext(name)[1]}"
            shutil.copy(src, os.path.join(job.inp, inp))
            job.run({"op": "parse", "package": PACKAGE, "file": {"name": name, "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
            files[name] = _sha(src)
        req = {"package": PACKAGE, "parsed": parsed, "defaults": defaults or {}, "today": today}
        wanted = dict(answers or {})
        given: dict = {}
        asked: list = []
        stop = None
        pre = None
        for _ in range(16):
            pre = job.run({**req, "op": "preflight", "answers": given, "message": message})
            new = [f for f in pre["findings"] if f["key"] not in given]
            if not new:
                break
            by_key = {resolve(k, files, pre["roles"]): v for k, v in wanted.items()}
            for f in new:
                asked.append(f)
                a = by_key.get(f["key"], wanted.get(f["code"]))
                a = f["default"] if a is None else a
                if f["kind"] == "request_files" and a in ("wait", "cancel"):
                    stop = f["code"]
                    break
                given[f["key"]] = a
            if stop:
                break
        out = {"files": files, "asked": asked, "answers": given, "preflight": pre, "stops_at": stop}
        if stop:
            return out
        creq = {**req, "op": "compute", "roles": pre["roles"], "period": pre["period"], "answers": given}
        if answered_by:
            defaulted = {f["key"] for f in asked if given.get(f["key"]) == f["default"]}
            creq["answered_by"] = {k: {"name": answered_by, "via": "card"} for k in given if k not in defaulted}
        comp = job.run(creq)
        with open(os.path.join(job.out, "computed.json"), "rb") as f:
            raw = f.read()
        computed = json.loads(raw)
        shutil.copy(os.path.join(job.out, "computed.json"), os.path.join(job.inp, "computed.json"))
        rend = job.run({"op": "render", "package": PACKAGE, "computed": "computed.json", "formats": ["xlsx"],
                        "names": {"xlsx": "bank_reconciliation.xlsx"}, "theme": {"id": "navy"},
                        "run": {"date": today, "preparer": preparer, "agent_version": "1.1.0"}})
        book = os.path.join(job.out, "bank_reconciliation.xlsx")
        keep = os.environ.get("BANKKIT_KEEP")
        if keep:
            os.makedirs(keep, exist_ok=True)
            shutil.copy(book, os.path.join(keep, "bank_reconciliation.xlsx"))
        from openpyxl import load_workbook

        wb = load_workbook(book)
        sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        out.update({"computed": computed, "computed_bytes": raw, "summary": comp["summary"], "render": rend, "sheets": sheets})
        return out
    finally:
        job.close()


def run_case(case: dict) -> dict:
    cases = load_cases()
    defaults = {**cases["defaults"], **(case.get("defaults") or {})}
    res = run_files([(os.path.join(FIX, n), n) for n in case["files"]], defaults=defaults, answers=case.get("answers"),
                    message=case.get("message") or "", today=cases["today"])
    res["id"] = case["id"]
    return res


def invariants(res: dict) -> list[str]:
    """What holds for every reconciliation, whatever the case expects."""
    c = res["computed"]
    errors = []
    g = c["match"]["groups"]
    seen: dict = {}
    for name in ("matched", "suspect"):
        for e in g[name]:
            for r in e["left"] + e["right"]:
                seen.setdefault(r["id"], []).append(name)
    for name in ("missing", "extra"):
        for r in g[name]:
            seen.setdefault(r["id"], []).append(name)
    twice = {k: v for k, v in seen.items() if len(v) != 1}
    if twice:
        errors.append(f"rows in more than one group: {twice}")
    n = c["match"]["counts"]
    if len(seen) != n["left"] + n["right"]:
        errors.append(f"{len(seen)} rows in the groups, {n['left'] + n['right']} in the tables")
    for m in g["matched"]:
        if m["tier"] != "exact" and m["confirmed"] != "person":
            errors.append(f"{m['pair']}: tier {m['tier']} matched without a person")
        if m["tier"] == "exact" and m["confirmed"] != "rule":
            errors.append(f"{m['pair']}: an exact pair is matched by its rule")
        if not m["pass"] or not m["tier"]:
            errors.append(f"{m['pair']}: no pass or tier recorded")
        for r in m["left"] + m["right"]:
            if not (r["file"] and r["sheet"] and r["row"]):
                errors.append(f"{r['id']}: no provenance")
    st, gate = c["statement"], c["gate"]
    tied = st["difference"] == "0.00"
    if gate["passed"] != tied or st["tied"] != tied:
        errors.append(f"gate {gate['passed']} / tied {st['tied']} / difference {st['difference']}")
    if not gate["passed"] and not gate["what"].startswith("ยังไม่ลงตัว"):
        errors.append(f"a round that does not tie says so: {gate['what']!r}")
    if not gate["passed"] and not (gate.get("next") and gate.get("reasons")):
        errors.append("a round that does not tie says why and what to do next")
    total = Decimal(0)
    for j in c["journal"]:
        dr = sum(Decimal(l["debit"]) for l in j["lines"])
        cr = sum(Decimal(l["credit"]) for l in j["lines"])
        if dr != cr or dr == 0:
            errors.append(f"{j['id']}: debit {dr} credit {cr}")
        if not j["draft"]:
            errors.append(f"{j['id']}: not marked as a draft")
        bank = j["lines"][0]
        total += Decimal(bank["debit"]) - Decimal(bank["credit"])
    if total != Decimal(st["book"]["adjustments"]):
        errors.append(f"drafted entries move the bank account by {total}, the book side adjusts by {st['book']['adjustments']}")
    for a in res["render"]["artifacts"]:
        bad = [k["name"] for k in a["quality"]["checks"] if not k["ok"]]
        if bad or not a["quality"]["passed"]:
            errors.append(f"quality gate: {bad}")
    face = {r[0]: r[1] for r in res["sheets"]["งบกระทบยอด"] if r and isinstance(r[0], str)}
    for label in ("นิติบุคคล", "ธนาคาร", "ชื่อบัญชี", "เลขที่บัญชี", "งวด", "ตัดยอด ณ วันที่", "ผู้จัดทำ", "ผู้สอบทาน", "ผลการกระทบยอด"):
        if not face.get(label):
            errors.append(f"the statement has no {label}")
    return errors


def check(case: dict, res: dict) -> list[str]:
    exp = case["expect"]
    errors = []
    asked = [f["code"] for f in res["asked"]]
    if asked != exp.get("asked", []):
        errors.append(f"asked {asked}, expected {exp.get('asked', [])}")
    if exp.get("stops_at") or res["stops_at"]:
        if exp.get("stops_at") != res["stops_at"]:
            errors.append(f"stopped at {res['stops_at']}, expected {exp.get('stops_at')}")
        return errors
    errors += invariants(res)
    c = res["computed"]
    st, t = c["statement"], c["totals"]
    got = {"bank_balance": st["bank"]["balance"], "book_balance": st["book"]["balance"], "adjusted_bank": st["bank"]["adjusted"],
           "adjusted_book": st["book"]["adjusted"], "difference": st["difference"], "tied": st["tied"], "gate": c["gate"]["code"],
           "matched": t["matched"], "by_person": t["by_person"], "suspect": t["suspect"], "journal": t["journal"],
           "period": c["period"]}
    for k, v in got.items():
        if k in exp and exp[k] != v:
            errors.append(f"{k}: {v!r}, expected {exp[k]!r}")
    if "what" in exp and c["gate"]["what"] != exp["what"]:
        errors.append(f"says {c['gate']['what']!r}, expected {exp['what']!r}")
    if "tiers" in exp:
        tiers = {x["tier"]: x["groups"] for x in c["match"]["tiers"]}
        if tiers != exp["tiers"]:
            errors.append(f"tiers {tiers}, expected {exp['tiers']}")
    if "items" in exp:
        kinds: dict = {}
        for i in c["items"]:
            kinds[i["type"]] = kinds.get(i["type"], 0) + 1
        if kinds != exp["items"]:
            errors.append(f"items {kinds}, expected {exp['items']}")
    if "review" in exp:
        review = sorted(e["reason_code"] for e in c["run_exceptions"] if e["severity"] == "review")
        if review != sorted(exp["review"]):
            errors.append(f"exceptions for a person {review}, expected {sorted(exp['review'])}")
    if "not_included" in exp:
        names = sorted(e["name"] for e in c["not_included"])
        if names != sorted(exp["not_included"]):
            errors.append(f"not included {names}, expected {sorted(exp['not_included'])}")
    if "can_restore" in exp:
        names = sorted(e["name"] for e in c["not_included"] if e["can_restore"] and e["restore"]["drop"])
        if names != sorted(exp["can_restore"]):
            errors.append(f"can be brought back {names}, expected {sorted(exp['can_restore'])}")
    if "not_included_code" in exp:
        codes = sorted(e["code"] for e in c["not_included"])
        if codes != [exp["not_included_code"]]:
            errors.append(f"not included because {codes}, expected {exp['not_included_code']}")
    if "passes" in exp:
        made = {p["id"]: p["by_rule"] + p["by_person"] for p in c["match"]["passes"]}
        for k, v in exp["passes"].items():
            if made.get(k, 0) != v:
                errors.append(f"pass {k} matched {made.get(k, 0)}, expected {v}")
    if exp.get("from_pdf"):
        rows = [r for m in c["match"]["groups"]["matched"] for r in m["right"]]
        if not rows or not all(str((r["cells"] or {}).get("date") or "").startswith("หน้า ") for r in rows):
            errors.append("a row read from a PDF is not cited by its page and line")
    if "shapes" in exp:
        shapes: dict = {}
        for m in c["match"]["groups"]["matched"]:
            if m["shape"] != "1:1":
                shapes[m["shape"]] = shapes.get(m["shape"], 0) + 1
        if shapes != exp["shapes"]:
            errors.append(f"shapes {shapes}, expected {exp['shapes']}")
    if "carried_cleared" in exp:
        cleared = sum(1 for x in c["carried_in"] if x["cleared"])
        still = sum(1 for x in c["carried_in"] if not x["cleared"])
        if (cleared, still) != (exp["carried_cleared"], exp["carried_open"]):
            errors.append(f"carried in: {cleared} cleared, {still} open; expected {exp['carried_cleared']}, {exp['carried_open']}")
    if "stale" in exp and len(c["ageing"]["stale"]) != exp["stale"]:
        errors.append(f"stale cheques {len(c['ageing']['stale'])}, expected {exp['stale']}")
    if "outside_period" in exp and t["outside_period"] != exp["outside_period"]:
        errors.append(f"outside the period {t['outside_period']}, expected {exp['outside_period']}")
    if "window" in exp and c["settings"]["matching"]["date_window_days"] != exp["window"]:
        errors.append(f"window {c['settings']['matching']['date_window_days']}, expected {exp['window']}")
    if exp.get("success") is False and c["gate"]["passed"]:
        errors.append("a round that does not tie was called a success")
    return errors


def tier_breakdown(results: list) -> dict:
    """The eval set as a whole: how many pairs each tier and each pass matched, and who confirmed them."""
    tiers: dict = {}
    passes: dict = {}
    for res in results:
        if res.get("stops_at"):
            continue
        for x in res["computed"]["match"]["tiers"]:
            t = tiers.setdefault(x["tier"], {"label": x["label"], "groups": 0, "by_rule": 0, "by_person": 0, "waiting": 0})
            for k in ("groups", "by_rule", "by_person", "waiting"):
                t[k] += x[k]
        for p in res["computed"]["match"]["passes"]:
            q = passes.setdefault(p["id"], {"label": p["label"], "tier": p["tier"], "auto": p["auto"], "by_rule": 0, "by_person": 0,
                                            "waiting": 0})
            for k in ("by_rule", "by_person", "waiting"):
                q[k] += p[k]
    return {"tiers": tiers, "passes": passes}


def main(argv: list) -> int:
    cases = load_cases()["cases"]
    want = set(argv)
    failed, results = 0, []
    for case in cases:
        if want and case["id"] not in want:
            continue
        res = run_case(case)
        results.append(res)
        errors = check(case, res)
        failed += 1 if errors else 0
        sys.stdout.write(f"{'FAIL' if errors else 'ok  '} {case['id']}\n")
        for e in errors:
            sys.stdout.buffer.write(("       " + e + "\n").encode("utf-8"))
    b = tier_breakdown(results)
    sys.stdout.buffer.write((json.dumps(b, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    sys.stdout.write(f"{len(results) - failed}/{len(results)} cases\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
