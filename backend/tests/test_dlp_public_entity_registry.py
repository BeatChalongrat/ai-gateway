"""Phase 3 — Thai Royal/Public Entity Registry tests.

Covers the matcher, span-scoped V2 relaxation (public name/place/org →
ALLOW), conflict precedence (secrets/identifiers/private attrs/numeric
never relaxed), multi-message isolation, numeric interaction, direction
policy, shadow purity, and registry-failure fail-closed.

V2 is simulated in-process only (.env never touched). Name findings are
constructed synthetically (real name detectors rarely fire on royal
names) so the relaxation logic is tested deterministically; sensitive
findings come from the real detectors.
"""

from __future__ import annotations

import pytest

import app.core.config as cfg
from app.domain.dlp import DetectionMatch, DetectionResult, EnforcementAction, SensitivityLevel
from app.services.dlp.detectors.registry import get_default_registry
import app.services.dlp.policy_engine as PE
from app.services.dlp.policy_engine import (
    POLICY_V2_METADATA_KEY,
    DLPPolicyEngine,
    DLPRequestContext,
    ScanDirection,
    SourceType,
)
from app.services.dlp.numeric_context import NumericTextSegment
from app.services.dlp.public_entity_registry import (
    public_entity_registry,
    PublicEntityType,
)
from app.services.policy.profiles import DEFAULT_PROFILE

_REG = get_default_registry(ner_enabled=False)
_ENG = DLPPolicyEngine()


@pytest.fixture
def v2_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", True)
    monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", False)


def _person(text, sub, mi=0, detector="presidio_ner", entity="person", category="pii"):
    s = text.find(sub)
    assert s >= 0
    return DetectionMatch(
        detector_name=detector, entity_type=entity, category=category,
        start=s, end=s + len(sub), matched_text=sub,
        severity=SensitivityLevel.MEDIUM,
    )


def _run(msgs, synth=None):
    """msgs=[(role,text)]; synth=[(mi, DetectionMatch)]. Returns dict of
    (detector_name, matched_text) -> action.name, post per-finding refine."""
    M = [type("M", (), {"role": r, "content": c})() for r, c in msgs]
    det = _REG.scan_messages(
        [__import__("app.schemas.common", fromlist=["Message"]).Message(role=r, content=c) for r, c in msgs],
        direction="input",
    )
    matches = list(det.matches)
    if synth:
        matches.extend(m for _, m in synth)
    det.matches = matches
    segs = [NumericTextSegment(message_index=i, role=r, text=c, source_type="direct_api")
            for i, (r, c) in enumerate(msgs)]
    dec = _ENG.evaluate_decision(
        detection_result=det, profile=DEFAULT_PROFILE, segments=segs,
        context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.INPUT),
    )
    return {
        (ma.match.detector_name, ma.match.matched_text): ma.action
        for ma in dec.match_actions
    }


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


class TestMatcher:
    def _spans(self, txt):
        s = public_entity_registry.match_segments(
            [NumericTextSegment(0, "user", txt, "direct_api")])
        return [(x.entity_type, x.alias_type, txt[x.start:x.end]) for x in s]

    @pytest.mark.parametrize("txt,etype", [
        ("รัชกาลที่ 9", PublicEntityType.ROYAL_REIGN_REFERENCE),
        ("รัชกาลที่ ๙", PublicEntityType.ROYAL_REIGN_REFERENCE),
        ("ร.9", PublicEntityType.ROYAL_REIGN_REFERENCE),
        ("ร. 9", PublicEntityType.ROYAL_REIGN_REFERENCE),
        ("รัชกาลที่ 10", PublicEntityType.ROYAL_REIGN_REFERENCE),
        ("พระบาทสมเด็จพระเจ้าอยู่หัว", PublicEntityType.ROYAL_TITLE),
        ("พระบรมมหาราชวัง", PublicEntityType.PUBLIC_PLACE),
        ("สำนักพระราชวัง", PublicEntityType.PUBLIC_ORGANIZATION),
    ])
    def test_matches(self, txt, etype):
        spans = self._spans(txt)
        assert spans and spans[0][0] == etype

    def test_reign_boundary_no_overmatch(self):
        assert self._spans("รัชกาลที่ 90") == []   # digit guard
        assert self._spans("เลข 9") == []

    def test_ambiguous_alias_needs_context(self):
        assert self._spans("ในหลวง") == []                       # no context
        assert self._spans("พระมหากษัตริย์ของในหลวง") != []      # context present

    def test_no_raw_text_in_span(self):
        spans = public_entity_registry.match_segments(
            [NumericTextSegment(0, "user", "รัชกาลที่ 9", "direct_api")])
        # PublicEntitySpan carries offsets/ids only — no matched text field.
        assert not hasattr(spans[0], "matched_text")


# ---------------------------------------------------------------------------
# Public-only relaxation
# ---------------------------------------------------------------------------


class TestPublicOnly:
    def test_public_name_relaxed(self, v2_on):
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        acts = _run([("user", txt)], synth=[(0, _person(txt, txt))])
        assert acts[("presidio_ner", txt)] == EnforcementAction.ALLOW

    def test_public_place_relaxed(self, v2_on):
        txt = "พระบรมมหาราชวัง"
        acts = _run([("user", txt)], synth=[(0, _person(txt, txt, entity="location"))])
        assert acts[("presidio_ner", txt)] == EnforcementAction.ALLOW

    def test_public_org_relaxed(self, v2_on):
        txt = "สำนักพระราชวัง"
        acts = _run([("user", txt)], synth=[(0, _person(txt, txt, entity="organization"))])
        assert acts[("presidio_ner", txt)] == EnforcementAction.ALLOW

    def test_place_does_not_relax_person(self, v2_on):
        # A PUBLIC_PLACE span must NOT relax a PERSON finding.
        txt = "พระบรมมหาราชวัง"
        acts = _run([("user", txt)], synth=[(0, _person(txt, txt, entity="person"))])
        assert acts[("presidio_ner", txt)] == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Public name + private data — private data still protected
# ---------------------------------------------------------------------------


class TestPublicPlusPrivate:
    def test_public_name_phone(self, v2_on):
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว โทร 0812345678"
        acts = _run([("user", txt)], synth=[(0, _person(txt, "พระบาทสมเด็จพระเจ้าอยู่หัว"))])
        assert acts[("presidio_ner", "พระบาทสมเด็จพระเจ้าอยู่หัว")] == EnforcementAction.ALLOW
        assert any(d == "phone" and a == EnforcementAction.MASK for (d, _), a in acts.items())

    def test_public_name_thai_id(self, v2_on):
        # checksum-valid Thai ID
        base = [1, 1, 0, 1, 7, 0, 0, 2, 3, 0, 7, 0]
        w = [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
        nid = "".join(map(str, base)) + str((11 - sum(d*x for d, x in zip(base, w)) % 11) % 10)
        txt = f"รัชกาลที่ 9 เลขบัตร {nid}"
        acts = _run([("user", txt)])
        assert any(d == "thai_national_id" and a == EnforcementAction.MASK for (d, _), a in acts.items())

    def test_public_name_salary(self, v2_on):
        txt = "รัชกาลที่ 9 เงินเดือน 50000"
        acts = _run([("user", txt)])
        # confidential numeric still masked (force_mask)
        assert any(a == EnforcementAction.MASK for a in acts.values())

    def test_public_name_otp(self, v2_on):
        txt = "รัชกาลที่ 9 OTP 123456"
        acts = _run([("user", txt)])
        assert any(a == EnforcementAction.REDACT for a in acts.values())


# ---------------------------------------------------------------------------
# Private / collision
# ---------------------------------------------------------------------------


class TestCollision:
    def test_private_name_not_relaxed(self, v2_on):
        txt = "นายสมชาย ใจดี"
        acts = _run([("user", txt)], synth=[(0, _person(txt, "สมชาย ใจดี"))])
        assert acts[("presidio_ner", "สมชาย ใจดี")] == EnforcementAction.MASK

    def test_public_substring_in_longer_private_name_no_relax(self, v2_on):
        # A private PERSON span that merely CONTAINS a reign-ref-like
        # substring but isn't an exact public span → not relaxed.
        txt = "นายรัชกาล สุขใจ"
        acts = _run([("user", txt)], synth=[(0, _person(txt, "รัชกาล สุขใจ"))])
        assert acts[("presidio_ner", "รัชกาล สุขใจ")] == EnforcementAction.MASK

    def test_public_and_private_same_sentence(self, v2_on):
        txt = "รัชกาลที่ 9 และ นายสมชาย"
        acts = _run([("user", txt)], synth=[(0, _person(txt, "นายสมชาย"))])
        # private name masked; (no public name finding to relax here)
        assert acts[("presidio_ner", "นายสมชาย")] == EnforcementAction.MASK


# ---------------------------------------------------------------------------
# Multi-message isolation
# ---------------------------------------------------------------------------


class TestMultiMessage:
    def test_public_ref_and_private_person_isolated(self, v2_on):
        acts = _run(
            [("user", "ประวัติรัชกาลที่ 9"), ("user", "นายสมชาย ใจดี")],
            synth=[(1, _person("นายสมชาย ใจดี", "สมชาย ใจดี", mi=1))],
        )
        assert acts[("presidio_ner", "สมชาย ใจดี")] == EnforcementAction.MASK

    def test_public_ref_msg1_phone_msg2(self, v2_on):
        acts = _run([("user", "รัชกาลที่ 9"), ("user", "โทร 0812345678")])
        assert any(d == "phone" and a == EnforcementAction.MASK for (d, _), a in acts.items())


# ---------------------------------------------------------------------------
# Direction policy
# ---------------------------------------------------------------------------


def _decide_dir(text, direction, synth_person_sub=None):
    matches = list(_REG.scan_text(text))
    if synth_person_sub:
        matches.append(_person(text, synth_person_sub))
    det = DetectionResult(matches=matches, scanned_direction=direction)
    return _ENG.evaluate_decision(
        detection_result=det, profile=DEFAULT_PROFILE, text=text,
        context=DLPRequestContext(source_type=SourceType.DIRECT_API, direction=ScanDirection.coerce(direction)),
    )


class TestDirection:
    def test_inbound_relax(self, v2_on):
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        d = _decide_dir(txt, "input", synth_person_sub=txt)
        assert d.overall_action == EnforcementAction.ALLOW

    def test_outbound_relax(self, v2_on):
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        d = _decide_dir(txt, "output", synth_person_sub=txt)
        assert d.overall_action == EnforcementAction.ALLOW

    def test_outbound_secret_still_redacted(self, v2_on):
        d = _decide_dir("รัชกาลที่ 9 OTP 123456", "output")
        assert d.overall_action == EnforcementAction.REDACT


# ---------------------------------------------------------------------------
# Shadow purity + failure
# ---------------------------------------------------------------------------


class _BoomRegistry:
    def match_segments(self, segments):
        raise RuntimeError("registry boom")


class TestShadowAndFailure:
    def test_shadow_does_not_relax(self, monkeypatch):
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_enabled", False)
        monkeypatch.setattr(cfg.settings, "dlp_policy_engine_v2_shadow", True)
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        d = _decide_dir(txt, "input", synth_person_sub=txt)
        # Legacy authoritative: PERSON masked (no public relaxation in shadow).
        assert d.overall_action == EnforcementAction.MASK

    def test_registry_failure_no_relax(self, v2_on, monkeypatch):
        # match_segments raises internally → caught → [] → no relaxation.
        monkeypatch.setattr(PE, "public_entity_registry", _BoomRegistry())
        txt = "พระบาทสมเด็จพระเจ้าอยู่หัว"
        d = _decide_dir(txt, "input", synth_person_sub=txt)
        assert d.overall_action == EnforcementAction.MASK  # fail closed (still masked)

    def test_relaxed_finding_metadata_has_no_name(self, v2_on):
        txt = "พระบรมมหาราชวัง"
        det = DetectionResult(matches=[_person(txt, txt, entity="location")], scanned_direction="input")
        seg = [NumericTextSegment(0, "user", txt, "direct_api")]
        dec = _ENG.evaluate_decision(detection_result=det, profile=DEFAULT_PROFILE, segments=seg)
        import json
        pe = (dec.match_actions[0].match.metadata or {}).get(POLICY_V2_METADATA_KEY, {}).get("public_entity", {})
        assert pe and txt not in json.dumps(pe, ensure_ascii=False)
