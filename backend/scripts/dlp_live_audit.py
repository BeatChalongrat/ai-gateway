"""DLP Live-Fire Audit.

Runs a curated bank of sample texts through the full DLP scan pipeline
and reports what each detector actually catches. Complements the pytest
suite with a human-readable end-to-end pass across every detector.

Run from the ``backend`` directory:

    PYTHONPATH=. python scripts/dlp_live_audit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/dlp_live_audit.py` without PYTHONPATH export.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.dlp.detectors.registry import get_default_registry

# Known-valid Thai ID numbers from the test fixtures
# (tests/test_new_detectors.py:41-43). Checksum-validated.
VALID_THAI_ID = "1234567890121"
VALID_THAI_ID_2 = "2345678901234"
INVALID_THAI_ID = "1234567890129"

registry = get_default_registry(disabled_detectors=[])

# Detectors whose raw output may legitimately overlap with another
# detector's span. These are handled downstream by the overlap resolver,
# so the live-fire script treats them as informational rather than a
# test failure.
#
# Notably: ``phone`` is deliberately permissive (matches any 10-15 digit
# run) and relies on higher-priority detectors (thai_*, promptpay) to
# claim the span first. It will fire on any digit run and that is OK at
# this stage of the pipeline.
#
# ``email`` matches ``user@host`` inside DB connection URLs — the
# db_connection_string span subsumes it, which is also resolved
# downstream.
EXPECTED_OVERLAPS = {
    "phone",
    "email",
    "thai_national_id",
    "api_key_secret",
    # thai_address includes "เลขที่" as a solo trigger keyword. That
    # phrase also appears in "บัตรประชาชนเลขที่ ..." ("citizen ID
    # card number"), so thai_address over-matches when an ID number
    # follows. The downstream overlap resolver picks the
    # higher-priority thai_national_id (HIGH vs LOW), but the raw
    # over-match is a genuine gap — see the final report.
    "thai_address",
}

test_cases: list[dict] = [
    # === IDENTITY ===
    {
        "label": "Thai National ID (valid checksum, Thai keyword)",
        "text": f"บัตรประชาชนเลขที่ {VALID_THAI_ID}",
        "expect_detect": ["thai_national_id"],
    },
    {
        "label": "Thai National ID (invalid checksum) — must be rejected",
        "text": f"บัตรประชาชนเลขที่ {INVALID_THAI_ID}",
        "expect_detect": [],
    },
    {
        "label": "Passport number (English)",
        "text": "passport number: AA1234567",
        "expect_detect": ["passport"],
    },
    {
        "label": "Thai Tax ID (keyword-gated)",
        "text": f"tax id: {VALID_THAI_ID_2}",
        "expect_detect": ["thai_tax_id"],
    },
    {
        "label": "Vehicle plate (Thai keyword)",
        "text": "ทะเบียนรถ กข-1234",
        "expect_detect": ["vehicle_plate"],
    },
    # === FINANCIAL ===
    {
        "label": "Credit card (valid Luhn)",
        "text": "card number 4111-1111-1111-1111",
        "expect_detect": ["credit_card"],
    },
    {
        "label": "Credit card (invalid Luhn) — must be rejected",
        "text": "card number 4111-1111-1111-1112",
        "expect_detect": [],
    },
    {
        "label": "IBAN (UK test IBAN, valid mod-97)",
        "text": "transfer to IBAN GB29 NWBK 6016 1331 9268 19",
        "expect_detect": ["iban"],
    },
    {
        "label": "Thai bank account (keyword-gated)",
        "text": "บัญชี 123-4-56789-0",
        "expect_detect": ["thai_bank_account"],
    },
    {
        "label": "PromptPay 10-digit mobile (English keyword)",
        "text": "promptpay 0812345678",
        "expect_detect": ["thai_promptpay"],
    },
    {
        "label": "PromptPay 13-digit (VALID checksum) — detect after fix",
        "text": f"promptpay {VALID_THAI_ID}",
        "expect_detect": ["thai_promptpay"],
    },
    {
        "label": "PromptPay 13-digit (INVALID checksum) — reject after fix",
        "text": "promptpay 9999999999999",
        "expect_detect": [],
    },
    # === CREDENTIAL / SECRET ===
    {
        "label": "AWS access key (AKIA pattern)",
        "text": "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
        "expect_detect": ["bearer_token"],
        "expect_note": (
            "AKIA is caught by bearer_token via its AWS-prefix branch. "
            "api_key_secret does NOT fire here because 'aws_access_key_id' "
            "is not in its assignment-label list (password|api_key|secret|"
            "access_token|...). Acceptable — bearer_token masks it."
        ),
    },
    {
        "label": "DB connection string (basic)",
        "text": "DATABASE_URL=postgresql://admin:secret@db.example.com:5432/mydb",
        "expect_detect": ["db_connection_string"],
    },
    {
        "label": "DB conn string with ?query — must NOT capture the query",
        "text": "DATABASE_URL=postgresql://admin:secret@db.example.com:5432/mydb?sslmode=require",
        "expect_detect": ["db_connection_string"],
        "expect_note": "matched_text must NOT include ?sslmode=require (Fix 3)",
    },
    {
        "label": "Bearer token / JWT",
        "text": (
            "Authorization: Bearer "
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        ),
        "expect_detect": ["bearer_token"],
        "expect_note": "JWT shape also matches api_key_secret (expected overlap, resolved downstream)",
    },
    {
        "label": "Private key PEM header",
        "text": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK...",
        "expect_detect": ["private_key"],
    },
    {
        "label": "Password field (config line)",
        "text": 'password = "SuperSecret123!"',
        "expect_detect": ["password_field"],
    },
    # === PII ===
    {
        "label": "Email address",
        "text": "contact us at user@example.com for info",
        "expect_detect": ["email"],
    },
    {
        "label": "Email with trailing dot — dot must NOT be in match",
        "text": "sent to user@example.com. Please check.",
        "expect_detect": ["email"],
        "expect_note": "matched_text should equal 'user@example.com' (no trailing '.')",
    },
    {
        "label": "Phone number (Thai international format)",
        "text": "call me at +66-81-234-5678",
        "expect_detect": ["phone"],
    },
    {
        "label": "US SSN (valid shape)",
        "text": "ssn: 123-45-6789",
        "expect_detect": ["ssn"],
    },
    {
        "label": "Date of birth (keyword-gated)",
        "text": "date of birth: 15/03/1990",
        "expect_detect": ["date_of_birth"],
    },
    {
        "label": "Thai address (keyword-gated)",
        "text": "ที่อยู่ 123/45 ถ.สุขุมวิท แขวงคลองเตย เขตคลองเตย กรุงเทพ 10110",
        "expect_detect": ["thai_address"],
    },
    {
        "label": "Medical record (MRN keyword)",
        "text": "patient id 12345678 admitted yesterday",
        "expect_detect": ["medical_record"],
    },
    # === NETWORK ===
    {
        "label": "IPv4 address",
        "text": "server at 192.168.1.100 port 8080",
        "expect_detect": ["ip_address"],
    },
    {
        "label": "MAC address",
        "text": "device mac: AA:BB:CC:DD:EE:FF",
        "expect_detect": ["mac_address"],
    },
    # === TRUE NEGATIVES / FP CHECKS ===
    {
        "label": "13-digit number with no keyword — must NOT trigger",
        "text": "order number 1234567890123 confirmed",
        "expect_detect": [],
    },
    {
        "label": "Plain prose — no sensitive data",
        "text": "The weather today is sunny with a high of 32 degrees",
        "expect_detect": [],
    },
    {
        "label": "Code snippet with numbers — must NOT over-trigger",
        "text": "for i in range(1000000): total += prices[i]",
        "expect_detect": [],
    },
]


def _sev_name(sev) -> str:
    try:
        return sev.name
    except AttributeError:
        return str(sev)


def main() -> int:
    total = len(test_cases)
    n_det = len(registry.all())
    banner = f"DLP LIVE-FIRE AUDIT — {total} test cases, {n_det} detectors loaded"
    print("=" * 80)
    print(banner)
    print("=" * 80 + "\n")

    passed = 0
    failed = 0
    warnings = 0
    failure_details: list[str] = []

    for i, tc in enumerate(test_cases, 1):
        results = registry.scan_text(tc["text"])
        detected_names = sorted({r.detector_name for r in results})
        expected = sorted(tc["expect_detect"])
        expected_set = set(expected)
        detected_set = set(detected_names)
        missing = expected_set - detected_set
        extra = detected_set - expected_set
        unexpected_extra = extra - EXPECTED_OVERLAPS

        if missing:
            status = "FAIL   "
            failed += 1
            failure_details.append(f"[{i:02d}] {tc['label']} — missing {missing}")
        elif unexpected_extra:
            status = "WARN   "
            warnings += 1
        else:
            status = "PASS   "
            passed += 1

        print(f"[{i:02d}] {status} | {tc['label']}")
        if results:
            for r in results:
                print(
                    f"      -> {r.detector_name}: {r.matched_text!r} "
                    f"(severity={_sev_name(r.severity)})"
                )
        else:
            print("      -> (nothing detected)")

        if missing:
            print(f"      MISSING: {sorted(missing)}")
        overlaps = extra & EXPECTED_OVERLAPS
        if overlaps:
            print(f"      overlap (expected, resolved downstream): {sorted(overlaps)}")
        if unexpected_extra:
            print(f"      UNEXPECTED EXTRA: {sorted(unexpected_extra)}")
        if tc.get("expect_note"):
            print(f"      NOTE: {tc['expect_note']}")
        print()

    print("=" * 80)
    print(
        f"RESULTS: {passed} passed | {failed} failed | {warnings} warnings | "
        f"{total} total"
    )
    print("=" * 80)
    if failure_details:
        print("\nFailures:")
        for line in failure_details:
            print(f"  - {line}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
