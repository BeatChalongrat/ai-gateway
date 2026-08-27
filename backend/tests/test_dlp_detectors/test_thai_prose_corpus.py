"""FIX26 §1.1 — the ordinary-Thai-prose corpus: every registered detector
must stay SILENT on every sentence.

``data/thai_prose_corpus.json`` holds ~100 sentences a person actually
types into this product (questions, instructions, small talk, technical
requests, work chatter, numbers that are not identifiers) and contains
NO personal data. Two user-found bugs (FIX23 ``thai_address`` eating
``เลขที่ให้จำเมื่อกี้คืออะไร``; the FIX24 sweep's ``thai_name_no_prefix``
eating ``ชนิด ตอบสั้นๆ``) would both have failed here. This is the audit
that was missing; keep the corpus growing.

Registers NOT covered (say so rather than pretend): formal / legal
documents, Isan and Northern dialect, mixed-script transliteration of
names, poetry, OCR noise.

Also pins that the Python and Rust stop-lists for ``thai_name_no_prefix``
are the same lists (the live engine is Rust).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.preprocessor import normalize_for_scan

_CORPUS = Path(__file__).parent / "data" / "thai_prose_corpus.json"
_SENTENCES = json.loads(_CORPUS.read_text(encoding="utf-8"))["sentences"]


def _registry():
    # ner_enabled=False = the deterministic regex set (Presidio, when
    # importable, runs an English model that cannot see Thai anyway).
    return get_default_registry(ner_enabled=False)


def test_corpus_is_the_promised_size_and_shape():
    assert len(_SENTENCES) >= 100
    ids = [s["id"] for s in _SENTENCES]
    assert len(ids) == len(set(ids)), "duplicate corpus ids"
    for s in _SENTENCES:
        assert s["text"].strip(), s["id"]
        assert s["register"] in {"question", "instruction", "smalltalk", "tech", "work", "numbers"}, s["id"]


@pytest.mark.parametrize("sentence", _SENTENCES, ids=[s["id"] for s in _SENTENCES])
def test_no_detector_fires_on_ordinary_prose(sentence):
    """Per detector, on the same normalised text the registry hands it."""
    reg = _registry()
    norm = normalize_for_scan(sentence["text"]).normalized
    fired = {}
    for d in reg.all():
        ms = d.detect(norm)
        if ms:
            fired[d.name] = [m.matched_text for m in ms]
    assert not fired, f"{sentence['id']}: {fired!r} in {sentence['text']!r}"


def test_resolved_scan_is_silent_on_the_whole_corpus():
    """The registry's resolved view (what the pipeline would mask)."""
    reg = _registry()
    hits = [(s["id"], m.detector_name, m.matched_text) for s in _SENTENCES for m in reg.scan_text(s["text"])]
    assert hits == []


# ── Python ↔ Rust stop-list parity (thai_name_no_prefix) ──────────────
_RS = Path(__file__).resolve().parents[3] / "dlp-sidecar" / "src" / "detectors" / "thai_name_no_prefix.rs"


def _rust_list(name: str) -> set[str]:
    src = _RS.read_text(encoding="utf-8")
    m = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.S)
    assert m, f"{name} not found in {_RS}"
    return set(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))


@pytest.mark.skipif(not _RS.exists(), reason="dlp-sidecar source not checked out beside backend/")
@pytest.mark.parametrize(
    "py_name,rs_name",
    [("_STOP_EXACT", "STOP_EXACT"), ("_STOP_PREFIX", "STOP_PREFIX"), ("_STOP_SUFFIX", "STOP_SUFFIX"), ("_TRAIL_LABELS", "TRAIL_LABELS")],
)
def test_stop_lists_match_the_rust_port(py_name, rs_name):
    from app.services.dlp.detectors import thai_name_no_prefix as mod

    py = set(getattr(mod, py_name))
    rs = _rust_list(rs_name)
    assert py == rs, f"only_py={sorted(py - rs)} only_rust={sorted(rs - py)}"
