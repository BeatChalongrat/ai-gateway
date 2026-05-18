//! Detector subpackage — one file per Python detector.
//!
//! Batch 1: email, ssn, iban, ip_address (v4 + v6), mac_address,
//! bearer_token, private_key, password_field, line_id.
//! Batch 2 (this commit): phone, api_key_secret,
//! db_connection_string, medical_record, date_of_birth,
//! vehicle_plate, address, thai_address, income, religion_belief,
//! health_condition, criminal_record, biometric_data.

pub mod address;
pub mod api_key_secret;
pub mod base;
pub mod bearer_token;
pub mod biometric_data;
pub mod credit_card;
pub mod criminal_record;
pub mod date_of_birth;
pub mod db_connection_string;
pub mod email;
pub mod health_condition;
pub mod iban;
pub mod income;
pub mod ip_address;
pub mod line_id;
pub mod mac_address;
pub mod medical_record;
pub mod passport;
pub mod password_field;
pub mod phone;
pub mod private_key;
pub mod religion_belief;
pub mod ssn;
pub mod thai_address;
pub mod thai_bank_account;
pub mod thai_name;
pub mod thai_name_no_prefix;
pub mod thai_national_id;
pub mod thai_promptpay;
pub mod thai_tax_id;
pub mod vehicle_plate;

use base::{Detector, RawMatch};

/// Look up a detector by Python ``name`` attribute.
pub fn detector_for(name: &str) -> Option<Box<dyn Detector>> {
    match name {
        // Batch 1
        "email" => Some(Box::new(email::EmailDetector)),
        "ssn" => Some(Box::new(ssn::SsnDetector)),
        "iban" => Some(Box::new(iban::IbanDetector)),
        "ip_address" => Some(Box::new(IpAddressDetector)),
        "mac_address" => Some(Box::new(mac_address::MacAddressDetector)),
        "bearer_token" => Some(Box::new(bearer_token::BearerTokenDetector)),
        "private_key" => Some(Box::new(private_key::PrivateKeyDetector)),
        "password_field" => Some(Box::new(password_field::PasswordFieldDetector)),
        "line_id" => Some(Box::new(line_id::LineIdDetector)),
        // Batch 2
        "phone" => Some(Box::new(phone::PhoneDetector)),
        "api_key_secret" => Some(Box::new(api_key_secret::ApiKeySecretDetector)),
        "db_connection_string" => {
            Some(Box::new(db_connection_string::DbConnectionStringDetector))
        }
        "medical_record" => Some(Box::new(medical_record::MedicalRecordDetector)),
        "date_of_birth" => Some(Box::new(date_of_birth::DateOfBirthDetector)),
        "vehicle_plate" => Some(Box::new(vehicle_plate::VehiclePlateDetector)),
        "address" => Some(Box::new(address::AddressDetector)),
        "thai_address" => Some(Box::new(thai_address::ThaiAddressDetector)),
        "income" => Some(Box::new(income::IncomeDetector)),
        "religion_belief" => Some(Box::new(religion_belief::ReligionBeliefDetector)),
        "health_condition" => {
            Some(Box::new(health_condition::HealthConditionDetector))
        }
        "criminal_record" => Some(Box::new(criminal_record::CriminalRecordDetector)),
        "biometric_data" => Some(Box::new(biometric_data::BiometricDataDetector)),
        // Batch 3 — checksum / banking / promptpay
        "thai_national_id" => {
            Some(Box::new(thai_national_id::ThaiNationalIdDetector))
        }
        "thai_tax_id" => Some(Box::new(thai_tax_id::ThaiTaxIdDetector)),
        "thai_bank_account" => {
            Some(Box::new(thai_bank_account::ThaiBankAccountDetector))
        }
        "thai_promptpay" => Some(Box::new(thai_promptpay::ThaiPromptPayDetector)),
        // Batch 4 — name / credit_card / passport
        "thai_name" => Some(Box::new(thai_name::ThaiNameDetector)),
        "thai_name_no_prefix" => {
            Some(Box::new(thai_name_no_prefix::ThaiNameNoPrefixDetector))
        }
        "credit_card" => Some(Box::new(credit_card::CreditCardDetector)),
        "passport" => Some(Box::new(passport::PassportDetector)),
        _ => None,
    }
}

/// Combined IPv4 + IPv6 detector — Python emits both from one class.
struct IpAddressDetector;

impl Detector for IpAddressDetector {
    fn name(&self) -> &'static str {
        "ip_address"
    }
    fn entity_type(&self) -> &'static str {
        "ipv4_address"
    }
    fn category(&self) -> &'static str {
        "network"
    }
    fn severity(&self) -> u32 {
        2
    }
    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = ip_address::Ipv4Detector.detect(text);
        out.extend(ip_address::Ipv6Detector.detect(text));
        out
    }
}
