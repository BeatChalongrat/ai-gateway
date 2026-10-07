"""AM13 probe: what each package's bundled sample holds, as far as an order of its rows goes.
Run inside the sandbox image (scripts/probes/AM13/py.sh explore_samples.py)."""
import datetime
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from agent_market.tests import samplekit  # noqa: E402

po = samplekit.render_sample("po_matching", defaults=samplekit.self_test_defaults("po_matching"))["computed"]
print("po lines:", len(po["lines"]), "| variances:", [(x.get("price_variance") or {}).get("extended") for x in po["lines"]])

bank = samplekit.render_sample("bank_reconciliation")["computed"]
print("bank items:", len(bank["items"]), "|", [(i["date"], i["amount"]) for i in bank["items"]])
print("bank carried_out:", len(bank["carried_out"]))

inv = samplekit.render_sample("invoice_processing")["computed"]
docs = [d for d in inv["documents"] if d["status"] == "included"]
print("invoice docs:", len(inv["documents"]), "included", len(docs))
for d in docs:
    print("   ", d["document_date"], "|", d["seller_name"], "|", d["thb_total"], "|", d["currency"])

from agent_market.packages import procurement as P  # noqa: E402

base = os.path.join(os.path.dirname(P.__file__), "sample-data")
research = json.load(open(os.path.join(base, "research.json"), encoding="utf-8"))
today = datetime.date(2026, 10, 6)
defaults = {"proc_vat_rate": "7.00", "proc_sample_pick": "first"}
tmp = tempfile.mkdtemp()
pre = P.preflight([], message="x", defaults=defaults, answers={}, today=today, proposals={"brief": research["brief"]}, lang="th")
c = P.compute([], {}, pre["period"], answers={}, defaults=defaults, today=today, proposals=research, in_dir=base, out_dir=tmp, lang="th")
print("procurement keys:", sorted(c.keys()))
for it in c["items"]:
    print(" item", it["key"], it["name"], "qty", it.get("quantity"), "| candidates", len(it["candidates"]), "| dropped", len(it["dropped"]))
    for x in it["candidates"]:
        print("    no", x["no"], x["key"], x["host"], "| price", (x.get("price") or {}).get("amount"), x.get("currency"), "| sub", x.get("subtotal"),
              "| total", x.get("total"), "| vat", (x.get("vat") or {}).get("says"), "| open", x["open"], "| not_found", len(x.get("not_found") or []))
print("picks:", c.get("picks"), "| lang:", c.get("lang"))
