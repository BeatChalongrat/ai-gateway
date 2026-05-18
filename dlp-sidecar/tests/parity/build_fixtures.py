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
