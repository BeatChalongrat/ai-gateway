//! Detector registry — all 4 batches (30 detectors).
//!
//! ``thai_name_no_prefix`` is registered **always** (30 detectors),
//! matching Python's current ``get_default_registry`` (registry.py:323):
//! the old ``not ner_enabled`` gate was removed because the backend
//! image ships only ``en_core_web_sm`` (no Thai NER), so the regex
//! fallback is needed even when NER is on. It runs at priority -5 so
//! the overlap resolver still prefers Presidio on shared spans. The
//! ``ner_enabled`` flag (threaded from the gRPC request) no longer
//! affects the regex registry composition — Presidio NER lives in the
//! Python ner-service, not the sidecar.

use crate::detectors::base::{take_engine_error, Detector, RawMatch};
use crate::detectors::*;

/// One match, tagged with the detector metadata the server maps to proto:
/// (detector name, entity type, category, severity, match).
pub type Tagged = (String, &'static str, &'static str, u32, RawMatch);

/// Run the registry over one text — or refuse the scan (FIX103).
///
/// A detector that hit a regex engine error used to either spin forever (a
/// swallowed iterator error — the FIX101/FIX102 wedge) or quietly stop
/// looking, and a detector that panicked was logged and SKIPPED. In every
/// case the gateway was told the scan succeeded, with whatever that
/// detector would have found missing.
///
/// Now the scan is refused, naming the detector. The gateway's Rust client
/// gets an RPC error, returns `None`, and `orchestrator._run_dlp_scan`
/// rescans the whole request on the Python registry — the same detectors,
/// no backtrack limit. A refusal costs one fallback; a skip cost a mask.
pub fn run_detectors(
    text: &str,
    detectors: &[Box<dyn Detector>],
    disabled: &[String],
    from_decoded: bool,
) -> Result<Vec<Tagged>, String> {
    let mut out = Vec::new();
    for det in detectors {
        let name = det.name();
        if disabled.iter().any(|d| d == name) {
            continue;
        }
        // A stale error left on this worker thread must not blame this detector.
        let _ = take_engine_error();
        let matches = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            det.detect_ctx(text, from_decoded)
        }))
        .map_err(|_| format!("{name}: detector panicked"))?;
        if let Some(err) = take_engine_error() {
            return Err(format!("{name}: regex engine error ({err})"));
        }
        let (entity, category, severity) = (det.entity_type(), det.category(), det.severity());
        for m in matches {
            // Per-match entity_type override — api_key_secret /
            // password_field emit a distinct type per pattern;
            // ``None`` falls back to the detector's uniform type.
            let et = m.entity_type.unwrap_or(entity);
            out.push((name.to_string(), et, category, severity, m));
        }
    }
    out.sort_by_key(|(_, _, _, _, m)| (m.start, m.end));
    Ok(out)
}

/// Build the registry for one request. 30 detectors regardless of
/// ``_ner_enabled`` (kept for API stability + future use).
pub fn default_detectors(_ner_enabled: bool) -> Vec<Box<dyn Detector>> {
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
    // Always registered (priority -5 floor) — matches Python registry.py:323.
    detectors.push(Box::new(thai_name_no_prefix::ThaiNameNoPrefixDetector));
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
