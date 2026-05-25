"""Tests for P3-03: Deterministic detectors MVP pack.

Each detector class gets:
- Positive cases (matches expected input)
- Negative cases (rejects non-matching input)
- Span integrity check (text[start:end] == matched_text)
- Correct metadata (detector_name, entity_type, category, severity)

Final section tests the default registry integration.
"""

import pytest

from app.domain.dlp import SensitivityLevel
from app.services.dlp.detectors.api_key_secret import APIKeySecretDetector
from app.services.dlp.detectors.credit_card import CreditCardDetector
from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector
from app.services.dlp.detectors.email import EmailDetector
from app.services.dlp.detectors.iban import IBANDetector
from app.services.dlp.detectors.ip_address import IPAddressDetector
from app.services.dlp.detectors.passport import PassportDetector
from app.services.dlp.detectors.phone import PhoneDetector
from app.services.dlp.detectors.ssn import SSNDetector
from app.services.dlp.detectors.vehicle_plate import VehiclePlateDetector
from app.services.dlp.detectors.bearer_token import BearerTokenDetector
from app.services.dlp.detectors.private_key import PrivateKeyDetector
from app.services.dlp.detectors.password_field import PasswordFieldDetector
from app.services.dlp.detectors.registry import get_default_registry


def _assert_span(text: str, match):
    """Verify span integrity: text[start:end] == matched_text."""
    assert text[match.start:match.end] == match.matched_text, (
        f"Span mismatch: text[{match.start}:{match.end}]="
        f"{text[match.start:match.end]!r} != {match.matched_text!r}"
    )


# ---------------------------------------------------------------------------
# EmailDetector
# ---------------------------------------------------------------------------


class TestEmailDetector:
    det = EmailDetector()

    def test_simple_email(self):
        text = "contact user@example.com for info"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "user@example.com"
        assert matches[0].entity_type == "email_address"
        assert matches[0].category == "pii"
        assert matches[0].severity == SensitivityLevel.MEDIUM
        _assert_span(text, matches[0])

    def test_plus_tag(self):
        text = "send to user+tag@sub.domain.co.uk"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "user+tag@sub.domain.co.uk"

    def test_multiple_emails(self):
        text = "a@b.com and c@d.org"
        matches = self.det.detect(text)
        assert len(matches) == 2

    def test_no_match_bare_at(self):
        assert self.det.detect("user @ example") == []

    def test_no_match_no_tld(self):
        assert self.det.detect("user@localhost") == []

    def test_clean_text(self):
        assert self.det.detect("nothing here") == []


# ---------------------------------------------------------------------------
# PhoneDetector
# ---------------------------------------------------------------------------


class TestPhoneDetector:
    det = PhoneDetector()

    def test_us_format_with_dashes(self):
        text = "call 555-123-4567 now"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "phone_number"
        _assert_span(text, matches[0])

    def test_us_format_with_dots(self):
        text = "phone: 555.123.4567"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_international_format(self):
        text = "call +1-555-123-4567"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_bare_10_digits(self):
        # Bare digit runs now require a phone-context keyword in a ±25 char
        # window (tightened to eliminate epoch / order-ID false positives).
        text = "phone number is 5551234567 ok"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "5551234567"

    def test_too_few_digits_rejected(self):
        assert self.det.detect("code 12345") == []

    def test_clean_text(self):
        assert self.det.detect("no phone here") == []

    def test_short_number_rejected(self):
        """7-digit numbers should not match."""
        assert self.det.detect("call 5551234") == []


# ---------------------------------------------------------------------------
# SSNDetector
# ---------------------------------------------------------------------------


class TestSSNDetector:
    det = SSNDetector()

    def test_valid_ssn(self):
        text = "SSN: 123-45-6789"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "123-45-6789"
        assert matches[0].entity_type == "us_ssn"
        assert matches[0].severity == SensitivityLevel.HIGH
        _assert_span(text, matches[0])

    def test_rejects_area_000(self):
        assert self.det.detect("000-12-3456") == []

    def test_rejects_area_666(self):
        assert self.det.detect("666-12-3456") == []

    def test_rejects_area_9xx(self):
        assert self.det.detect("900-12-3456") == []
        assert self.det.detect("999-12-3456") == []

    def test_rejects_group_00(self):
        assert self.det.detect("123-00-4567") == []

    def test_rejects_serial_0000(self):
        assert self.det.detect("123-45-0000") == []

    def test_no_dashes_not_matched(self):
        """Deliberate: bare 9 digits without dashes are not SSNs."""
        assert self.det.detect("123456789") == []

    def test_clean_text(self):
        assert self.det.detect("nothing sensitive") == []

    def test_multiple_ssns(self):
        text = "a: 123-45-6789, b: 234-56-7890"
        matches = self.det.detect(text)
        assert len(matches) == 2


# ---------------------------------------------------------------------------
# CreditCardDetector
# ---------------------------------------------------------------------------


class TestCreditCardDetector:
    det = CreditCardDetector()

    def test_visa_16_digit_no_delimiter(self):
        text = "My card is 4111111111111111 thanks"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "4111111111111111"
        assert matches[0].entity_type == "credit_card_number"
        assert matches[0].category == "financial"
        assert matches[0].severity == SensitivityLevel.HIGH
        _assert_span(text, matches[0])

    def test_visa_16_digit_with_hyphens(self):
        text = "card: 4111-1111-1111-1111"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "4111-1111-1111-1111"

    def test_visa_16_digit_with_spaces(self):
        text = "4111 1111 1111 1111"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_amex_15_digit(self):
        # A valid Luhn 15-digit Amex test number
        text = "amex 378282246310005"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "378282246310005"

    def test_rejects_luhn_invalid(self):
        # 16 digits but fails Luhn
        assert self.det.detect("1234567890123456") == []

    def test_rejects_short_number(self):
        # Phone-length digit run: 10 digits, not a card
        assert self.det.detect("5551234567") == []

    def test_rejects_too_long(self):
        # 20+ digit runs are not cards
        assert self.det.detect("1234567890123456789012") == []

    def test_clean_text(self):
        assert self.det.detect("no cards here") == []


# ---------------------------------------------------------------------------
# IPAddressDetector
# ---------------------------------------------------------------------------


class TestIPAddressDetector:
    det = IPAddressDetector()

    def test_ipv4_dotted_quad(self):
        text = "server at 192.168.1.100 responded"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "192.168.1.100"
        assert matches[0].entity_type == "ipv4_address"
        assert matches[0].category == "network"
        _assert_span(text, matches[0])

    def test_ipv4_public_address(self):
        matches = self.det.detect("ping 8.8.8.8 please")
        assert len(matches) == 1
        assert matches[0].matched_text == "8.8.8.8"

    def test_ipv4_rejects_out_of_range(self):
        assert self.det.detect("999.999.999.999") == []

    def test_ipv4_does_not_match_version_strings(self):
        # SemVer-ish three-segment strings should not match as ipv4.
        assert self.det.detect("version 1.2.3") == []

    def test_ipv6_full_form(self):
        text = "addr: 2001:0db8:85a3:0000:0000:8a2e:0370:7334 done"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "ipv6_address"

    def test_ipv6_compressed(self):
        matches = self.det.detect("addr 2001:db8::1 end")
        assert len(matches) == 1
        assert "2001:db8::1" in matches[0].matched_text

    def test_clean_text(self):
        assert self.det.detect("nothing networky here") == []


# ---------------------------------------------------------------------------
# PassportDetector
# ---------------------------------------------------------------------------


class TestPassportDetector:
    det = PassportDetector()

    def test_passport_keyword_letter_plus_digits(self):
        text = "my passport is A12345678 thanks"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "A12345678"
        assert matches[0].category == "identity"

    def test_passport_thai_keyword(self):
        text = "หนังสือเดินทาง AA1234567"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "AA1234567"

    def test_us_nine_digit_passport_with_keyword(self):
        text = "passport number 123456789 is mine"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "123456789"

    def test_requires_context_keyword(self):
        # Bare A12345678 with no "passport" nearby is NOT flagged.
        assert self.det.detect("just some token A12345678 here") == []

    def test_clean_text(self):
        assert self.det.detect("no passport info") == []

    def test_stacked_joiners(self):
        # Regression: the joiner group previously allowed only one
        # token, so "passport number: AA1234567" would consume
        # "number" but then fail on the ":". Ensure combinations work.
        for text in [
            "passport number: AA1234567",
            "Passport No.: 123456789",
            "passport no # AA1234567",
        ]:
            matches = self.det.detect(text)
            assert len(matches) == 1, f"no match for: {text!r}"


# ---------------------------------------------------------------------------
# IBANDetector
# ---------------------------------------------------------------------------


class TestIBANDetector:
    det = IBANDetector()

    def test_valid_gb_iban(self):
        # Canonical GB example from Wikipedia
        text = "IBAN: GB29NWBK60161331926819 please"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "GB29NWBK60161331926819"
        assert matches[0].category == "financial"

    def test_valid_de_iban(self):
        matches = self.det.detect("DE89370400440532013000")
        assert len(matches) == 1

    def test_rejects_bad_checksum(self):
        # Valid shape (GB + 22 chars) but wrong check digits → rejected
        assert self.det.detect("GB00NWBK60161331926819") == []

    def test_rejects_random_alphanum(self):
        assert self.det.detect("XX99ABCDEFGHIJKLMNOPQR") == []

    def test_clean_text(self):
        assert self.det.detect("nothing bank-like here") == []


# ---------------------------------------------------------------------------
# DateOfBirthDetector
# ---------------------------------------------------------------------------


class TestDateOfBirthDetector:
    det = DateOfBirthDetector()

    def test_born_keyword_dmy_slashes(self):
        text = "I was born on 15/05/1990 in Bangkok"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "15/05/1990"
        assert matches[0].category == "pii"

    def test_dob_keyword_iso(self):
        matches = self.det.detect("DOB: 1990-05-15 confirmed")
        assert len(matches) == 1
        assert matches[0].matched_text == "1990-05-15"

    def test_birthday_keyword_month_name(self):
        matches = self.det.detect("birthday May 15, 1990 yes")
        assert len(matches) == 1
        assert "May 15, 1990" == matches[0].matched_text

    def test_requires_context_keyword(self):
        # Plain "2024-05-01 meeting" is NOT a DOB.
        assert self.det.detect("the meeting on 2024-05-01") == []

    def test_thai_keyword(self):
        matches = self.det.detect("วันเกิด 15/05/1990 ของฉัน")
        assert len(matches) == 1
        assert matches[0].matched_text == "15/05/1990"

    def test_clean_text(self):
        assert self.det.detect("no dates today") == []


# ---------------------------------------------------------------------------
# VehiclePlateDetector
# ---------------------------------------------------------------------------


class TestVehiclePlateDetector:
    det = VehiclePlateDetector()

    def test_plate_with_license_keyword(self):
        text = "license plate AA-1234 seen"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].matched_text == "AA-1234"
        assert matches[0].category == "identity"

    def test_plate_with_thai_keyword(self):
        text = "ทะเบียนรถ AB 1234 ครับ"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_plate_with_province_suffix(self):
        matches = self.det.detect("AA-1234 Bangkok")
        assert len(matches) == 1

    def test_plate_without_context_not_matched(self):
        # No keyword, no province → skip.
        assert self.det.detect("I like AA 1234 stuff") == []

    def test_clean_text(self):
        assert self.det.detect("no vehicles mentioned") == []


# ---------------------------------------------------------------------------
# APIKeySecretDetector
# ---------------------------------------------------------------------------


class TestAPIKeySecretDetector:
    det = APIKeySecretDetector()

    def test_anthropic_key(self):
        text = "key is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABC-xyz ok"
        matches = self.det.detect(text)
        flagged = [m for m in matches if m.entity_type == "anthropic_api_key"]
        assert len(flagged) == 1
        assert flagged[0].category == "credential"
        assert flagged[0].severity == SensitivityLevel.CRITICAL

    def test_jwt_token(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc-def_ghi"
        matches = self.det.detect(f"token: {jwt} end")
        flagged = [m for m in matches if m.entity_type == "jwt_token"]
        assert len(flagged) == 1
        assert flagged[0].matched_text == jwt

    def test_google_api_key(self):
        text = "key AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz1234567 end"
        matches = self.det.detect(text)
        flagged = [m for m in matches if m.entity_type == "google_api_key"]
        assert len(flagged) == 1

    def test_generic_password_assignment(self):
        text = 'config: password="SuperSecret12345" next'
        matches = self.det.detect(text)
        flagged = [m for m in matches if m.entity_type == "credential_assignment"]
        assert len(flagged) == 1
        assert flagged[0].matched_text == "SuperSecret12345"

    def test_generic_api_key_assignment(self):
        text = "api_key=abcdef1234567890"
        matches = self.det.detect(text)
        flagged = [m for m in matches if m.entity_type == "credential_assignment"]
        assert len(flagged) == 1

    def test_clean_text(self):
        assert self.det.detect("no credentials") == []


# ---------------------------------------------------------------------------
# BearerTokenDetector
# ---------------------------------------------------------------------------


class TestBearerTokenDetector:
    det = BearerTokenDetector()

    def test_bearer_header(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "bearer_token"
        assert matches[0].severity == SensitivityLevel.CRITICAL
        _assert_span(text, matches[0])

    def test_aws_access_key(self):
        text = "key is AKIAIOSFODNN7EXAMPLE"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "aws_access_key"
        assert matches[0].matched_text == "AKIAIOSFODNN7EXAMPLE"

    def test_openai_key(self):
        text = "OPENAI_API_KEY=sk-abcdefghij1234567890abcd"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "openai_api_key"

    def test_openai_project_key(self):
        text = "key: sk-proj-abcdefghij1234567890abcd"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "openai_api_key"

    def test_github_pat(self):
        text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "github_token"

    def test_slack_bot_token(self):
        text = "SLACK_TOKEN=xoxb-123456-abcdef-ghijklmn"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "slack_token"

    def test_wrong_prefix_ignored(self):
        """BKIA... is not an AWS key."""
        assert self.det.detect("BKIAIOSFODNN7EXAMPLE") == []

    def test_short_token_ignored(self):
        """sk- followed by too few chars should not match."""
        assert self.det.detect("sk-short") == []

    def test_clean_text(self):
        assert self.det.detect("no tokens here") == []


# ---------------------------------------------------------------------------
# PrivateKeyDetector
# ---------------------------------------------------------------------------


class TestPrivateKeyDetector:
    det = PrivateKeyDetector()

    def test_generic_private_key(self):
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvQ..."
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "private_key_header"
        assert matches[0].severity == SensitivityLevel.CRITICAL
        _assert_span(text, matches[0])

    def test_rsa_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_ec_private_key(self):
        matches = self.det.detect("-----BEGIN EC PRIVATE KEY-----")
        assert len(matches) == 1

    def test_openssh_private_key(self):
        matches = self.det.detect("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert len(matches) == 1

    def test_public_key_not_matched(self):
        """Public keys should NOT trigger this detector."""
        assert self.det.detect("-----BEGIN PUBLIC KEY-----") == []

    def test_certificate_not_matched(self):
        assert self.det.detect("-----BEGIN CERTIFICATE-----") == []

    def test_clean_text(self):
        assert self.det.detect("no keys here") == []


# ---------------------------------------------------------------------------
# PasswordFieldDetector
# ---------------------------------------------------------------------------


class TestPasswordFieldDetector:
    det = PasswordFieldDetector()

    def test_equals_unquoted(self):
        text = "DB_PASSWORD=hunter2"
        matches = self.det.detect(text)
        assert len(matches) == 1
        assert matches[0].entity_type == "password_in_config"
        assert matches[0].severity == SensitivityLevel.HIGH
        _assert_span(text, matches[0])

    def test_colon_double_quoted(self):
        text = 'password: "my secret value"'
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_colon_single_quoted(self):
        text = "password: 'changeme'"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_case_insensitive(self):
        text = "PASSWORD=secret123"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_api_secret_variant(self):
        text = "api_secret=abc123xyz"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_secret_key_variant(self):
        text = "secret_key: mykey123"
        matches = self.det.detect(text)
        assert len(matches) == 1

    def test_no_label_no_match(self):
        """Bare password value without label should not match."""
        assert self.det.detect("hunter2") == []

    def test_clean_text(self):
        assert self.det.detect("set a strong password policy") == []

    def test_word_password_without_assignment(self):
        """The word 'password' without = or : should not match."""
        assert self.det.detect("enter your password in the form") == []


# ---------------------------------------------------------------------------
# Default registry integration
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    def test_has_all_detectors(self):
        reg = get_default_registry()
        assert len(reg) == 31
        names = {d.name for d in reg.all()}
        assert names == {
            "email", "phone", "ssn", "credit_card", "iban",
            "ip_address", "passport", "date_of_birth", "vehicle_plate",
            "bearer_token", "api_key_secret",
            "private_key", "password_field",
            # DLP coverage expansion.
            "thai_national_id", "thai_tax_id", "thai_bank_account",
            "thai_promptpay", "thai_address", "thai_name",
            "address", "income",
            "db_connection_string",
            "medical_record", "mac_address",
            # PDPA Section 26 sensitive data.
            "religion_belief", "health_condition", "criminal_record", "biometric_data",
            # NER + regex Thai-name fallback (regex registered
            # unconditionally since the Thai-name fix; see registry.py).
            "presidio_ner",
            "thai_name_no_prefix",
            # Thai-market contact identifier.
            "line_id",
        }

    def test_categories(self):
        reg = get_default_registry()
        pii = reg.by_category("pii")
        assert {d.name for d in pii} == {
            "email", "phone", "ssn", "date_of_birth",
            "thai_address", "thai_name", "address", "medical_record",
            "religion_belief", "health_condition", "criminal_record", "biometric_data",
            # NER + regex fallback (registered unconditionally since
            # the Thai-name fix; see registry.py).
            "presidio_ner", "thai_name_no_prefix",
            "line_id",
        }
        financial = reg.by_category("financial")
        assert {d.name for d in financial} == {
            "credit_card", "iban", "thai_bank_account", "thai_promptpay",
            "income",
        }
        network = reg.by_category("network")
        assert {d.name for d in network} == {"ip_address", "mac_address"}
        identity = reg.by_category("identity")
        assert {d.name for d in identity} == {
            "passport", "vehicle_plate",
            "thai_national_id", "thai_tax_id",
        }
        credential = reg.by_category("credential")
        assert {d.name for d in credential} == {
            "api_key_secret", "db_connection_string",
        }
        secrets = reg.by_category("secret")
        assert {d.name for d in secrets} == {"bearer_token", "private_key", "password_field"}

    def test_multi_detector_scan(self):
        reg = get_default_registry()
        text = "email: user@example.com, ssn: 123-45-6789, key: AKIAIOSFODNN7EXAMPLE"
        matches = reg.scan_text(text)
        assert len(matches) == 3
        types = {m.detector_name for m in matches}
        assert types == {"email", "ssn", "bearer_token"}
        # Verify sorted by offset
        offsets = [m.start for m in matches]
        assert offsets == sorted(offsets)

    def test_all_spans_valid(self):
        reg = get_default_registry()
        text = (
            "contact user@example.com, SSN 123-45-6789, "
            "key AKIAIOSFODNN7EXAMPLE, -----BEGIN PRIVATE KEY-----, "
            "DB_PASSWORD=secret123"
        )
        for m in reg.scan_text(text):
            _assert_span(text, m)

    def test_clean_text_no_matches(self):
        reg = get_default_registry()
        assert reg.scan_text("This is a perfectly clean message.") == []
