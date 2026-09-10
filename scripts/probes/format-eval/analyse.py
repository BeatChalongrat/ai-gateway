import json, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import score
SP=sys.argv[1]; F=sys.argv[2]
rows=[json.loads(l) for l in open(os.path.join(SP,F),encoding='utf-8')]
rows=[r for r in rows if not r["err"] and r["text"].strip()]
for r in rows: r["s"]=score(r["text"], r["label"])
def arm(a): return [r for r in rows if r["arm"]==a]
def pct(v): return round(100*v,1)
print(f"scored rows: {len(rows)}   errors/empties dropped: {sum(1 for l in open(os.path.join(SP,F),encoding='utf-8'))-len(rows)}")
print(f"cache hits: {sum(1 for r in rows if r.get('cached'))}")
print()
print(f"{'metric':34s} {'BEFORE':>16s} {'AFTER':>16s}   delta")
b,a=arm("before"),arm("after")
def med(v): return round(st.median(v),1) if v else 0
mb,ma = sum(r["s"]["match"] for r in b)/max(len(b),1), sum(r["s"]["match"] for r in a)/max(len(a),1)
print(f"{'format match':34s} {pct(mb):>15}% {pct(ma):>15}%   {pct(ma-mb):+.1f}pp")
lb,la = med([r["s"]["chars"] for r in b]), med([r["s"]["chars"] for r in a])
print(f"{'length (median chars)':34s} {lb:>16} {la:>16}   {round(100*(la-lb)/max(lb,1),1):+.1f}%")
bb,ba = med([r["s"]["bullets"] for r in b]), med([r["s"]["bullets"] for r in a])
print(f"{'bullets (median)':34s} {bb:>16} {ba:>16}")
tb,ta = sum(r["s"]["tables"] for r in b), sum(r["s"]["tables"] for r in a)
print(f"{'tables (total)':34s} {tb:>16} {ta:>16}")
thb=[r["s"]["thai"] for r in b if r["lang"]=="th" and r["s"]["thai"] is not None]
tha=[r["s"]["thai"] for r in a if r["lang"]=="th" and r["s"]["thai"] is not None]
print(f"{'thai ratio (median, TH asks)':34s} {med(thb):>16} {med(tha):>16}   {round(100*(st.median(tha)-st.median(thb)),2):+.2f}pp")
print()
print("by label:")
for lab in ("prose","table","numbered","code"):
    bl=[r for r in b if r["label"]==lab]; al=[r for r in a if r["label"]==lab]
    if not bl: continue
    print(f"  {lab:9s} before {sum(r['s']['match'] for r in bl)}/{len(bl)}   after {sum(r['s']['match'] for r in al)}/{len(al)}")
print()
print("per-ask match (before -> after), n runs each:")
ids=sorted({r["id"] for r in rows})
for i in ids:
    bi=[r for r in b if r["id"]==i]; ai=[r for r in a if r["id"]==i]
    lab=(bi or ai)[0]["label"]
    print(f"  {i} {lab:9s} {sum(r['s']['match'] for r in bi)}/{len(bi)} -> {sum(r['s']['match'] for r in ai)}/{len(ai)}"
          f"   dom_before={sorted({r['s']['dominant'] for r in bi})} dom_after={sorted({r['s']['dominant'] for r in ai})}")
