//! Detector subpackage — one file per Python detector.
//!
//! Batch 1 (this commit) ships the nine simple-regex detectors:
//! email, ssn, iban, ip_address (v4 + v6), mac_address,
//! bearer_token, private_key, password_field, line_id.
//!
//! Future batches (2-4) add context-window, checksum, and Thai-
//! specific detectors. The registry lookup below is what the
//! parity tests dispatch through; the Batch 4 gRPC server will
//! reuse the same lookup.

pub mod base;
pub mod bearer_token;
pub mod email;
pub mod iban;
pub mod ip_address;
pub mod line_id;
pub mod mac_address;
pub mod password_field;
pub mod private_key;
pub mod ssn;

use base::{Detector, RawMatch};

/// Look up a detector by Python ``name`` attribute. Returns boxed
/// trait objects keyed by the same string the Python registry
/// reports — keeps the parity fixture format detector-name driven.
pub fn detector_for(name: &str) -> Option<Box<dyn Detector>> {
    match name {
        "email" => Some(Box::new(email::EmailDetector)),
        "ssn" => Some(Box::new(ssn::SsnDetector)),
        "iban" => Some(Box::new(iban::IbanDetector)),
        // The IP detector emits both v4 and v6 matches under the
        // same Python ``name="ip_address"`` so the parity wrapper
        // below produces the same flat ordering: v4 matches first,
        // then v6 (matches Python's ``detect()`` body).
        "ip_address" => Some(Box::new(IpAddressDetector)),
        "mac_address" => Some(Box::new(mac_address::MacAddressDetector)),
        "bearer_token" => Some(Box::new(bearer_token::BearerTokenDetector)),
        "private_key" => Some(Box::new(private_key::PrivateKeyDetector)),
        "password_field" => Some(Box::new(password_field::PasswordFieldDetector)),
        "line_id" => Some(Box::new(line_id::LineIdDetector)),
        _ => None,
    }
}

/// Combined IPv4 + IPv6 detector — the Python class emits both
/// match types from a single ``detect()`` so this wrapper preserves
/// that ordering for the parity test.
struct IpAddressDetector;

impl Detector for IpAddressDetector {
    fn name(&self) -> &'static str {
        "ip_address"
    }
    fn entity_type(&self) -> &'static str {
        // Per-match entity_type ("ipv4_address" / "ipv6_address")
        // would require splitting RawMatch — Batch 4 will do that
        // via the gRPC response schema. For parity testing the
        // start/end + count are sufficient.
        "ipv4_address"
    }
    fn category(&self) -> &'static str {
        "network"
    }
    fn severity(&self) -> u32 {
        2  // MEDIUM
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = ip_address::Ipv4Detector.detect(text);
        out.extend(ip_address::Ipv6Detector.detect(text));
        out
    }
}
