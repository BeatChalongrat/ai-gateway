"""Phase 3.1 — public-entity collision & ambiguous-context hardening.

Covers the two audit blockers:
1. any-overlap → strict containment (private suffix/prefix never relaxed);
2. ambiguous-alias lexical boundary + sentence-bounded context.

V2 simulated in-process only (.env never touched). Name/place/org findings
are synthetic so the relaxation decision is tested deterministically;
sensitive findings come from the real detectors.
"""

from __future__ import annotations

import pytest

import app.core.config as cfg
from app.domain.dlp import DetectionMatch, DetectionResult, EnforcementAction, SensitivityLevel
from app.schemas.common import Message
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.policy_engine import (
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
    _public_span_safely_contains_finding,
)
from app.services.dlp.numeric_context import NumericTextSegment
from app.services.dlp.public_entity_registry import public_entity_registry
from app.services.policy.profiles import DEFAULT_PROFILE

_REG = get_default_registry(ner_enabled=False)
_ENG = DLPPolicyEngine()
_TX_REG = public_entity_registry


@pytest.fixture
def v2_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", False)


def _finding(text, sub, entity="person", category="pii", detector="presidio_ner"):
    s = text.find(sub)
    assert s >= 0, sub
    return DetectionMatch(detector, entity, category, s, s + len(sub), sub, SensitivityLevel.MEDIUM)


def _act(text, finding, mi=0):
    det = DetectionResult(matches=[finding], scanned_direction="input")
    seg = [NumericTextSegment(mi, "user", text, "direct_api")]
    dec = _ENG.evaluate_decision(
        detection_result=det, profile=DEFAULT_PROFILE, segments=seg,
        context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.INPUT),
    )
    return dec.match_actions[0].action.name


def _spans(text):
    return [x.entity_type.value for x in _TX_REG.match_segments(
        [NumericTextSegment(0, "user", text, "direct_api")])]


# ---------------------------------------------------------------------------
# Containment: exact / fully-contained → ALLOW; glued/partial → MASK
# ---------------------------------------------------------------------------


class TestContainment:
    @pytest.mark.parametrize("text,sub,entity", [
        ("พระบาทสมเด็จพระเจ้าอยู่หัว", "พระบาทสมเด็จพระเจ้าอยู่หัว", "person"),
        ("รัชกาลที่ 9", "รัชกาลที่ 9", "person"),
        ("พระบรมมหาราชวัง", "พระบรมมหาราชวัง", "location"),
        ("สำนักพระราชวัง", "สำนักพระราชวัง", "organization"),
    ])
    def test_exact_relaxed(self, v2_on, text, sub, entity):
        assert _act(text, _finding(text, sub, entity)) == "ALLOW"

    @pytest.mark.parametrize("text,sub,entity", [
        ("พระบาทสมเด็จพระเจ้าอยู่หัวสมชาย", "พระบาทสมเด็จพระเจ้าอยู่หัวสมชาย", "person"),
        ("สมชายพระบาทสมเด็จพระเจ้าอยู่หัว", "สมชายพระบาทสมเด็จพระเจ้าอยู่หัว", "person"),
        ("สมชายพระบาทสมเด็จพระเจ้าอยู่หัวใจดี", "สมชายพระบาทสมเด็จพระเจ้าอยู่หัวใจดี", "person"),
        ("รัชกาลที่ 9 สมชาย", "รัชกาลที่ 9 สมชาย", "person"),
        ("สมชาย รัชกาลที่ 9", "สมชาย รัชกาลที่ 9", "person"),
        ("พระบรมมหาราชวังวิว", "พระบรมมหาราชวังวิว", "location"),
        ("หมู่บ้านพระบรมมหาราชวังวิว", "หมู่บ้านพระบรมมหาราชวังวิว", "location"),
        ("สำนักพระราชวังเทรดดิ้ง", "สำนักพระราชวังเทรดดิ้ง", "organization"),
        ("บริษัทสำนักพระราชวังเทรดดิ้ง จำกัด", "บริษัทสำนักพระราชวังเทรดดิ้ง จำกัด", "organization"),
        ("สมเด็จพระนางเจ้าฯการค้า", "สมเด็จพระนางเจ้าฯการค้า", "person"),
    ])
    def test_finding_contains_public_stays_masked(self, v2_on, text, sub, entity):
        assert _act(text, _finding(text, sub, entity)) == "MASK"

    def test_partial_overlap_masked(self, v2_on):
        # Span sits mid-text; findings that EXTEND BEYOND it (true partial
        # overlap, not containment) must stay MASK.
        text = "abc รัชกาลที่ 9 xyz"
        i = text.find("รัชกาลที่ 9")
        end = i + len("รัชกาลที่ 9")
        # starts before span, ends inside
        f1 = DetectionMatch("presidio_ner", "person", "pii", 0, i + 4, text[0:i + 4], SensitivityLevel.MEDIUM)
        # starts inside, ends after span
        f2 = DetectionMatch("presidio_ner", "person", "pii", i + 4, len(text), text[i + 4:], SensitivityLevel.MEDIUM)
        # overlaps only the last char of the span
        f3 = DetectionMatch("presidio_ner", "person", "pii", end - 1, end + 3, text[end - 1:end + 3], SensitivityLevel.MEDIUM)
        assert _act(text, f1) == "MASK"
        assert _act(text, f2) == "MASK"
        assert _act(text, f3) == "MASK"

    def test_fully_contained_subspan_relaxed(self, v2_on):
        # A PERSON finding wholly inside the public reign span → relax.
        text = "รัชกาลที่ 9 สมชาย"
        f = DetectionMatch("presidio_ner", "person", "pii", 0, 9, text[0:9], SensitivityLevel.MEDIUM)  # "รัชกาลที่"
        assert _act(text, f) == "ALLOW"

    def test_helper_semantics(self):
        class S:  # minimal span
            def __init__(s, a, b): s.start, s.end = a, b
        class F:
            def __init__(f, a, b): f.start, f.end = a, b
        assert _public_span_safely_contains_finding(S(0, 10), F(0, 10)) is True
        assert _public_span_safely_contains_finding(S(0, 10), F(2, 8)) is True
        assert _public_span_safely_contains_finding(S(0, 10), F(0, 12)) is False
        assert _public_span_safely_contains_finding(S(2, 10), F(0, 8)) is False
        assert _public_span_safely_contains_finding(S(0, 10), F(5, 5)) is False  # empty


# ---------------------------------------------------------------------------
# Ambiguous alias lexical boundary
# ---------------------------------------------------------------------------


class TestAmbiguousBoundary:
    @pytest.mark.parametrize("text", [
        "ในหลวง", "ในหลวงชัย", "ชัยในหลวง", "ประวัติในหลวงชัย",
        "พระมหากษัตริย์กับในหลวงชัย", "ในหลวง123", "abcในหลวง", "ร้านในหลวง",
    ])
    def test_no_span(self, text):
        assert _spans(text) == []

    @pytest.mark.parametrize("text", [
        "พระมหากษัตริย์และในหลวง",
        "(ในหลวง) พระมหากษัตริย์",
        "พระมหากษัตริย์: ในหลวง",
        # alias bounded by a space before the next word → matches with context
        "วันนี้กล่าวถึงในหลวง ในรัชสมัยพระมหากษัตริย์",
    ])
    def test_span_with_context(self, text):
        assert "ROYAL_TITLE" in _spans(text)

    def test_thai_suffix_glue_is_conservative_miss(self):
        # KNOWN, SAFE limitation: when the alias is glued to a FOLLOWING
        # Thai word (no space), the post-guard rejects it even though the
        # following word is a separate token (no tokenizer → conservative).
        # Stays masked → safe.
        assert _spans("วันนี้กล่าวถึงในหลวงในรัชสมัยพระมหากษัตริย์") == []


# ---------------------------------------------------------------------------
# Sentence-bounded context
# ---------------------------------------------------------------------------


class TestSentenceBound:
    def test_newline_blocks_cross_sentence(self):
        assert _spans("พระมหากษัตริย์ทรงครองราชย์\nวันนี้พบในหลวง") == []

    def test_punctuation_blocks_cross_sentence(self):
        assert _spans("พระมหากษัตริย์ทรงครองราชย์. วันนี้พบในหลวง") == []

    def test_same_sentence_after_alias(self):
        assert "ROYAL_TITLE" in _spans("วันนี้พบในหลวง ทรงพระเจริญพระมหากษัตริย์")

    def test_context_in_prior_message_does_not_leak(self, v2_on):
        # Per-segment: msg2 alias can't borrow msg1 context.
        spans = _TX_REG.match_segments([
            NumericTextSegment(0, "user", "พระมหากษัตริย์", "direct_api"),
            NumericTextSegment(1, "user", "ในหลวง", "direct_api"),
        ])
        assert spans == []


# ---------------------------------------------------------------------------
# Post-transformer (real mask_messages)
# ---------------------------------------------------------------------------


def _mask(msgs, synth):
    M = [Message(role=r, content=c) for r, c in msgs]
    det = _REG.scan_messages(M, direction="input")
    det.matches = list(det.matches) + [m for _, m in synth]
    segs = [NumericTextSegment(i, r, c, "direct_api") for i, (r, c) in enumerate(msgs)]
    dec = _ENG.evaluate_decision(
        detection_result=det, profile=DEFAULT_PROFILE, segments=segs,
        context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.INPUT),
    )
    from app.services.dlp.transformer import DLPTransformer
    out, _, _ = DLPTransformer().mask_messages(M, dec.match_actions)
    return [m.content for m in out]


class TestPostTransformer:
    def test_exact_public_passthrough(self, v2_on):
        t = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        out = _mask([("user", t)], [(0, _finding(t, t))])
        assert out[0] == t  # raw public title preserved

    def test_glued_private_suffix_masked(self, v2_on):
        t = "พระบาทสมเด็จพระเจ้าอยู่หัวสมชาย"
        out = _mask([("user", t)], [(0, _finding(t, t))])
        assert "สมชาย" not in out[0]  # whole finding masked, no leak

    def test_public_plus_separate_phone(self, v2_on):
        t = "พระบาทสมเด็จพระเจ้าอยู่หัว โทร 0812345678"
        out = _mask([("user", t)], [(0, _finding(t, "พระบาทสมเด็จพระเจ้าอยู่หัว"))])
        assert "พระบาทสมเด็จพระเจ้าอยู่หัว" in out[0]  # public allowed
        assert "0812345678" not in out[0]              # phone masked

    def test_public_plus_otp(self, v2_on):
        t = "รัชกาลที่ 9 OTP 123456"
        out = _mask([("user", t)], [])
        assert "123456" not in out[0]


# ---------------------------------------------------------------------------
# Shadow purity + numeric non-regression
# ---------------------------------------------------------------------------


class TestShadowAndNumeric:
    def test_shadow_does_not_relax(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)
        t = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        assert _act(t, _finding(t, t)) == "MASK"  # legacy authoritative

    def test_public_plus_salary_masks(self, v2_on):
        out = _mask([("user", "รัชกาลที่ 9 เงินเดือน 50000")], [])
        assert "50000" not in out[0]
