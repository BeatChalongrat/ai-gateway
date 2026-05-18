//! Batch-1 parity tests.
//!
//! Loads each fixture from ``tests/parity/fixtures/{detector}.json``,
//! resolves the matching Rust detector via
//! :fn:`dlp_sidecar::detectors::detector_for`, and asserts:
//!
//!   * total match count = ``expected_count``
//!   * for each emitted match, (start, end) == ``expected_spans[i]``
//!
//! Failure here means the Rust port disagrees with Python on the
//! same input — a parity violation that must be resolved before the
//! detector can ship through the gRPC server in Batch 4.

use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Fixture {
    detector: String,
    cases: Vec<Case>,
}

#[derive(Debug, Deserialize)]
struct Case {
    id: String,
    input: String,
    #[allow(dead_code)]
    should_match: bool,
    expected_count: usize,
    expected_spans: Vec<Span>,
}

#[derive(Debug, Deserialize, PartialEq)]
struct Span {
    start: usize,
    end: usize,
}

fn run_fixture(path: &str) {
    let raw = std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("read {}: {}", path, e));
    let fixture: Fixture =
        serde_json::from_str(&raw).unwrap_or_else(|e| panic!("parse {}: {}", path, e));

    let detector = dlp_sidecar::detectors::detector_for(&fixture.detector).unwrap_or_else(
        || panic!("no Rust detector registered for {}", fixture.detector),
    );

    for case in fixture.cases {
        let matches = detector.detect(&case.input);

        assert_eq!(
            matches.len(),
            case.expected_count,
            "[{}/{}] count mismatch — input={:?}, got spans={:?}",
            fixture.detector,
            case.id,
            case.input,
            matches.iter().map(|m| (m.start, m.end)).collect::<Vec<_>>(),
        );

        for (i, expected) in case.expected_spans.iter().enumerate() {
            let got = &matches[i];
            assert_eq!(
                got.start, expected.start,
                "[{}/{}] span[{}].start: expected {}, got {} — input={:?}",
                fixture.detector, case.id, i, expected.start, got.start, case.input,
            );
            assert_eq!(
                got.end, expected.end,
                "[{}/{}] span[{}].end: expected {}, got {} — input={:?}",
                fixture.detector, case.id, i, expected.end, got.end, case.input,
            );
        }
    }
}

// One #[test] per fixture so failures pinpoint the offending detector.
// Macro keeps the registration boilerplate-free.
macro_rules! parity_test {
    ($name:ident, $file:expr) => {
        #[test]
        fn $name() {
            run_fixture($file);
        }
    };
}

parity_test!(email, "tests/parity/fixtures/email.json");
parity_test!(ssn, "tests/parity/fixtures/ssn.json");
parity_test!(iban, "tests/parity/fixtures/iban.json");
parity_test!(ip_address, "tests/parity/fixtures/ip_address.json");
parity_test!(mac_address, "tests/parity/fixtures/mac_address.json");
parity_test!(bearer_token, "tests/parity/fixtures/bearer_token.json");
parity_test!(private_key, "tests/parity/fixtures/private_key.json");
parity_test!(password_field, "tests/parity/fixtures/password_field.json");
parity_test!(line_id, "tests/parity/fixtures/line_id.json");
// Batch 2 — context-window detectors
parity_test!(phone, "tests/parity/fixtures/phone.json");
parity_test!(api_key_secret, "tests/parity/fixtures/api_key_secret.json");
parity_test!(
    db_connection_string,
    "tests/parity/fixtures/db_connection_string.json"
);
parity_test!(medical_record, "tests/parity/fixtures/medical_record.json");
parity_test!(date_of_birth, "tests/parity/fixtures/date_of_birth.json");
parity_test!(vehicle_plate, "tests/parity/fixtures/vehicle_plate.json");
parity_test!(address, "tests/parity/fixtures/address.json");
parity_test!(thai_address, "tests/parity/fixtures/thai_address.json");
parity_test!(income, "tests/parity/fixtures/income.json");
parity_test!(religion_belief, "tests/parity/fixtures/religion_belief.json");
parity_test!(health_condition, "tests/parity/fixtures/health_condition.json");
parity_test!(criminal_record, "tests/parity/fixtures/criminal_record.json");
parity_test!(biometric_data, "tests/parity/fixtures/biometric_data.json");
// Batch 3 — checksum / banking / promptpay
parity_test!(thai_national_id, "tests/parity/fixtures/thai_national_id.json");
parity_test!(thai_tax_id, "tests/parity/fixtures/thai_tax_id.json");
parity_test!(thai_bank_account, "tests/parity/fixtures/thai_bank_account.json");
parity_test!(thai_promptpay, "tests/parity/fixtures/thai_promptpay.json");
