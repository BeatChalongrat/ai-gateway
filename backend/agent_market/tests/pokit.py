"""Run the invoice ↔ PO eval set the way agent-runner runs a real case.

    python -m agent_market.tests.pokit [case-id ...]      (inside the sandbox image)

Every step goes through engine/step.py with files on disk (parse → parsed.json → preflight → cards
→ compute → render), so the JSON round trip and the quality gate are the ones production uses. A
simulated person answers the case's answers; a card left unanswered takes its default, as a skipped
card does; a request for files that is answered "wait" stops the case there, as a parked run does.
A case's earlier rounds run first: what each records (index_entries) is the install's history of
the next, newest first, as the runner hands it over. Expected values come from the scenario in
tools/make_po_fixtures.py, never from this package.

Checked on every case that reaches a result, whatever it says:
  * every invoice line is in one state: linked to a PO line, waiting for a person, a cost nobody
    ordered, or on no PO;
  * every link below the exact tier was confirmed by a person, and says its pass and its tier;
  * every line says whether it was tested three-way or two-way and why; a three-way line names the
    receipts it was tested against, a two-way line has none;
  * an invoice that passes has no reason code; one that does not pass has one;
  * a status is one of three, and none of them is an approval;
  * every PO line's billed quantity is the sum of the lines counted on it;
  * the summary's counts are the invoices', and its face says how the lines were tested;
  * the working paper passed its own quality gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from decimal import Decimal

from agent_market.tests.invoicekit import Job

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "packages", "po_matching")
FIX = os.path.join(PKG, "evals", "fixtures")
PACKAGE = "po_matching"
BOOK = "po_matching.xlsx"
STATUSES = ("pass", "blocked", "person")
DEPTHS = ("three_way", "two_way", "none")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_cases() -> dict:
    return _load(os.path.join(PKG, "evals", "cases.json"))


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _answer(wanted: dict, f: dict):
    """The case's answer to a card: by its key, else by its code (every card of that code)."""
    if f["key"] in wanted:
        return wanted[f["key"]]
    return wanted.get(f["code"])


def run_files(paths: list, *, defaults: dict | None = None, answers: dict | None = None, message: str = "",
              today: str = "2026-10-02", preparer: str = "ผู้จัดทำ ตัวอย่าง", answered_by: str | None = "สมหญิง ผู้ตรวจ",
              history: list | None = None, keep: str | None = None, lang: str | None = None) -> dict:
    """paths: [(path on disk, the name the person gave the file)]. ``history``: what earlier rounds
    recorded. ``answered_by``: the name of the person who answers the cards. ``lang``: the run's language, when
    it is not Thai (AM14)."""
    job = Job(lang)
    try:
        parsed, files = [], {}
        for i, (src, name) in enumerate(paths):
            inp = f"in_{i:02d}{os.path.splitext(name)[1]}"
            shutil.copy(src, os.path.join(job.inp, inp))
            res = job.run({"op": "parse", "package": PACKAGE, "file": {"name": name, "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
            for n in res["summary"].get("vision_pages", []) or []:
                p = os.path.join(job.out, n)
                if os.path.exists(p):
                    os.remove(p)
            files[name] = _sha(src)
        req = {"package": PACKAGE, "parsed": parsed, "defaults": defaults or {}, "today": today}
        if history is not None:
            req["history"] = job.write("history.json", history)
        wanted = dict(answers or {})
        given: dict = {k: v for k, v in wanted.items() if k.startswith("pomatch_receipt:")}   # set by an action, not a card
        asked: list = []
        stop = None
        pre = None
        for _ in range(16):
            pre = job.run({**req, "op": "preflight", "answers": given, "message": message})
            new = [f for f in pre["findings"] if f["key"] not in given]
            if not new:
                break
            for f in new:
                asked.append(f)
                a = _answer(wanted, f)
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
            defaulted = {f["key"] for f in asked if given.get(f["key"]) == f["default"] and _answer(wanted, f) is None}
            creq["answered_by"] = {k: {"name": answered_by, "via": "card"} for k in given if k not in defaulted}
        comp = job.run(creq)
        with open(os.path.join(job.out, "computed.json"), "rb") as f:
            raw = f.read()
        computed = json.loads(raw)
        shutil.copy(os.path.join(job.out, "computed.json"), os.path.join(job.inp, "computed.json"))
        rend = job.run({"op": "render", "package": PACKAGE, "computed": "computed.json", "formats": ["xlsx"],
                        "names": {"xlsx": BOOK}, "theme": {"id": "navy"},
                        "run": {"date": today, "preparer": preparer, "agent_version": "1.3.0"}})
        book = os.path.join(job.out, BOOK)
        keep = keep or os.environ.get("POKIT_KEEP")
        if keep:
            os.makedirs(keep, exist_ok=True)
            shutil.copy(book, os.path.join(keep, BOOK))
            with open(os.path.join(keep, "computed.json"), "wb") as f:
                f.write(raw)
        from openpyxl import load_workbook

        wb = load_workbook(book)
        sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        # AM9: the wide tables as the check reads them — by header name, in the order they are built (AM10: from
        # the main sheet and its companions together)
        from agent_market.deliverables.reread import table
        from agent_market.packages.po_matching.scripts.workbook import H_INVOICES, H_LINES, S_INVOICES, S_LINES, parts

        out.update({"computed": computed, "computed_bytes": raw, "summary": comp["summary"], "render": rend, "sheets": sheets,
                    "lines": table(parts(wb, S_LINES), H_LINES), "invoices": table(parts(wb, S_INVOICES), H_INVOICES)})
        return out
    finally:
        job.close()


def history_after(history: list, res: dict) -> list:
    """The install's history once a round has recorded its documents: newest first."""
    if res.get("stops_at"):
        return history
    return list(res["computed"]["index_entries"]) + list(history)


def run_case(case: dict, lang: str | None = None, *, keep: str | None = None) -> dict:
    """``lang``: the run's language, when it is not Thai — of every round of the case (AM14)."""
    cases = load_cases()
    base = cases["defaults"]
    history: list = []
    earlier = []
    for r in case.get("rounds") or []:
        res = run_files([(os.path.join(FIX, n), n) for n in r["files"]], defaults={**base, **(case.get("defaults") or {})},
                        answers=r.get("answers"), today=cases["today"], history=history, lang=lang)
        earlier.append(res)
        history = history_after(history, res)
    res = run_files([(os.path.join(FIX, n), n) for n in case["files"]], defaults={**base, **(case.get("defaults") or {})},
                    answers=case.get("answers"), message=case.get("message") or "", today=cases["today"], history=history, keep=keep,
                    lang=lang)
    res["id"] = case["id"]
    res["earlier"] = earlier
    return res


def invariants(res: dict) -> list:
    """What holds for every round, whatever the case expects."""
    c = res["computed"]
    errors = []
    by_id = {x["id"]: x for x in c["lines"]}
    if len(by_id) != len(c["lines"]):
        errors.append("two lines share an id")
    for x in c["lines"]:
        states = [x["po_line"] is not None, x.get("proposed") is not None, x["unplanned"] is not None,
                  "item_not_on_po" in x["reasons"]]
        if sum(states) != 1:
            errors.append(f"{x['id']}: in {sum(states)} states (linked, waiting, not ordered, on no PO)")
        if x["depth"] not in DEPTHS or not x["depth_label"] or not x["depth_why"]:
            errors.append(f"{x['id']}: no match depth")
        if x["po_line"] is not None:
            link = x["link"]
            if not link or not link["pass"] or not link["tier"]:
                errors.append(f"{x['id']}: a link without its pass or tier")
            elif link["tier"] != "exact" and link["confirmed"] != "person":
                errors.append(f"{x['id']}: tier {link['tier']} linked without a person")
            elif link["tier"] == "exact" and link["confirmed"] != "rule":
                errors.append(f"{x['id']}: an exact link is made by its rule")
            if x["depth"] == "none":
                errors.append(f"{x['id']}: linked and not tested")
            if not (x["po_line"].get("file") and x["po_line"].get("row")):
                errors.append(f"{x['id']}: the PO line has no provenance")
        elif x["depth"] != "none":
            errors.append(f"{x['id']}: not linked, yet {x['depth']}")
        if x["depth"] == "three_way" and not x["receipts"]:
            errors.append(f"{x['id']}: three-way without a receipt")
        if x["depth"] == "two_way" and x["receipts"]:
            errors.append(f"{x['id']}: two-way with a receipt")
        for g in x["receipts"]:
            if not g["in_system"] and not (g["file"] and g["row"]):
                errors.append(f"{x['id']}: a receipt without provenance")
        if x["status"] not in STATUSES:
            errors.append(f"{x['id']}: status {x['status']!r}")
        for k in x["checks"]:
            if k["tolerance"] and not set(k["tolerance"]) <= set(x["tolerances"]):
                errors.append(f"{x['id']}: a tolerance that was used is not recorded on the line")
    for m in c["match"]["links"]:
        if m["state"] == "linked" and m["tier"] != "exact" and m["confirmed"] != "person":
            errors.append(f"{m['pair']}: tier {m['tier']} linked without a person")
    for i in c["invoices"]:
        if i["status"] not in STATUSES:
            errors.append(f"{i['number']}: status {i['status']!r}")
        if (i["status"] == "pass") != (not i["reasons"]):
            errors.append(f"{i['number']}: status {i['status']} with reasons {i['reasons']}")
        for r in i["reasons"]:
            if r not in c["reason_codes"]:
                errors.append(f"{i['number']}: unknown reason {r}")
        mine = [by_id[k] for k in i["lines"]]
        tested = [x for x in mine if x["unplanned"] is None]
        got = {d: sum(1 for x in tested if x["depth"] == d) for d in DEPTHS}
        if got != i["depths"]:
            errors.append(f"{i['number']}: depths {i['depths']}, its lines say {got}")
    text = json.dumps(c, ensure_ascii=False)
    for word in ("อนุมัติแล้ว", "จ่ายแล้ว", "approved", "paid"):
        if word in text:
            errors.append(f"the result says {word!r}")
    t, s = c["totals"], c["summary"]
    if sum(x["count"] for x in s["by_status"]) != len(c["invoices"]) or t["invoices"] != len(c["invoices"]):
        errors.append("the summary's counts are not the invoices'")
    for d, key in (("three_way", "three_way"), ("two_way", "two_way"), ("none", "unlinked")):
        n = sum(1 for x in c["lines"] if x["depth"] == d and x["unplanned"] is None)
        if t[key] != n:
            errors.append(f"{key}: the summary says {t[key]}, the lines {n}")
    counted: dict = {}
    for x in c["lines"]:
        if x["counted"] and x["po_line"] is not None and x.get("quantity_in_po_unit") not in (None, ""):
            inv = next(i for i in c["invoices"] if i["key"] == x["invoice"])
            sign = -1 if inv["doc_type"] == "credit_note" else 1
            counted[x["po_line"]["id"]] = counted.get(x["po_line"]["id"], Decimal(0)) + sign * Decimal(x["quantity_in_po_unit"])
    for p in c["po_lines"]:
        now = sum((Decimal(e["quantity"]) for e in p["invoices"] if e["from"] == "round"), Decimal(0))
        if now != counted.get(p["id"], Decimal(0)):
            errors.append(f"{p['po']} line {p['line_no']}: billed in this round {now}, its lines add up to {counted.get(p['id'], 0)}")
        if Decimal(p["billed"]) != sum((Decimal(e["quantity"]) for e in p["invoices"]), Decimal(0)):
            errors.append(f"{p['po']} line {p['line_no']}: billed {p['billed']} is not the sum of its invoices")
    for e in c["run_exceptions"]:
        if not e["choices"]:
            errors.append(f"an exception without choices: {e['reason_code']}")
    for a in res["render"]["artifacts"]:
        bad = [k["name"] for k in a["quality"]["checks"] if not k["ok"]]
        if bad or not a["quality"]["passed"]:
            errors.append(f"quality gate: {bad}")
    face = {r[0]: r[1] for r in res["sheets"]["สรุป"] if r and isinstance(r[0], str)}
    for label in ("นิติบุคคล", "งวด", "ผู้จัดทำ", "บริษัทออกใบรับของหรือไม่", "วิธีตรวจของรอบนี้", "การอนุมัติจ่าย"):
        if not face.get(label):
            errors.append(f"the summary has no {label}")
    return errors


def _line(c: dict, ref: str) -> dict | None:
    number, _, n = ref.partition("#")
    inv = next((i for i in c["invoices"] if i["number"] == number), None)
    if inv is None:
        return None
    return next((x for x in c["lines"] if x["invoice"] == inv["key"] and str(x["n"]) == n), None)


def check(case: dict, res: dict) -> list:
    exp = case["expect"]
    errors = []
    asked = [f["code"] for f in res["asked"]]
    if asked != exp.get("asked", []):
        errors.append(f"asked {asked}, expected {exp.get('asked', [])}")
    if exp.get("stops_at") or res["stops_at"]:
        if exp.get("stops_at") != res["stops_at"]:
            errors.append(f"stopped at {res['stops_at']}, expected {exp.get('stops_at')}")
        return errors
    for n, (r, early) in enumerate(zip(case.get("rounds") or [], res["earlier"])):
        if early.get("stops_at"):
            errors.append(f"round {n + 1} stopped at {early['stops_at']}")
            continue
        errors += [f"round {n + 1}: {e}" for e in invariants(early)]
        want = (r.get("expect") or {}).get("invoices") or {}
        got = {i["number"]: {"status": i["status"], "reasons": sorted(i["reasons"])} for i in early["computed"]["invoices"]}
        for k, v in want.items():
            if got.get(k) != v:
                errors.append(f"round {n + 1}: {k} is {got.get(k)}, expected {v}")
        if "period" in (r.get("expect") or {}) and early["computed"]["period"] != r["expect"]["period"]:
            errors.append(f"round {n + 1}: period {early['computed']['period']}")
    errors += invariants(res)
    c = res["computed"]
    t = c["totals"]
    got = {i["number"]: {"status": i["status"], "reasons": sorted(i["reasons"])} for i in c["invoices"]}
    want = dict(exp["invoices"])
    if exp.get("status_is"):
        # what the scenario calls it and what the rules make of it once every reason is counted
        want = {k: {**v, "status": exp["status_is"]} for k, v in want.items()}
    if got != want:
        errors.append(f"invoices {got}, expected {want}")
    for k, v in exp["totals"].items():
        if exp.get("status_is") and k in ("passed", "blocked", "person"):
            continue
        if t[k] != v:
            errors.append(f"{k}: {t[k]!r}, expected {v!r}")
    if not exp.get("status_is"):
        value = {"pass": t["value_passed"], "blocked": t["value_blocked"], "person": t["value_person"]}
        if value != exp["value"]:
            errors.append(f"value {value}, expected {exp['value']}")
    grir = {k: c["grir"][k] for k in ("received_not_billed", "billed_not_received")}
    if grir != exp["grir"]:
        errors.append(f"GR/IR {grir}, expected {exp['grir']}")
    tiers = {x["tier"]: x["groups"] for x in c["match"]["tiers"]}
    if tiers != exp["tiers"]:
        errors.append(f"tiers {tiers}, expected {exp['tiers']}")
    if t["by_person"] != exp["by_person"]:
        errors.append(f"by a person {t['by_person']}, expected {exp['by_person']}")
    if c["period"] != exp["period"]:
        errors.append(f"period {c['period']}, expected {exp['period']}")
    face = {r[0]: r[1] for r in res["sheets"]["สรุป"] if r and isinstance(r[0], str)}
    if "depth_on_face" in exp and not str(face.get("วิธีตรวจของรอบนี้") or "").startswith(exp["depth_on_face"]):
        errors.append(f"the face says {face.get('วิธีตรวจของรอบนี้')!r}, expected {exp['depth_on_face']!r}")
    if "two_way_warning" in exp:
        from agent_market.packages.po_matching.scripts.workbook import TWO_WAY_WARNING

        warned = any(r and r[0] == TWO_WAY_WARNING for r in res["sheets"]["สรุป"])
        if warned != exp["two_way_warning"]:
            errors.append(f"the face warns about two-way lines: {warned}, expected {exp['two_way_warning']}")
    if "two_way_why" in exp:
        why = {x["why"]: x["lines"] for x in c["summary"]["two_way_why"]}
        if why != exp["two_way_why"]:
            errors.append(f"two-way because {why}, expected {exp['two_way_why']}")
    if "reason_lines" in exp:
        lines = {r["code"]: r["lines"] for r in c["summary"]["by_reason"]}
        for k, v in exp["reason_lines"].items():
            if lines.get(k) != v:
                errors.append(f"{k} on {lines.get(k)} lines, expected {v}")
    if "recommended" in exp:
        for f in res["asked"]:
            if f["code"] in exp["recommended"] and f.get("recommended") != exp["recommended"][f["code"]]:
                errors.append(f"{f['code']} recommends {f.get('recommended')!r}, expected {exp['recommended'][f['code']]!r}")
    if "practice" in exp and c["practice"]["gr"] != exp["practice"]:
        errors.append(f"practice {c['practice']['gr']}, expected {exp['practice']}")
    if "offers" in exp and len(c["receipt_offers"]) != exp["offers"]:
        errors.append(f"{len(c['receipt_offers'])} receipts offered for confirmation, expected {exp['offers']}")
    for ref, want_line in (exp.get("lines") or {}).items():
        x = _line(c, ref)
        cum = (x or {}).get("cumulative") or {}
        for k, v in want_line.items():
            g = cum.get(k)
            if g is None or Decimal(g) != Decimal(v):
                errors.append(f"{ref} {k}: {g!r}, expected {v!r}")
    for ref, want_var in (exp.get("variance") or {}).items():
        x = _line(c, ref)
        var = (x or {}).get("price_variance") or {}
        for k, v in want_var.items():
            if var.get(k) != v:
                errors.append(f"{ref} price variance {k}: {var.get(k)!r}, expected {v!r}")
    for ref in exp.get("price_variance_zero") or []:
        x = _line(c, ref)
        if ((x or {}).get("price_variance") or {}).get("extended") != "0.00":
            errors.append(f"{ref}: a cost nobody ordered moved the line's price variance")
    for ref in exp.get("tolerance_recorded") or []:
        x = _line(c, ref) or {}
        if not {"price_tolerance_pct", "price_tolerance_abs"} <= set(x.get("tolerances") or {}):
            errors.append(f"{ref}: the price tolerances are not recorded on the line")
    if "unplanned" in exp:
        got_u = [{"invoice": u["invoice_number"], "kind": u["kind"], "amount": u["amount"]} for u in c["unplanned"]]
        if got_u != exp["unplanned"]:
            errors.append(f"not ordered {got_u}, expected {exp['unplanned']}")
    if "notes" in exp:
        got_n = [{"number": n["number"], "corrects": n["corrects"]} for n in c["notes"]]
        if got_n != exp["notes"]:
            errors.append(f"notes {got_n}, expected {exp['notes']}")
    if "note_has" in exp:
        x = _line(c, exp["note_has"][0]) or {}
        if not any(exp["note_has"][1] in n for n in x.get("notes") or []):
            errors.append(f"{exp['note_has'][0]}: no note saying {exp['note_has'][1]!r}")
    if "vat_claim" in exp:
        for i in c["invoices"]:
            if i["vat"]["claimable"] != exp["vat_claim"]:
                errors.append(f"{i['number']}: input VAT claimable {i['vat']['claimable']}, expected {exp['vat_claim']}")
    if "fx" in exp:
        for i in c["invoices"]:
            if i["number"] in exp["fx"] and (i["fx_rate"] is None or Decimal(i["fx_rate"]) != Decimal(exp["fx"][i["number"]])
                                             or not i["fx_from"]):
                errors.append(f"{i['number']}: rate {i['fx_rate']} from {i['fx_from']!r}")
    if "po_read_from" in exp:
        for i in c["invoices"]:
            if exp["po_read_from"] not in (i["refs_read"] or ""):
                errors.append(f"{i['number']}: PO reference read from {i['refs_read']!r}, expected {exp['po_read_from']!r}")
    if "confirmed_by" in exp:
        names = [x["link"]["confirmed_by"] for x in c["lines"] if x["link"] and x["link"]["confirmed"] == "person"]
        if names != [exp["confirmed_by"]] * len(names) or not names:
            errors.append(f"confirmed by {names}, expected {exp['confirmed_by']!r}")
    if "passes" in exp:
        made = {p["id"]: p["by_rule"] + p["by_person"] for p in c["match"]["passes"]}
        for k, v in exp["passes"].items():
            if made.get(k, 0) != v:
                errors.append(f"pass {k} made {made.get(k, 0)} links, expected {v}")
    if "waiting" in exp and t["waiting"] != exp["waiting"]:
        errors.append(f"waiting {t['waiting']}, expected {exp['waiting']}")
    for ref in exp.get("receipts_in_system") or []:
        x = _line(c, ref) or {}
        if x.get("depth") != "three_way" or not all(g["in_system"] for g in x.get("receipts") or [{}]):
            errors.append(f"{ref}: not three-way by a receipt confirmed in the system")
    if exp.get("carried") and not any(a["code"] == "carried_values" for a in c["assumptions"]):
        errors.append("the round does not say that values were carried down from an order's first line")
    if "never_in_output" in exp:
        blob = res["computed_bytes"].decode("utf-8") + json.dumps(res["summary"], ensure_ascii=False) + \
            json.dumps(res["sheets"], ensure_ascii=False, default=str) + json.dumps(res["preflight"], ensure_ascii=False)
        for word in exp["never_in_output"]:
            if word in blob:
                errors.append(f"{word!r} from the document is in what the round wrote")
    for name in exp.get("alone_each_passes") or []:
        alone = run_files([(os.path.join(FIX, n), n) for n in case["files"][:2] + [name]],
                          defaults={**load_cases()["defaults"], **(case.get("defaults") or {})}, today=load_cases()["today"], history=[])
        bad = [i["number"] for i in alone["computed"]["invoices"] if i["status"] != "pass"]
        if bad:
            errors.append(f"{name} alone: {bad} does not pass")
    return errors


def breakdown(results: list) -> dict:
    """The eval set as a whole: invoices by status and by reason code, lines by match depth, links by tier and pass."""
    out = {"invoices": 0, "status": {}, "reasons": {}, "depth": {}, "tiers": {}, "passes": {}}
    for res in results:
        if res.get("stops_at"):
            continue
        c = res["computed"]
        out["invoices"] += len(c["invoices"])
        for i in c["invoices"]:
            out["status"][i["status"]] = out["status"].get(i["status"], 0) + 1
            for r in i["reasons"]:
                b = out["reasons"].setdefault(r, {"label": c["reason_codes"][r], "invoices": 0, "cases": []})
                b["invoices"] += 1
                if res["id"] not in b["cases"]:
                    b["cases"].append(res["id"])
        for x in c["lines"]:
            if x["unplanned"] is None:
                out["depth"][x["depth"]] = out["depth"].get(x["depth"], 0) + 1
        for x in c["match"]["tiers"]:
            t = out["tiers"].setdefault(x["tier"], {"label": x["label"], "groups": 0, "by_rule": 0, "by_person": 0, "waiting": 0})
            for k in ("groups", "by_rule", "by_person", "waiting"):
                t[k] += x[k]
        for p in c["match"]["passes"]:
            q = out["passes"].setdefault(p["id"], {"label": p["label"], "tier": p["tier"], "auto": p["auto"], "by_rule": 0,
                                                   "by_person": 0, "waiting": 0})
            for k in ("by_rule", "by_person", "waiting"):
                q[k] += p[k]
    return out


def main(argv: list) -> int:
    cases = load_cases()["cases"]
    want = set(a for a in argv if not a.startswith("-"))
    failed, results = 0, []
    for case in cases:
        if want and case["id"] not in want:
            continue
        try:
            res = run_case(case)
            errors = check(case, res)
        except Exception as exc:  # noqa: BLE001 — a case that cannot run is a failed case, with its reason
            import traceback

            res, errors = {"id": case["id"], "stops_at": "error"}, [f"{type(exc).__name__}: {exc}", traceback.format_exc()[-1500:]]
        results.append(res)
        failed += 1 if errors else 0
        sys.stdout.write(f"{'FAIL' if errors else 'ok  '} {case['id']}\n")
        for e in errors:
            sys.stdout.buffer.write(("       " + str(e) + "\n").encode("utf-8"))
        sys.stdout.flush()
    sys.stdout.flush()
    if "--breakdown" in argv:
        sys.stdout.buffer.write((json.dumps(breakdown(results), ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    sys.stdout.write(f"{len(results) - failed}/{len(results)} cases\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
