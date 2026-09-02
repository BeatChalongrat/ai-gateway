"""FIX38 — the pieces added on top of the restored FIX37 layer.

* C1 — Thai numerals in phone numbers (the 62% leak the FIX37 corpus
  measured). The Rust side has its own tests in ``phone.rs``; the parity
  gate (``make dlp-eval``) covers both engines on the same items.
* C2 — the decoy-aware matcher in the eval harness: a nickname inside a
  surviving decoy (``โอ๊ต`` in ``ข้าวโอ๊ต``) is not a leak.
* B3 — the decision trace explains a finding by layer / confidence /
  signals and never carries the matched text.
* A7 — the ner-service probe on /health says out loud when the Thai
  tagger is missing.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.dlp.detectors.phone import PhoneDetector


# ── C1: Thai numerals ─────────────────────────────────────────────────────

def _phones(text: str) -> list[str]:
    return [m.matched_text for m in PhoneDetector().detect(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("โทร ๐๘๑-๒๓๔-๕๖๗๘ นะ", ["๐๘๑-๒๓๔-๕๖๗๘"]),
        ("เบอร์เขาคือ ๐๘๑๒๒๒๓๓๔๔ ช่วยโทรที", ["๐๘๑๒๒๒๓๓๔๔"]),
        # the two 9-digit shapes that leaked: Thai zero + Thai area digit
        ("เบอร์ร้าน ๐๒๗๗๘๘๙๙๐ ช่วงนี้", ["๐๒๗๗๘๘๙๙๐"]),
        ("ที่ติดต่อได้คือ ๐๙๔๕๕๖๖๗๗ ค่ะ", ["๐๙๔๕๕๖๖๗๗"]),
        ("ติดต่อ ๐๒-๗๗๘-๘๙๙๐", ["๐๒-๗๗๘-๘๙๙๐"]),
    ],
)
def test_thai_numeral_phones_are_caught(text, expected):
    assert _phones(text) == expected


def test_thai_numerals_that_are_not_phones_stay():
    assert _phones("ปี ๒๕๖๗ งบ ๑๒๓๔๕ บาท") == []
    # an example number, all one digit, in Thai numerals
    assert _phones("ตัวอย่างเบอร์ ๐๐๐-๐๐๐-๐๐๐๐ ครับ") == []
    # bare 10 Thai digits with no phone keyword nearby
    assert _phones("รหัสพัสดุ ๐๘๑๒๒๒๓๓๔๔ ส่งแล้ว") == []


def test_ascii_shapes_are_unchanged():
    assert _phones("เบอร์ร้าน 027788990 ช่วงนี้") == ["027788990"]
    assert _phones("โทร 081-234-5678 นะ") == ["081-234-5678"]
    assert _phones("order 1234567890 shipped") == []


# ── C2: the decoy-aware matcher ───────────────────────────────────────────

def test_a_name_inside_a_surviving_decoy_is_not_a_leak():
    from evals.mask_quality import harness as H

    masked = "<<PERSON_A>> ขอข้าวโอ๊ตเพิ่ม 2 ถุง"
    # locked substring rule: โอ๊ต is "present"
    assert H.present("โอ๊ต", masked) is True
    # corrected tool: the only occurrence is inside the declared decoy
    assert H.present_outside_decoys("โอ๊ต", masked, ["ข้าวโอ๊ต"]) is False


def test_a_real_leak_is_still_a_leak_under_the_strict_matcher():
    from evals.mask_quality import harness as H

    leaked = "โอ๊ต ขอข้าวโอ๊ตเพิ่ม 2 ถุง"
    assert H.present_outside_decoys("โอ๊ต", leaked, ["ข้าวโอ๊ต"]) is True
    # no decoy declared → identical to the locked rule
    assert H.present_outside_decoys("โอ๊ต", "ขอข้าวโอ๊ตเพิ่ม", []) is True


def test_strict_matcher_keeps_the_digit_run_rule():
    from evals.mask_quality import harness as H

    assert H.present_outside_decoys("081-234-5678", "โทร 0812345678", ["x"]) is True
    assert H.present_outside_decoys("081-234-5678", "โทร <<PHONE_1>>", ["x"]) is False


# ── B3: the decision trace ────────────────────────────────────────────────

def _decision_for(text: str):
    from app.schemas.common import Message
    from app.services.dlp.classifier import SensitivityClassifier
    from app.services.dlp.detectors.registry import get_default_registry
    from app.services.dlp.policy_engine import DLPPolicyEngine
    from app.services.policy.evaluator import PolicyEvaluator
    from app.services.policy.profiles import get_profile

    msgs = [Message(role="user", content=text)]
    det = get_default_registry(ner_enabled=False, thai_ner=False).scan_messages(msgs, direction="input")
    return DLPPolicyEngine(
        classifier=SensitivityClassifier(), evaluator=PolicyEvaluator()
    ).evaluate_decision(detection_result=det, profile=get_profile("default"), text=text)


def test_trace_names_the_layer_and_never_the_text(monkeypatch):
    from app.services.dlp import decision_trace as dt

    monkeypatch.setattr(settings, "dlp_decision_trace_enabled", True, raising=False)
    captured: list[dict] = []

    class _Log:
        def info(self, event, **kw):
            captured.append({"event": event, **kw})

    text = "โทร 081-234-5678 บัตร 1234567890121 ครับ"
    dt.trace_decision(_decision_for(text), direction="input", log=_Log())
    assert len(captured) == 1
    line = captured[0]
    assert line["event"] == "dlp_decision_trace"
    layers = {e["detector"]: e["layer"] for e in line["trace"]}
    assert layers["phone"] == "pattern+context"
    assert layers["thai_national_id"] == "pattern+validator"
    blob = repr(line)
    assert "081-234-5678" not in blob
    assert "1234567890121" not in blob
    for e in line["trace"]:
        assert set(e) >= {"detector", "layer", "entity", "span", "conf", "action", "reason"}


def test_trace_is_silent_when_there_is_nothing_to_explain(monkeypatch):
    from app.services.dlp import decision_trace as dt

    monkeypatch.setattr(settings, "dlp_decision_trace_enabled", True, raising=False)
    captured: list = []

    class _Log:
        def info(self, *a, **kw):
            captured.append(1)

    dt.trace_decision(_decision_for("วันนี้อากาศดีมาก"), direction="input", log=_Log())
    assert captured == []


def test_trace_carries_the_thai_ner_signals():
    from app.domain.dlp import DetectionMatch, EnforcementAction, MatchAction, SensitivityLevel
    from app.services.dlp import decision_trace as dt

    m = DetectionMatch(
        detector_name="thai_ner", entity_type="person", category="pii",
        start=3, end=6, matched_text="ตูน", severity=SensitivityLevel.MEDIUM,
        confidence=0.75, priority=-3, metadata={"signals": "base:nickname,title"},
    )
    e = dt.describe(MatchAction(match=m, action=EnforcementAction.MASK,
                                reason_code="pii_mask_policy", reason_message="x"))
    assert e["layer"] == "ner+context"
    assert e["signals"] == "base:nickname,title"
    assert e["conf"] == 0.75
    assert "ตูน" not in repr(e)


def test_trace_can_be_switched_off(monkeypatch):
    from app.services.dlp import decision_trace as dt

    monkeypatch.setattr(settings, "dlp_decision_trace_enabled", False, raising=False)
    captured: list = []

    class _Log:
        def info(self, *a, **kw):
            captured.append(1)

    dt.trace_decision(_decision_for("โทร 081-234-5678"), direction="input", log=_Log())
    assert captured == []


# ── A7: the ner-service probe is loud ─────────────────────────────────────

def test_ner_probe_reports_a_missing_thai_tagger(monkeypatch):
    from app.api.routes import health

    monkeypatch.setattr(settings, "ner_service_url", "http://ner:8001", raising=False)
    monkeypatch.setattr(settings, "dlp_thai_ner_enabled", True, raising=False)

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "degraded", "model": "en=en_core_web_lg, th_ner=error: boom"}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    out = health._probe_ner_service()
    assert out["status"].startswith("degraded")
    assert out["th_ner"] == "not_ready"
    assert out["thai_ner_layer"] is True


def test_ner_probe_reports_unreachable_without_raising(monkeypatch):
    from app.api.routes import health

    monkeypatch.setattr(settings, "ner_service_url", "http://ner:8001", raising=False)
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _boom)
    assert health._probe_ner_service()["status"] == "unreachable"


# ── Part E: the two context-enhancer additions ────────────────────────────

def test_a_bare_ambiguous_name_with_a_person_predicate_is_a_person():
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "รัตนาป่วยเป็นเบาหวาน"
    assert sc.boundary_predicate(text, 0, 5) is True
    score, reasons = sc.score_person(text, 0, 5, source="gazetteer_ambiguous")
    assert "boundary-predicate" in reasons and "-dictionary-word" not in reasons
    assert score >= 0.5


def test_the_predicate_rule_does_not_reach_verbs_of_saying():
    """``ประกาศแจ้งว่า…`` — an announcement says things; ประกาศ is also a name."""
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "ประกาศแจ้งว่าวันศุกร์หยุด"
    assert sc.boundary_predicate(text, 0, 6) is False


def test_the_predicate_rule_needs_a_left_boundary():
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "ทีมวิชัยถูกฟ้อง"  # วิชัย glued to the previous word
    i = text.index("วิชัย")
    assert sc.boundary_predicate(text, i, i + 5) is False


def test_a_predicate_alone_is_not_enough_without_a_context_word():
    """``มานะมาสายอีกแล้ว`` has the shape but no sensitive / person context:
    the waiver lifts the dictionary penalty and nothing else, so it stays
    under the bar. Reported as a known miss, not patched around."""
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "มานะมาสายอีกแล้ว"
    score, _ = sc.score_person(text, 0, 4, source="gazetteer_ambiguous")
    assert score < 0.5


def test_building_addresses_now_carry_address_context():
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "ส่งที่ 23 ตึกบี ห้อง 402"
    i = text.index("ตึกบี")
    score, reasons = sc.score_location(text, i, i + 5)
    assert "address-context" in reasons and score >= 0.5


def test_a_function_word_never_takes_the_predicate_waiver():
    """``ควรเซ็นสัญญา…`` — "should sign": ควร is a modal AND a given name.
    Found by the adversarial must-not-fire sentences on the first run."""
    from app.services.dlp.detectors import thai_ner_scoring as sc

    from app.services.dlp.detectors import thai_ner as tn

    text = "ควรเซ็นสัญญาให้เรียบร้อยก่อนเริ่มงานทุกครั้ง"
    assert sc.boundary_predicate(text, 0, 3) is False
    # the protection is the gate in the detector: no title, no regex
    # agreement, no boundary-predicate → the candidate never reaches a score
    tn._cache.clear()
    monkeypatch_target = tn.ThaiNerDetector
    orig = monkeypatch_target._model_spans
    try:
        monkeypatch_target._model_spans = lambda self, t, lang: []
        assert tn.ThaiNerDetector().detect(text) == []
    finally:
        monkeypatch_target._model_spans = orig
        tn._cache.clear()


# ── Part G: the address ends where the clause does ────────────────────────

def _addr(text: str) -> list[str]:
    from app.services.dlp.detectors.thai_address import ThaiAddressDetector

    return [m.matched_text for m in ThaiAddressDetector().detect(text)]


def test_the_owners_live_sentence_keeps_the_order_and_the_request():
    """Before FIX38 this masked ONE <<ADDRESS_1>> from 55/12 to the last
    word, eating the product, the quantities and the request itself."""
    t = "พี่ตูน โทร 081-234-5678 ที่อยู่ 55/12 ซอย 5 บางนา สั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ช่วยร่างข้อความตอบลูกค้าให้หน่อย"
    assert _addr(t) == ["ที่อยู่ 55/12 ซอย 5 บางนา"]


def test_district_and_province_without_keywords_are_kept():
    # (``อยู่ที่`` alone is the AMBIGUOUS grade and needs a component word —
    # that shape is a documented FIX26 gap the NER layer covers; the HIGH
    # keyword ``ที่อยู่`` is what exercises the trim here.)
    assert _addr("ที่อยู่ 77/1 บางใหญ่ นนทบุรี ตอนนี้ไม่มีคนอยู่") == ["ที่อยู่ 77/1 บางใหญ่ นนทบุรี"]


def test_a_postcode_still_ends_the_body():
    assert _addr("ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540 ส่งด่วนนะ") == [
        "ที่อยู่ 9 หมู่ 3 ต.บางพลี อ.บางพลี จ.สมุทรปราการ 10540"
    ]


def test_building_addresses_survive_the_trim():
    assert _addr("ที่อยู่ 199/22 คอนโดลุมพินี ชั้น 5 ห้อง 512 แล้วโทรบอกด้วย") == [
        "ที่อยู่ 199/22 คอนโดลุมพินี ชั้น 5 ห้อง 512"
    ]


def test_a_name_built_from_a_time_word_needs_a_title():
    """``เสนอวันใหม่สองตัวเลือก`` — "offer two new dates". วันใหม่ is a real
    given name in the gazetteer; without a title it is the phrase."""
    from app.services.dlp.detectors import thai_ner_scoring as sc

    text = "ช่วยร่างคำขอโทษพร้อมเสนอวันใหม่สองตัวเลือก"
    i = text.index("วันใหม่")
    score, reasons = sc.score_person(text, i, i + 7, source="gazetteer")
    assert "-classifier-compound" in reasons and score < 0.5
    titled = "คุณวันใหม่ โทรมาแจ้งว่าของยังไม่ถึง"
    j = titled.index("วันใหม่")
    score2, reasons2 = sc.score_person(titled, j, j + 7, source="gazetteer")
    assert "-classifier-compound" not in reasons2 and score2 >= 0.5
