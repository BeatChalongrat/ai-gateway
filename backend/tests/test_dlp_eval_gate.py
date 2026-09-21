"""FIX28 Part 1 — the DLP detection-quality regression gate.

Runs the eval (evals/dlp/run.py) and fails when a category's must-catch
recall drops below the stored baseline, a swallowed-word hit appears, or the
must-not-fire corpus gains a false fire. The Python engine is always
measured; the live Rust sidecar is measured when reachable. Set
``DLP_EVAL_REQUIRE_RUST=1`` (the in-container CI lane) to make an unreachable
sidecar a failure instead of a note — the baseline carries Rust numbers, and a
run that cannot see the live engine must not be mistaken for a clean one.

Update the baseline deliberately, never by accident:
    python -m evals.dlp.run --write-baseline
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from evals.dlp import run as ev  # noqa: E402


@pytest.fixture(scope="module")
def results():
    sets, corpus = ev.load_sets(), ev.load_corpus()
    py = ev.run_engine("python", ev.python_engine(), sets, corpus)
    rs = None
    scan = ev.rust_engine()
    if scan is not None:
        rs = ev.run_engine("rust", scan, sets, corpus)
    elif os.environ.get("DLP_EVAL_REQUIRE_RUST") == "1":
        pytest.fail("DLP_EVAL_REQUIRE_RUST=1 but the Rust DLP sidecar is not reachable — the live engine was not measured")
    return {"python": py, "rust": rs}


def test_baseline_exists():
    assert ev.BASELINE.exists(), "no evals/dlp/baseline.json — run `python -m evals.dlp.run --write-baseline`"


def test_every_category_has_a_must_catch_set():
    sets = ev.load_sets()
    for cat in ev.EXPECTED:
        assert cat in sets and len(sets[cat]) >= 20, f"{cat}: {len(sets.get(cat, []))} items (need ≥ 20)"


@pytest.mark.quarantine(
    reason=(
        "possible real regression, parked area (DLP): python AND rust "
        "thai_address must-catch recall 41/41 -> 36/41 since 2026-09-17 "
        "(same at a36124f, so data/environment, not a code change in that "
        "window); full addresses like '128/4 ซอยรามคำแหง 24 ... 10240' are "
        "missed. Not investigated while DLP is paused; do NOT rewrite the "
        "baseline to make this pass."
    ),
    ticket="FIX126-O8-6",
)
def test_no_regression_against_baseline(results):
    baseline = json.loads(ev.BASELINE.read_text(encoding="utf-8"))
    problems = ev.compare_to_baseline(results, baseline)
    if results["rust"] is None:
        # not measured → not a regression, but say so in the test output
        problems = [p for p in problems if not p.startswith("rust:")]
        print("NOTE: Rust sidecar not reachable — only the Python engine was gated")
    assert not problems, "DLP quality regression:\n  - " + "\n  - ".join(problems)


def test_python_and_rust_agree_when_both_ran(results):
    """The Python↔Rust delta: any drift between the tested engine and the one
    that serves requests fails here, per category."""
    rs = results["rust"]
    if rs is None:
        pytest.skip("Rust sidecar not reachable")
    py = results["python"]
    drift = []
    for cat, c in py["categories"].items():
        r = rs["categories"][cat]
        if c["caught"] != r["caught"] or c["swallow"] != r["swallow"]:
            drift.append(f"{cat}: python {c['caught']}/{c['total']} (swallow {c['swallow']}) vs rust {r['caught']}/{r['total']} (swallow {r['swallow']})")
    if len(py["corpus_fires"]) != len(rs["corpus_fires"]):
        drift.append(f"corpus fires: python {len(py['corpus_fires'])} vs rust {len(rs['corpus_fires'])}")
    assert not drift, "Python↔Rust drift:\n  - " + "\n  - ".join(drift)
