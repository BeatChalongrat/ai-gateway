"""Run the Invoice Processing eval set the way agent-runner runs a real case.

    python -m agent_market.tests.invoicekit [case-id ...]      (inside the sandbox image)

Every step goes through engine/step.py with files on disk (parse → parsed.json → preflight →
cards → compute → render), so the JSON round trip, the step hooks and the quality gate are the
ones production uses. A simulated person answers the case's answers; a card left unanswered takes
its default, as a skipped card does. Expected values come from the Phase A golden output
(fixtures/e2/expected), never from this package.

The invariant checked on every case: a recorded document's values are the golden ones (a value
the person typed excepted) — no silent wrong number reaches the register.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
from decimal import Decimal

from agent_market.engine import step
from agent_market.tests import e2kit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "packages", "invoice_processing")
E2 = os.path.join(ROOT, "fixtures", "e2")
TODAY = _dt.date(2026, 9, 30)
MODEL = "qwen3.8-27b-fp8"
CHECKED = ("seller_tax_id", "document_number", "document_date", "subtotal", "vat_amount", "total", "wht_amount",
           "net_payable")
TRIAGE = ("seller_tax_id", "document_date", "vat_amount", "total")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_cases() -> dict:
    cases = _load(os.path.join(PKG, "evals", "cases.json"))
    # AM6: the eval set ships inside the runtime zip, so it names no company; its cases are the
    # fixture pack's documents (not shipped), and the pack's buyer is every case's install settings
    ref = cases.get("company") or {}
    if "from" in ref:
        org = _load(os.path.join(ROOT, ref["from"]))
        cases["company"] = {"company": org["name"], "company_tax_id": org["tax_id"], "company_branch": org["branch"]}
    return cases


def _golden(fid: str) -> dict:
    return _load(os.path.join(E2, "expected", fid + ".json"))


def _files() -> dict:
    return {f["id"]: f["files"][0] for f in _load(os.path.join(E2, "index.json"))["fixtures"]}


def truth_fields(fid: str) -> dict:
    t = _golden(fid)["truth"]
    out = {}
    for k in ("seller_name", "buyer_name"):
        if t.get(k):
            out[k] = t[k]["value"]
    for k in ("seller_tax_id", "buyer_tax_id"):
        if t.get(k):
            out[k] = t[k]["value"]
    for k in ("seller_branch", "buyer_branch", "document_number", "document_date", "due_date", "currency", "sum_lines",
              "discount", "subtotal", "vat_amount", "total", "wht_amount", "net_payable"):
        if t.get(k) is not None:
            out[k] = t[k]
    # the bank account is printed in the spec's notes (the golden output predates the field)
    import re

    from agent_market.tools.e2fix import specs

    m = re.search(r"บัญชี.*?เลขที่\s*([\d-]{10,})", (specs.resolved().get(fid) or {}).get("notes") or "")
    if m:
        out["bank_account"] = m.group(1).replace("-", "")
    return out


class Job:
    """/job/in and /job/out for one case, as the runner lays them out."""

    def __init__(self, lang: str | None = None):
        self.lang = lang   # AM14: the run's language, as the runner tells every step of a run that is not Thai
        self.base = tempfile.mkdtemp(prefix="am_inv_")
        self.inp, self.out = os.path.join(self.base, "in"), os.path.join(self.base, "out")
        os.makedirs(self.inp)
        os.makedirs(self.out)

    def run(self, req: dict) -> dict:
        step.JOB_IN, step.JOB_OUT = self.inp, self.out
        res = step.run({**req, "lang": self.lang} if self.lang else req)
        assert res.get("ok"), res
        return res

    def take(self, name: str, as_name: str) -> str:
        shutil.move(os.path.join(self.out, name), os.path.join(self.inp, as_name))
        return as_name

    def write(self, name: str, obj) -> str:
        with open(os.path.join(self.inp, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        return name

    def close(self):
        shutil.rmtree(self.base, ignore_errors=True)


def _person(case: dict, finding: dict, keys: dict) -> object | None:
    """What the simulated person answers to a card (None: the card is skipped → its default)."""
    ans = case.get("_answers", {})
    if finding["key"] in ans:
        return ans[finding["key"]]
    if not case.get("accept_proposals"):
        return None
    fid = next((f for f, k in keys.items() if k and finding["subject"].startswith(k)), None)
    field = (finding.get("facts") or {}).get("field")
    if fid is None or field is None or finding["code"] not in ("e2_low_confidence", "e2_source_conflict",
                                                               "e2_field_missing", "e2_typed_invalid"):
        return None
    truth = truth_fields(fid).get(field)
    if truth is None:
        return "absent" if any(o["value"] == "absent" for o in finding["options"]) else None
    # the person reads the document: picks the proposal that is right, or types the value
    for o in finding["options"]:
        v = o["value"]
        if isinstance(v, dict) and "accept" in v and e2kit._norm_core(str(v["accept"])) == e2kit._norm_core(str(truth)):
            return v
    return {"typed": str(truth)}


def run_case(case: dict, lang: str | None = None) -> dict:
    """``lang``: the run's language, when it is not Thai (AM14)."""
    fmap = _files()
    job = Job(lang)
    try:
        defaults = dict(load_cases()["company"])
        parsed, keys, vision = [], {}, {}
        for i, fid in enumerate(case["files"]):
            src = os.path.join(E2, fmap[fid])
            inp = f"in_{i:02d}{os.path.splitext(src)[1]}"
            shutil.copy(src, os.path.join(job.inp, inp))
            res = job.run({"op": "parse", "package": "invoice_processing",
                           "file": {"name": os.path.basename(src), "input": inp}})
            parsed.append(job.take("parsed.json", f"parsed_{i:02d}.json"))
            keys[fid] = res["summary"]["doc_key"]
            vision[fid] = [n for n in res["summary"].get("vision_pages", []) if os.path.exists(os.path.join(job.out, n))]
            for n in res["summary"].get("vision_pages", []):
                os.remove(os.path.join(job.out, n))
        req = {"package": "invoice_processing", "parsed": parsed, "defaults": defaults, "today": TODAY.isoformat()}
        if case.get("history"):
            req["history"] = job.write("history.json", e2kit.history_for({"history": case["history"]}, {}))
        if case.get("proposals"):
            props = {}
            for fid, p in case["proposals"].items():
                # AM16: ``reader`` — which model the runner says read the page (a test of a page read outside)
                props[keys[fid]] = {**(case.get("reader") or {"model": MODEL, "self_hosted": True}),
                                    "fields": truth_fields(fid) if p == "truth" else p}
            req["proposals"] = job.write("proposals.json", props)
        answers_in = {}
        for k, v in (case.get("answers") or {}).items():
            for fid, dk in keys.items():
                k = k.replace("{" + fid + "}", dk)
            answers_in[k] = v
        case = {**case, "_answers": answers_in}
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
                a = _person(case, f, keys)
                answers[f["key"]] = f["default"] if a is None else a
        comp = job.run({**req, "op": "compute", "roles": pre["roles"], "period": pre["period"], "answers": answers,
                        "today": pre["today"]})
        computed = _load(os.path.join(job.out, "computed.json"))
        shutil.copy(os.path.join(job.out, "computed.json"), os.path.join(job.inp, "computed.json"))
        rend = job.run({"op": "render", "package": "invoice_processing", "computed": "computed.json", "formats": ["xlsx"],
                        "names": {"xlsx": "register.xlsx"}, "theme": {"id": "navy"}, "run": {"date": TODAY.isoformat()}})
        from openpyxl import load_workbook

        book = load_workbook(os.path.join(job.out, "register.xlsx"))
        sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets}
        return {"id": case["id"], "keys": keys, "vision_pages": vision, "first": first, "asked": asked,
                "answers": answers, "computed": computed, "summary": comp["summary"], "render": rend, "sheets": sheets,
                "paths": [os.path.join(E2, fmap[fid]) for fid in case["files"]]}
    finally:
        job.close()


def check(case: dict, res: dict) -> list[str]:
    exp = case["expect"]
    errors = []
    keys, comp = res["keys"], res["computed"]
    by_key = {v: k for k, v in keys.items()}
    docs = {by_key.get(d["doc_key"]): d for d in comp["documents"] if d["status"] == "included"}
    excl = {}
    for d in comp["documents"]:
        if d["status"] == "excluded":
            excl.setdefault(by_key.get(d["doc_key"]), []).extend(d["exclusion"].get("codes") or [d["exclusion"]["code"]])
    first = {}
    for f in res["first"]["findings"]:
        fid = next((x for x, k in keys.items() if f["subject"].startswith(k)), None)
        first.setdefault(fid, []).append(f)
    # 1 · what was asked first
    fq = exp.get("first_questions")
    if fq == "none" and res["first"]["findings"]:
        errors.append(f"asked on a zero-question case: {[f['key'] for f in res['first']['findings']]}")
    elif fq == "golden":
        for fid in set(case["files"]):
            g = _golden(fid)["questions"]
            got = sorted({_e2_code(f) for f in first.get(fid, [])})
            must = sorted({m.split(":")[0] for m in g["must"]})
            allowed = set(must) | {a.split(":")[0] for a in g.get("allowed_extra") or []}
            if any(m not in got for m in must):
                errors.append(f"{fid}: missing {must} (asked {got})")
            if not g["may"] and any(c not in allowed for c in got):
                errors.append(f"{fid}: asked {got}, allowed {sorted(allowed)}")
            if g["max"] is not None and len(first.get(fid, [])) > g["max"]:
                errors.append(f"{fid}: {len(first.get(fid, []))} questions > max {g['max']}")
    elif isinstance(fq, dict):
        for fid, codes in fq.items():
            got = sorted({f["code"] for f in first.get(fid, [])})
            if exp.get("first_questions_mode") == "includes":
                if not set(codes) <= set(got):
                    errors.append(f"{fid}: asked {got}, must include {codes}")
            elif got != sorted(set(codes)):
                errors.append(f"{fid}: asked {got}, expected {sorted(set(codes))}")
    # 2 · who is in, who is out, and why
    inc = exp.get("included")
    asked_fids = {fid for fid in first if fid}
    if inc == "all":
        inc = sorted(set(case["files"]))
    elif inc == "unasked":
        inc = sorted(set(case["files"]) - asked_fids)
    if sorted(docs) != sorted(inc):
        errors.append(f"included {sorted(docs)}, expected {sorted(inc)}")
    ex = exp.get("excluded")
    if ex == "asked":
        want = sorted(asked_fids)
        if sorted(k for k in excl if k in asked_fids) != want:
            errors.append(f"excluded {sorted(excl)}, expected {want}")
    elif isinstance(ex, dict):
        for fid, code in ex.items():
            if code not in excl.get(fid, []):
                errors.append(f"{fid}: excluded as {excl.get(fid)}, expected {code}")
        extra = set(excl) - set(ex) - ({case['files'][0]} if exp.get("same_file_twice") else set())
        if extra:
            errors.append(f"unexpectedly excluded: {sorted(extra)}")
    if exp.get("same_file_twice") and sum(c.count("same_file_twice") for c in excl.values()) != exp["same_file_twice"]:
        errors.append(f"same_file_twice: {excl}")
    for fid, codes in (exp.get("notes") or {}).items():
        got = sorted(e["code"] for e in comp["exceptions"] if e["doc_key"] == keys[fid] and e["severity"] == "recorded")
        if any(c not in got for c in codes):
            errors.append(f"{fid}: notes {got}, expected {codes}")
    for k in exp.get("asked_again") or []:
        for fid, dk in keys.items():
            k = k.replace("{" + fid + "}", dk)
        if k not in res["asked"]:
            errors.append(f"not asked again: {k}")
    # 3 · the invariant: a recorded value is the golden one, or the one the person typed
    typed = exp.get("typed") or {}
    for fid, d in docs.items():
        truth = _golden(fid)["truth"]
        rate = Decimal(exp.get("fx", {}).get(fid, "1"))
        scope = TRIAGE if d["triage_only"] else CHECKED
        for n in scope:
            if n in typed.get(fid, {}):
                if str(d[n]) != typed[fid][n]:
                    errors.append(f"{fid}.{n}: {d[n]} != typed {typed[fid][n]}")
                continue
            want = truth.get(n)
            want = want["value"] if isinstance(want, dict) else want
            got = d[n]
            if n in ("subtotal", "vat_amount", "total", "wht_amount", "net_payable"):
                ok = (want is None and got is None) or (want is not None and got is not None and Decimal(got) == Decimal(want))
                if n == "vat_amount" and want is None and truth.get("vat_mode") == "none":
                    ok = got is None or Decimal(got) == 0
            elif n == "document_number":
                ok = got is not None and want is not None and got.replace(" ", "") == want.replace(" ", "")
            else:
                ok = (got or None) == (want or None)
            if not ok:
                errors.append(f"SILENT {fid}.{n}: register {got!r}, truth {want!r}")
        if d["thb_total"] is not None and truth.get("total") is not None and "total" not in typed.get(fid, {}):
            if Decimal(d["thb_total"]) != (Decimal(truth["total"]) * rate).quantize(Decimal("0.01")):
                errors.append(f"{fid}: THB total {d['thb_total']} != {truth['total']} x {rate}")
    for p in comp["provenance"]:
        if p["decided_by"] == "model_proposal" and by_key.get(p["doc_key"]) in docs:
            errors.append(f"a model-only value recorded: {by_key.get(p['doc_key'])}.{p['field']}")
    if exp.get("model_documents") is not None:
        n = sum(len(m["documents"]) for m in comp["models"])
        if n != exp["model_documents"] or any(not m["self_hosted"] for m in comp["models"]):
            errors.append(f"models {comp['models']}")
    # 4 · the deliverable passed its gate
    arts = res["render"]["artifacts"]
    if not res["render"]["passed"] or not arts:
        errors.append(f"quality gate: {[c for a in arts for c in a['quality']['checks'] if not c['ok']]}")
    for fid, pages in res["vision_pages"].items():
        tier = _golden(fid)["class"]
        if (tier in ("scan300", "scan200", "photo")) != bool(pages):
            errors.append(f"{fid}: vision pages {pages} for a {tier} document")
    return errors


def _e2_code(f: dict) -> str:
    return {"e2_field_missing": "field_missing", "e2_low_confidence": "low_confidence", "e2_check_failed": "check_failed",
            "e2_source_conflict": "source_conflict", "e2_duplicate": "duplicate_candidate", "e2_copy": "copy_not_original",
            "e2_doc_type": "document_type_unknown", "e2_page_quality": "page_quality",
            "e2_typed_invalid": "typed_invalid"}.get(f["code"], f["code"])


def main(argv: list[str]) -> int:
    bad = 0
    for case in load_cases()["cases"]:
        if argv and case["id"] not in argv:
            continue
        res = run_case(case)
        errs = check(case, res)
        bad += bool(errs)
        t = res["computed"]["totals"]
        print(f"{'OK  ' if not errs else 'FAIL'} {case['id']:38s} docs {t['documents']:2d} in {t['included']:2d} "
              f"out {t['excluded']:2d} asked {len(res['asked']):2d}  THB {t['thb']['total']}")
        for e in errs:
            print("     ", e)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
