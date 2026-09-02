"""FIX46 Part D — measure the end-trim alternatives on BOTH axes.

    docker exec -e GATEWAY_API_KEY=… ai-gateway-backend-1 \
        python -m evals.dlp.fix46_d_options [--model]

Each option monkeypatches the trim's knobs in thai_address (Python engine,
in-process) and runs the same measurements:
  safe   — the three axis-A leak sentences (is the tail inside the span?),
           the FIX28 must-catch set, the prose corpus (must not fire),
           the locked-72 detect (pii_masked_rate / decoy_survival);
  smart  — the FIX40 overmask set: keep_survival (detect-only) and, with
           --model, answer_accuracy_adjacent / _context (judge model).

Options:
  O1  widen _MAX_BARE_RUN 2 → 3, nothing else            (the tempting one)
  O2  "province + postcode" unit only (postcode lookahead)
  O3  locality list + postcode unit + short-prefix rule + bare-token bound
      (what FIX46 ships)
"""

from __future__ import annotations

import json
import re
import sys

from app.services.dlp.detectors import thai_address as TA
from evals.dlp import run as E
from evals.mask_quality import run as MQ

LEAKS = {
    "cp09": ("เขาอยู่ 88/4 ถนนชัยพฤกษ์ ต.บางกรวย นนทบุรี 11130", "นนทบุรี 11130"),
    "sf03": ("ไปวัดหน้างานที่ ซอยลาดพร้าว 71 แขวงลาดพร้าว กรุงเทพ", "ซอยลาดพร้าว 71"),
    "hr11": ("ที่อยู่ตามบัตรคือ 7 หมู่ 12 ต.ในเมือง อ.เมือง ขอนแก่น 40000", "ขอนแก่น 40000"),
    "hatyai": ("ที่อยู่ 5 ถนนนิพัทธ์อุทิศ หาดใหญ่ สงขลา 90110", "หาดใหญ่ สงขลา 90110"),
}

BASE = dict(_MAX_BARE_RUN=TA._MAX_BARE_RUN, _LOCALITIES=TA._LOCALITIES, _SHORT_PREFIX_MAX_TOKEN=TA._SHORT_PREFIX_MAX_TOKEN,
            _BARE_MAX_CHARS=TA._BARE_MAX_CHARS, _CLAUSE_INSIDE=TA._CLAUSE_INSIDE, _POSTCODE_RE=TA._POSTCODE_RE)

OPTIONS = {
    # O1: FIX38 trim as it was, bare run 3
    "O1_bare_run_3": dict(_MAX_BARE_RUN=3, _LOCALITIES=frozenset(), _SHORT_PREFIX_MAX_TOKEN=10**6,
                          _BARE_MAX_CHARS=10**6, _CLAUSE_INSIDE=(), _POSTCODE_RE=re.compile(r"(?!x)x")),
    # O2: FIX38 trim + postcode unit only
    "O2_postcode_unit": dict(_MAX_BARE_RUN=2, _LOCALITIES=frozenset(), _SHORT_PREFIX_MAX_TOKEN=10**6,
                             _BARE_MAX_CHARS=10**6, _CLAUSE_INSIDE=(), _POSTCODE_RE=BASE["_POSTCODE_RE"]),
    # O3: everything FIX46 ships
    "O3_fix46": dict(BASE),
}


def apply(opt: dict) -> None:
    for k, v in opt.items():
        setattr(TA, k, v)


def measure(name: str, with_model: bool) -> dict:
    det = TA.ThaiAddressDetector()
    leaks = {}
    for k, (text, tail) in LEAKS.items():
        hits = [m.matched_text for m in det.detect(text)]
        leaks[k] = {"hits": hits, "tail_inside": any(tail in h for h in hits)}
    sets = E.load_sets()
    corpus = E.load_corpus()
    py = E.run_engine("python", E.python_engine(), sets, corpus)
    addr = py["categories"]["thai_address"]
    det72 = MQ.stage_detect(False)
    om = MQ.stage_overmask_detect(False)
    out = {
        "option": name,
        "leaks_closed": sum(1 for v in leaks.values() if v["tail_inside"]),
        "leak_detail": leaks,
        "must_catch_thai_address": f"{addr['caught']}/{addr['total']}",
        "swallow": addr["swallow"],
        "corpus_fires": len(py["corpus_fires"]),
        "locked72_pii_masked_rate": det72["pii_masked_rate"]["overall"],
        "locked72_thai_address": det72["pii_masked_rate"]["by_type"].get("thai_address"),
        "locked72_decoy_survival": det72["decoy_survival"],
        "keep_survival": om["keep_survival"],
        "overmask_pii_masked_rate": om["pii_masked_rate"]["overall"],
    }
    if with_model:
        ax = MQ.stage_overmask_axis(False, om, 4, None)
        out["answer_accuracy_adjacent"] = ax["answer_accuracy_adjacent"]
        out["answer_accuracy_context"] = ax["answer_accuracy_context"]
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with_model = "--model" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    rows = []
    for name, opt in OPTIONS.items():
        if only and name not in only:
            continue
        apply(opt)
        r = measure(name, with_model)
        rows.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "leak_detail"}, ensure_ascii=False))
    apply(BASE)
    json.dump(rows, open("/app/evals/mask_quality/results/fix46-d-options.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
