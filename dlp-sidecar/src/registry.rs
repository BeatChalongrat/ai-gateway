//! Detector registry — Batch 1 stub.
//!
//! Wires the 9 detectors from :mod:`detectors`. Batch 2-4 will
//! extend this with per-tenant gating, NER on/off, and the keyword/
//! context-window machinery that the Python registry handles in
//! ``app.services.dlp.detectors.registry``.

use crate::detectors::base::{Detector, RawMatch};
use crate::detectors::*;

/// Default registry — Batch 1 ships the 9 simple detectors.
pub fn default_detectors() -> Vec<Box<dyn Detector>> {
    vec![
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
    ]
}

/// Scan ``text`` through every detector, returning their RawMatches
/// flat-concatenated in registration order. The Batch 4 gRPC server
/// will wrap each into a DetectionMatch proto with detector metadata.
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
