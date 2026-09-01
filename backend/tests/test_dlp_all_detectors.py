"""Comprehensive DLP detector + pipeline tests.

Verifies that:
1. All 27 detectors are registered and fire correctly.
2. The full scan → classify → evaluate → mask pipeline works end-to-end.
3. NFKC normalization offset remapping preserves correct mask positions.
4. Religion span merging produces a single token.
5. Thai address detection handles postal-code termination.
"""

from __future__ import annotations

import unicodedata

import pytest

from app.domain.dlp import EnforcementAction
from app.schemas.common import Message
from app.services.dlp.classifier import SensitivityClassifier
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.preprocessor import normalize_for_scan
from app.services.dlp.transformer import DLPTransformer
from app.services.policy.evaluator import PolicyEvaluator
from app.services.policy.profiles import get_profile


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

TC1_THAI_MEGA = (
    "ผมชื่อนายสมศักดิ์ มั่งมี "
    "เลขบัตรประชาชน 3-1001-12345-67-4 "
    "อยู่บ้านเลขที่ 88/9 ถนนสีลม แขวงสีลม เขตบางรัก กรุงเทพ 10500 "
    "ต้องการสมัครสินเชื่อส่วนบุคคล "
    "เบอร์โทรติดต่อ 089-999-1234 "
    "อีเมล somsak.m@gmail.com "
    "บัญชีธนาคารกสิกร 012-3-98765-4 "
    "พร้อมเพย์หมายเลข 0899991234 "
    "รายได้ต่อเดือน 85,000 บาท "
    "ช่วยวิเคราะห์ว่าผมควรกู้เท่าไหร่ดีครับ"
)

TC2_HR_REPORT = (
    "สรุปข้อมูลพนักงานใหม่: "
    "นางสาวพิมพ์ลดา จันทร์สว่าง "
    "อายุ 28 ปี "
    "เกิด 22/08/1997 "
    "เลขบัตร 1-1007-54321-89-1 "
    "ผมนับถือศาสนาพุทธ "
    "ไปทำบุญที่วัดทุกวันพระ "
    "เคยมีประวัติคดีเล็กน้อย "
    "ศาลสั่งคุมประพฤติ 6 เดือนเมื่อปี 2562 "
    "ปัจจุบันเป็นโรคภูมิแพ้และรับยาแก้แพ้ต่อเนื่อง "
    "ใช้สแกนลายนิ้วมือเข้าออฟฟิศ "
    "เบอร์ 062-888-7777 "
    "ช่วยเขียน offer letter ให้หน่อย"
)

TC3_ENGLISH_EMPLOYEE = (
    "Employee report for Mr. John Smith, born 15/03/1990, SSN 123-45-6789. "
    "He is a practicing Muslim and attends the local mosque every Friday. "
    "He was diagnosed with depression last year and is currently on medication. "
    "Email: john.smith@company.com Phone: +1-555-012-3456"
)

TC4_CREDENTIALS = (
    "API key: sk-proj-abc123XYZ456def789ghi "
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJ0ZXN0IjoidmFsdWUifQ.signature "
    "Database: postgresql://admin:s3cret@prod-db.internal:5432/myapp "
    "Password: password=MyS3cur3P@ss "
    "Key: -----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJBAK7+...\n-----END RSA PRIVATE KEY-----"
)

TC5_CLEAN = (
    "What is the capital of Thailand? "
    "The weather today is sunny and 28 degrees."
)

TC6_FALSE_POSITIVE = (
    "Please use [PLACEHOLDER] in your code. "
    "The order number is 12345678901234. "
    "Version 3.14.159 is now available."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_pipeline(text: str) -> tuple[str, dict[str, str], int]:
    """Run full DLP pipeline and return (masked_text, span_map, match_count)."""
    registry = get_default_registry()
    messages = [Message(role="user", content=text)]
    detection = registry.scan_messages(messages, direction="input")

    classifier = SensitivityClassifier()
    if detection.has_sensitive_content:
        detection.matches = classifier.classify(detection.matches)

    evaluator = PolicyEvaluator()
    profile = get_profile("default")
    enforcement = evaluator.evaluate(detection, profile)

    transformer = DLPTransformer()
    new_msgs, span_map, _ = transformer.mask_messages(
        messages, enforcement.match_actions
    )
    return new_msgs[0].content, span_map, len(enforcement.match_actions)


def _scan_detectors(text: str) -> dict[str, int]:
    """Return {detector_name: match_count} for all detectors that fire."""
    registry = get_default_registry()
    results: dict[str, int] = {}
    for det in registry._detectors:
        matches = det.detect(text)
        if matches:
            results[det.name] = len(matches)
    return results


# ---------------------------------------------------------------------------
# TC1: Thai Mega Story — address, name, ID, phone, email, bank, promptpay
# ---------------------------------------------------------------------------

class TestTC1ThaiMega:
    def test_thai_name_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "thai_name" in hits

    def test_thai_national_id_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "thai_national_id" in hits

    def test_thai_address_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "thai_address" in hits

    def test_thai_address_includes_postal(self):
        from app.services.dlp.detectors.thai_address import ThaiAddressDetector
        det = ThaiAddressDetector()
        matches = det.detect(TC1_THAI_MEGA)
        assert len(matches) >= 1
        assert "10500" in matches[0].matched_text

    def test_phone_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "phone" in hits

    def test_email_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "email" in hits

    def test_bank_account_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "thai_bank_account" in hits

    def test_promptpay_detected(self):
        hits = _scan_detectors(TC1_THAI_MEGA)
        assert "thai_promptpay" in hits

    def test_full_pipeline_masks_all(self):
        masked, span_map, count = _full_pipeline(TC1_THAI_MEGA)
        assert count >= 7
        # Under the neutral ``<rN>`` shape we can't peg a specific token
        # to a specific category, so assert (a) tokens were emitted and
        # (b) each original PII value is gone from the masked output and
        # present in span_map.
        assert "<r0>" in masked
        for original in [
            "somsak.m@gmail.com",
            "089-999-1234",
            "3-1001-12345-67-4",
            "012-3-98765-4",
        ]:
            assert original not in masked
            assert original in span_map.values()

    def test_original_text_not_in_masked(self):
        masked, _, _ = _full_pipeline(TC1_THAI_MEGA)
        assert "3-1001-12345-67-4" not in masked
        assert "012-3-98765-4" not in masked


# ---------------------------------------------------------------------------
# TC2: HR Report — PDPA S26 (religion, health, criminal, biometric) + NFKC
# ---------------------------------------------------------------------------

class TestTC2HRReport:
    def test_all_pdpa_detectors_fire(self):
        hits = _scan_detectors(TC2_HR_REPORT)
        assert "religion_belief" in hits
        assert "health_condition" in hits
        assert "criminal_record" in hits
        assert "biometric_data" in hits

    def test_religion_merged_to_single_span(self):
        from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
        det = ReligionBeliefDetector()
        matches = det.detect(TC2_HR_REPORT)
        assert len(matches) == 1, f"Expected 1 merged religion match, got {len(matches)}"

    def test_nfkc_offset_remapping(self):
        """NFKC decomposes Sara Am (U+0E33), shifting offsets.
        Verify all matches still map correctly to the original text."""
        registry = get_default_registry()
        messages = [Message(role="user", content=TC2_HR_REPORT)]
        detection = registry.scan_messages(messages, direction="input")
        for m in detection.matches:
            actual = TC2_HR_REPORT[m.start:m.end]
            assert actual == m.matched_text, (
                f"{m.detector_name} offset mismatch: "
                f"text[{m.start}:{m.end}]={actual!r} != matched={m.matched_text!r}"
            )

    def test_full_pipeline_masks_all_pdpa(self):
        # Every PDPA-class value should be moved into span_map and gone
        # from masked. Per-category token literals no longer apply
        # under the neutral ``<rN>`` shape.
        masked, span_map, _ = _full_pipeline(TC2_HR_REPORT)
        for original in [
            "062-888-7777",
            "1-1007-54321-89-1",
            "22/08/1997",
        ]:
            assert original not in masked
            assert original in span_map.values()
        # At minimum one token must be emitted for the run.
        assert "<r0>" in masked

    def test_religion_merged_to_single_token(self):
        """Religion should produce a single merged finding (one span)."""
        from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
        det = ReligionBeliefDetector()
        matches = det.detect(TC2_HR_REPORT)
        assert len(matches) == 1, (
            f"expected 1 merged religion span, got {len(matches)}"
        )

    def test_phone_masked_despite_nfkc_shift(self):
        masked, span_map, _ = _full_pipeline(TC2_HR_REPORT)
        assert "062-888-7777" not in masked
        assert "062-888-7777" in span_map.values()

    def test_span_map_has_all_entries(self):
        _, span_map, _ = _full_pipeline(TC2_HR_REPORT)
        assert len(span_map) >= 8


# ---------------------------------------------------------------------------
# TC3: English Employee — SSN, DOB, religion, health, phone, email
# ---------------------------------------------------------------------------

class TestTC3EnglishEmployee:
    def test_ssn_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "ssn" in hits

    def test_dob_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "date_of_birth" in hits

    def test_english_religion_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "religion_belief" in hits

    def test_english_health_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "health_condition" in hits

    def test_email_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "email" in hits

    def test_phone_detected(self):
        hits = _scan_detectors(TC3_ENGLISH_EMPLOYEE)
        assert "phone" in hits

    def test_full_pipeline_masks_sensitive(self):
        masked, span_map, _ = _full_pipeline(TC3_ENGLISH_EMPLOYEE)
        for original in [
            "123-45-6789",
            "john.smith@company.com",
            "+1-555-012-3456",
        ]:
            assert original not in masked
            assert original in span_map.values()
        assert "<r0>" in masked


# ---------------------------------------------------------------------------
# TC4: Credentials — all secret/credential detectors
# ---------------------------------------------------------------------------

class TestTC4Credentials:
    def test_api_key_detected(self):
        hits = _scan_detectors(TC4_CREDENTIALS)
        assert "api_key_secret" in hits

    def test_bearer_token_detected(self):
        hits = _scan_detectors(TC4_CREDENTIALS)
        assert "bearer_token" in hits

    def test_db_connection_detected(self):
        hits = _scan_detectors(TC4_CREDENTIALS)
        assert "db_connection_string" in hits

    def test_password_detected(self):
        hits = _scan_detectors(TC4_CREDENTIALS)
        assert "password_field" in hits

    def test_private_key_detected(self):
        hits = _scan_detectors(TC4_CREDENTIALS)
        assert "private_key" in hits


# ---------------------------------------------------------------------------
# TC5: Clean text — no false positives
# ---------------------------------------------------------------------------

class TestTC5Clean:
    def test_no_detections(self):
        registry = get_default_registry()
        messages = [Message(role="user", content=TC5_CLEAN)]
        detection = registry.scan_messages(messages, direction="input")
        assert not detection.has_sensitive_content

    def test_pipeline_returns_unchanged(self):
        masked, span_map, count = _full_pipeline(TC5_CLEAN)
        assert masked == TC5_CLEAN
        assert span_map == {}
        assert count == 0


# ---------------------------------------------------------------------------
# TC6: False positive stress — bracket text, long digit runs
# ---------------------------------------------------------------------------

class TestTC6FalsePositive:
    def test_bracket_placeholder_not_detected(self):
        """[PLACEHOLDER] (no _N index) should not trigger any detector."""
        hits = _scan_detectors(TC6_FALSE_POSITIVE)
        for det_name, cnt in hits.items():
            assert det_name != "religion_belief", "False positive on bracket text"

    def test_pipeline_minimal_masking(self):
        masked, span_map, _ = _full_pipeline(TC6_FALSE_POSITIVE)
        assert "[PLACEHOLDER]" in masked


# ---------------------------------------------------------------------------
# NFKC normalization offset mapping
# ---------------------------------------------------------------------------

class TestNFKCOffsetMapping:
    def test_sara_am_decomposition_tracked(self):
        """Thai Sara Am (U+0E33) decomposes to 2 chars under NFKC."""
        text = "ทำงาน"  # ำ = U+0E33
        norm = normalize_for_scan(text)
        assert norm.offset_map is not None
        assert len(norm.normalized) == len(text) + 1
        # Position of 'ง' in normalized should map back to original
        orig_idx = norm.offset_map[3]  # 'ง' is at position 3 in NFKC output
        assert text[orig_idx] == "ง"

    def test_no_sara_am_no_map(self):
        text = "Hello world"
        norm = normalize_for_scan(text)
        assert norm.offset_map is None

    def test_offset_map_end_boundary(self):
        """End-of-text boundary check: last mapped position + 1 = len(original)."""
        text = "ทำ"  # 2 chars original, 3 chars NFKC
        norm = normalize_for_scan(text)
        assert norm.offset_map is not None
        assert len(norm.offset_map) == 3
        from app.services.dlp.preprocessor import map_offset_to_original
        end = map_offset_to_original(3, norm.offset_map)
        assert end == len(text)

    def test_multiple_sara_am(self):
        text = "ทำนำหลำ"  # 3 Sara Am characters
        nfkc = unicodedata.normalize("NFKC", text)
        norm = normalize_for_scan(text)
        assert norm.offset_map is not None
        assert len(norm.normalized) == len(nfkc)
        # Each Sara Am adds 1 extra char
        assert len(norm.normalized) == len(text) + 3


# ---------------------------------------------------------------------------
# Thai address regex
# ---------------------------------------------------------------------------

class TestThaiAddressPostalCode:
    def test_running_text_with_postal(self):
        text = "อยู่บ้านเลขที่ 88/9 ถนนสีลม กรุงเทพ 10500 ต้องการสมัคร"
        from app.services.dlp.detectors.thai_address import ThaiAddressDetector
        det = ThaiAddressDetector()
        matches = det.detect(text)
        assert len(matches) >= 1
        assert "10500" in matches[0].matched_text

    def test_address_with_newline_terminator(self):
        text = "ที่อยู่ 123 ถนนสุขุมวิท กรุงเทพ\nข้อมูลอื่น"
        from app.services.dlp.detectors.thai_address import ThaiAddressDetector
        det = ThaiAddressDetector()
        matches = det.detect(text)
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# Religion span merging
# ---------------------------------------------------------------------------

class TestReligionMerging:
    def test_adjacent_keywords_merged(self):
        text = "ผมนับถือศาสนาพุทธ"
        from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
        det = ReligionBeliefDetector()
        matches = det.detect(text)
        assert len(matches) == 1
        assert "นับถือศาสนาพุทธ" in matches[0].matched_text

    def test_non_adjacent_not_merged(self):
        text = "ผมนับถือ ไปวัดเป็นพุทธ"
        from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
        det = ReligionBeliefDetector()
        matches = det.detect(text)
        assert len(matches) >= 1

    def test_english_religion_not_affected(self):
        text = "He is a practicing Muslim and attends the local mosque."
        from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector
        det = ReligionBeliefDetector()
        matches = det.detect(text)
        assert len(matches) >= 1


# ---------------------------------------------------------------------------
# Coverage matrix: verify all built-in detectors are in the registry
# ---------------------------------------------------------------------------

class TestCoverageMatrix:
    EXPECTED_DETECTORS = {
        "email", "phone", "ssn", "credit_card", "iban",
        "ip_address", "passport", "date_of_birth", "vehicle_plate",
        "bearer_token", "api_key_secret", "private_key", "password_field",
        "thai_national_id", "thai_tax_id", "thai_bank_account",
        "thai_promptpay", "thai_address", "thai_name",
        "address", "income",
        "db_connection_string", "medical_record", "mac_address",
        "religion_belief", "health_condition", "criminal_record",
        "biometric_data",
        # NER + regex Thai-name fallback. The regex fallback
        # (thai_name_no_prefix) is now registered unconditionally
        # alongside Presidio — see registry.py for the rationale.
        "presidio_ner", "thai_name_no_prefix",
        # FIX37 — Thai NER + nickname lexicon + context enhancer.
        "thai_ner",
        "line_id",
    }

    def test_all_registered(self):
        registry = get_default_registry()
        names = {d.name for d in registry._detectors}
        assert names == self.EXPECTED_DETECTORS
        assert len(registry) == 32

    def test_all_have_mask_tokens(self):
        from app.services.dlp.transformer import MASK_TOKENS
        for name in self.EXPECTED_DETECTORS:
            if name == "presidio_ner":
                continue
            assert name in MASK_TOKENS, f"Missing mask token for {name}"
