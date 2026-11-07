"""FIX37 Phase 2 / N1 — bake-off between Thai NER candidates.

Runs OUTSIDE the product image (a throwaway lab container) so the
gateway's dependencies are untouched while the candidates are measured.

    python lab_thai_ner.py --engine crf
    python lab_thai_ner.py --engine wangchan

Scored on the Phase-1 corpus:
  name_recall    — declared thai_name values overlapped by a PERSON span
  addr_recall    — declared thai_address values overlapped by a LOCATION span
  decoy_fires    — must_not_mask strings a span lands on (over-masking)
  prose_fires    — sentences in the 108-sentence must-not-fire corpus that
                   produce ANY span (the hard gate lives here)
  ms/text        — p50 / p95 wall time per message
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "backend" / "evals" / "mask_quality" / "data"
PROSE = REPO / "backend" / "tests" / "test_dlp_detectors" / "data" / "thai_prose_corpus.json"

PERSON = {"PERSON", "PER", "B-PERSON", "I-PERSON"}
PLACE = {"LOCATION", "LOC", "GPE", "FACILITY", "B-LOCATION", "I-LOCATION"}


def load_corpus():
    msgs = []
    for p in sorted(DATA.glob("messages_*.json")):
        msgs.extend(json.loads(p.read_text(encoding="utf-8"))["messages"])
    prose = json.loads(PROSE.read_text(encoding="utf-8"))["sentences"]
    return msgs, prose


# ── engines ───────────────────────────────────────────────────────────────

def engine_crf():
    """PyThaiNLP's CRF ThaiNER (`thainer` engine). Small, no torch."""
    from pythainlp.tag.named_entity import NER

    ner = NER(engine="thainer")

    def run(text: str):
        out = []
        cur_tag, cur_txt = None, ""
        for tok, _pos, tag in ner.tag(text, pos=True):
            base = tag.split("-", 1)[-1] if "-" in tag else tag
            if tag.startswith("B-"):
                if cur_tag:
                    out.append((cur_tag, cur_txt))
                cur_tag, cur_txt = base, tok
            elif tag.startswith("I-") and cur_tag == base:
                cur_txt += tok
            else:
                if cur_tag:
                    out.append((cur_tag, cur_txt))
                cur_tag, cur_txt = None, ""
        if cur_tag:
            out.append((cur_tag, cur_txt))
        return out

    return run, "pythainlp thainer (CRF)"


def engine_wangchan():
    """PyThaiNLP ThaiNER v2 — WangchanBERTa token classification."""
    from pythainlp.tag.named_entity import NER

    ner = NER(engine="thainer-v2")

    def run(text: str):
        out = []
        for item in ner.tag(text):
            word, tag = (item[0], item[-1])
            base = tag.split("-", 1)[-1] if "-" in tag else tag
            if base in ("O", ""):
                continue
            if tag.startswith("I-") and out and out[-1][0] == base:
                out[-1] = (base, out[-1][1] + word)
            else:
                out.append((base, word))
        return out

    return run, "pythainlp thainer_v2 (WangchanBERTa)"


ENGINES = {"crf": engine_crf, "wangchan": engine_wangchan}


def overlap(value: str, span: str) -> bool:
    v, s = value.strip(), span.strip()
    if not v or not s:
        return False
    if v in s or s in v:
        return True
    return any(v[i:i + 3] in s for i in range(0, max(1, len(v) - 2)))


def main() -> int:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    msgs, prose = load_corpus()
    t_load = time.perf_counter()
    run, label = ENGINES[a.engine]()
    run("อุ่นเครื่องคุณสมชาย ใจดี")
    load_s = round(time.perf_counter() - t_load, 2)

    names = [0, 0]
    addrs = [0, 0]
    decoy_fires = []
    times = []
    for m in msgs:
        t0 = time.perf_counter()
        spans = run(m["text"])
        times.append((time.perf_counter() - t0) * 1000)
        person = [t for lbl, t in spans if lbl.upper() in PERSON]
        place = [t for lbl, t in spans if lbl.upper() in PLACE]
        for p in m.get("pii", []):
            if p["type"] == "thai_name":
                names[1] += 1
                names[0] += int(any(overlap(p["value"], s) for s in person))
            elif p["type"] == "thai_address":
                addrs[1] += 1
                addrs[0] += int(any(overlap(p["value"], s) for s in place))
        for d in m.get("must_not_mask", []):
            if any(overlap(d, s) for _l, s in spans):
                decoy_fires.append({"id": m["id"], "decoy": d})

    prose_fires = []
    for s in prose:
        if run(s["text"]):
            prose_fires.append(s["id"])

    times.sort()
    p = lambda q: round(times[min(len(times) - 1, int(q * (len(times) - 1)))], 1)
    res = {
        "engine": a.engine, "label": label, "load_seconds": load_s,
        "name_recall": round(names[0] / names[1], 4) if names[1] else 0,
        "name_n": names[1],
        "addr_recall": round(addrs[0] / addrs[1], 4) if addrs[1] else 0,
        "addr_n": addrs[1],
        "decoy_fires": len(decoy_fires), "decoy_detail": decoy_fires[:20],
        "prose_fires": len(prose_fires), "prose_total": len(prose),
        "prose_detail": prose_fires[:20],
        "ms_p50": p(0.50), "ms_p95": p(0.95), "ms_p99": p(0.99),
    }
    print(json.dumps({k: v for k, v in res.items() if not k.endswith("detail")}, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
