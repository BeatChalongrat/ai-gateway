//! Detector registry — all 4 batches (up to 30 detectors).
//!
//! ``thai_name_no_prefix`` is the **only NER-gated detector**:
//! Python's ``get_default_registry`` registers it solely when
//! ``ner_enabled=False`` (the regex acts as a coarse fallback when
//! Presidio's PERSON recognizer isn't running). When NER *is* on,
//! the regex would just shadow Presidio and inflate the finding
//! count with duplicates. We mirror that gate here so shadow-mode
//! comparisons between Python and Rust don't see a constant
//! ``only_rust`` delta on every request.
//!
//! The bool flag is threaded all the way from the gRPC request
//! (``ScanRequest.ner_enabled`` / ``MessagesRequest.ner_enabled``)
//! so the registry composition is per-request, not per-process.

use crate::detectors::base::{Detector, RawMatch};
use crate::detectors::*;

/// Build the registry for one request.
///
/// * ``ner_enabled = true``  → omit ``thai_name_no_prefix`` (29 detectors)
/// * ``ner_enabled = false`` → include ``thai_name_no_prefix`` (30 detectors)
pub fn default_detectors(ner_enabled: bool) -> Vec<Box<dyn Detector>> {
    let mut detectors: Vec<Box<dyn Detector>> = vec![
        // Batch 1 — simple regex
        Box::new(email::EmailDetector),
        Box::new(ssn::SsnDetector),
        Box::new(iban::IbanDetector),
        Box::new(ip_address::Ipv4Detector),
        Box::new(ip_address::Ipv6Detector),
        Box::new(mac_address::MacAddressDetector),
        Box::new(bearer_token::BearerTokenDetector),
        Box::new(private_key::PrivateKeyDetector),
        Box::new(password_field::PasswordFieldDetector),
        Box::new(line_id::LineIdDetector),
        // Batch 2 — context window
        Box::new(phone::PhoneDetector),
        Box::new(api_key_secret::ApiKeySecretDetector),
        Box::new(db_connection_string::DbConnectionStringDetector),
        Box::new(medical_record::MedicalRecordDetector),
        Box::new(date_of_birth::DateOfBirthDetector),
        Box::new(vehicle_plate::VehiclePlateDetector),
        Box::new(address::AddressDetector),
        Box::new(thai_address::ThaiAddressDetector),
        Box::new(income::IncomeDetector),
        Box::new(religion_belief::ReligionBeliefDetector),
        Box::new(health_condition::HealthConditionDetector),
        Box::new(criminal_record::CriminalRecordDetector),
        Box::new(biometric_data::BiometricDataDetector),
        // Batch 3 — checksum / banking / promptpay
        Box::new(thai_national_id::ThaiNationalIdDetector),
        Box::new(thai_tax_id::ThaiTaxIdDetector),
        Box::new(thai_bank_account::ThaiBankAccountDetector),
        Box::new(thai_promptpay::ThaiPromptPayDetector),
        // Batch 4 — name / credit_card / passport
        Box::new(thai_name::ThaiNameDetector),
        Box::new(credit_card::CreditCardDetector),
        Box::new(passport::PassportDetector),
    ];
    if !ner_enabled {
        detectors.push(Box::new(thai_name_no_prefix::ThaiNameNoPrefixDetector));
    }
    detectors
}

pub fn scan_all(text: &str) -> Vec<(String, RawMatch)> {
    let mut out = Vec::new();
    // ``scan_all`` is used by ad-hoc tests / benchmarks that want
    // the broadest detector coverage — ``ner_enabled=false`` to
    // include the regex fallback.
    for det in default_detectors(false) {
        let name = det.name().to_string();
        for m in det.detect(text) {
            out.push((name.clone(), m));
        }
    }
    out
}
