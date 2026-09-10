//! Private-key PEM-header detector — port of ``private_key.py``.

use fancy_regex::Regex;
use crate::detectors::base::{EngineOk};
use once_cell::sync::Lazy;

use crate::detectors::base::{byte_to_char, Detector, RawMatch};

static PEM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----")
        .expect("pem regex")
});

pub struct PrivateKeyDetector;

impl Detector for PrivateKeyDetector {
    fn name(&self) -> &'static str {
        "private_key"
    }
    fn entity_type(&self) -> &'static str {
        "private_key_header"
    }
    fn category(&self) -> &'static str {
        "secret"
    }
    fn severity(&self) -> u32 {
        4  // CRITICAL
    }

    fn detect(&self, text: &str) -> Vec<RawMatch> {
        let mut out = Vec::new();
        for m in PEM_RE.find_iter(text).engine_ok() {
            out.push(RawMatch {
                start: byte_to_char(text, m.start()),
                end: byte_to_char(text, m.end()),
                matched_text: m.as_str().to_string(),
                confidence: 1.0,
                priority: 0,
                entity_type: None,
            });
        }
        out
    }
}
