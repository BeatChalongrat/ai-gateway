"""AM12 — what the procurement package hands to a thread, in Thai and in English.

Run inside the sandbox image (the package's own home — it needs the document libraries):

    bash scripts/probes/AM12/dump_procurement_texts.sh          # writes the Go table

Every finding the package can raise (which of several things is meant, how many, where to deliver, which
candidate), the compute summary, every quality check of the rendered files and of a filled form, and every
assumption — each in both languages, as the runner receives them. Written as the Go test table
services/chat-edge-api/internal/agentmarket/am12_packages_test.go (const procurementOutputs), which the
control plane's own door is then tried on: a sentence of the package must come out of it as it went in.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path[0:0] = ["/src", "/sandbox/scripts"]

from agent_market.deliverables import template_fill as tf, template_out as to  # noqa: E402
from agent_market.engine import step  # noqa: E402
from agent_market.packages import procurement as P  # noqa: E402
from agent_market.tests import prformkit as K  # noqa: E402

PKG = os.path.dirname(os.path.abspath(P.__file__))
TODAY = datetime.date(2026, 10, 6)
findings, summaries, checks, assumptions = [], [], [], []
seen = set()


def once(kind, obj) -> bool:
    k = (kind, json.dumps(obj, ensure_ascii=False, sort_keys=True))
    if k in seen:
        return False
    seen.add(k)
    return True


def keep_findings(case, out):
    for f in out.get("findings") or []:
        f = dict(f)
        # the comparison is data the screen draws (am12_web.test.tsx); its words are checked where they are made
        f["evidence"] = {}
        if once("finding", {k: f.get(k) for k in ("header", "question", "options", "input")}):
            findings.append({"package": "procurement", "case": case, "finding": f})
    for a in out.get("assumptions") or []:
        if once("assumption", a):
            assumptions.append({"package": "procurement", "case": case, "assumption": a})


def main():
    with open(os.path.join(PKG, "sample-data", "research.json"), encoding="utf-8") as f:
        research = json.load(f)
    base = os.path.join(PKG, "sample-data")
    for lang in ("th", "en"):
        tmp = tempfile.mkdtemp()
        inp, out = os.path.join(tmp, "in"), os.path.join(tmp, "out")
        os.makedirs(inp), os.makedirs(out)
        # 1. a request that needs asking about: which thing, how many, where
        vague = {"items": [{"name": "กระดาน", "quantity": None, "unit": "", "requirements": [],
                            "could_mean": ["กระดานไวท์บอร์ด", "กระดานประกาศ", "กระดานดำ"]},
                           {"name": "ปากกาไวท์บอร์ด", "quantity": None, "unit": "ด้าม", "requirements": [], "could_mean": []}],
                 "delivery_place": None, "needed_by": None, "budget": None, "preferred_suppliers": []}
        pre = P.preflight([], message="x", defaults={}, answers={}, today=TODAY, proposals={"brief": vague}, lang=lang)
        keep_findings(f"{lang}/ask", pre)
        # … and what is taken for granted when the person skips
        skipped = P.preflight([], message="x", defaults={}, today=TODAY, proposals={"brief": vague}, lang=lang,
                              answers={"proc_variant:i1": "any", "proc_quantity:i1": "skip", "proc_quantity:i2": {"typed": "สิบ"}, "proc_place": "blank"})
        keep_findings(f"{lang}/skip", skipped)
        many = dict(vague, items=[dict(vague["items"][1], name=f"รายการ {n}", quantity="1") for n in range(1, 6)])
        keep_findings(f"{lang}/many", P.preflight([], message="x", defaults={}, answers={}, today=TODAY, proposals={"brief": many}, lang=lang))
        # 2. the sample: the choice, then the files
        pre = P.preflight([], message="x", defaults={}, answers={}, today=TODAY, proposals={"brief": research["brief"]}, lang=lang)
        first = P.compute([], {}, pre["period"], answers={}, defaults={}, today=TODAY, proposals=research, in_dir=base, out_dir=out, lang=lang)
        keep_findings(f"{lang}/pick", P.postcompute([], {}, pre["period"], first, answers={}, defaults={}, lang=lang))
        for a in first.get("assumptions") or []:
            if once("assumption", a):
                assumptions.append({"package": "procurement", "case": f"{lang}/sample", "assumption": a})
        for pick, case in ((first["items"][0]["candidates"][0]["key"], "chosen"), ("none", "none")):
            c = P.compute([], {}, pre["period"], answers={"proc_pick:i1": pick}, defaults={}, today=TODAY, proposals=research,
                          in_dir=base, out_dir=out, lang=lang)
            s = P.summarize(c)
            if once("summary", s):
                summaries.append({"package": "procurement", "case": f"{lang}/{case}", "summary": s})
            for name in c["evidence_files"]:
                shutil.copy(os.path.join(out, name), os.path.join(inp, name))
            with open(os.path.join(inp, "computed.json"), "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False)
            step.JOB_IN, step.JOB_OUT = inp, out
            req = {"op": "render", "package": "procurement", "computed": "computed.json", "formats": ["xlsx", "docx"],
                   "names": {"xlsx": f"{case}.xlsx", "docx": f"{case}.docx"}, "theme": {"id": "navy", "company": "Sample Co"},
                   "run": {"preparer": "Somchai", "organisation": "Sample Co", "date": "2026-10-06"}, "lang": lang}
            res = step.run(req)
            # 3. the same round in the organisation's own form, of each kind
            forms = [("request_form.docx", K.DOCX_MAPPING), ("request_form.xlsx", K.XLSX_MAPPING), ("request_form.pdf", K.PDF_MAPPING)] if case == "chosen" else []
            arts = list(res.get("artifacts") or [])
            for name, mapping in forms:
                ext = os.path.splitext(name)[1]
                src = K.FORMS[name](os.path.join(inp, "template" + ext))
                sl = to.slots(tf.inspect(src, reads=tuple(P.MANIFEST["templates"]["reads"])))
                filled = step.run(dict(req, formats=[], template={"input": "template" + ext, "name": "filled" + ext,
                                                                  "saved": {"slots": sl, "mapping": mapping}}))
                arts += [a for a in filled.get("artifacts") or [] if a.get("kind") == "template"]
            for a in arts:
                for ck in a["quality"]["checks"]:
                    if once("check", ck):
                        checks.append({"package": "procurement", "case": f"{lang}/{case}", "file": a["name"], "check": ck})
        shutil.rmtree(tmp, ignore_errors=True)
    doc = {"findings": findings, "summaries": summaries, "checks": checks, "assumptions": assumptions}
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    lines = ["package agentmarket", "",
             "// What the procurement package hands to a thread, in Thai and in English: every finding it",
             "// can raise, its compute summaries, every quality check of its files and of a filled form,",
             "// and every assumption. Written by scripts/probes/AM12/dump_procurement_texts.py (in the",
             "// sandbox image); not edited by hand.",
             "const procurementOutputs = " + json.dumps(body, ensure_ascii=False), ""]
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"{len(findings)} findings, {len(summaries)} summaries, {len(checks)} checks, {len(assumptions)} assumptions")


if __name__ == "__main__":
    main()
