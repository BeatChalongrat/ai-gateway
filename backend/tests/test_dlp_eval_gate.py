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
    return {"python": py, "rust": rs, "thai_ner_model": ev.thai_ner_model_source()}


def test_baseline_exists():
    assert ev.BASELINE.exists(), "no evals/dlp/baseline.json — run `python -m evals.dlp.run --write-baseline`"


def test_every_category_has_a_must_catch_set():
    sets = ev.load_sets()
    for cat in ev.EXPECTED:
        assert cat in sets and len(sets[cat]) >= 20, f"{cat}: {len(sets.get(cat, []))} items (need ≥ 20)"


def test_no_regression_against_baseline(results):
    """FIX133 (was FIX126-O8-6, "thai_address 41/41 → 36/41 since 2026-09-17"):
    not a detector regression. The five items (ad_province_bare, ad_prose_gap,
    ad_no_house_number, ad_chat_full, ad_office) have no address keyword —
    the documented gaps of the regex — and were caught in the FIX46 baseline
    by the Thai NER layer's LOCATION spans, which come from the ner-service
    alone (baseline: caught 41, by_expected 36). The hermetic lane has no
    ``ner_service_url``, so ``ThaiNerDetector._model_spans`` returns nothing
    by design and those five are unreachable by any detector. The baseline is
    NOT rewritten: ``compare_to_baseline`` compares ``by_expected`` when the
    model source differs from the baseline's, so the regex and lexicon
    detectors are still gated (36/41 here) and the full 41/41 is gated
    wherever the ner-service answers."""
    baseline = json.loads(ev.BASELINE.read_text(encoding="utf-8"))
    problems = ev.compare_to_baseline(results, baseline)
    if results["rust"] is None:
        # not measured → not a regression, but say so in the test output
        problems = [p for p in problems if not p.startswith("rust:")]
        print("NOTE: Rust sidecar not reachable — only the Python engine was gated")
    if results["thai_ner_model"] != "reachable":
        print(f"NOTE: Thai NER model {results['thai_ner_model']} — gated on each category's own detectors (by_expected)")
    assert not problems, "DLP quality regression:\n  - " + "\n  - ".join(problems)


# ── the gate's like-for-like rule, without the engines ─────────────────────

def _cat(caught: int, by_expected: int, total: int = 41, swallow: int = 0) -> dict:
    return {"total": total, "caught": caught, "by_expected": by_expected, "swallow": swallow, "recall": round(caught / total, 4)}


def _run(cat: dict, model: str) -> dict:
    return {"python": {"categories": {"thai_address": cat}, "corpus_fires": []}, "rust": None, "thai_ner_model": model}


# FIX46's baseline: recorded in the container, where the ner-service answers
# (no thai_ner_model key), 41 caught of which 36 by the address detectors.
_BASELINE_WITH_MODEL = {"python": {"categories": {"thai_address": _cat(41, 36)}, "corpus_fires": []}}


def test_gate_without_the_ner_model_compares_the_detectors_own_recall():
    # the hermetic lane: 36 caught, all by the regex — the regex did not regress
    assert ev.compare_to_baseline(_run(_cat(36, 36), "unreachable"), _BASELINE_WITH_MODEL) == []


def test_gate_without_the_ner_model_still_catches_a_regex_regression():
    problems = ev.compare_to_baseline(_run(_cat(35, 35), "unreachable"), _BASELINE_WITH_MODEL)
    assert len(problems) == 1 and problems[0].startswith("python/thai_address:") and "36/41 → 35/41" in problems[0]


def test_gate_with_the_ner_model_compares_full_recall():
    problems = ev.compare_to_baseline(_run(_cat(36, 36), "reachable"), _BASELINE_WITH_MODEL)
    assert len(problems) == 1 and "41/41 → 36/41" in problems[0]


def test_gate_recorded_without_the_model_compares_full_recall_offline():
    # a baseline written by --write-baseline in the hermetic lane says so, and
    # an offline run is then compared on caught, like for like
    offline_baseline = {**_BASELINE_WITH_MODEL, "thai_ner_model": "unreachable"}
    assert ev.compare_to_baseline(_run(_cat(41, 36), "unreachable"), offline_baseline) == []
    assert "41/41 → 40/41" in ev.compare_to_baseline(_run(_cat(40, 36), "unreachable"), offline_baseline)[0]


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
