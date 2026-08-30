"""FIX29 — Thai given-name lexicon with a segmentation-aware right boundary.

Pins: the two lexicon files are byte-identical in both engines; a glued
given name ends at the name; a dictionary word is never a name; the titled
detector's over-capture is trimmed at the gazetteer name; the corpus stays
silent (the corpus test itself covers that).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.dlp.detectors.thai_gazetteer import find_names, names, trim_run, words
from app.services.dlp.detectors.thai_name import ThaiNameDetector
from app.services.dlp.detectors.thai_name_no_prefix import ThaiNameNoPrefixDetector

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize("fname", ["thai_given_names.txt", "thai_given_names_ambiguous.txt", "thai_words.txt"])
def test_lexicon_files_are_identical_in_both_engines(fname):
    py = BACKEND / "app" / "services" / "dlp" / "data" / fname
    rs = REPO / "dlp-sidecar" / "data" / fname
    if not rs.exists():
        pytest.skip("dlp-sidecar not checked out beside backend/")
    assert _sha(py) == _sha(rs), f"{fname} differs between backend and dlp-sidecar"


def test_lexicon_is_a_names_list_with_dictionary_words_removed():
    n, w = names(), words()
    assert len(n) > 9000 and len(w) > 50000
    assert not (n & w), "a name that is also a dictionary word must not be in the lexicon"
    assert "ชนิด" not in n and "ประชุม" not in n and "ส่ง" not in n


def test_glued_given_name_ends_at_the_name():
    text = "ณัฐพลบอกว่าจะมาสาย"
    n = len("ณัฐพล")
    assert find_names(text) == [(0, n)]


def test_no_prefix_detector_masks_the_glued_name_with_a_correct_span():
    text = "ศิริพรโอนเงินมาแล้ว"
    spans = [(m.start, m.end, text[m.start : m.end]) for m in ThaiNameNoPrefixDetector().detect(text)]
    n = len("ศิริพร")
    assert (0, n, "ศิริพร") in spans
    assert all(not e > n for s, e, _ in spans if s == 0)


def test_name_label_glued_to_the_name_is_a_left_boundary():
    text = "ลูกค้าชื่อสมชาย เบอร์ 081-234-5678"
    s = len("ลูกค้าชื่อ")
    assert find_names(text) == [(s, s + len("สมชาย"))]


def test_dictionary_words_and_prose_do_not_fire():
    for text in ("ประชุมเลื่อนไปพรุ่งนี้", "บอกชื่อสัตว์มา 1 ชนิด ตอบสั้นๆ", "เมื่อวาน ฝนตก หนักมาก รถติด"):
        assert find_names(text) == [], text


def test_titled_run_is_trimmed_at_the_name_never_swallowing_the_verb():
    text = "คุณมีชัยจองห้องประชุมไว้"
    assert trim_run(text, 3, len(text)) == 3 + len("มีชัย")
    hits = [text[m.start : m.end] for m in ThaiNameDetector().detect(text)]
    assert hits == ["คุณมีชัย"]


def test_titled_two_word_name_is_untouched():
    text = "ลูกค้าชื่อคุณสมชาย ใจดี โทรมา"
    hits = [text[m.start : m.end] for m in ThaiNameDetector().detect(text)]
    assert hits == ["คุณสมชาย ใจดี"]
