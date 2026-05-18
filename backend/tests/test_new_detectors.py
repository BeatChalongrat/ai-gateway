"""Unit + integration tests for the DLP coverage expansion detectors.

Covers the 8 new detectors (Thai national ID, Thai tax ID, Thai bank
account, Thai PromptPay, Thai address, DB connection string, medical
record, MAC address) plus the ``/admin/dlp/coverage`` and
``/admin/dlp/test`` sandbox endpoints.

Test fixture notes
------------------
``VALID_THAI_ID`` and ``VALID_THAI_ID_2`` were computed by applying
the Thai national ID checksum formula to a 12-digit base — see
``thai_national_id._thai_id_valid`` for the algorithm. If the base
digits are changed, the check digits must be recomputed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.dlp import EnforcementAction, MatchAction
from app.main import app
from app.services.dlp.detectors.address import AddressDetector
from app.services.dlp.detectors.db_connection_string import DbConnectionStringDetector
from app.services.dlp.detectors.health_condition import HealthConditionDetector
from app.services.dlp.detectors.income import IncomeDetector
from app.services.dlp.detectors.mac_address import MacAddressDetector
from app.services.dlp.detectors.medical_record import MedicalRecordDetector
from app.services.dlp.detectors.registry import get_default_registry
from app.services.dlp.detectors.thai_address import ThaiAddressDetector
from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector
from app.services.dlp.detectors.thai_national_id import (
    ThaiNationalIdDetector,
    _thai_id_valid,
)
from app.services.dlp.detectors.thai_promptpay import ThaiPromptPayDetector
from app.services.dlp.detectors.thai_tax_id import ThaiTaxIdDetector
from app.services.dlp.transformer import DLPTransformer


client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"Authorization": "Bearer demo-key"}


# Precomputed valid Thai national IDs (12 base digits + 1 check digit).
# See the module docstring for the algorithm.
VALID_THAI_ID = "1234567890121"
VALID_THAI_ID_2 = "2345678901234"
INVALID_THAI_ID = "1234567890129"  # wrong check digit


# ---------------------------------------------------------------------------
# Checksum unit tests
# ---------------------------------------------------------------------------


class TestThaiIdChecksum:
    def test_valid_id_passes(self):
        assert _thai_id_valid(VALID_THAI_ID) is True

    def test_invalid_id_fails(self):
        assert _thai_id_valid(INVALID_THAI_ID) is False

    def test_leading_zero_rejected(self):
        # First digit 0 is not a valid Thai ID.
        assert _thai_id_valid("0234567890123") is False

    def test_wrong_length_rejected(self):
        assert _thai_id_valid("123") is False
        assert _thai_id_valid("") is False


# ---------------------------------------------------------------------------
# Thai national ID detector
# ---------------------------------------------------------------------------


class TestThaiNationalIdDetector:
    def setup_method(self):
        self.det = ThaiNationalIdDetector()

    def test_detects_valid_pattern(self):
        text = f"citizen id {VALID_THAI_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == VALID_THAI_ID
        assert matches[0].entity_type == "thai_national_id"

    def test_rejects_invalid_checksum(self):
        # The text contains a 13-digit number that fails the checksum.
        matches = self.det.detect(f"number {INVALID_THAI_ID}")
        assert matches == []

    def test_accepts_formatted_id(self):
        # Formatted with dashes (same check digit).
        formatted = f"{VALID_THAI_ID[0]}-{VALID_THAI_ID[1:5]}-{VALID_THAI_ID[5:10]}-{VALID_THAI_ID[10:12]}-{VALID_THAI_ID[12]}"
        matches = self.det.detect(f"id {formatted}")
        assert len(matches) == 1
        digits = "".join(ch for ch in matches[0].matched_text if ch.isdigit())
        assert digits == VALID_THAI_ID

    # --- Format coverage --------------------------------------------------
    # Heavy multi-detector scenario used "1-2345-67890-12-3" which the
    # regex matched but the checksum correctly rejected (those digits
    # don't validate). The next three tests lock the detector's contract
    # for the three accepted separator shapes using a checksum-valid ID,
    # so a future regex tweak can't silently lose any of them.

    HEAVY_SCENARIO_VALID_ID = "1101700230708"  # checksum-validated

    def test_format_no_separator(self):
        text = f"national id {self.HEAVY_SCENARIO_VALID_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == self.HEAVY_SCENARIO_VALID_ID

    def test_format_hyphen_separated(self):
        # The "1-XXXX-XXXXX-XX-X" form used in physical Thai NID cards
        # and the heavy multi-detector live scenario.
        formatted = "1-1017-00230-70-8"
        text = f"เลขบัตรประชาชน {formatted}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == formatted

    def test_format_space_separated(self):
        # Less common but supported by `[-\s]?` in the regex.
        formatted = "1 1017 00230 70 8"
        text = f"national id {formatted}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == formatted

    # --- Negative / false-positive prevention -----------------------------
    # Adjacent shapes that must NOT match. A future regex change that
    # widens the pattern (or weakens the checksum gate) would break one
    # of these and surface immediately.

    def test_phone_number_not_matched(self):
        # 10-digit Thai mobile — wrong length for an NID.
        matches = self.det.detect("call me at 0812345678 later")
        assert matches == []

    def test_credit_card_not_matched(self):
        # 16-digit card with the same hyphen pattern — wrong length, and
        # even after stripping hyphens the digit count is off.
        matches = self.det.detect("card 4532-1234-5678-9010 expired")
        assert matches == []

    def test_too_short_run_not_matched(self):
        # 12-digit run — one digit short of an NID.
        matches = self.det.detect("order id 123456789012 was shipped")
        assert matches == []

    def test_too_long_run_not_matched(self):
        # 14-digit run. The negative-lookahead `(?!\d)` guards against
        # adjacent digits inflating the match.
        matches = self.det.detect("ref 12345678901234 logged")
        assert matches == []

    def test_thirteen_digits_invalid_checksum_with_keyword_is_matched(self):
        # Policy change (DLP coverage audit, May 2026): a 13-digit run
        # preceded by an ID-labelling keyword is treated as an ID even
        # when the checksum fails. The keyword (operator-supplied field
        # label like ``เลขบัตรประชาชน``) is the strongest possible
        # signal that this column holds IDs; checksum-invalid
        # placeholders in such columns are still PDPA-sensitive (they
        # identify the row even if not the canonical citizen). Better
        # to over-mask the rare invalid-checksum placeholder than to
        # leak a labelled ID field.
        text = "เลขบัตรประชาชน 1234567890123"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "1234567890123"

    def test_thirteen_digits_invalid_checksum_with_dashes_still_rejected(self):
        # Hyphenated form does NOT hit the keyword-context gate (which
        # requires the bare 13-digit run to follow the keyword); the
        # checksum gate still rejects the dashed shape, so this case
        # remains a non-match.
        text = "เลขบัตรประชาชน 1-2345-67890-12-3"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Thai tax ID detector
# ---------------------------------------------------------------------------


class TestThaiTaxIdDetector:
    def setup_method(self):
        self.det = ThaiTaxIdDetector()

    def test_detects_with_context_english(self):
        text = f"tax id: {VALID_THAI_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "thai_tax_id"

    def test_detects_with_context_thai(self):
        text = f"เลขประจำตัวผู้เสียภาษี {VALID_THAI_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_requires_context_keyword(self):
        # Bare ID without tax keyword → no match.
        text = f"here is a number {VALID_THAI_ID} just for reference"
        matches = self.det.detect(text)
        assert matches == []

    def test_rejects_invalid_checksum_even_with_context(self):
        text = f"tax id: {INVALID_THAI_ID}"
        matches = self.det.detect(text)
        assert matches == []

    # --- Heavy-scenario regression coverage -------------------------------
    # Heavy multi-detector live test used "1234567890123" with
    # "เลขผู้เสียภาษี" context. Regex matched, context fired, but the
    # checksum correctly rejected (same invalid digit run as the
    # thai_national_id case). Lock that contract.

    HEAVY_SCENARIO_VALID_TAX_ID = "5011000234569"  # checksum-validated

    def test_thai_short_keyword_form(self):
        # "ผู้เสียภาษี" (the short keyword) plus a valid ID — heavy
        # scenario uses this short form, not the longer
        # "เลขประจำตัวผู้เสียภาษี".
        text = f"เลขผู้เสียภาษี {self.HEAVY_SCENARIO_VALID_TAX_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == self.HEAVY_SCENARIO_VALID_TAX_ID

    def test_alternate_thai_keyword_lek_phasi(self):
        # "เลขภาษี" is a separate keyword in the gate list — confirm
        # it independently triggers detection.
        text = f"เลขภาษี {self.HEAVY_SCENARIO_VALID_TAX_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_english_tin_keyword(self):
        # "tin" is in the keyword list. Match must be case-insensitive.
        text = f"TIN {self.HEAVY_SCENARIO_VALID_TAX_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_heavy_scenario_invalid_checksum_rejected(self):
        # Exact heavy-scenario shape: short Thai keyword + 13-digit run
        # with invalid checksum. Regex + context gate both fire; the
        # checksum is the only thing keeping this out.
        text = "เลขผู้เสียภาษี 1234567890123"
        matches = self.det.detect(text)
        assert matches == []

    def test_heavy_scenario_hyphenated_invalid_rejected(self):
        # Same digits, hyphenated form. Same outcome — checksum
        # operates on the stripped digits.
        text = "เลขผู้เสียภาษี 1-2345-67890-12-3"
        matches = self.det.detect(text)
        assert matches == []

    def test_no_match_when_only_nid_keyword_present(self):
        # If the surrounding context names the number as a national ID
        # (no tax-related keyword in the window), the tax_id detector
        # must NOT fire — that's the whole point of context gating
        # versus thai_national_id.
        text = f"เลขบัตรประชาชน {self.HEAVY_SCENARIO_VALID_TAX_ID}"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Thai bank account detector
# ---------------------------------------------------------------------------


class TestThaiBankAccountDetector:
    def setup_method(self):
        self.det = ThaiBankAccountDetector()

    def test_detects_with_thai_keyword(self):
        text = "บัญชี 123-4-56789-0"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "thai_bank_account"

    def test_detects_with_english_keyword(self):
        text = "account no 123-4-56789-0"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_requires_context_keyword(self):
        # Bare 10-digit number with no bank keyword → no match.
        text = "order id 123-4-56789-0 was shipped yesterday"
        matches = self.det.detect(text)
        assert matches == []

    # --- Heavy-scenario regression coverage -------------------------------
    # Heavy multi-detector live test had the form
    #   "บัญชีธนาคารกสิกรสำหรับรับเงินคืน 123-4-56789-0"
    # with 28 chars of prose between "บัญชี" and the digits. Layer-by-
    # layer diagnosis (regex / digit-count / context-window) confirmed
    # the detector handles this correctly. Lock that contract.

    def test_heavy_scenario_prose_form(self):
        # Full heavy line: keyword "บัญชี" + 28 chars of prose + digits.
        # Both "บัญชี" and "ธนาคาร" hit the keyword list inside the
        # 60-char window.
        text = "- บัญชีธนาคารกสิกรสำหรับรับเงินคืน 123-4-56789-0"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "123-4-56789-0"

    def test_alternate_keyword_lekbanchi(self):
        # "เลขบัญชี" is a separate gate-list entry — confirm it
        # independently triggers detection.
        text = "เลขบัญชี 123-4-56789-0"
        matches = self.det.detect(text)
        assert len(matches) == 1

    # --- Negative / false-positive prevention -----------------------------
    # Cases where 'บัญชี' or 'ธนาคาร' appear but the surrounding text
    # is NOT an account number — different word sense, marketing copy,
    # cost figures, etc. The regex's 10-12 digit gate is what keeps
    # these out today; tests lock that boundary in.

    def test_banking_copy_no_account_number(self):
        # Marketing line: keyword present, no account-shaped digit run.
        # Only a single digit ("5") nearby.
        text = "เปิดบัญชีธนาคาร 5 นาที"
        matches = self.det.detect(text)
        assert matches == []

    def test_banchi_meaning_inventory_list(self):
        # Different word sense for บัญชี ("list/inventory" not "account").
        # Three-digit run, far below the 10-12 minimum.
        text = "บัญชีรายชื่อ 3-1-2"
        matches = self.det.detect(text)
        assert matches == []

    def test_banchi_meaning_fee_amount(self):
        # Cost figure adjacent to keyword. Three-digit run, regex skips.
        text = "ค่าธรรมเนียมบัญชี 200 บาท"
        matches = self.det.detect(text)
        assert matches == []

    def test_keyword_beyond_window_rejected(self):
        # Keyword exists but >60 chars before the digit run — context
        # window must reject. Use 70 filler chars.
        text = "บัญชี " + "x" * 70 + " 123-4-56789-0"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Thai PromptPay detector
# ---------------------------------------------------------------------------


class TestThaiPromptPayDetector:
    def setup_method(self):
        self.det = ThaiPromptPayDetector()

    def test_detects_mobile_with_thai_keyword(self):
        text = "พร้อมเพย์ 0812345678"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "0812345678"

    def test_detects_with_english_keyword(self):
        text = "PromptPay 0812345678"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_requires_keyword(self):
        text = "call me at 0812345678 later"
        matches = self.det.detect(text)
        assert matches == []

    def test_13_digit_valid_checksum_near_keyword_detected(self):
        # PromptPay IDs can be 13-digit national IDs. A valid Thai ID
        # near the keyword should be detected.
        text = f"promptpay {VALID_THAI_ID}"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == VALID_THAI_ID

    def test_13_digit_invalid_checksum_near_keyword_rejected(self):
        # A 13-digit sequence that fails the mod-11 checksum must NOT
        # be masked as PromptPay, even with the keyword nearby.
        text = f"promptpay {INVALID_THAI_ID}"
        matches = self.det.detect(text)
        assert matches == []

    def test_13_digit_random_digits_near_keyword_rejected(self):
        # Random 13-digit sequence (fails checksum) must be rejected.
        text = "promptpay 9999999999999"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Thai address detector
# ---------------------------------------------------------------------------


class TestThaiAddressDetector:
    def setup_method(self):
        self.det = ThaiAddressDetector()

    def test_detects_with_prefix_keyword(self):
        text = "ที่อยู่ 123/45 ซอย 6 ถนนสุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "thai_address"
        assert "ที่อยู่" in matches[0].matched_text

    def test_ignores_text_without_keyword(self):
        text = "ไปเที่ยวกรุงเทพวันหยุด"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Labeled address detector
# ---------------------------------------------------------------------------


class TestAddressDetector:
    def setup_method(self):
        self.det = AddressDetector()

    def test_detects_english_address(self):
        text = "Address: 99/88 Sukhumvit Road, Bangkok 10110"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "address"
        assert matches[0].matched_text == "99/88 Sukhumvit Road, Bangkok 10110"

    def test_detects_home_address(self):
        text = "Home Address: 123 Rama 9 Road, Huai Khwang, Bangkok 10310"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_detects_thai_labeled_address(self):
        text = "ที่อยู่: 55/12 ถนนสุขุมวิท กรุงเทพ 10110"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert "55/12" in matches[0].matched_text

    def test_unlabeled_road_sentence_not_detected(self):
        assert self.det.detect("I walked on Sukhumvit Road yesterday") == []


# ---------------------------------------------------------------------------
# Health condition detector
# ---------------------------------------------------------------------------


class TestHealthConditionDetector:
    def setup_method(self):
        self.det = HealthConditionDetector()

    def test_detects_medical_label(self):
        text = "Medical: diabetes and high blood pressure"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "health_condition"
        assert matches[0].matched_text == "diabetes and high blood pressure"

    def test_detects_diagnosis_label(self):
        matches = self.det.detect("Diagnosis: hypertension")
        assert len(matches) == 1

    def test_detects_health_condition_label(self):
        matches = self.det.detect("Health condition: asthma")
        assert len(matches) == 1

    def test_detects_thai_disease_label(self):
        matches = self.det.detect("โรค: เบาหวาน ความดันสูง")
        assert len(matches) == 1

    def test_generic_sick_sentence_not_detected(self):
        assert self.det.detect("I feel sick and bad today") == []


# ---------------------------------------------------------------------------
# Income detector
# ---------------------------------------------------------------------------


class TestIncomeDetector:
    def setup_method(self):
        self.det = IncomeDetector()

    def test_detects_salary_thb(self):
        text = "Salary: 85,000 THB/month"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "income"
        assert matches[0].category == "financial"

    def test_detects_monthly_income_baht(self):
        matches = self.det.detect("Monthly income: 120000 baht per month")
        assert len(matches) == 1

    def test_detects_thai_salary(self):
        matches = self.det.detect("เงินเดือน: 45000 บาท")
        assert len(matches) == 1

    def test_detects_thai_income_month(self):
        matches = self.det.detect("รายได้: 60,000 บาท/เดือน")
        assert len(matches) == 1

    def test_product_price_not_detected(self):
        assert self.det.detect("The item costs 500 baht") == []


class TestSyntheticProfileCoverage:
    def test_full_profile_detects_address_health_and_income(self):
        reg = get_default_registry(disabled_detectors=[], ner_enabled=True)
        text = (
            "Please summarize this customer profile and store it:\n"
            "Name: Somchai Jaidee\n"
            "Thai National ID: 1101700203451\n"
            "Passport: AA1234567\n"
            "Email: somchai.jaidee@example.com\n"
            "Phone: +66 81-234-5678\n"
            "Address: 99/88 Sukhumvit Road, Bangkok 10110\n"
            "Credit Card: 4111 1111 1111 1111\n"
            "Bank Account: 123-4-56789-0\n"
            "Password: P@ssw0rd123!\n"
            "API Key: sk-test-1234567890abcdef\n"
            "Medical: diabetes and high blood pressure\n"
            "Salary: 85,000 THB/month\n"
        )
        detectors = {m.detector_name for m in reg.scan_text(text)}
        assert "address" in detectors
        assert "health_condition" in detectors
        assert "income" in detectors

    def test_full_profile_masks_new_sensitive_fields(self):
        reg = get_default_registry(disabled_detectors=[], ner_enabled=True)
        text = (
            "Address: 99/88 Sukhumvit Road, Bangkok 10110\n"
            "Medical: diabetes and high blood pressure\n"
            "Salary: 85,000 THB/month\n"
        )
        matches = reg.scan_text(text)
        actions = [
            MatchAction(
                match=m,
                action=EnforcementAction.MASK,
                reason_code="test_mask",
                reason_message="test",
            )
            for m in matches
            if m.detector_name in {"address", "health_condition", "income"}
        ]

        masked, span_map, _ = DLPTransformer().mask_text(text, actions)

        assert "99/88 Sukhumvit Road" not in masked
        assert "diabetes and high blood pressure" not in masked
        assert "85,000 THB/month" not in masked
        assert "[ADDRESS_" in masked
        assert "[HEALTH_CONDITION_" in masked
        assert "[INCOME_" in masked
        assert set(span_map.values()) == {
            "99/88 Sukhumvit Road, Bangkok 10110",
            "diabetes and high blood pressure",
            "Salary: 85,000 THB/month",
        }

    def test_rejects_too_short(self):
        # Short trailing text doesn't qualify (< 6 char body).
        text = "ที่อยู่ a"
        matches = ThaiAddressDetector().detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# DB connection string detector
# ---------------------------------------------------------------------------


class TestDbConnectionStringDetector:
    def setup_method(self):
        self.det = DbConnectionStringDetector()

    def test_detects_postgres_with_credentials(self):
        text = "connect to postgresql://user:pass@db.example.com:5432/myapp now"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert "postgresql://" in matches[0].matched_text

    def test_detects_mongodb_srv(self):
        text = "URL is mongodb+srv://admin:secret@cluster.mongodb.net/prod"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_ignores_credentialless_url(self):
        # No user:pass → must not fire.
        text = "docs at postgresql://db.example.com:5432/myapp"
        matches = self.det.detect(text)
        assert matches == []

    def test_detects_redis_password(self):
        text = "use redis://:s3cret@redis.internal:6379/0"
        matches = self.det.detect(text)
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# Medical record detector
# ---------------------------------------------------------------------------


class TestMedicalRecordDetector:
    def setup_method(self):
        self.det = MedicalRecordDetector()

    def test_detects_with_hn_keyword(self):
        text = "Patient HN 1234567 admitted"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "1234567"

    def test_detects_with_thai_keyword(self):
        text = "หมายเลขผู้ป่วย 9876543"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_requires_context_keyword(self):
        # Bare 7-digit number with no medical keyword → no match.
        text = "order id 9876543 was shipped yesterday"
        matches = self.det.detect(text)
        assert matches == []

    def test_no_false_positive_on_hn_substring(self):
        """``hn`` must only match as a full word — ``john``, ``johnson``,
        and ``henderson`` used to trigger the context gate before
        medical_record switched from ``kw in window`` to a
        word-boundary regex."""
        for text in (
            "email john@test.com 0123456789",
            "contact johnson for details 1234567",
            "henderson mentioned 9876543",
        ):
            assert self.det.detect(text) == [], f"false positive on: {text!r}"

    def test_no_false_positive_on_mrn_substring(self):
        """``mrn`` must not match inside ``mrna``."""
        assert self.det.detect("mrna sequence 1234567") == []

    def test_keyword_flush_against_digits_rejected(self):
        """``hn123456`` (no separator) must not fire — the boundary
        at the slice edge used to produce a false match before the
        detector switched to full-text regex search."""
        assert self.det.detect("hn123456") == []


# ---------------------------------------------------------------------------
# MAC address detector
# ---------------------------------------------------------------------------


class TestMacAddressDetector:
    def setup_method(self):
        self.det = MacAddressDetector()

    def test_detects_colon_separated(self):
        text = "hardware aa:bb:cc:dd:ee:ff is the interface"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_detects_hyphen_separated(self):
        text = "mac AA-BB-CC-DD-EE-FF on eth0"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_rejects_broadcast(self):
        text = "broadcast ff:ff:ff:ff:ff:ff"
        matches = self.det.detect(text)
        assert matches == []

    def test_rejects_all_zeros(self):
        text = "zero mac 00:00:00:00:00:00"
        matches = self.det.detect(text)
        assert matches == []


# ---------------------------------------------------------------------------
# Default registry integration
# ---------------------------------------------------------------------------


class TestRegistryIncludesNewDetectors:
    def test_registry_size_after_expansion(self):
        reg = get_default_registry(disabled_detectors=[])
        names = {d.name for d in reg.all()}
        for new in (
            "thai_national_id", "thai_tax_id", "thai_bank_account",
            "thai_promptpay", "thai_address", "db_connection_string",
            "medical_record", "mac_address", "address", "income",
        ):
            assert new in names, f"missing detector: {new}"

    def test_new_detectors_have_mask_tokens(self):
        from app.services.dlp.transformer import MASK_TOKENS
        for new in (
            "thai_national_id", "thai_tax_id", "thai_bank_account",
            "thai_promptpay", "thai_address", "db_connection_string",
            "medical_record", "mac_address", "address", "income",
        ):
            assert new in MASK_TOKENS, f"mask token missing for {new}"


# ---------------------------------------------------------------------------
# Coverage endpoint
# ---------------------------------------------------------------------------


class TestCoverageEndpoint:
    def test_coverage_endpoint_returns_all_detectors(self):
        resp = client.get("/admin/dlp/coverage", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        # 30 = previous 28 + labeled address + income.
        assert body["summary"]["total"] == 30

        names = {d["name"] for d in body["detectors"]}
        assert "thai_national_id" in names
        assert "db_connection_string" in names
        assert "mac_address" in names

    def test_coverage_endpoint_has_known_gaps(self):
        resp = client.get("/admin/dlp/coverage", headers=_HEADERS)
        body = resp.json()
        assert isinstance(body["known_gaps"], list)
        assert len(body["known_gaps"]) >= 3
        for gap in body["known_gaps"]:
            assert "description" in gap
            assert "priority" in gap
            assert gap["priority"] in ("high", "medium", "low")

    def test_coverage_groups_detectors_by_category(self):
        resp = client.get("/admin/dlp/coverage", headers=_HEADERS)
        body = resp.json()
        by_cat = body["summary"]["by_category"]
        # Every category must have count == len(detectors list).
        for cat, data in by_cat.items():
            assert data["count"] == len(data["detectors"])
        # The PII category must include thai_address and medical_record.
        assert "thai_address" in by_cat["pii"]["detectors"]
        assert "medical_record" in by_cat["pii"]["detectors"]
        assert "address" in by_cat["pii"]["detectors"]
        assert "income" in by_cat["financial"]["detectors"]


# ---------------------------------------------------------------------------
# Test endpoint (POST /admin/dlp/test)
# ---------------------------------------------------------------------------


class TestDlpTestEndpoint:
    def test_masks_thai_id(self):
        resp = client.post(
            "/admin/dlp/test",
            headers=_HEADERS,
            json={"text": f"id {VALID_THAI_ID}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Thai ID detector must be in the findings list.
        assert any(
            f["detector"] == "thai_national_id" for f in body["findings"]
        )
        # Security: raw ID must not leak into the masked text.
        assert VALID_THAI_ID not in body["masked_text"]
        # And with priority tiebreak in place, the Thai ID mask token
        # wins over the generic phone token on the same span.
        assert "<r0>" in body["masked_text"]

    def test_masks_credit_card(self):
        # Test number that passes Luhn.
        resp = client.post(
            "/admin/dlp/test",
            headers=_HEADERS,
            json={"text": "card 4111 1111 1111 1111 for testing"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert any(
            f["detector"] == "credit_card" for f in body["findings"]
        )
        assert "<r0>" in body["masked_text"]

    def test_masks_db_connection_string(self):
        resp = client.post(
            "/admin/dlp/test",
            headers=_HEADERS,
            json={
                "text": "conn: postgresql://user:pass@db.example.com:5432/myapp"
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        findings = body["findings"]
        assert any(f["detector"] == "db_connection_string" for f in findings)
        # Password must not leak through the masked output.
        assert "user:pass" not in body["masked_text"]

    def test_db_connection_string_stops_at_query_and_fragment(self):
        # Query string and fragment are not credentials — they must stay
        # in the masked output, while the credential URL gets masked.
        from app.services.dlp.detectors.db_connection_string import (
            DbConnectionStringDetector,
        )
        det = DbConnectionStringDetector()
        text = "db postgresql://user:pass@host:5432/db?sslmode=require extra"
        matches = det.detect(text)
        assert len(matches) == 1
        # Must NOT capture the ?query portion.
        assert "?sslmode" not in matches[0].matched_text
        assert matches[0].matched_text.endswith("/db")

        text2 = "mongodb+srv://u:p@cluster.example.net/db#frag after"
        matches2 = det.detect(text2)
        assert len(matches2) == 1
        assert "#frag" not in matches2[0].matched_text
        assert matches2[0].matched_text.endswith("/db")

    def test_clean_text_has_no_findings(self):
        resp = client.post(
            "/admin/dlp/test",
            headers=_HEADERS,
            json={"text": "what is the capital of France?"},
        )
        assert resp.status_code == 200
        assert resp.json()["findings"] == []

    def test_rejects_empty_text(self):
        resp = client.post(
            "/admin/dlp/test",
            headers=_HEADERS,
            json={"text": ""},
        )
        assert resp.status_code == 422
