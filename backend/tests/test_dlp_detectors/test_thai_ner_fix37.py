"""FIX37 — the Thai NER layer: switch, boundaries, scoring, token stability.

The model itself lives in the ner-service; everything here exercises the
gateway-side layer with the model stubbed, so the suite stays hermetic and
fast. What it locks down:

* N5 — with the switch off the registry is byte-for-byte the pre-FIX37
  set and no ``thai_ner`` finding can appear.
* N2 — the outbound registry never carries the detector.
* N7 — one person keeps one placeholder across a conversation, and the
  regex detector wins the span when both engines see the same name.
* the context enhancer's individual signals, including the three
  false-positive shapes the must-not-fire corpus caught.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.dlp.detectors import thai_ner as tn
from app.services.dlp.detectors import thai_ner_scoring as sc
from app.services.dlp.detectors.registry import get_default_registry


@pytest.fixture(autouse=True)
def _clean_cache():
    tn._cache.clear()
    yield
    tn._cache.clear()


@pytest.fixture
def stub_model(monkeypatch):
    """Replace the ner-service call with a fixed span list."""

    def _install(spans):
        monkeypatch.setattr(
            tn.ThaiNerDetector, "_model_spans", lambda self, text, language: list(spans)
        )

    return _install


@pytest.fixture
def ner_on(monkeypatch):
    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", True, raising=False)
    monkeypatch.setattr(settings, "dlp_thai_ner_threshold", 0.55, raising=False)


# ── N5: the switch ────────────────────────────────────────────────────────

def test_registry_without_thai_ner_is_the_pre_fix37_set():
    off = get_default_registry(ner_enabled=False, thai_ner=False)
    on = get_default_registry(ner_enabled=False, thai_ner=True)
    assert "thai_ner" not in off
    assert "thai_ner" in on
    assert len(on) == len(off) + 1
    assert {d.name for d in on.all()} - {d.name for d in off.all()} == {"thai_ner"}


def test_detector_is_inert_when_the_switch_is_off(monkeypatch, stub_model):
    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", False, raising=False)
    stub_model([("PERSON", 0, 6)])
    assert tn.ThaiNerDetector().detect("คุณสมชาย โทรมาแจ้งว่าของยังไม่ถึง") == []


def test_switch_off_leaves_the_scan_identical(monkeypatch, stub_model):
    text = "ลูกค้าชื่อคุณสมชาย ใจดี โทรมาบ่นว่าของยังไม่ถึง"
    stub_model([("PERSON", 10, 23)])

    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", False, raising=False)
    before = get_default_registry(ner_enabled=False, thai_ner=False).scan_text(text)

    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", True, raising=False)
    with_layer = get_default_registry(ner_enabled=False, thai_ner=True).scan_text(text)

    assert not any(m.detector_name == "thai_ner" for m in before)
    assert {(m.detector_name, m.start, m.end) for m in before} <= {
        (m.detector_name, m.start, m.end) for m in with_layer
    }


# ── N2: input only ────────────────────────────────────────────────────────

def test_outbound_registry_never_carries_the_detector(monkeypatch):
    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", True, raising=False)
    from app.services.orchestrator import GatewayOrchestrator

    orch = GatewayOrchestrator()
    for ner_axis in (True, False):
        assert "thai_ner" not in orch._select_registry(ner_axis, for_output=True)
    assert "thai_ner" in orch._select_registry(False)


# ── N7: one person, one placeholder ───────────────────────────────────────

def test_regex_span_wins_when_both_engines_see_the_same_name(ner_on, stub_model):
    text = "ลูกค้าชื่อคุณสมชาย ใจดี โทรมาแจ้งว่าของยังไม่ถึง"
    stub_model([("PERSON", text.index("คุณสมชาย"), text.index("คุณสมชาย") + 13)])
    matches = get_default_registry(ner_enabled=False, thai_ner=True).scan_text(text)
    by_name = {m.detector_name for m in matches}
    assert "thai_name" in by_name
    ner_hits = [m for m in matches if m.detector_name == "thai_ner"]
    regex_hits = [m for m in matches if m.detector_name == "thai_name"]
    # Lower priority => the transformer's overlap resolver keeps the regex
    # span, so the person is masked once, not twice.
    assert all(h.priority < regex_hits[0].priority for h in ner_hits)


def test_same_name_twice_in_one_text_yields_one_placeholder(ner_on, stub_model):
    from app.schemas.common import Message
    from app.services.dlp.classifier import SensitivityClassifier
    from app.services.dlp.policy_engine import DLPPolicyEngine
    from app.services.dlp.transformer import DLPTransformer
    from app.services.policy.evaluator import PolicyEvaluator
    from app.services.policy.profiles import get_profile

    text = "พี่ตูนโทรมาแจ้งเรื่องของ แล้วพี่ตูนก็ฝากบอกเรื่องใบเสร็จด้วย"
    stub_model([])
    msgs = [Message(role="user", content=text)]
    reg = get_default_registry(ner_enabled=False, thai_ner=True)
    det = reg.scan_messages(msgs, direction="input")
    decision = DLPPolicyEngine(
        classifier=SensitivityClassifier(), evaluator=PolicyEvaluator()
    ).evaluate_decision(detection_result=det, profile=get_profile("default"), text=text)
    masked, span_map, _ = DLPTransformer().mask_messages(msgs, decision.match_actions)
    assert "ตูน" not in masked[0].content
    # One value => one token, however many times it occurs.
    assert len({v for v in span_map.values()}) == len(set(span_map.values()))
    assert len(span_map) == 1


# ── the context enhancer ──────────────────────────────────────────────────

def test_title_prefix_is_recognised_without_a_space():
    text = "คุณสมชาย โทรมา"
    assert sc.title_prefix_before(text, text.index("สมชาย")) == "คุณ"
    assert sc.title_prefix_before("พี่ตูนโทรมา", 3) == "พี่"
    assert sc.title_prefix_before("รถตูนจอดอยู่", 2) is None


def test_a_name_inside_a_longer_word_is_a_fragment():
    text = "แก้วน้ำแตกสามใบ"
    assert sc.continues_into_word(text, 0, 4) is True


def test_place_classifier_blocks_a_person_reading():
    assert sc.non_name_prefix_before("ไปงานบุญที่วัดชัยมงคล", len("ไปงานบุญที่วัด")) == "วัด"
    assert sc.non_name_prefix_before("ช่วงนี้หน้าฝนแล้ว", len("ช่วงนี้หน้า")) == "หน้า"
    assert sc.non_name_prefix_before("ลูกค้าชื่อสมชาย", len("ลูกค้าชื่อ")) is None


def test_pronoun_title_does_not_excuse_an_everyday_word():
    """``คุณคิดว่า…`` is "do you think", not a person called คิด."""
    text = "คุณคิดว่าอันไหนดีกว่ากัน"
    score, reasons = sc.score_person(text, 3, 6, source="ner")
    assert "-dictionary-word" in reasons
    assert score < 0.55


def test_a_strong_title_does_excuse_an_everyday_word():
    text = "น้องแก้วโทรมาแจ้งว่าของยังไม่ถึง"
    score, reasons = sc.score_person(text, 4, 8, source="nickname")
    assert "-dictionary-word" not in reasons
    assert score >= 0.55


def test_list_context_pushes_a_nickname_below_the_bar():
    text = "รายชื่อสินค้า: กล้วย, ส้ม, มะม่วง"
    i = text.index("ส้ม")
    score, reasons = sc.score_person(text, i, i + 3, source="nickname")
    assert "-list-context" in reasons
    assert score < 0.55


def test_sensitive_context_lifts_a_bare_given_name():
    text = "นิภาเป็นหนี้บัตรเครดิต"
    _score, reasons = sc.score_person(text, 0, 4, source="gazetteer_ambiguous")
    assert "sensitive-context" in reasons


# ── span hygiene ──────────────────────────────────────────────────────────

def test_an_over_extended_run_is_cut_to_the_name(ner_on, stub_model):
    text = "มีชัยจองห้องประชุมไว้แล้ว"
    stub_model([("PERSON", 0, len(text))])
    hits = tn.ThaiNerDetector().detect(text)
    assert all(len(h.matched_text) <= len("มีชัย") + 2 for h in hits)
    assert all("ห้องประชุม" not in h.matched_text for h in hits)


def test_a_run_with_no_name_inside_is_dropped_not_masked(ner_on, stub_model):
    text = "ขอบคุณที่ติดต่อเรามาทางอีเมลนะครับผมจะรีบตรวจสอบให้"
    stub_model([("PERSON", 0, len(text))])
    assert tn.ThaiNerDetector().detect(text) == []


def test_the_title_is_not_masked_with_the_name(ner_on, stub_model):
    text = "คุณสมชาย ใจดี โทรมาแจ้งว่าของยังไม่ถึง"
    stub_model([("PERSON", 0, 13)])
    hits = tn.ThaiNerDetector().detect(text)
    assert hits, "expected a person finding"
    assert not any(h.matched_text.startswith("คุณ") for h in hits)


# ── N6: the cheap outs ────────────────────────────────────────────────────

def test_the_model_is_not_called_for_text_that_cannot_contain_thai(ner_on, monkeypatch):
    called = []
    monkeypatch.setattr(
        tn.ThaiNerDetector,
        "_model_spans",
        lambda self, text, language: called.append(text) or [],
    )
    tn.ThaiNerDetector().detect("please send the invoice to accounting")
    tn.ThaiNerDetector().detect("สั้น")
    assert called == []


def test_a_repeated_message_hits_the_cache(ner_on, monkeypatch):
    calls = []
    monkeypatch.setattr(
        tn.ThaiNerDetector,
        "_model_spans",
        lambda self, text, language: calls.append(1) or [],
    )
    text = "ลูกค้าชื่อคุณสมชาย ใจดี โทรมาแจ้งว่าของยังไม่ถึง"
    det = tn.ThaiNerDetector()
    det.detect(text)
    det.detect(text)
    assert len(calls) == 1


def test_a_very_long_turn_skips_the_model(ner_on, monkeypatch):
    monkeypatch.setattr(settings, "dlp_thai_ner_max_chars", 100, raising=False)
    calls = []
    monkeypatch.setattr(
        tn.ThaiNerDetector,
        "_model_spans",
        lambda self, text, language: calls.append(1) or [],
    )
    tn.ThaiNerDetector().detect("ลูกค้าชื่อคุณสมชาย ใจดี " * 20)
    assert calls == []


# ── failure model ─────────────────────────────────────────────────────────

def test_an_unreachable_model_degrades_to_no_findings(ner_on, monkeypatch):
    def _boom(self, text, language):
        raise RuntimeError("ner-service down")

    monkeypatch.setattr(tn.ThaiNerDetector, "_model_spans", _boom)
    assert tn.ThaiNerDetector().detect("ลูกค้าชื่อคุณสมชาย ใจดี โทรมา") == []
