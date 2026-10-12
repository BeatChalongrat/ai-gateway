"""P0 DLP Live Integration Test — exercises the full pipeline via /admin/dlp/test
plus local classifier + policy evaluator to show mask/block decisions.

Run: python -m pytest tests/test_dlp_p0_live.py -v -s
"""

import textwrap

import pytest
from fastapi.testclient import TestClient

from app.domain.dlp import DetectionResult, SensitivityLevel
from app.main import app
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import DEFAULT_PROFILE, STRICT_PROFILE

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _safe(text: str) -> str:
    """Replace non-ASCII chars with their repr to avoid cp1252 encoding errors on Windows."""
    return text.encode("ascii", errors="replace").decode("ascii")


def _call_dlp_test(text: str) -> dict:
    resp = client.post("/admin/dlp/test", headers=_HEADERS, json={"text": text})
    assert resp.status_code == 200, f"Endpoint returned {resp.status_code}: {resp.text}"
    return resp.json()


def _full_pipeline(text: str, profile=DEFAULT_PROFILE) -> dict:
    """Run detector -> classifier -> evaluator locally for policy decisions."""
    registry = get_default_registry(disabled_detectors=[])
    matches = registry.scan_text(text)
    classifier = SensitivityClassifier()
    classified = classifier.classify(matches)
    result = DetectionResult(matches=classified)
    evaluator = PolicyEvaluator()
    decision = evaluator.evaluate(result, profile)
    return {
        "overall_action": decision.overall_action.name.lower(),
        "is_blocked": decision.is_blocked,
        "match_actions": [
            {
                "detector": ma.match.detector_name,
                "entity_type": ma.match.entity_type,
                "category": ma.match.category,
                "severity": ma.match.severity.name,
                "action": ma.action.name.lower(),
                "matched_text": ma.match.matched_text[:40],
            }
            for ma in decision.match_actions
        ],
    }


def _print_results(label: str, text: str, api_result: dict, pipeline: dict, expected: list[dict]):
    """Pretty-print detection results and compare against expected."""
    p = lambda s: print(_safe(s))
    p(f"\n{'='*80}")
    p(f"  {label}")
    p(f"{'='*80}")
    p(f"\n--- Masked text ---")
    for line in api_result["masked_text"].split("\n"):
        p(f"  {line}")
    p(f"\n--- Detections ({len(api_result['findings'])} findings) ---")
    p(f"  {'Detector':<22} {'Severity':<10} {'Category':<12} {'Matched Text'}")
    p(f"  {'-'*22} {'-'*10} {'-'*12} {'-'*40}")
    for f in sorted(api_result["findings"], key=lambda x: x["start"]):
        mt = f["matched_text"][:40]
        p(f"  {f['detector']:<22} {f['severity']:<10} {f['category']:<12} {mt}")

    p(f"\n--- Policy pipeline (DEFAULT_PROFILE) ---")
    p(f"  Overall action: {pipeline['overall_action'].upper()}")
    p(f"  Blocked: {pipeline['is_blocked']}")
    if pipeline["match_actions"]:
        p(f"  {'Detector':<22} {'Severity':<10} {'Action':<8} {'Matched Text'}")
        p(f"  {'-'*22} {'-'*10} {'-'*8} {'-'*40}")
        for ma in pipeline["match_actions"]:
            p(f"  {ma['detector']:<22} {ma['severity']:<10} {ma['action']:<8} {ma['matched_text']}")

    p(f"\n--- Expected vs Actual ---")
    detected_detectors = {f["detector"] for f in api_result["findings"]}
    hits = 0
    misses = 0
    for exp in expected:
        name = exp["detector"]
        found = name in detected_detectors
        icon = "[OK]" if found else "[MISS]"
        sev = exp.get("severity", "")
        note = exp.get("note", "")
        if found:
            actual = [f for f in api_result["findings"] if f["detector"] == name]
            actual_sev = actual[0]["severity"] if actual else "?"
            sev_match = actual_sev == sev
            sev_icon = "[OK]" if sev_match else f"[WARN] (got {actual_sev})"
            p(f"  {icon} {name:<22} severity: {sev} {sev_icon}  {note}")
            hits += 1
        else:
            p(f"  {icon} {name:<22} severity: {sev}  MISSED  {note}")
            misses += 1
    p(f"\n  Score: {hits}/{len(expected)} detected")
    return hits, misses


# ---------------------------------------------------------------------------
# Test messages
# ---------------------------------------------------------------------------

MSG1_THAI = textwrap.dedent("""\
สวัสดีครับ ผมชื่อสมชาย ใจดี อยู่บ้านเลขที่ 123/45 ซอยสุขุมวิท 55 แขวงคลองตันเหนือ เขตวัฒนา กรุงเทพ 10110
ผมเป็นเบาหวานมา 3 ปีแล้ว หมอที่โรงพยาบาลรามาให้ยา Metformin ทุกวัน
เลขบัตรประชาชน 1-1012-12345-67-1 เบอร์โทร 089-123-4567
บัญชีธนาคารกสิกร 012-3-45678-9 พร้อมเพย์เบอร์ 0891234567
ผมนับถือศาสนาอิสลามครับ ไปละหมาดที่มัสยิดทุกวันศุกร์
ผมเคยมีคดีลักทรัพย์ ศาลพิพากษาจำคุก 2 ปี รอลงอาญา
ผมใช้ลายนิ้วมือสแกนเข้าออฟฟิศทุกวัน
อีเมล somchai@gmail.com""")

MSG1_EXPECTED = [
    {"detector": "thai_address", "severity": "MEDIUM", "note": "ที่อยู่"},
    {"detector": "health_condition", "severity": "HIGH", "note": "เบาหวาน + โรงพยาบาล"},
    {"detector": "thai_national_id", "severity": "HIGH", "note": "เลขบัตรประชาชน"},
    {"detector": "phone", "severity": "HIGH", "note": "089-123-4567"},
    {"detector": "thai_bank_account", "severity": "HIGH", "note": "012-3-45678-9"},
    {"detector": "thai_promptpay", "severity": "MEDIUM", "note": "0891234567"},
    {"detector": "religion_belief", "severity": "HIGH", "note": "ศาสนาอิสลาม + ละหมาด"},
    {"detector": "criminal_record", "severity": "HIGH", "note": "คดีลักทรัพย์ + จำคุก"},
    {"detector": "biometric_data", "severity": "HIGH", "note": "ลายนิ้วมือสแกน"},
    {"detector": "email", "severity": "MEDIUM", "note": "somchai@gmail.com"},
]

# Base64 of "sk-ant-api-key-1234567890" = c2stYW50LWFwaS1rZXktMTIzNDU2Nzg5MA==
# Zero-width spaces (U+200B) between credit card groups
# Cyrillic homoglyphs in "password" -> р а ѕ ѕ w о r d
# Base64 of "0891234567" = MDg5MTIzNDU2Nw==
MSG2_ENCODING = (
    "Here is some data:\n"
    "API key: c2stYW50LWFwaS1rZXktMTIzNDU2Nzg5MA==\n"
    "My credit card is 4532​0151​2345​6789\n"
    "Database: postgresql://admin:р​а​ѕ​ѕ​w​о​r​d@db.internal:5432/prod\n"
    "Phone encoded: MDg5MTIzNDU2Nw=="
)

MSG2_EXPECTED = [
    {"detector": "api_key_secret", "severity": "CRITICAL", "note": "base64 decode -> sk-ant-api-key"},
    {"detector": "credit_card", "severity": "HIGH", "note": "zero-width strip -> 4532015123456789"},
    {"detector": "db_connection_string", "severity": "CRITICAL", "note": "homoglyph normalization -> postgresql://"},
    {"detector": "phone", "severity": "HIGH", "note": "base64 decode -> 0891234567"},
]


MSG3_ENGLISH = textwrap.dedent("""\
My name is John Smith, I was diagnosed with HIV last year and I've been on antiretroviral therapy since.
I'm a practicing Muslim and attend the local mosque every Friday.
My SSN is 123-45-6789 and email john.smith@company.com
I was convicted of tax fraud in 2019, sentenced to 3 years probation.
My passport number is AA1234567.
Here's my private key:
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF1PbnGMn
-----END RSA PRIVATE KEY-----""")

MSG3_EXPECTED = [
    {"detector": "health_condition", "severity": "HIGH", "note": "HIV + antiretroviral therapy"},
    {"detector": "religion_belief", "severity": "HIGH", "note": "Muslim + mosque"},
    {"detector": "ssn", "severity": "HIGH", "note": "123-45-6789"},
    {"detector": "email", "severity": "MEDIUM", "note": "john.smith@company.com"},
    {"detector": "criminal_record", "severity": "HIGH", "note": "convicted + fraud + probation"},
    {"detector": "passport", "severity": "HIGH", "note": "AA1234567"},
    {"detector": "private_key", "severity": "CRITICAL", "note": "RSA PRIVATE KEY -> BLOCKED"},
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDlpP0LiveMsg1Thai:
    """Message 1: Thai story with multiple PII types + PDPA Section 26."""

    def test_msg1_detections(self):
        api = _call_dlp_test(MSG1_THAI)
        pipeline = _full_pipeline(MSG1_THAI)
        hits, misses = _print_results(
            "MSG 1: Thai PII + PDPA Section 26", MSG1_THAI, api, pipeline, MSG1_EXPECTED,
        )
        assert hits >= 7, f"Expected at least 7/10 detections, got {hits}"

    def test_msg1_email_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "email" in detectors

    def test_msg1_thai_national_id_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "thai_national_id" in detectors

    def test_msg1_religion_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "religion_belief" in detectors

    def test_msg1_criminal_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "criminal_record" in detectors

    def test_msg1_biometric_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "biometric_data" in detectors

    def test_msg1_health_detected(self):
        api = _call_dlp_test(MSG1_THAI)
        detectors = {f["detector"] for f in api["findings"]}
        assert "health_condition" in detectors

    def test_msg1_overall_not_blocked(self):
        """No CRITICAL findings in msg1 -> overall action is mask, not block."""
        pipeline = _full_pipeline(MSG1_THAI)
        assert not pipeline["is_blocked"]
        assert pipeline["overall_action"] == "mask"


class TestDlpP0LiveMsg2Encoding:
    """Message 2: Encoding bypass attempts — base64, zero-width, homoglyphs."""

    def test_msg2_detections(self):
        api = _call_dlp_test(MSG2_ENCODING)
        pipeline = _full_pipeline(MSG2_ENCODING)
        hits, misses = _print_results(
            "MSG 2: Encoding Bypass Attempts", MSG2_ENCODING, api, pipeline, MSG2_EXPECTED,
        )
        assert hits >= 2, f"Expected at least 2/4 encoding bypass detections, got {hits}"

    def test_msg2_db_connection_detected(self):
        api = _call_dlp_test(MSG2_ENCODING)
        detectors = {f["detector"] for f in api["findings"]}
        assert "db_connection_string" in detectors

    def test_msg2_has_critical_findings(self):
        """At least one CRITICAL finding (db_connection_string or api_key)."""
        api = _call_dlp_test(MSG2_ENCODING)
        severities = {f["severity"] for f in api["findings"]}
        assert "CRITICAL" in severities

    def test_msg2_blocked_by_policy(self):
        """CRITICAL credential findings -> masked (request proceeds) by
        DEFAULT_PROFILE, blocked by STRICT_PROFILE."""
        pipeline = _full_pipeline(MSG2_ENCODING)
        critical = [ma for ma in pipeline["match_actions"] if ma["severity"] == "CRITICAL"]
        assert critical and all(ma["action"] == "mask" for ma in critical)
        assert not pipeline["is_blocked"]
        assert pipeline["overall_action"] == "mask"

        strict = _full_pipeline(MSG2_ENCODING, STRICT_PROFILE)
        critical = [ma for ma in strict["match_actions"] if ma["severity"] == "CRITICAL"]
        assert critical and all(ma["action"] == "block" for ma in critical)
        assert strict["is_blocked"]
        assert strict["overall_action"] == "block"


class TestDlpP0LiveMsg3English:
    """Message 3: English story with mixed PII + private key (CRITICAL)."""

    def test_msg3_detections(self):
        api = _call_dlp_test(MSG3_ENGLISH)
        pipeline = _full_pipeline(MSG3_ENGLISH)
        hits, misses = _print_results(
            "MSG 3: English Mixed PII + Private Key", MSG3_ENGLISH, api, pipeline, MSG3_EXPECTED,
        )
        assert hits >= 4, f"Expected at least 4/7 detections, got {hits}"

    def test_msg3_ssn_detected(self):
        api = _call_dlp_test(MSG3_ENGLISH)
        detectors = {f["detector"] for f in api["findings"]}
        assert "ssn" in detectors

    def test_msg3_private_key_detected(self):
        api = _call_dlp_test(MSG3_ENGLISH)
        detectors = {f["detector"] for f in api["findings"]}
        assert "private_key" in detectors

    def test_msg3_email_detected(self):
        api = _call_dlp_test(MSG3_ENGLISH)
        detectors = {f["detector"] for f in api["findings"]}
        assert "email" in detectors

    def test_msg3_passport_detected(self):
        api = _call_dlp_test(MSG3_ENGLISH)
        detectors = {f["detector"] for f in api["findings"]}
        assert "passport" in detectors

    def test_msg3_blocked_by_policy(self):
        """Private key is CRITICAL -> the message is masked, not blocked, by
        DEFAULT_PROFILE and blocked by STRICT_PROFILE."""
        pipeline = _full_pipeline(MSG3_ENGLISH)
        assert not pipeline["is_blocked"]
        assert pipeline["overall_action"] == "mask"

        strict = _full_pipeline(MSG3_ENGLISH, STRICT_PROFILE)
        assert strict["is_blocked"]
        assert strict["overall_action"] == "block"

    def test_msg3_private_key_action_is_block(self):
        """The private_key match specifically: action=mask under
        DEFAULT_PROFILE, action=block under STRICT_PROFILE."""
        pipeline = _full_pipeline(MSG3_ENGLISH)
        pk_actions = [
            ma for ma in pipeline["match_actions"]
            if ma["detector"] == "private_key"
        ]
        assert len(pk_actions) > 0
        assert pk_actions[0]["action"] == "mask"

        strict = _full_pipeline(MSG3_ENGLISH, STRICT_PROFILE)
        pk_actions = [
            ma for ma in strict["match_actions"]
            if ma["detector"] == "private_key"
        ]
        assert len(pk_actions) > 0
        assert pk_actions[0]["action"] == "block"
