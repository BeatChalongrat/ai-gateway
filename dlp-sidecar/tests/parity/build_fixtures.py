"""Build Batch-1 parity fixtures by running the Python registry as
the source of truth.

For each detector we hand-author a list of test inputs (should-match
+ should-not-match + boundary cases) and let the live Python
detector compute ``expected_count`` + ``expected_spans``. The
fixture JSON is then the contract Rust must reproduce match-for-
match before its detector ships.

Run::

    docker compose exec backend python /work/dlp-sidecar/tests/parity/build_fixtures.py

(The script mounts the repo at /work in the backend container; the
output lands in ``dlp-sidecar/tests/parity/fixtures/*.json``.)
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass

# Resolve sys.path to the backend so the imports below work whether
# the script runs from inside the backend container (/app) or from
# the repo root. We try a few candidate locations and pick whichever
# exists — keeps the script runnable from CI, host shells, and
# ``docker exec`` invocations with the file copied to /tmp.
_CANDIDATES = [
    pathlib.Path("/app"),
    pathlib.Path(__file__).resolve().parent.parent.parent.parent / "backend"
    if len(pathlib.Path(__file__).resolve().parents) >= 4
    else None,
]
for cand in _CANDIDATES:
    if cand is not None and cand.exists():
        sys.path.insert(0, str(cand))
        break

from app.services.dlp.detectors.email import EmailDetector  # noqa: E402
from app.services.dlp.detectors.ssn import SSNDetector  # noqa: E402
from app.services.dlp.detectors.iban import IBANDetector  # noqa: E402
from app.services.dlp.detectors.ip_address import IPAddressDetector  # noqa: E402
from app.services.dlp.detectors.mac_address import MacAddressDetector  # noqa: E402
from app.services.dlp.detectors.bearer_token import BearerTokenDetector  # noqa: E402
from app.services.dlp.detectors.private_key import PrivateKeyDetector  # noqa: E402
from app.services.dlp.detectors.password_field import PasswordFieldDetector  # noqa: E402
from app.services.dlp.detectors.line_id import LineIdDetector  # noqa: E402

# Batch 2
from app.services.dlp.detectors.phone import PhoneDetector  # noqa: E402
from app.services.dlp.detectors.api_key_secret import APIKeySecretDetector  # noqa: E402
from app.services.dlp.detectors.db_connection_string import DbConnectionStringDetector  # noqa: E402
from app.services.dlp.detectors.medical_record import MedicalRecordDetector  # noqa: E402
from app.services.dlp.detectors.date_of_birth import DateOfBirthDetector  # noqa: E402
from app.services.dlp.detectors.vehicle_plate import VehiclePlateDetector  # noqa: E402
from app.services.dlp.detectors.address import AddressDetector  # noqa: E402
from app.services.dlp.detectors.thai_address import ThaiAddressDetector  # noqa: E402
from app.services.dlp.detectors.income import IncomeDetector  # noqa: E402
from app.services.dlp.detectors.religion_belief import ReligionBeliefDetector  # noqa: E402
from app.services.dlp.detectors.health_condition import HealthConditionDetector  # noqa: E402
from app.services.dlp.detectors.criminal_record import CriminalRecordDetector  # noqa: E402
from app.services.dlp.detectors.biometric_data import BiometricDataDetector  # noqa: E402

# Batch 3
from app.services.dlp.detectors.thai_national_id import ThaiNationalIdDetector  # noqa: E402
from app.services.dlp.detectors.thai_tax_id import ThaiTaxIdDetector  # noqa: E402
from app.services.dlp.detectors.thai_bank_account import ThaiBankAccountDetector  # noqa: E402
from app.services.dlp.detectors.thai_promptpay import ThaiPromptPayDetector  # noqa: E402

# Batch 4
from app.services.dlp.detectors.thai_name import ThaiNameDetector  # noqa: E402
from app.services.dlp.detectors.thai_name_no_prefix import ThaiNameNoPrefixDetector  # noqa: E402
from app.services.dlp.detectors.credit_card import CreditCardDetector  # noqa: E402
from app.services.dlp.detectors.passport import PassportDetector  # noqa: E402


# Allow override so the script can run inside a backend container
# (where the fixtures don't live on disk) and dump to /tmp instead.
import os as _os
FIXTURES_DIR = pathlib.Path(
    _os.environ.get(
        "FIXTURES_OUT_DIR",
        str(pathlib.Path(__file__).resolve().parent / "fixtures"),
    )
)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Case:
    id: str
    input: str
    should_match: bool


@dataclass
class DetectorSpec:
    name: str
    entity_type: str
    category: str
    severity: int
    detector: object
    cases: list[Case]


SPECS = [
    DetectorSpec(
        name="email",
        entity_type="email_address",
        category="pii",
        severity=2,  # MEDIUM
        detector=EmailDetector(),
        cases=[
            Case("simple_email", "contact me at user@example.com please", True),
            Case("plus_address", "ping alice+work@foo.co.uk now", True),
            Case("two_emails", "from a@b.com to c@d.com hi", True),
            Case("no_at_sign", "plain text no email here", False),
            Case("no_tld", "user@host with no dot", False),
            Case("just_at", "@ alone is not an email", False),
        ],
    ),
    DetectorSpec(
        name="ssn",
        entity_type="us_ssn",
        category="pii",
        severity=3,
        detector=SSNDetector(),
        cases=[
            Case("valid_dashed", "SSN: 123-45-6789 thanks", True),
            Case("valid_low_serial", "002-23-0001 is valid", True),
            Case("invalid_area_000", "000-12-3456 invalid area", False),
            Case("invalid_area_666", "666-12-3456 invalid area", False),
            Case("invalid_area_9xx", "900-12-3456 invalid area", False),
            Case("invalid_group_00", "123-00-6789 invalid group", False),
            Case("invalid_serial_0000", "123-45-0000 invalid serial", False),
            Case("no_dashes_no_match", "123456789 should not match", False),
        ],
    ),
    DetectorSpec(
        name="iban",
        entity_type="iban",
        category="financial",
        severity=3,
        detector=IBANDetector(),
        cases=[
            # Well-known valid IBAN test vectors (ISO 13616).
            Case("valid_de", "Wire to DE89370400440532013000 today", True),
            Case("valid_gb_spaced", "GB82 WEST 1234 5698 7654 32 confirmed", True),
            Case("invalid_checksum", "DE00370400440532013001 wrong digits", False),
            Case("no_iban_present", "no bank details in this text", False),
            Case("too_short", "DE89 1234 not an iban", False),
            Case("lowercase_country_not_match", "de89370400440532013000 lowercase", False),
        ],
    ),
    DetectorSpec(
        name="ip_address",
        entity_type="ipv4_address",
        category="network",
        severity=2,
        detector=IPAddressDetector(),
        cases=[
            Case("ipv4_simple", "server at 192.168.1.1 listening", True),
            Case("ipv4_high_octets", "edge 255.255.255.255 broadcast", True),
            Case("ipv6_full", "addr 2001:0db8:85a3:0000:0000:8a2e:0370:7334 reached", True),
            Case("ipv6_compressed", "addr ::1 here", True),
            # Python's IPv4 detector greedily matches each in-range
            # octet group; ``999.999.999.999`` falls outside the
            # 0-255 alternation so no match emits.
            Case("ipv4_octet_overflow", "999.999.999.999 invalid", False),
            # Bare 4-digit version strings should not match — no dot
            # sequence of four octets present.
            Case("no_dots_no_match", "version 12345 deployed", False),
            Case("no_ip_present", "no addresses here", False),
        ],
    ),
    DetectorSpec(
        name="mac_address",
        entity_type="mac_address",
        category="network",
        severity=1,
        detector=MacAddressDetector(),
        cases=[
            Case("mac_colon", "device aa:bb:cc:dd:ee:ff present", True),
            Case("mac_dash", "device AA-BB-CC-DD-EE-FF present", True),
            Case("mac_mixed_case", "MAC 0A:1b:2C:3d:4E:5f detected", True),
            Case("broadcast_skipped", "FF:FF:FF:FF:FF:FF is broadcast", False),
            Case("all_zeros_skipped", "00:00:00:00:00:00 placeholder", False),
            Case("not_a_mac", "no mac in this text", False),
        ],
    ),
    DetectorSpec(
        name="bearer_token",
        entity_type="bearer_token",
        category="secret",
        severity=4,
        detector=BearerTokenDetector(),
        cases=[
            Case(
                "bearer_token",
                "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature here",
                True,
            ),
            Case("aws_access_key", "use AKIAIOSFODNN7EXAMPLE for upload", True),
            Case(
                "openai_key",
                "key sk-proj-1a2b3c4d5e6f7g8h9i0jKLMNOP12345 done",
                True,
            ),
            Case(
                "github_pat",
                "tok ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ stored",
                True,
            ),
            Case("anthropic_excluded", "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA done", False),
            Case("plain_word_bearer", "she said bear her load no token", False),
            Case("no_secret_present", "hello world nothing sensitive", False),
        ],
    ),
    DetectorSpec(
        name="private_key",
        entity_type="private_key_header",
        category="secret",
        severity=4,
        detector=PrivateKeyDetector(),
        cases=[
            Case(
                "rsa_header",
                "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
                True,
            ),
            Case(
                "openssh_header",
                "-----BEGIN OPENSSH PRIVATE KEY-----\nbody",
                True,
            ),
            Case(
                "plain_private_key",
                "before -----BEGIN PRIVATE KEY----- after",
                True,
            ),
            Case("public_key_header_no_match", "-----BEGIN PUBLIC KEY----- nope", False),
            Case("no_pem_block", "no key material here", False),
            Case("end_only", "-----END PRIVATE KEY----- no begin", False),
        ],
    ),
    DetectorSpec(
        name="password_field",
        entity_type="password_in_config",
        category="secret",
        severity=3,
        detector=PasswordFieldDetector(),
        cases=[
            Case("password_equals", "password=secret123 yay", True),
            Case("password_colon_quoted", 'password: "hunter2" line', True),
            Case("db_password_uppercase", "DB_PASSWORD='changeme' env", True),
            Case("secret_key_yaml", "secret_key: abcdef end", True),
            Case("password_bare_no_assign", "the password is hidden", False),
            Case("login_word_no_label", "login=admin no password", False),
        ],
    ),
    DetectorSpec(
        name="line_id",
        entity_type="line_id",
        category="pii",
        severity=2,
        detector=LineIdDetector(),
        cases=[
            Case("line_id_basic", "LINE @somchai_dev for help", True),
            Case("line_id_with_id", "LINE ID: @sales_bangkok please", True),
            Case("line_id_lowercase", "line: @helpdesk now", True),
            Case("twitter_mention_no_match", "follow @username on twitter", False),
            Case("at_price_no_match", "ราคา @100 บาท", False),
            Case("everyone_no_match", "@everyone meeting at 3pm", False),
        ],
    ),
    # ---------------------------------------------------------------
    # Batch 2 — context-window detectors
    # ---------------------------------------------------------------
    DetectorSpec(
        name="phone",
        entity_type="phone_number",
        category="pii",
        severity=3,
        detector=PhoneDetector(),
        cases=[
            Case("formatted_thai_mobile", "โทร 081-234-5678 ครับ", True),
            Case("formatted_intl", "Phone: +66 81 234 5678 today", True),
            Case("thai_landline", "office 02-555-1234 line", True),
            Case("bare_with_thai_context", "เบอร์ 0812345678 ติดต่อ", True),
            Case("bare_without_context", "0812345678 alone is just digits", False),
            Case("plain_prose_no_phone", "no contact info here", False),
            # FIX27 Part 3 — a file UUID (attachment envelope fileId) is not a phone.
            Case("fix27_uuid_is_not_a_phone", "fileId 7a86d915-4821-4469-9475-efbb535a5916 uploaded", False),
            Case("fix27_uuid_next_to_real_phone", "id 7a86d915-4821-4469-9475-efbb535a5916 โทร 081-234-5678", True),
        ],
    ),
    DetectorSpec(
        name="api_key_secret",
        entity_type="api_key_secret",
        category="credential",
        severity=4,
        detector=APIKeySecretDetector(),
        cases=[
            Case(
                "anthropic_long",
                "key sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa committed",
                True,
            ),
            Case(
                "google_api_key",
                "GOOGLE_API_KEY=AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q here",
                True,
            ),
            Case(
                "jwt_token",
                "token eyJhbGciOiJI.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF here",
                True,
            ),
            Case(
                "assignment_capture",
                'config api_key="abcd1234EFGHabcd1234EFGH" loaded',
                True,
            ),
            Case("no_secret_present", "normal text no credentials", False),
            Case("anthropic_too_short", "sk-ant-api03-short bad", False),
            Case("low_entropy_no_label", "abcdefghij just letters", False),
        ],
    ),
    DetectorSpec(
        name="db_connection_string",
        entity_type="db_connection_string",
        category="credential",
        severity=4,
        detector=DbConnectionStringDetector(),
        cases=[
            Case("postgres_url", "DSN postgresql://user:pass@host:5432/db now", True),
            Case("mysql_url", "url mysql://admin:secret@db.example.com/app done", True),
            Case("mongodb_srv", "uri mongodb+srv://u:p@cluster.mongodb.net/x ready", True),
            Case("redis_password_only", "redis://:hunter2@cache:6379 used", True),
            Case("no_credentials", "postgresql://host:5432/db readonly", False),
            Case("plain_prose", "we use mysql for our analytics db", False),
        ],
    ),
    DetectorSpec(
        name="medical_record",
        entity_type="medical_record",
        category="pii",
        severity=3,
        detector=MedicalRecordDetector(),
        cases=[
            Case("hn_thai_keyword", "ผู้ป่วย HN 123456 admitted today", True),
            Case("mrn_english", "MRN 7890123 transferred to ward 5", True),
            Case("patient_id", "Patient ID 4567890 ready for surgery", True),
            Case("bare_six_digits_no_context", "code 123456 unrelated", False),
            Case("hn_substring_no_match", "Johnson 123456 stayed home", False),
            Case("no_number_present", "HN keyword alone no number", False),
        ],
    ),
    DetectorSpec(
        name="date_of_birth",
        entity_type="date_of_birth",
        category="pii",
        severity=3,
        detector=DateOfBirthDetector(),
        cases=[
            Case("english_dob_iso", "DOB: 1990-05-15 confirmed", True),
            Case("english_born_phrase", "born May 15, 1990 in Bangkok", True),
            Case("thai_full_month", "วันเกิด 15 มีนาคม 2532 ตรวจ", True),
            Case("thai_short_month_dotted", "เกิด 15 มี.ค. 32 บัตร", True),
            Case("date_alone_no_keyword", "meeting on 2024-05-01 next week", False),
            Case("compound_thai_no_match", "เหตุเกิดวันที่ไม่ระบุ ส่งต่อ", False),
        ],
    ),
    DetectorSpec(
        name="vehicle_plate",
        entity_type="vehicle_plate",
        category="identity",
        severity=2,
        detector=VehiclePlateDetector(),
        cases=[
            Case("thai_keyword_plate", "ทะเบียน กข 1234 ของผม", True),
            Case("english_keyword_plate", "license plate AB 1234 lost", True),
            Case("plate_then_province", "AB 1234 Bangkok seen", True),
            Case("plate_thai_province", "กข 1234 ภูเก็ต พบ", True),
            Case("no_keyword_no_province", "AB 1234 unrelated", False),
            Case("plain_prose", "we drove around for hours", False),
        ],
    ),
    DetectorSpec(
        name="address",
        entity_type="address",
        category="pii",
        severity=2,
        detector=AddressDetector(),
        cases=[
            Case(
                "home_address_label",
                "Home Address: 123 Sukhumvit Road, Bangkok 10110 ok",
                True,
            ),
            Case(
                "shipping_address",
                "Shipping Address: 456 Rama IV Road Bangkok 10500 confirmed",
                True,
            ),
            Case(
                "thai_address_label",
                "ที่อยู่: 789/12 ถนนสุขุมวิท กรุงเทพ 10110 ครับ",
                True,
            ),
            Case("address_label_no_content", "address: hi", False),
            Case("plain_walk_prose", "I walked on Sukhumvit Road today", False),
            Case("no_label", "123 Sukhumvit Road, Bangkok 10110", False),
        ],
    ),
    DetectorSpec(
        name="thai_address",
        entity_type="thai_address",
        category="pii",
        severity=2,
        detector=ThaiAddressDetector(),
        cases=[
            # FIX26 §1.2 — keyword-precision split, both directions.
            Case("fix26_high_thi_yu_condo_masks", "ที่อยู่ 199/22 คอนโดลุมพินี", True),
            Case("fix26_high_thi_yu_english_building_masks", "ที่อยู่ 199/22 Lumpini Condo", True),
            Case("fix26_high_ban_lek_thi_village_masks", "บ้านเลขที่ 45/6 หมู่บ้านสัมมากร", True),
            Case("fix26_high_thi_yu_colon_masks", "ที่อยู่: 88/1 เดอะทรัสต์", True),
            Case("fix26_glued_thi_yu_with_component_masks", "จำที่อยู่ 99/88 ถนนสุขุมวิท 10110 ไว้", True),
            Case("fix26_ambig_song_thi_building_no_component", "ส่งที่ 12 อาคารเอ ชั้น 5", False),
            Case("fix26_ambig_yu_thi_bare_province", "อยู่ที่ 77/1 บางใหญ่ นนทบุรี", False),
            Case("fix26_ambig_lek_thi_prose", "เลขที่ให้จำเมื่อกี้คืออะไร", False),
            Case("fix26_thi_yu_relative_clause_no_mask", "สินค้าที่อยู่ในคลังมี 120 ชิ้น", False),
            Case("fix26_thi_yu_locative_prep_no_mask", "ที่อยู่ใน 3 อันดับแรก", False),
            Case("fix26_thi_yu_no_digit_no_mask", "ที่อยู่ของบริษัทคืออะไร ต้องไปเอาจากหน้าไหน", False),
            Case(
                "thai_address_full",
                "ที่อยู่ 123/45 ซอย 7 ถนนสุขุมวิท กรุงเทพ 10110 ครับ",
                True,
            ),
            Case(
                "house_number_keyword",
                "บ้านเลขที่ 99/1 ซอย 5 ถนน A กรุงเทพ 10120 ส่ง",
                True,
            ),
            Case(
                "deliver_to_keyword",
                "ส่งที่ 100 ถนน B จังหวัด นนทบุรี 11000 ขอบคุณ",
                True,
            ),
            # FIX23 — keyword words used as ordinary prose (no house
            # number / digit within 30 chars) must NOT be an address.
            # The first case is the exact recall turn that echoed.
            Case("fix23_recall_question_prose", "เลขที่ให้จำเมื่อกี้คืออะไร", False),
            Case("fix23_where_is_it", "ตอนนี้อยู่ที่ไหน", False),
            Case("fix23_answer_quoting_number", "เลขที่ให้จำคือ **74-กระต่าย-2951**", False),
            # FIX23b §2 — recall probes. expected_count is whatever the
            # Python detector does TODAY (parity contract), NOT the intent;
            # intent lives in backend/tests/test_dlp_detectors/
            # test_thai_address_recall.py (xfail = accepted gap).
            Case("fix23b_road_only", "บ้านเลขที่ 12 ถนนพระราม 4", True),
            Case("fix23b_condo_only", "ที่อยู่ 199/22 คอนโดลุมพินี", True),  # FIX26: HIGH keyword recovers it
            Case("fix23b_building_floor", "ส่งที่ 12 อาคารเอ ชั้น 5", False),
            Case("fix23b_village", "บ้านเลขที่ 45/6 หมู่บ้านสัมมากร", True),
            Case("fix23b_province_bare", "อยู่ที่ 77/1 บางใหญ่ นนทบุรี", False),
            Case("fix23b_soi_abbrev", "ที่อยู่ 5 ซ.ลาดพร้าว 101", True),
            Case("fix23b_thai_digits", "บ้านเลขที่ ๙๙/๘๘ ถนนสีลม", True),
            Case("fix23b_postcode_only", "ส่งที่ 88 มัณฑนา 10540", True),
            Case("fix23b_abbrev_m_t", "ที่อยู่ 45 ม.7 ต.บางพลี", True),
            Case("fix23b_english_building", "ที่อยู่ 199/22 Lumpini Condo", True),  # FIX26: HIGH keyword recovers it
            Case("fix23b_long_prose_gap", "ที่อยู่สำหรับจัดส่งสินค้าของลูกค้ารายนี้คือ 99/88 ถนนสุขุมวิท 10110", False),
            Case("fix23b_amphoe_word", "อยู่ที่ 10 อำเภอเมือง เชียงใหม่", True),
            Case("fix23b_building_room", "ส่งที่ 23 ตึกบี ห้อง 402", False),
            Case("fix23b_condo_postcode", "ที่อยู่ 199/22 คอนโดลุมพินี 10120", True),
            Case("fix23b_industrial_estate", "อยู่ที่ 55 นิคมอุตสาหกรรมอมตะ ชลบุรี", False),
            Case("fix23b_district_only", "บ้านเลขที่ 12/3 แขวงบางนา", True),
            Case("fix23b_no_house_number", "ที่อยู่ ถนนสุขุมวิท กรุงเทพ", False),
            Case("fix23b_room_tower", "ส่งที่ คอนโดไอดีโอ ตึก A ห้อง 1205", False),
            Case("fix23b_apartment", "อยู่ที่ 9/1 อพาร์ทเม้นท์สุขใจ", False),
            Case("fix23b_moo_number", "บ้านเลขที่ 3 หมู่ 5 ตำบลบางรัก", True),
            Case("fix23_company_address_question", "ที่อยู่ของบริษัทคืออะไร", False),
            Case(
                "fix23_registered_address_prose_then_number",
                "ที่อยู่ตามทะเบียนบ้าน 99/88 หมู่ 3 ต.บางรัก กรุงเทพ 10500",
                True,
            ),
            Case("plain_thai_prose", "วันนี้อากาศดีครับ", False),
            Case("english_prose_no_keyword", "today is a good day", False),
        ],
    ),
    DetectorSpec(
        name="income",
        entity_type="income",
        category="financial",
        severity=3,
        detector=IncomeDetector(),
        cases=[
            Case("salary_english", "Salary: 120,000 THB / month ok", True),
            Case("monthly_income", "Monthly income: 75000 baht confirmed", True),
            Case("thai_salary", "เงินเดือน 50,000 บาท เดือน ครับ", True),
            Case("product_price", "this item costs 500 baht only", False),
            Case("no_label", "120,000 alone", False),
            Case("label_no_value", "salary:", False),
        ],
    ),
    DetectorSpec(
        name="religion_belief",
        entity_type="religion_belief",
        category="pii",
        severity=3,
        detector=ReligionBeliefDetector(),
        cases=[
            Case("english_practicing", "I am a practicing Buddhist actually", True),
            Case("thai_religion_with_pronoun", "ผมนับถือศาสนาพุทธ มานานแล้ว", True),
            Case(
                "english_their_faith",
                "their faith is important to them, said the elder",
                True,
            ),
            Case(
                "tourism_no_personal",
                "The temple is a beautiful tourist site daily",
                False,
            ),
            Case(
                "the_following_prose",
                "Please review the following document carefully",
                False,
            ),
            Case("plain_thai_prose", "ฝนตกหนักวันนี้", False),
        ],
    ),
    DetectorSpec(
        name="health_condition",
        entity_type="health_condition",
        category="pii",
        severity=3,
        detector=HealthConditionDetector(),
        cases=[
            Case("english_diagnosed", "I was diagnosed with diabetes last year", True),
            Case(
                "thai_health_with_pronoun",
                "ผมป่วยเป็นเบาหวาน มาห้าปี ครับ",
                True,
            ),
            Case(
                "labelled_medical_value",
                "Medical: diabetes and high blood pressure now",
                True,
            ),
            Case(
                "generic_disease_discussion",
                "Diabetes is a common disease worldwide today",
                False,
            ),
            Case("plain_thai_prose", "อากาศเปลี่ยนแปลงบ่อย", False),
            Case("no_health_keyword", "she went to the market today", False),
        ],
    ),
    DetectorSpec(
        name="criminal_record",
        entity_type="criminal_record",
        category="pii",
        severity=3,
        detector=CriminalRecordDetector(),
        cases=[
            Case(
                "english_personal",
                "I was arrested for theft last summer here",
                True,
            ),
            Case("thai_personal", "เขาถูกจับเมื่อวานนี้ที่บ้าน ตามข่าว", True),
            Case(
                "english_my_record",
                "my criminal record was sealed by the court",
                True,
            ),
            Case(
                "news_no_pronoun",
                "Court news report from yesterday",
                False,
            ),
            Case("plain_thai_prose", "ตลาดเปิดเช้าวันนี้", False),
            Case("legal_no_personal", "Criminal law studies are interesting", False),
        ],
    ),
    DetectorSpec(
        name="biometric_data",
        entity_type="biometric_data",
        category="pii",
        severity=3,
        detector=BiometricDataDetector(),
        cases=[
            Case(
                "english_my_fingerprint",
                "I use my fingerprint to unlock the door",
                True,
            ),
            Case(
                "thai_fingerprint",
                "ผมลงทะเบียนลายนิ้วมือ ที่บริษัท เรียบร้อย",
                True,
            ),
            Case(
                "face_id_personal",
                "He uses face ID on his phone every day",
                True,
            ),
            Case(
                "biometric_textbook",
                "Biometric authentication is a security concept here",
                False,
            ),
            Case("plain_thai_prose", "วันนี้ทำงานหนัก", False),
            Case("no_biometric_keyword", "she went to lunch with friends", False),
        ],
    ),
    # ---------------------------------------------------------------
    # Batch 3 — checksum-based detectors. Valid IDs are real
    # checksum-passing 13-digit runs generated from Python (see the
    # commit message); invalid IDs are deliberately off-by-N on the
    # check digit so the same regex fires but the validator rejects.
    # ---------------------------------------------------------------
    DetectorSpec(
        name="thai_national_id",
        entity_type="thai_national_id",
        category="identity",
        severity=3,
        detector=ThaiNationalIdDetector(),
        cases=[
            Case(
                "valid_checksum_no_separator",
                "ID 1234567890121 verified",
                True,
            ),
            Case(
                "valid_checksum_dashed",
                "ID 1-2345-67890-12-1 verified",
                True,
            ),
            Case(
                "another_valid_id",
                "user has 5000000000001 on file",
                True,
            ),
            Case(
                "keyword_overrides_invalid_checksum",
                "national id: 1234567890123 placeholder",
                True,
            ),
            Case(
                "thai_keyword_gate",
                "บัตรประชาชน 1111111111111 ส่งให้",
                True,
            ),
            Case(
                "invalid_checksum_no_keyword",
                "the number 1234567890123 is here",
                False,
            ),
            Case(
                "starts_with_zero",
                "0234567890125 is not valid",
                False,
            ),
            Case(
                "too_short",
                "123456789012 only twelve",
                False,
            ),
            Case(
                "digit_immediately_before",
                "X1234567890121",  # leading non-digit, must still match
                True,
            ),
            Case(
                "embedded_in_longer_run",
                "999912345678901219999",
                False,
            ),
        ],
    ),
    DetectorSpec(
        name="thai_tax_id",
        entity_type="thai_tax_id",
        category="identity",
        severity=3,
        detector=ThaiTaxIdDetector(),
        cases=[
            Case(
                "thai_keyword_valid",
                "เลขประจำตัวผู้เสียภาษี 1234567890121 ของบริษัท",
                True,
            ),
            Case(
                "english_keyword_valid",
                "tax id 9876543210121 on the receipt",
                True,
            ),
            Case(
                "tin_keyword_valid",
                "TIN: 5000000000001 confirmed",
                True,
            ),
            Case(
                "valid_checksum_no_keyword",
                "the number 1234567890121 is here",
                False,
            ),
            Case(
                "keyword_but_invalid_checksum",
                "tax id 1234567890123 wrong",
                False,
            ),
            Case(
                "english_prose",
                "no tax info in this sentence",
                False,
            ),
        ],
    ),
    DetectorSpec(
        name="thai_bank_account",
        entity_type="thai_bank_account",
        category="financial",
        severity=3,
        detector=ThaiBankAccountDetector(),
        cases=[
            Case(
                "thai_keyword_dashed",
                "บัญชี 123-4-56789-0 ออมทรัพย์",
                True,
            ),
            Case(
                "english_keyword",
                "account number 1234567890 confirmed",
                True,
            ),
            Case(
                "bank_keyword",
                "ธนาคารกรุงเทพ 123-4-56789-012 ส่ง",
                True,
            ),
            Case(
                "bare_number_no_keyword",
                "the number 1234567890 unrelated",
                False,
            ),
            Case(
                "wrong_length",
                "account number 12345 too short",
                False,
            ),
            Case("plain_prose", "today is a good day", False),
        ],
    ),
    DetectorSpec(
        name="thai_promptpay",
        entity_type="thai_promptpay",
        category="financial",
        severity=2,
        detector=ThaiPromptPayDetector(),
        cases=[
            Case(
                "mobile_thai_keyword",
                "พร้อมเพย์เบอร์ 0812345678 โอนได้",
                True,
            ),
            Case(
                "mobile_english_keyword",
                "promptpay 0812345678 ready",
                True,
            ),
            Case(
                "valid_id_thai_keyword",
                "พร้อมเพย์ 1234567890121 ของผม",
                True,
            ),
            Case(
                "id_invalid_checksum_thai_keyword",
                "พร้อมเพย์ 1234567890123 placeholder",
                False,
            ),
            Case(
                "mobile_without_keyword",
                "the number 0812345678 alone",
                False,
            ),
            Case(
                "valid_id_without_keyword",
                "the user 1234567890121 paid",
                False,
            ),
            Case("plain_prose", "today is a good day", False),
            Case("thai_prose", "ฝนตกหนักวันนี้", False),
        ],
    ),
    # ---------------------------------------------------------------
    # Batch 4 — names / credit card / passport
    # ---------------------------------------------------------------
    DetectorSpec(
        name="thai_name",
        entity_type="thai_name",
        category="pii",
        severity=3,
        detector=ThaiNameDetector(),
        cases=[
            # FIX26 §1.2 — full parity + the คุณ-particle guard, both directions.
            Case("fix26_khun_no_surname_masks", "ฝากบอกคุณสมชายด้วย", True),
            Case("fix26_khun_surname_masks", "คุณสมหญิง รักดี โทรมา", True),
            Case("fix26_compound_title_masks", "ผศ.ดร. สมศักดิ์ มากมี", True),
            Case("fix26_trailing_label_trimmed", "คุณสมหญิง จำนวน 10,000 บาท", True),
            Case("fix26_khob_khun_mak_no_mask", "ขอบคุณมากครับ ช่วยได้เยอะเลย", False),
            Case("fix26_khob_khun_na_no_mask", "เยี่ยมมาก 👍 ขอบคุณนะ", False),
            Case("fix26_khun_keu_krai_no_mask", "คุณคือใคร ชื่ออะไร", False),
            Case("fix26_khun_tham_no_mask", "คุณทำอะไรได้บ้าง", False),
            Case("fix26_khun_thi_rak_no_mask", "คุณที่รักทั้งหลาย", False),
            Case("thai_prefix_full", "ลูกค้าชื่อ นาย สมชาย ใจดี", True),
            Case("nang_sao", "ผู้รับ นางสาว สมหญิง สมบูรณ์", True),
            Case("doctor", "ดร. สมศักดิ์ มากมี เข้าร่วม", True),
            Case("english_dr_thai_name", "Dr. สมชาย ใจดี attended", True),
            Case("nayok_compound_no_match", "นายกรัฐมนตรี กล่าวต่อ", False),
            Case("nay_pol_compound_no_match", "นายพล ผู้บัญชาการ", False),
            Case("plain_prose_no_match", "วันนี้ฝนตก", False),
            # FIX28 — live FP: "คุณ" + a verb is address, never a name
            Case("fix28_khun_verb_no_mask", "คำตอบแรกที่คุณตอบผมคืออะไร ตอบสั้นๆ", False),
            Case("fix28_khun_think_no_mask", "คุณคิดว่าอันไหนดีกว่ากัน", False),
            Case("fix28_khun_real_name_still_masks", "ลูกค้าชื่อคุณสมชาย ใจดี โทรมา", True),
        ],
    ),
    DetectorSpec(
        name="thai_name_no_prefix",
        entity_type="thai_name",
        category="pii",
        severity=2,
        detector=ThaiNameNoPrefixDetector(),
        cases=[
            Case(
                "two_word_with_context_kw",
                "ลูกค้า สมชาย ใจดี ลงทะเบียน",
                True,
            ),
            Case(
                "song_word_chue",
                "ชื่อ สมหญิง สมบูรณ์ ครับ",
                True,
            ),
            Case(
                "no_context_kw",
                "สมชาย ใจดี ไปทำงาน",
                False,
            ),
            Case("plain_thai_prose", "วันนี้ฝนตกหนัก", False),
            Case("english_prose", "no thai content here", False),
            # FIX26 §1.2 — stop rules + label trim, both directions.
            Case("fix26_kind_answer_briefly_no_mask", "บอกชื่อสัตว์มา 1 ชนิด ตอบสั้นๆ", False),
            Case("fix26_label_pair_no_mask", "ลูกค้าชื่อ เบอร์ เลขบัตร ต้องเก็บ", False),
            Case("fix26_phrase_pairs_no_mask", "พนักงาน ทุกคน ต้องส่ง รายงาน ก่อนศุกร์", False),
            Case("fix26_question_pair_no_mask", "ชื่อไฟล์ ควรตั้ง ยังไง ให้หาง่าย", False),
            Case("fix26_asked_pair_no_mask", "ลูกค้า ถามมา ว่าสินค้า จะเข้าเมื่อไหร่", False),
            Case("fix26_label_trim_keeps_name", "ชื่อ สมชาย เบอร์ ติดต่อกลับ", True),
            Case("fix26_label_trim_no_label_before_no_mask", "ลูกค้า ปิยะพงศ์ เบอร์ ติดต่อกลับ", False),
            Case("fix26_real_pair_still_masks", "ลูกค้าชื่อ ปิยะพงศ์ วงศ์สุวรรณ ลงทะเบียน", True),
            Case("fix26_real_pair_after_name_label", "name: อรอนงค์ ศรีสุข", True),
            # FIX27 Part 3 — Pass 2 (gazetteer + sensitive context) and Pass 3 (person
            # list) were never in the Rust port; these pin them.
            Case("fix27_gazetteer_with_sensitive_context", "อัญชลี บุญรอด ลาออกแล้ว", True),
            Case("fix27_gazetteer_with_money_context", "ภาณุ ทรัพย์มาก ผ่อนบ้านอยู่", True),
            Case("fix27_gazetteer_bare_no_context_no_mask", "อัญชลี มาถึงแล้ว", False),
            Case("fix27_list_prefixed_items", "รายชื่อผู้เข้าร่วม: คุณสมชาย, คุณสมหญิง", True),
            Case("fix27_list_gazetteer_item", "รายชื่อพนักงาน: อัญชลี, ภาณุ", True),
            Case("fix27_list_products_no_mask", "รายชื่อสินค้า: กล้วย, มะม่วง, ส้ม", False),
        ],
    ),
    DetectorSpec(
        name="credit_card",
        entity_type="credit_card_number",
        category="financial",
        severity=3,
        detector=CreditCardDetector(),
        cases=[
            Case("visa_no_separators", "card 4111111111111111 expires", True),
            Case("visa_dashed", "card 4111-1111-1111-1111 ok", True),
            Case("mastercard_spaces", "card 5500 0055 5555 5559 ok", True),
            Case("amex_15_digits", "card 378282246310005 charged", True),
            Case("discover_test", "card 6011111111111117 saved", True),
            Case("invalid_luhn", "the number 4111111111111112 wrong", False),
            Case("too_short", "card 411111111111 only 12", False),
            Case("plain_prose", "no card here", False),
        ],
    ),
    DetectorSpec(
        name="passport",
        entity_type="passport_number",
        category="identity",
        severity=3,
        detector=PassportDetector(),
        cases=[
            Case("passport_no_letter_prefix", "passport AB1234567 issued", True),
            Case("passport_number_colon", "passport number: A1234567 ok", True),
            Case("thai_keyword", "หนังสือเดินทาง AA1234567 ของผม", True),
            Case("us_bare_9_digit", "passport 123456789 confirmed", True),
            Case("no_keyword_no_match", "AB1234567 just sitting there", False),
            Case("plain_prose", "no travel info today", False),
        ],
    ),
]


def build_fixture(spec: DetectorSpec) -> dict:
    cases_out = []
    for case in spec.cases:
        matches = spec.detector.detect(case.input)
        spans = [{"start": m.start, "end": m.end} for m in matches]
        if case.should_match:
            assert matches, (
                f"[{spec.name}/{case.id}] should-match case produced 0 matches "
                f"on input {case.input!r} — Python detector disagrees with the fixture"
            )
        else:
            assert not matches, (
                f"[{spec.name}/{case.id}] should-not-match case produced "
                f"{len(matches)} matches on input {case.input!r}: {spans}"
            )
        cases_out.append(
            {
                "id": case.id,
                "input": case.input,
                "should_match": case.should_match,
                "expected_count": len(matches),
                "expected_spans": spans,
            }
        )
    return {
        "detector": spec.name,
        "entity_type": spec.entity_type,
        "category": spec.category,
        "severity": spec.severity,
        "cases": cases_out,
    }


def main() -> int:
    total = 0
    for spec in SPECS:
        fixture = build_fixture(spec)
        out_path = FIXTURES_DIR / f"{spec.name}.json"
        out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
        print(
            f"  [OK] {spec.name:<16s} {len(fixture['cases'])} cases "
            f"({sum(c['expected_count'] for c in fixture['cases'])} matches)"
        )
        total += len(fixture["cases"])
    print(f"\nWrote {len(SPECS)} fixture(s), {total} cases total → {FIXTURES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
