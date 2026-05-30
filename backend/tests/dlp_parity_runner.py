"""DLP Python↔Rust parity runner (Phase-0 cutover GATE).

Runs every parity fixture (and a set of encoding-bypass / evasion
cases) through BOTH engines and reports divergences:

  * Python  → ``DetectorRegistry.scan_text`` (in-process, the source
    of truth; includes the encoding-bypass preprocessor).
  * Rust    → ``RustDlpClient.scan_text`` over gRPC to ``dlp-sidecar``.

Both run with ``ner_enabled=False`` so the comparison isolates the
**regex/heuristic detector set** (the spaCy NER detector is Python-only
and intentionally not ported, so leaving it on would create noise that
is not a real parity gap).

A case is FULL-MATCH iff the two engines return the identical set of
``(detector_name, start, end)`` tuples. We never print matched text —
only detector names + spans — so the report is safe to commit.

Run inside the backend container (fixtures are mounted at /work)::

    docker exec ai-gateway-backend-1 python /app/tests/dlp_parity_runner.py

Writes a Markdown report to stdout. This is the gate that must reach
100% detection-parity before ``DLP_ENGINE`` may be flipped to ``rust``.
"""

from __future__ import annotations

import asyncio
import base64
import glob
import json
import os
import statistics
import time

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.rust_dlp_client import RustDlpClient

FIXTURE_DIRS = [
    "/work/dlp-sidecar/tests/parity/fixtures",
    os.path.join(os.path.dirname(__file__), "..", "..", "dlp-sidecar",
                 "tests", "parity", "fixtures"),
]


def _fixture_dir() -> str:
    for d in FIXTURE_DIRS:
        if os.path.isdir(d):
            return d
    raise SystemExit("parity fixtures dir not found")


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


# Encoding-bypass / evasion cases — these exercise the Python
# preprocessor (base64 decode-and-scan, zero-width strip, homoglyph
# map) that the Rust path does NOT yet port. Category "evasion".
EVASION_CASES = [
    {
        "detector": "evasion",
        "id": "base64_embedded_email",
        # decodes to "contact alice@example.com now"
        "input": f"payload {_b64('contact alice@example.com now')} end",
    },
    {
        "detector": "evasion",
        "id": "base64_embedded_credit_card",
        "input": f"data {_b64('card 4111111111111111 thanks')} done",
    },
    {
        "detector": "evasion",
        "id": "zero_width_split_email",
        "input": "reach me at alice​@example.com please",
    },
    {
        "detector": "evasion",
        "id": "homoglyph_email_cyrillic",
        # the 'e' in "alice" is Cyrillic U+0435
        "input": "email alicе@example.com today",
    },
    {
        "detector": "evasion",
        "id": "zero_width_split_credit_card",
        "input": "card 4111​1111​1111​1111 ok",
    },
]


# Gap-coverage cases — one per remediated detector gap (the original
# fixtures didn't exercise these, which is why the report's 95.4% was a
# ceiling). The runner compares Python vs Rust on each; after the fix
# both engines must agree. Category "gap".
_GHP = "ghp_" + "a" * 36
_GH_FINE = "github_pat_" + "A" * 82
GAP_CASES = [
    {"detector": "gap", "id": "tax_leading_zero_corporate",
     "input": "เลขประจำตัวผู้เสียภาษี 0105558123456 ครับ"},
    {"detector": "gap", "id": "tax_strong_prefix_checksum_invalid",
     "input": "เลขประจำตัวผู้เสียภาษีบริษัทคือ 0105558123999"},
    {"detector": "gap", "id": "tax_keyword_vat_registration",
     "input": "vat registration 0994000123456 on file"},
    {"detector": "gap", "id": "password_thai_prose",
     "input": "รหัสผ่านคือ Secur3P@ssw0rd"},
    {"detector": "gap", "id": "password_english_nosep",
     "input": "username admin password Hunter2024!"},
    {"detector": "gap", "id": "github_classic_pat",
     "input": f"export GH_TOKEN={_GHP}"},
    {"detector": "gap", "id": "github_finegrained_pat",
     "input": f"token: {_GH_FINE}"},
    {"detector": "gap", "id": "income_wage_english",
     "input": "his wage 45000 baht per month"},
    {"detector": "gap", "id": "income_salary_pronoun",
     "input": "salary my 50000"},
    {"detector": "gap", "id": "income_thai_kha_jang",
     "input": "ค่าจ้าง 30000 ต่อเดือน"},
    {"detector": "gap", "id": "dob_role_noun",
     "input": "ลูกค้าเกิดวันที่ 12/05/1998 ครับ"},
    {"detector": "gap", "id": "national_id_dashed_keyword",
     "input": "บัตรประชาชน 1-2345-67890-12-3"},
    {"detector": "gap", "id": "health_allergy_personal",
     "input": "ผมมีโรคภูมิแพ้รุนแรง"},
    {"detector": "gap", "id": "phone_all_same_digit_reject",
     "input": "call us at 000-000-0000 anytime"},
]


def py_scan(registry, text):
    return sorted((m.detector_name, m.start, m.end)
                  for m in registry.scan_text(text))


async def rust_scan(client, text):
    res = await client.scan_text(text, ner_enabled=False)
    if res is None:
        return None
    return sorted((m.detector_name, m.start, m.end) for m in res.matches)


async def main() -> None:
    registry = get_default_registry(ner_enabled=False)
    client = RustDlpClient.instance()

    cases = []  # (group, case_id, text)
    for fp in sorted(glob.glob(os.path.join(_fixture_dir(), "*.json"))):
        data = json.load(open(fp, encoding="utf-8"))
        grp = data["detector"]
        for c in data.get("cases", []):
            cases.append((grp, c["id"], c["input"]))
    for c in EVASION_CASES:
        cases.append((c["detector"], c["id"], c["input"]))
    for c in GAP_CASES:
        cases.append((c["detector"], c["id"], c["input"]))

    # Per-group tally + global lists.
    from collections import defaultdict
    total = full = detect_match = 0
    only_py_total = only_rust_total = 0
    rust_unavailable = 0
    by_group = defaultdict(lambda: {"total": 0, "full": 0, "mismatch": 0})
    by_category = defaultdict(lambda: {"total": 0, "full": 0})
    mismatches = []  # (group, id, only_py, only_rust)
    py_lat, rust_lat = [], []

    # Map fixture group → category (read once from fixture files).
    grp_cat = {}
    for fp in glob.glob(os.path.join(_fixture_dir(), "*.json")):
        d = json.load(open(fp, encoding="utf-8"))
        grp_cat[d["detector"]] = d.get("category", "?")
    grp_cat["evasion"] = "evasion"
    grp_cat["gap"] = "gap"

    for grp, cid, text in cases:
        t0 = time.perf_counter()
        pm = py_scan(registry, text)
        py_lat.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        rm = await rust_scan(client, text)
        rust_lat.append((time.perf_counter() - t0) * 1000)

        total += 1
        by_group[grp]["total"] += 1
        cat = grp_cat.get(grp, "?")
        by_category[cat]["total"] += 1

        if rm is None:
            rust_unavailable += 1
            mismatches.append((grp, cid, ["<rust unavailable>"], []))
            by_group[grp]["mismatch"] += 1
            continue

        ps, rs = set(pm), set(rm)
        if ps == rs:
            full += 1
            detect_match += 1
            by_group[grp]["full"] += 1
            by_category[cat]["full"] += 1
        else:
            if bool(ps) == bool(rs):
                detect_match += 1
            only_py = sorted(ps - rs)
            only_rust = sorted(rs - ps)
            only_py_total += len(only_py)
            only_rust_total += len(only_rust)
            by_group[grp]["mismatch"] += 1
            mismatches.append((grp, cid,
                               [list(t) for t in only_py],
                               [list(t) for t in only_rust]))

    # ---- Markdown report --------------------------------------------
    def pct(n, d):
        return f"{(100.0 * n / d):.1f}%" if d else "n/a"

    print("# DLP Python↔Rust Parity Report")
    print()
    print(f"- Total cases: **{total}**  (fixtures + {len(EVASION_CASES)} evasion)")
    print(f"- Full parity (identical detector+span set): **{full}/{total} = {pct(full, total)}**")
    print(f"- Detection parity (both match / both clear): **{detect_match}/{total} = {pct(detect_match, total)}**")
    print(f"- Rust false-negatives (only_python spans): **{only_py_total}**")
    print(f"- Rust false-positives (only_rust spans): **{only_rust_total}**")
    print(f"- Rust unavailable (gRPC None): **{rust_unavailable}**")
    print(f"- Latency/scan — Python p50 {statistics.median(py_lat):.2f}ms / "
          f"Rust p50 {statistics.median(rust_lat):.2f}ms")
    print()
    print("## By category")
    print()
    print("| Category | Cases | Full parity |")
    print("|---|---|---|")
    for cat in sorted(by_category):
        s = by_category[cat]
        print(f"| {cat} | {s['total']} | {s['full']}/{s['total']} ({pct(s['full'], s['total'])}) |")
    print()
    print("## By detector group")
    print()
    print("| Group | Cases | Full parity | Mismatch | Verdict |")
    print("|---|---|---|---|---|")
    for grp in sorted(by_group):
        s = by_group[grp]
        verdict = "✅" if s["mismatch"] == 0 else "❌"
        print(f"| {grp} | {s['total']} | {s['full']} | {s['mismatch']} | {verdict} |")
    print()
    print("## Mismatch detail (detector_name, start, end — no raw text)")
    print()
    if not mismatches:
        print("_None — 100% parity._")
    else:
        for grp, cid, only_py, only_rust in mismatches:
            print(f"- **{grp}/{cid}** — only_python={only_py} only_rust={only_rust}")
    print()
    gate = "PASS ✅" if (full == total) else "FAIL ❌ — do NOT flip DLP_ENGINE=rust"
    print(f"## Gate: {gate}")


if __name__ == "__main__":
    asyncio.run(main())
