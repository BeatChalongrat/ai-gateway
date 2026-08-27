"""FIX28 Part 1 — DLP detection-quality eval: a number we produce every build.

Two directions per category, both engines, one table:

  must-catch   — synthetic PII embedded in natural Thai chat sentences
                 (evals/dlp/data/must_catch/<category>.json): is it caught?
  must-not-fire — the 104-sentence ordinary-Thai corpus
                 (tests/test_dlp_detectors/data/thai_prose_corpus.json): does
                 anything fire?

Engines: the Python detector registry (always) and the LIVE Rust sidecar
(when reachable — the engine that serves requests). The Python↔Rust delta is
reported per category so an older/partial port can never drift back in
silently (FIX26/FIX27 found exactly that).

Scoring (per must-catch item):
  caught      — any detector's match overlaps the planted value (the product
                question: "does this reach the model in clear?")
  by expected — one of the category's own detectors caught it (secondary)
  swallow     — a hit runs past the value into the next Thai word (the FIX23
                <<ADDRESS_1>> shape)

Usage (inside the backend container, where both engines are reachable):
  python -m evals.dlp.run                 # print the table
  python -m evals.dlp.run --json out.json # + machine-readable results
  python -m evals.dlp.run --write-baseline
  python -m evals.dlp.run --check-baseline   # exit 1 on any regression
  python -m evals.dlp.run --require-rust     # fail if the sidecar is unreachable

Everything in the data set is synthetic (see data/gen_data.py).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
DATA_DIR = HERE / "data" / "must_catch"
CORPUS = BACKEND / "tests" / "test_dlp_detectors" / "data" / "thai_prose_corpus.json"
import os

# DLP_EVAL_BASELINE lets a test point the gate at a different baseline file.
BASELINE = Path(os.environ.get("DLP_EVAL_BASELINE") or (HERE / "baseline.json"))

# category → detectors whose catch counts as "by expected"
EXPECTED: dict[str, list[str]] = {
    "phone": ["phone", "thai_promptpay"],
    "credit_card": ["credit_card"],
    "email": ["email"],
    "thai_national_id": ["thai_national_id"],
    "thai_bank_account": ["thai_bank_account", "thai_promptpay"],
    "api_key_secret": ["api_key_secret", "bearer_token", "private_key", "password_field", "db_connection_string"],
    "thai_name": ["thai_name", "thai_name_no_prefix"],
    "thai_address": ["thai_address", "address"],
}

_THAI = set(chr(c) for c in range(0x0E01, 0x0E5C))


def _thai(s: str) -> bool:
    return any(c in _THAI for c in s)


@dataclass
class Hit:
    detector: str
    text: str


@dataclass
class CategoryResult:
    category: str
    total: int
    caught: int = 0
    by_expected: int = 0
    swallow: int = 0
    misses: list[dict] = field(default_factory=list)
    swallows: list[dict] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.caught / self.total if self.total else 0.0


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _overlaps(value: str, hit: str) -> bool:
    """A hit overlaps the planted value when one contains the other, or (for
    digit-bearing values) the hit carries the value's digit run — offsets are
    not compared because the registry rescans a normalised copy of the text."""
    v, h = value.strip(), hit.strip()
    if not v or not h:
        return False
    if v in h or h in v:
        return True
    dv = _digits(v)
    if len(dv) >= 7 and dv in _digits(h):
        return True
    # a hit that covers the value's core (e.g. a name caught without its
    # surname, or a key without its prefix) still counts when it shares ≥ 6
    # consecutive characters with the value
    return any(v[i : i + 6] in h for i in range(0, max(1, len(v) - 5)))


def _swallows(value: str, hit: str) -> bool:
    """The hit contains the value and continues into further Thai letters."""
    v, h = value.strip(), hit.strip()
    i = h.find(v)
    if i < 0:
        return False
    tail = h[i + len(v) :]
    return _thai(tail.lstrip())


def score(items: list[dict], hits_for: dict[str, list[Hit]], category: str) -> CategoryResult:
    r = CategoryResult(category, len(items))
    expected = set(EXPECTED.get(category, []))
    for it in items:
        hits = hits_for[it["id"]]
        rel = [h for h in hits if _overlaps(it["value"], h.text)]
        if rel:
            r.caught += 1
            if any(h.detector in expected for h in rel):
                r.by_expected += 1
            sw = [h for h in rel if _swallows(it["value"], h.text)]
            if sw:
                r.swallow += 1
                r.swallows.append({"id": it["id"], "value": it["value"], "hit": sw[0].text, "detector": sw[0].detector})
        else:
            r.misses.append({"id": it["id"], "text": it["text"], "value": it["value"], "other_hits": [(h.detector, h.text) for h in hits]})
    return r


# ── engines ───────────────────────────────────────────────────────────────

def python_engine():
    from app.services.dlp.detectors.registry import get_default_registry

    reg = get_default_registry(ner_enabled=False)

    def scan(text: str) -> list[Hit]:
        return [Hit(m.detector_name, m.matched_text) for m in reg.scan_text(text)]

    return scan


def rust_engine():
    """Returns a scan callable, or None when the live sidecar is unreachable."""
    # ONE event loop for the whole run: the gRPC channel is bound to the loop
    # that created it, so a fresh asyncio.run() per call silently returns
    # nothing after the first (the first version of this file scored the live
    # engine 0/… everywhere for exactly that reason).
    loop = asyncio.new_event_loop()
    try:
        from app.services.dlp.rust_dlp_client import RustDlpClient

        client = RustDlpClient.instance()
        res = loop.run_until_complete(client.scan_text("ping 0812345678 โทร", ner_enabled=False))
        if res is None:
            return None
    except Exception:  # noqa: BLE001
        return None

    def scan(text: str) -> list[Hit]:
        r = loop.run_until_complete(client.scan_text(text, ner_enabled=False))
        if r is None:
            raise RuntimeError("Rust sidecar stopped answering mid-run")
        return [Hit(m.detector_name, text[m.start : m.end] if 0 <= m.start <= m.end <= len(text) else getattr(m, "matched_text", "")) for m in r.matches]

    return scan


# ── run ───────────────────────────────────────────────────────────────────

def load_sets() -> dict[str, list[dict]]:
    sets: dict[str, list[dict]] = {}
    for p in sorted(DATA_DIR.glob("*.json")):
        sets[p.stem] = json.loads(p.read_text(encoding="utf-8"))["items"]
    return sets


def load_corpus() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["sentences"]


def run_engine(name: str, scan, sets: dict[str, list[dict]], corpus: list[dict]) -> dict:
    t0 = time.perf_counter()
    out: dict = {"engine": name, "categories": {}, "corpus_fires": [], "seconds": 0.0}
    for cat, items in sets.items():
        hits_for = {it["id"]: scan(it["text"]) for it in items}
        r = score(items, hits_for, cat)
        out["categories"][cat] = {
            "total": r.total,
            "caught": r.caught,
            "by_expected": r.by_expected,
            "swallow": r.swallow,
            "recall": round(r.recall, 4),
            "misses": r.misses,
            "swallows": r.swallows,
        }
    for s in corpus:
        hits = scan(s["text"])
        if hits:
            out["corpus_fires"].append({"id": s["id"], "text": s["text"], "hits": [(h.detector, h.text) for h in hits]})
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def verdict(recall: float, fires: int | None) -> str:
    if fires:
        return "FALSE-FIRES"
    if recall >= 0.8:
        return "ok"
    if recall >= 0.5:
        return "WEAK"
    return "POOR"


def render(py: dict, rs: dict | None, corpus_n: int) -> str:
    lines = []
    lines.append(f"{'category':18} {'must-catch recall (Py / Rust / Δ)':38} {'swallow Py/Rs':14} verdict")
    lines.append("-" * 86)
    for cat, c in py["categories"].items():
        r = rs["categories"][cat] if rs else None
        pyr = f"{c['caught']}/{c['total']} ({c['recall']*100:.0f}%)"
        if r:
            d = r["caught"] - c["caught"]
            rsr = f"{r['caught']}/{r['total']} ({r['recall']*100:.0f}%)"
            delta = f"{d:+d}" if d else "0"
        else:
            rsr, delta = "n/a", "n/a"
        sw = f"{c['swallow']}/{r['swallow'] if r else '-'}"
        worst = min(c["recall"], r["recall"] if r else c["recall"])
        lines.append(f"{cat:18} {pyr:>12} / {rsr:>12} / {delta:>4}   {sw:14} {verdict(worst, 0)}")
    fp = len(py["corpus_fires"])
    fr = len(rs["corpus_fires"]) if rs else None
    lines.append("-" * 86)
    lines.append(f"must-not-fire corpus ({corpus_n} sentences): fires Python={fp}  Rust={fr if rs else 'n/a'}   {'FALSE-FIRES' if (fp or fr) else 'ok'}")
    lines.append(f"time: Python {py['seconds']}s" + (f", Rust {rs['seconds']}s" if rs else ", Rust not run"))
    return "\n".join(lines)


def compare_to_baseline(results: dict, baseline: dict) -> list[str]:
    """Regressions only: a lower recall, a new swallow, or more corpus fires."""
    problems: list[str] = []
    for eng in ("python", "rust"):
        cur, base = results.get(eng), baseline.get(eng)
        if not cur or not base:
            continue
        for cat, b in base["categories"].items():
            c = cur["categories"].get(cat)
            if c is None:
                problems.append(f"{eng}: category {cat} missing from the run")
                continue
            if c["caught"] < b["caught"]:
                problems.append(f"{eng}/{cat}: recall dropped {b['caught']}/{b['total']} → {c['caught']}/{c['total']}")
            if c["swallow"] > b["swallow"]:
                problems.append(f"{eng}/{cat}: swallowed-word hits rose {b['swallow']} → {c['swallow']}")
        if len(cur["corpus_fires"]) > len(base["corpus_fires"]):
            new = [f["id"] for f in cur["corpus_fires"] if f["id"] not in {x["id"] for x in base["corpus_fires"]}]
            problems.append(f"{eng}: corpus false fires rose {len(base['corpus_fires'])} → {len(cur['corpus_fires'])} (new: {new})")
    if baseline.get("rust") and not results.get("rust"):
        problems.append("rust: baseline has Rust numbers but the sidecar was not reachable in this run")
    return problems


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--check-baseline", action="store_true")
    ap.add_argument("--require-rust", action="store_true")
    ap.add_argument("--no-rust", action="store_true")
    ap.add_argument("--misses", action="store_true", help="print every miss and swallow")
    a = ap.parse_args(argv)

    sets, corpus = load_sets(), load_corpus()
    py = run_engine("python", python_engine(), sets, corpus)
    rs = None
    if not a.no_rust:
        scan = rust_engine()
        if scan is None:
            if a.require_rust:
                print("ERROR: the Rust DLP sidecar is not reachable and --require-rust was given", file=sys.stderr)
                return 2
            print("NOTE: Rust sidecar not reachable — Python only (the live engine was NOT measured)", file=sys.stderr)
        else:
            rs = run_engine("rust", scan, sets, corpus)
    results = {"python": py, "rust": rs, "data": {cat: len(v) for cat, v in sets.items()}, "corpus": len(corpus)}

    print(render(py, rs, len(corpus)))
    if a.misses:
        for eng in (py, rs):
            if not eng:
                continue
            print(f"\n== {eng['engine']} misses / swallows")
            for cat, c in eng["categories"].items():
                for m in c["misses"]:
                    print(f"  MISS {cat:16} [{m['id']}] {m['text']}  → value={m['value']!r} other_hits={m['other_hits']}")
                for s in c["swallows"]:
                    print(f"  SWALLOW {cat:13} [{s['id']}] value={s['value']!r} hit={s['hit']!r} ({s['detector']})")
            for f in eng["corpus_fires"]:
                print(f"  FIRE {f['id']}: {f['text']} → {f['hits']}")

    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.write_baseline:
        slim = {k: ({"categories": {c: {kk: vv for kk, vv in v.items() if kk not in ("misses", "swallows")} for c, v in e["categories"].items()}, "corpus_fires": [{"id": f["id"]} for f in e["corpus_fires"]]} if e else None) for k, e in (("python", py), ("rust", rs))}
        slim["data"] = results["data"]
        slim["corpus"] = len(corpus)
        BASELINE.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nbaseline written → {BASELINE}")
    if a.check_baseline:
        if not BASELINE.exists():
            print("ERROR: no baseline — run with --write-baseline first", file=sys.stderr)
            return 2
        problems = compare_to_baseline(results, json.loads(BASELINE.read_text(encoding="utf-8")))
        if problems:
            print("\nREGRESSION vs baseline:")
            for p in problems:
                print("  - " + p)
            return 1
        print("\nbaseline check: no regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
