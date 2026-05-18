//! Detector registry — Batch 1 + Batch 2 detectors.

use crate::detectors::base::{Detector, RawMatch};
use crate::detectors::*;

pub fn default_detectors() -> Vec<Box<dyn Detector>> {
    vec![
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
    ]
}

pub fn scan_all(text: &str) -> Vec<(String, RawMatch)> {
    let mut out = Vec::new();
    for det in default_detectors() {
        let name = det.name().to_string();
        for m in det.detect(text) {
            out.push((name.clone(), m));
        }
    }
    out
}
